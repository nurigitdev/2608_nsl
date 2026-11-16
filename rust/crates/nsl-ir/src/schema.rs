mod nodes;

use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    str::FromStr,
};

use nsl_core::{
    CredentialMaterialError, CurrencyCode, DataClassification, NslDecimal, PayloadHash, RiskLevel,
    ToolCapability, ValueData, ensure_no_credential_material,
};

use crate::IrVersion;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NsoSchemaError {
    pub path: String,
    pub reason: &'static str,
}

impl fmt::Display for NsoSchemaError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "invalid NSO schema at {}: {}",
            self.path, self.reason
        )
    }
}

impl std::error::Error for NsoSchemaError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum NsoValidationError {
    CredentialMaterial(CredentialMaterialError),
    Schema(NsoSchemaError),
}

impl fmt::Display for NsoValidationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::CredentialMaterial(error) => error.fmt(formatter),
            Self::Schema(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for NsoValidationError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::CredentialMaterial(error) => Some(error),
            Self::Schema(error) => Some(error),
        }
    }
}

/// Validate structured NSO data prior to building an IR object.
/// The JSON boundary handles byte parsing and duplicate object keys.
pub fn validate_nso_document(data: &ValueData) -> Result<(), NsoValidationError> {
    ensure_no_credential_material(data).map_err(NsoValidationError::CredentialMaterial)?;
    validate_document(Cursor {
        data,
        path: "$".into(),
    })
    .map_err(NsoValidationError::Schema)
}

type Checked<T = ()> = Result<T, NsoSchemaError>;

struct Cursor<'a> {
    data: &'a ValueData,
    path: String,
}

impl<'a> Cursor<'a> {
    fn error(&self, reason: &'static str) -> NsoSchemaError {
        NsoSchemaError {
            path: self.path.clone(),
            reason,
        }
    }

    fn error_at(&self, name: &str, reason: &'static str) -> NsoSchemaError {
        NsoSchemaError {
            path: format!("{}.{name}", self.path),
            reason,
        }
    }

