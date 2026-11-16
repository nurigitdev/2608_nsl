use std::{collections::HashSet, fmt};

use nsl_core::{DataClassification, RiskLevel, ToolCapability, TypeRef};

use crate::{ContractError, OperationKind, WriteSemantics, is_blank};

pub const MAX_TOOL_TIMEOUT_MS: u32 = 2_147_483_647;

/// The declared capability. Only recognized values have a Core capability.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContractCapability(Capability);

#[derive(Clone, Debug, Eq, PartialEq)]
enum Capability {
    Known(ToolCapability),
    Unregistered(String),
}

impl ContractCapability {
    fn parse(text: String) -> Result<Self, ContractError> {
        if is_blank(&text) {
            return Err(ContractError::BlankCapability);
        }
        let capability = match text.parse() {
            Ok(known) => Capability::Known(known),
            Err(_) => Capability::Unregistered(text),
        };
        Ok(Self(capability))
    }

    pub fn as_str(&self) -> &str {
        match &self.0 {
            Capability::Known(capability) => capability.as_str(),
            Capability::Unregistered(text) => text,
        }
    }

    pub const fn known(&self) -> Option<ToolCapability> {
        match self.0 {
            Capability::Known(capability) => Some(capability),
            Capability::Unregistered(_) => None,
        }
    }
}

impl fmt::Display for ContractCapability {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolContractOptions {
    pub timeout_ms: i64,
    pub risk: String,
    pub empty_is_valid: bool,
    pub operation_kind: Option<String>,
    pub risk_level: Option<String>,
    pub write_semantics: Option<WriteSemantics>,
}

impl Default for ToolContractOptions {
    fn default() -> Self {
        Self {
            timeout_ms: 30_000,
            risk: "READ_ONLY".into(),
            empty_is_valid: true,
            operation_kind: None,
            risk_level: None,
            write_semantics: None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolContractSpec {
    pub tool_id: String,
    pub version: String,
    pub capability: String,
    pub input_types: Vec<(String, TypeRef)>,
    pub output_type: TypeRef,
    pub required_scope: String,
    pub output_classification: DataClassification,
    pub options: ToolContractOptions,
}

/// Validated provider-independent Tool Contract semantics.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolContract {
    tool_id: String,
    version: String,
    capability: ContractCapability,
    input_types: Vec<(String, TypeRef)>,
    output_type: TypeRef,
    required_scope: String,
    output_classification: DataClassification,
    timeout_ms: u32,
    risk: String,
    empty_is_valid: bool,
    operation_kind: Option<OperationKind>,
    risk_level: Option<RiskLevel>,
    write_semantics: Option<WriteSemantics>,
}

impl ToolContract {
    pub fn new(spec: ToolContractSpec) -> Result<Self, ContractError> {
        if !canonical_version(&spec.version) {
            return Err(ContractError::InvalidVersion);
        }
        let capability = ContractCapability::parse(spec.capability)?;
        if is_blank(&spec.options.risk) {
            return Err(ContractError::BlankRisk);
        }
        if spec.input_types.iter().any(|(name, _)| is_blank(name)) {
            return Err(ContractError::BlankInputName);
        }
        let mut names = HashSet::new();
        if spec.input_types.iter().any(|(name, _)| !names.insert(name)) {
            return Err(ContractError::DuplicateInputName);
        }
        let timeout_ms = u32::try_from(spec.options.timeout_ms)
            .ok()
            .filter(|timeout| (1..=MAX_TOOL_TIMEOUT_MS).contains(timeout))
            .ok_or(ContractError::InvalidTimeout)?;
        let risk_level = spec
            .options
            .risk_level
            .as_deref()
            .map(str::parse)
            .transpose()
            .map_err(|_| ContractError::InvalidRiskLevel)?;
        let operation_kind = spec
            .options
            .operation_kind
            .as_deref()
            .map(str::parse)
            .transpose()?;
        let write_semantics = spec.options.write_semantics;

        if capability.known() == Some(ToolCapability::Write) {
            if operation_kind.is_none() {
                return Err(ContractError::MissingWriteOperation);
            }
            if risk_level.is_none() {
                return Err(ContractError::MissingWriteRiskLevel);
            }
            if write_semantics.is_none() {
                return Err(ContractError::MissingWriteSemantics);
            }
        } else if operation_kind.is_some() || write_semantics.is_some() {
            return Err(ContractError::WriteFieldsRequireWriteCapability);
        }

        Ok(Self {
            tool_id: spec.tool_id,
            version: spec.version,
            capability,
            input_types: spec.input_types,
            output_type: spec.output_type,
            required_scope: spec.required_scope,
            output_classification: spec.output_classification,
            timeout_ms,
            risk: spec.options.risk,
            empty_is_valid: spec.options.empty_is_valid,
            operation_kind,
            risk_level,
            write_semantics,
        })
    }

    pub fn tool_id(&self) -> &str {
        &self.tool_id
    }

    pub fn version(&self) -> &str {
        &self.version
    }

    pub const fn capability(&self) -> &ContractCapability {
        &self.capability
    }

    pub fn input_types(&self) -> &[(String, TypeRef)] {
        &self.input_types
    }

    pub fn input_type(&self, name: &str) -> Option<&TypeRef> {
        self.input_types
            .iter()
            .find_map(|(key, ty)| (key == name).then_some(ty))
    }

    pub const fn output_type(&self) -> &TypeRef {
        &self.output_type
    }

    pub fn required_scope(&self) -> &str {
        &self.required_scope
    }

    pub const fn output_classification(&self) -> DataClassification {
        self.output_classification
    }

    pub const fn timeout_ms(&self) -> u32 {
        self.timeout_ms
    }

    pub fn risk(&self) -> &str {
        &self.risk
    }

    pub const fn empty_is_valid(&self) -> bool {
        self.empty_is_valid
    }

    pub const fn operation_kind(&self) -> Option<OperationKind> {
        self.operation_kind
    }

    pub const fn risk_level(&self) -> Option<RiskLevel> {
        self.risk_level
    }

    pub const fn write_semantics(&self) -> Option<&WriteSemantics> {
        self.write_semantics.as_ref()
    }
}

fn canonical_version(version: &str) -> bool {
    let mut parts = version.split('.');
    let (Some(major), Some(minor), Some(patch), None) =
        (parts.next(), parts.next(), parts.next(), parts.next())
    else {
        return false;
    };
    [major, minor, patch].into_iter().all(|part| {
        !part.is_empty()
            && part.bytes().all(|byte| byte.is_ascii_digit())
            && (part == "0" || !part.starts_with('0'))
    })
}
