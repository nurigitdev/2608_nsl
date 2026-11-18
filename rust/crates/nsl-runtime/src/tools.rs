use std::{collections::BTreeMap, fmt};

use nsl_contract::ContractEncodingError;
use nsl_core::{Completeness, DataClassification, Presence, TypeRef, Value, ValueEnvelope};

use crate::{ExecutionPrincipal, ResultEncodingError};

/// Provider-independent correlation data. Raw identities match the Oracle's
/// model boundary; admission/WRITE authority belongs to the execution boundary.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolCallRequest {
    pub execution_id: String,
    pub invocation_id: String,
    pub node_id: String,
    pub tool_id: String,
    pub tool_version: String,
    pub contract_hash: String,
    pub arguments: BTreeMap<String, ValueEnvelope>,
    pub principal: ExecutionPrincipal,
    pub authorization_decision_ref: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolResultEnvelope {
    pub invocation_id: String,
    pub tool_id: String,
    pub tool_version: String,
    pub value: Value,
    pub type_info: TypeRef,
    pub presence: Presence,
    pub completeness: Completeness,
    pub classification: DataClassification,
    pub result_hash: String,
    pub snapshot_ref: Option<String>,
}

impl ToolResultEnvelope {
    pub fn to_value(&self, provenance_ref: String) -> ValueEnvelope {
        ValueEnvelope::new(
            self.value.clone(),
            self.type_info.clone(),
            self.presence,
            self.completeness,
            self.classification,
            vec![provenance_ref],
        )
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolExecutionError {
    pub code: String,
    pub message: String,
}
impl ToolExecutionError {
    pub fn new(code: impl Into<String>, message: String) -> Self {
        Self {
            code: code.into(),
            message,
        }
    }
}
impl fmt::Display for ToolExecutionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}
impl std::error::Error for ToolExecutionError {}

/// Cancellation is distinct from business failure, provider timeout, and commit
/// state. Unexpected provider/sink details are retained for protected host use;
/// Display and the public error chain do not disclose them.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ToolCallError {
    Execution(ToolExecutionError),
    Encoding(ResultEncodingError),
    ContractEncoding(ContractEncodingError),
    Cancelled,
    Provider(String),
    ProviderTimeout,
    Measurement(String),
}
impl fmt::Display for ToolCallError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Execution(e) => e.fmt(f),
            Self::Encoding(e) => e.fmt(f),
            Self::ContractEncoding(e) => e.fmt(f),
            Self::Cancelled => f.write_str("tool execution cancelled"),
            Self::Provider(_) | Self::ProviderTimeout => f.write_str("tool provider failed"),
            Self::Measurement(_) => f.write_str("tool measurement failed"),
        }
    }
}
impl std::error::Error for ToolCallError {}
impl From<ToolExecutionError> for ToolCallError {
    fn from(e: ToolExecutionError) -> Self {
        Self::Execution(e)
    }
}
impl From<ResultEncodingError> for ToolCallError {
    fn from(e: ResultEncodingError) -> Self {
        Self::Encoding(e)
    }
}
impl From<ContractEncodingError> for ToolCallError {
    fn from(e: ContractEncodingError) -> Self {
        Self::ContractEncoding(e)
    }
}
