use std::{collections::BTreeSet, fmt, str::FromStr};

use nsl_core::{DataClassification, Integer, PayloadHash, RiskLevel, ToolCapability, TypeRef};

use crate::Statement;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IrVersion {
    V1_0,
    V1_1,
}

impl IrVersion {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::V1_0 => "1.0",
            Self::V1_1 => "1.1",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct UnsupportedIrVersion;

impl fmt::Display for UnsupportedIrVersion {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("unsupported NSO IR version")
    }
}

impl std::error::Error for UnsupportedIrVersion {}

impl FromStr for IrVersion {
    type Err = UnsupportedIrVersion;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "1.0" => Ok(Self::V1_0),
            "1.1" => Ok(Self::V1_1),
            _ => Err(UnsupportedIrVersion),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SymbolSpec {
    pub symbol_id: String,
    pub name: String,
    pub category: String,
    pub type_info: TypeRef,
    pub classification: DataClassification,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InputSpec {
    pub symbol_id: String,
    pub name: String,
    pub type_info: TypeRef,
    pub required: bool,
    pub classification: DataClassification,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextSpec {
    pub symbol_id: String,
    pub name: String,
    pub type_info: TypeRef,
    pub path: Vec<String>,
    pub classification: DataClassification,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutputField {
    pub name: String,
    pub type_info: TypeRef,
    pub classification: DataClassification,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RequiredToolKind {
    ReadV1_0,
    V1_1 {
        capability: ToolCapability,
        risk_level: RiskLevel,
    },
}

impl RequiredToolKind {
    pub const fn capability(self) -> ToolCapability {
        match self {
            Self::ReadV1_0 => ToolCapability::Read,
            Self::V1_1 { capability, .. } => capability,
        }
    }

    pub const fn risk_level(self) -> Option<RiskLevel> {
        match self {
            Self::ReadV1_0 => None,
            Self::V1_1 { risk_level, .. } => Some(risk_level),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RequiredTool {
    pub tool_ref: String,
    pub tool_id: String,
    pub version: String,
    pub kind: RequiredToolKind,
    pub contract_hash: PayloadHash,
    pub required_scope: String,
    pub output_classification: DataClassification,
}

// The Oracle schema applies minimums to counts but accepts integers wider than u64.
// Keep them exact in the model so later boundaries can apply their own checks.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResourceLimits {
    pub tool_calls: Integer,
    pub loop_iterations: Integer,
    pub emitted_rows: Integer,
    pub collection_size: Integer,
    pub duration_ms: Integer,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StaticAnalysis {
    pub max_tool_calls: Integer,
    pub max_loop_iterations: Integer,
    pub max_emit_records: Integer,
    pub bounded: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BuildSource {
    pub logical_path: String,
    pub content_hash: PayloadHash,
    pub size_bytes: Integer,
    pub is_root: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BuildMetadata {
    pub source_bundle_sha256: PayloadHash,
    pub root_source: String,
    pub sources: Vec<BuildSource>,
}

/// In-memory NSO. External document and cross-node validation belong to admission.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SkillObject {
    pub ir_version: IrVersion,
    pub language_version: String,
    pub skill_id: String,
    pub skill_version: String,
    pub risk: String,
    pub semantics_profile: String,
    pub semantic_hash: PayloadHash,
    pub features: BTreeSet<String>,
    pub symbols: Vec<SymbolSpec>,
    pub required_tools: Vec<RequiredTool>,
    pub limits: ResourceLimits,
    pub inputs: Vec<InputSpec>,
    pub contexts: Vec<ContextSpec>,
    pub outputs: Vec<OutputField>,
    pub body: Vec<Statement>,
    pub analysis: StaticAnalysis,
    pub build: BuildMetadata,
}
