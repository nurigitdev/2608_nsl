use std::{collections::BTreeMap, fmt::Write};

use nsl_contract::{
    CatalogErrorKind, CommitSemantics, ContractError, IdempotencySemantics, PostWriteVerification,
    ToolContract, ToolContractCatalog, ToolContractOptions, ToolContractSpec, WriteSemantics,
    WriteSemanticsSpec,
};
use nsl_core::{TypeKind, TypeRef};

const ORACLE: &str = include_str!("fixtures/contract-model-oracle.tsv");

type Settings<'a> = BTreeMap<&'a str, String>;

fn setting<'a>(data: &'a Settings<'_>, key: &str, default: &'a str) -> &'a str {
    data.get(key).map_or(default, String::as_str)
}

fn optional(data: &Settings<'_>, key: &str) -> Option<String> {
    let text = setting(data, key, "~");
    (text != "~").then(|| text.to_owned())
}

fn string_type() -> TypeRef {
    TypeRef::new(TypeKind::Primitive("String".into())).unwrap()
}

fn draft_type() -> TypeRef {
    TypeRef::new(TypeKind::Domain("Draft".into())).unwrap()
}

fn input(name: &str, ty: TypeRef) -> (String, TypeRef) {
    (name.into(), ty)
}

fn idempotency(data: &Settings<'_>) -> Result<IdempotencySemantics, ContractError> {
    IdempotencySemantics::new(
        setting(data, "required", "true").parse().unwrap(),
        setting(data, "scope", "TOOL_AND_TENANT"),
        setting(data, "retention", "PROVIDER_DEFINED"),
        setting(data, "duplicate_behavior", "RETURN_ORIGINAL_RESULT"),
        setting(data, "payload_conflict_behavior", "IDEMPOTENCY_CONFLICT"),
    )
}

fn verification(data: &Settings<'_>) -> Result<PostWriteVerification, ContractError> {
    let schema = match setting(data, "reference_schema", "~") {
        "~" => None,
        "domain" => Some(draft_type()),
        "optional" => Some(TypeRef::new(TypeKind::Optional(Box::new(string_type()))).unwrap()),
        value => panic!("invalid reference schema setting: {value}"),
    };
    PostWriteVerification::new(
        optional(data, "tool_id"),
        schema,
        optional(data, "consistency_hint"),
    )
}

fn write_semantics(data: &Settings<'_>) -> Result<WriteSemantics, ContractError> {
    let idem =
        (setting(data, "idempotency", "") != "~").then(|| idempotency(&Settings::new()).unwrap());
    let commit = (setting(data, "commit", "") != "~")
        .then(|| CommitSemantics::new("EXPLICIT".parse().unwrap()));
    let verification = (setting(data, "verification", "") == "full").then(|| {
        PostWriteVerification::new(
            Some("TOOL.VERIFY".into()),
            Some(draft_type()),
            Some(" READ_AFTER_WRITE ".into()),
        )
        .unwrap()
    });
    WriteSemantics::new(WriteSemanticsSpec {
        side_effect: setting(data, "side_effect", "true").parse().unwrap(),
        atomicity: setting(data, "atomicity", "ATOMIC").into(),
        idempotency: idem,
        commit,
        approval_required: setting(data, "approval_required", "true").parse().unwrap(),
        preconditions_supported: setting(data, "preconditions_supported", "true")
            .parse()
            .unwrap(),
        verification,
    })
}

fn spec(data: &Settings<'_>) -> ToolContractSpec {
    let ordered = || {
        let money = TypeRef::new(TypeKind::Money("KRW".parse().unwrap())).unwrap();
        vec![
            input("z_label", string_type()),
            input(
                "a_amounts",
                TypeRef::new(TypeKind::List(Box::new(money))).unwrap(),
            ),
        ]
    };
    let input_types = match setting(data, "inputs", "ordered") {
        "ordered" => ordered(),
        "empty" => Vec::new(),
        "duplicate" => {
            let mut values = ordered();
            values.push(input(
                "z_label",
                TypeRef::new(TypeKind::Domain("Duplicate".into())).unwrap(),
            ));
            values
        }
        "blank" => vec![input("\u{a0}\u{1f}", string_type())],
        "duplicate_and_blank" => ["same", "same", ""]
            .map(|key| input(key, string_type()))
            .to_vec(),
        "padded" => ["name", " name "]
            .map(|key| input(key, string_type()))
            .to_vec(),
        value => panic!("invalid inputs setting: {value}"),
    };
    let mut options = ToolContractOptions::default();
    if let Some(value) = data.get("timeout_ms") {
        options.timeout_ms = value.parse().unwrap();
    }
    if let Some(value) = data.get("risk") {
        options.risk = value.clone();
    }
    if let Some(value) = data.get("empty_is_valid") {
        options.empty_is_valid = value.parse().unwrap();
    }
    options.operation_kind = optional(data, "operation_kind");
    options.risk_level = optional(data, "risk_level");
    let write = setting(data, "write", "");
    if matches!(write, "complete" | "full") {
        options.write_semantics =
            Some(write_semantics(&Settings::from([("verification", write.into())])).unwrap());
    }
    ToolContractSpec {
        tool_id: setting(data, "tool_id", "TOOL.FETCH").into(),
        version: setting(data, "version", "1.0.0").into(),
        capability: setting(data, "capability", "READ").into(),
        input_types,
        output_type: draft_type(),
        required_scope: setting(data, "required_scope", "tool:read").into(),
        output_classification: setting(data, "classification", "INTERNAL").parse().unwrap(),
        options,
    }
}