    fn object(&self) -> Checked<&'a BTreeMap<String, ValueData>> {
        match self.data {
            ValueData::Object(fields) => Ok(fields),
            _ => Err(self.error("expected object")),
        }
    }

    fn exact(&self, required: &[&str], optional: &[&str]) -> Checked {
        self.exact_with(&[], required, optional)
    }

    fn exact_with(&self, common: &[&str], required: &[&str], optional: &[&str]) -> Checked {
        let fields = self.object()?;
        if common
            .iter()
            .chain(required)
            .any(|name| !fields.contains_key(*name))
        {
            return Err(self.error("missing required fields"));
        }
        if fields.keys().any(|name| {
            !common.contains(&name.as_str())
                && !required.contains(&name.as_str())
                && !optional.contains(&name.as_str())
        }) {
            return Err(self.error("unexpected fields"));
        }
        Ok(())
    }

    fn field(&self, name: &str) -> Checked<Self> {
        self.optional(name)?
            .ok_or_else(|| self.error_at(name, "missing required field"))
    }

    fn optional(&self, name: &str) -> Checked<Option<Self>> {
        Ok(self.object()?.get(name).map(|data| Self {
            data,
            path: format!("{}.{name}", self.path),
        }))
    }

    fn items(&self) -> Checked<impl DoubleEndedIterator<Item = Self> + '_> {
        let ValueData::Array(values) = self.data else {
            return Err(self.error("expected array"));
        };
        Ok(values.iter().enumerate().map(|(index, data)| Self {
            data,
            path: format!("{}[{index}]", self.path),
        }))
    }

    fn text(&self, allow_empty: bool) -> Checked<&'a str> {
        match self.data {
            ValueData::String(text) if allow_empty || !text.is_empty() => Ok(text),
            ValueData::String(_) => Err(self.error("must not be empty")),
            _ => Err(self.error("expected string")),
        }
    }

    fn name(&self, name: &str) -> Checked<&'a str> {
        self.field(name)?.text(false)
    }

    fn names(&self, names: &[&str]) -> Checked {
        for &name in names {
            self.name(name)?;
        }
        Ok(())
    }

    fn constant(&self, expected: &str, reason: &'static str) -> Checked {
        if matches!(self.data, ValueData::String(text) if text == expected) {
            Ok(())
        } else {
            Err(self.error(reason))
        }
    }

    fn boolean(&self) -> Checked<bool> {
        match self.data {
            ValueData::Bool(value) => Ok(*value),
            _ => Err(self.error("expected boolean")),
        }
    }

    fn integer(&self, positive: bool) -> Checked {
        let ValueData::Number(value) = self.data else {
            return Err(self.error("expected integer"));
        };
        let text = value.as_str();
        if positive && (text == "0" || text.starts_with('-')) {
            return Err(self.error("must be at least 1"));
        }
        if text.starts_with('-') {
            return Err(self.error("must be at least 0"));
        }
        Ok(())
    }

    fn parse<T: FromStr>(&self, reason: &'static str) -> Checked<T> {
        self.text(false)?.parse().map_err(|_| self.error(reason))
    }

    fn digest(&self) -> Checked {
        self.parse::<PayloadHash>("expected lowercase sha256 digest")
            .map(drop)
    }

    fn currency(&self) -> Checked {
        self.parse::<CurrencyCode>("expected 3-letter uppercase currency code")
            .map(drop)
    }

    fn classification(&self) -> Checked {
        self.parse::<DataClassification>("unknown data classification")
            .map(drop)
    }

    fn type_document(self) -> Checked {
        let mut pending = vec![self];
        while let Some(ty) = pending.pop() {
            match ty.name("kind")? {
                "primitive" | "domain" => {
                    ty.exact(&["kind", "name"], &[])?;
                    ty.name("name")?;
                }
                "money" => {
                    ty.exact(&["kind", "currency"], &[])?;
                    ty.field("currency")?.currency()?;
                }
                "list" => {
                    ty.exact(&["kind", "item"], &[])?;
                    pending.push(ty.field("item")?);
                }
                "record" => {
                    ty.exact(&["kind", "name", "fields"], &[])?;
                    ty.name("name")?;
                    for field in ty.field("fields")?.items()?.rev() {
                        field.exact(&["name", "type"], &[])?;
                        field.name("name")?;
                        pending.push(field.field("type")?);
                    }
                }
                "enum" => {
                    ty.exact(&["kind", "name", "values"], &[])?;
                    ty.name("name")?;
                    for value in ty.field("values")?.items()? {
                        value.text(false)?;
                    }
                }
                _ => return Err(ty.error_at("kind", "unknown type kind")),
            }
        }
        Ok(())
    }

    fn literal(&self) -> Checked {
        match self.data {
            ValueData::Bool(_) | ValueData::Number(_) | ValueData::String(_) => Ok(()),
            ValueData::Object(fields) => match fields.get("$type") {
                Some(ValueData::String(tag)) if tag == "Decimal" => {
                    self.exact(&["$type", "value"], &[])?;
                    self.field("value")?
                        .parse::<NslDecimal>("expected finite decimal string")
                        .map(drop)
                }
                Some(ValueData::String(tag)) if tag == "Money" => {
                    self.exact(&["$type", "amount", "currency"], &[])?;
                    self.field("amount")?
                        .parse::<NslDecimal>("expected finite decimal string")?;
                    self.field("currency")?.currency()
                }
                _ => Err(self.error("unknown encoded literal value")),
            },
            _ => Err(self.error("expected immutable literal value")),
        }
    }
}