fn field(text: Option<&str>) -> String {
    match text {
        None => "~".into(),
        Some("") => "-".into(),
        Some(text) => text.bytes().map(|byte| format!("{byte:02x}")).collect(),
    }
}

fn decode(field: &str) -> String {
    if field == "-" {
        return String::new();
    }
    assert!(field.len().is_multiple_of(2));
    let bytes = (0..field.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&field[i..i + 2], 16).unwrap())
        .collect();
    String::from_utf8(bytes).unwrap()
}

fn ty(item: &TypeRef) -> String {
    match item.kind() {
        TypeKind::Primitive(name) => format!("primitive:{name}"),
        TypeKind::Domain(name) => format!("domain:{name}"),
        TypeKind::Money(currency) => format!("money:{}", currency.as_str()),
        TypeKind::List(item) => format!("list:{}", ty(item)),
        TypeKind::Optional(item) => format!("optional:{}", ty(item)),
        other => panic!("unexpected fixture type: {other:?}"),
    }
}

fn observe_commit(out: &mut String, commit: CommitSemantics) {
    writeln!(out, "commit\t{}", commit.confirmation()).unwrap();
}

fn observe_idempotency(out: &mut String, value: &IdempotencySemantics) {
    writeln!(
        out,
        "idempotency\t{}\t{}\t{}\t{}\t{}",
        value.required(),
        field(Some(value.scope())),
        field(Some(value.retention())),
        field(Some(value.duplicate_behavior())),
        field(Some(value.payload_conflict_behavior()))
    )
    .unwrap();
}

fn observe_verification(out: &mut String, value: &PostWriteVerification) {
    let schema = value.reference_schema().map_or_else(|| "~".into(), ty);
    writeln!(
        out,
        "verification\t{}\t{schema}\t{}",
        field(value.tool_id()),
        field(value.consistency_hint())
    )
    .unwrap();
}

fn observe_write(out: &mut String, value: &WriteSemantics) {
    writeln!(
        out,
        "write\t{}\t{}\t{}\t{}",
        value.side_effect(),
        value.atomicity(),
        value.approval_required(),
        value.preconditions_supported()
    )
    .unwrap();
    observe_idempotency(out, value.idempotency());
    observe_commit(out, value.commit());
    if let Some(verification) = value.verification() {
        observe_verification(out, verification);
    }
}

fn observe_contract(out: &mut String, contract: &ToolContract) {
    let capability = contract.capability();
    let variant = if capability.known().is_some() {
        "known"
    } else {
        "unregistered"
    };
    assert_eq!(capability.to_string(), capability.as_str());
    if let Some(known) = capability.known() {
        assert_eq!(known.as_str(), capability.as_str());
    }
    let operation = contract
        .operation_kind()
        .map_or_else(|| "~".into(), |v| v.to_string());
    let level = contract.risk_level().map_or("~", |v| v.as_str());
    writeln!(
        out,
        "contract\t{}\t{}\t{}\t{variant}\t{}\t{}\t{}\t{}\t{}\t{operation}\t{level}",
        field(Some(contract.tool_id())),
        field(Some(contract.version())),
        field(Some(capability.as_str())),
        field(Some(contract.required_scope())),
        contract.output_classification(),
        contract.timeout_ms(),
        field(Some(contract.risk())),
        contract.empty_is_valid()
    )
    .unwrap();
    for (name, item) in contract.input_types() {
        assert_eq!(contract.input_type(name), Some(item));
        writeln!(out, "input\t{}\t{}", field(Some(name)), ty(item)).unwrap();
    }
    assert_eq!(contract.input_type("missing"), None);
    writeln!(out, "output\t{}", ty(contract.output_type())).unwrap();
    if let Some(write) = contract.write_semantics() {
        observe_write(out, write);
    }
}

fn observe_catalog(out: &mut String, data: &Settings<'_>) {
    let layout = setting(data, "layout", "basic");
    let mut first = spec(&Settings::new());
    if layout == "empty_id" {
        first.tool_id.clear();
    }
    let mut second = first.clone();
    second.version = "2.0.1".into();
    let mut other = first.clone();
    other.tool_id = "TOOL.OTHER".into();
    other.capability = "Legacy.Read".into();
    let first = ToolContract::new(first).unwrap();
    let second = ToolContract::new(second).unwrap();
    let other = ToolContract::new(other).unwrap();
    let contracts = match layout {
        "empty" => Vec::new(),
        "basic" => vec![first, second, other],
        "duplicate" => vec![first.clone(), second, first],
        "empty_id" => vec![first],
        value => panic!("invalid catalog layout: {value}"),
    };
    let catalog = ToolContractCatalog::new(contracts);
    let id = setting(data, "tool_id", "TOOL.FETCH");
    let version = setting(data, "version", "1.0.0");
    let result = catalog.as_ref().map_err(Clone::clone).and_then(|catalog| {
        match setting(data, "method", "resolve") {
            "resolve" => catalog.resolve(id, version),
            "get" => catalog.get(id, version),
            value => panic!("invalid catalog method: {value}"),
        }
    });
    match result {
        Ok(contract) => {
            writeln!(
                out,
                "lookup\t{}\t{}\t{}",
                field(Some(contract.tool_id())),
                field(Some(contract.version())),
                field(Some(contract.capability().as_str()))
            )
            .unwrap();
        }
        Err(error) => {
            assert_eq!(error.tool_id, id);
            assert_eq!(error.version, version);
            let (kind, message) = match error.kind {
                CatalogErrorKind::DuplicateToolContract => {
                    ("DuplicateToolContractError", "duplicate tool contract")
                }
                CatalogErrorKind::MissingExactContract => ("KeyError", "unknown tool contract"),
                CatalogErrorKind::UnknownToolContract => {
                    ("UnknownToolContractError", "unknown tool contract")
                }
                CatalogErrorKind::IncompatibleToolVersion => {
                    ("IncompatibleToolVersionError", "incompatible tool version")
                }
            };
            assert_eq!(error.to_string(), message);
            writeln!(out, "catalog_error\t{kind}").unwrap();
        }
    }
}

fn observe(kind: &str, data: &Settings<'_>) -> String {
    let mut out = String::new();
    let result = match kind {
        "contract" => {
            ToolContract::new(spec(data)).map(|contract| observe_contract(&mut out, &contract))
        }
        "idempotency" => idempotency(data).map(|value| observe_idempotency(&mut out, &value)),
        "verification" => verification(data).map(|value| observe_verification(&mut out, &value)),
        "write" => write_semantics(data).map(|value| observe_write(&mut out, &value)),
        "commit" => data["confirmation"]
            .parse()
            .map(|value| observe_commit(&mut out, CommitSemantics::new(value))),
        "catalog" => {
            observe_catalog(&mut out, data);
            Ok(())
        }
        value => panic!("invalid model: {value}"),
    };
    if let Err(error) = result {
        writeln!(out, "error\t{error}").unwrap();
    }
    out
}

#[test]
fn tool_contract_fields_validation_and_catalog_match_frozen_oracle() {
    let cases = ORACLE
        .strip_prefix("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a\ncase\t")
        .unwrap();
    for case in cases.split("\ncase\t") {
        let (input, expected) = case.split_once("\nexpected\n").unwrap();
        let mut rows = input.lines();
        let (kind, label) = rows.next().unwrap().split_once('\t').unwrap();
        let data: Settings<'_> = rows
            .map(|row| {
                let (field, value) = row.strip_prefix("set\t").unwrap().split_once('\t').unwrap();
                (field, decode(value))
            })
            .collect();
        let actual = observe(kind, &data);
        for (index, (actual, expected)) in actual.lines().zip(expected.lines()).enumerate() {
            assert_eq!(
                actual,
                expected,
                "{kind}: {label}, observation {}",
                index + 1
            );
        }
        assert_eq!(
            actual.lines().count(),
            expected.lines().count(),
            "{kind}: {label}"
        );
    }
}