fn validate_document(root: Cursor<'_>) -> Checked {
    root.exact(
        &[
            "format",
            "ir_version",
            "language",
            "skill",
            "semantics_profile",
            "features",
            "symbols",
            "requires",
            "limits",
            "inputs",
            "contexts",
            "output",
            "body",
            "analysis",
            "hashes",
            "build",
        ],
        &[],
    )?;
    root.field("format")?.constant("NSO", "expected NSO")?;
    let version: IrVersion = root.field("ir_version")?.parse("unsupported IR version")?;

    let language = root.field("language")?;
    language.exact(&["name", "version"], &[])?;
    language.field("name")?.constant("NSL", "expected NSL")?;
    language.name("version")?;
    let skill = root.field("skill")?;
    skill.exact(&["id", "version", "risk"], &[])?;
    skill.names(&["id", "version", "risk"])?;
    let profile = root.name("semantics_profile")?;
    for feature in root.field("features")?.items()? {
        feature.text(false)?;
    }
    for symbol in root.field("symbols")?.items()? {
        symbol.exact(
            &["symbol_id", "name", "category", "type", "classification"],
            &[],
        )?;
        symbol.names(&["symbol_id", "name", "category"])?;
        symbol.field("classification")?.classification()?;
        symbol.field("type")?.type_document()?;
    }
    let mut tools = Vec::new();
    for tool in root.field("requires")?.items()? {
        tools.push(validate_tool(tool, version)?);
    }
    let limits = root.field("limits")?;
    let limit_fields = [
        "tool_calls",
        "loop_iterations",
        "emitted_rows",
        "collection_size",
        "duration_ms",
    ];
    limits.exact(&limit_fields, &[])?;
    for name in limit_fields {
        limits.field(name)?.integer(true)?;
    }
    for input in root.field("inputs")?.items()? {
        input.exact(
            &["symbol_id", "name", "type", "required", "classification"],
            &[],
        )?;
        input.names(&["symbol_id", "name"])?;
        input.field("classification")?.classification()?;
        input.field("type")?.type_document()?;
        input.field("required")?.boolean()?;
    }
    for context in root.field("contexts")?.items()? {
        context.exact(
            &["symbol_id", "name", "type", "path", "classification"],
            &[],
        )?;
        context.names(&["symbol_id", "name"])?;
        context.field("classification")?.classification()?;
        context.field("type")?.type_document()?;
        for part in context.field("path")?.items()? {
            part.text(false)?;
        }
    }
    for output in root.field("output")?.items()? {
        output.exact(&["name", "type", "classification"], &[])?;
        output.name("name")?;
        output.field("type")?.type_document()?;
        output.field("classification")?.classification()?;
    }

    nodes::validate_body(root.field("body")?, version, profile, tools)?;

    let analysis = root.field("analysis")?;
    let counts = ["max_tool_calls", "max_loop_iterations", "max_emit_records"];
    analysis.exact_with(&counts, &["bounded"], &[])?;
    for name in counts {
        analysis.field(name)?.integer(false)?;
    }
    analysis.field("bounded")?.boolean()?;
    let hashes = root.field("hashes")?;
    hashes.exact(&["source_bundle_sha256", "semantic_sha256"], &[])?;
    hashes.field("source_bundle_sha256")?.digest()?;
    hashes.field("semantic_sha256")?.digest()?;
    validate_build(root.field("build")?)
}

struct Tool<'a> {
    reference: Cursor<'a>,
    capability: ToolCapability,
}

fn validate_tool(tool: Cursor<'_>, version: IrVersion) -> Checked<Tool<'_>> {
    let risk: &[&str] = if version == IrVersion::V1_1 {
        &["risk_level"]
    } else {
        &[]
    };
    tool.exact_with(
        &[
            "tool_ref",
            "tool_id",
            "version",
            "capability",
            "contract_hash",
            "required_scope",
            "output_classification",
        ],
        risk,
        &[],
    )?;
    tool.names(&["tool_ref", "tool_id", "version", "required_scope"])?;
    let capability = tool
        .field("capability")?
        .parse::<ToolCapability>("unknown tool capability")?;
    if version == IrVersion::V1_0 && capability != ToolCapability::Read {
        return Err(tool.error_at("capability", "IR 1.0 supports READ capability only"));
    }
    if version == IrVersion::V1_1 {
        tool.field("risk_level")?
            .parse::<RiskLevel>("unknown risk level")?;
    }
    tool.field("output_classification")?.classification()?;
    tool.field("contract_hash")?.digest()?;
    Ok(Tool {
        reference: tool.field("tool_ref")?,
        capability,
    })
}

fn validate_build(build: Cursor<'_>) -> Checked {
    build.exact(&["source_bundle_sha256", "root_source", "sources"], &[])?;
    build.field("source_bundle_sha256")?.digest()?;
    let root_source = build.name("root_source")?;
    let sources = build.field("sources")?;
    let mut paths = BTreeSet::new();
    let mut roots = Vec::new();
    for source in sources.items()? {
        source.exact(&["logical_path", "sha256", "size_bytes", "is_root"], &[])?;
        let path = source.name("logical_path")?;
        if !paths.insert(path) {
            return Err(source.error_at("logical_path", "duplicate source logical path"));
        }
        source.field("sha256")?.digest()?;
        source.field("size_bytes")?.integer(false)?;
        if source.field("is_root")?.boolean()? {
            roots.push(path);
        }
    }
    if paths.is_empty() {
        return Err(sources.error("must not be empty"));
    }
    let [root] = roots.as_slice() else {
        return Err(sources.error("must contain exactly one root"));
    };
    if *root != root_source {
        return Err(build.error_at("root_source", "must identify the root manifest entry"));
    }
    Ok(())
}
