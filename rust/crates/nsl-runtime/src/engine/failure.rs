use crate::*;
use nsl_core::{Completeness, ExecutionStatus};
use nsl_ir::NsoCodecError;
use std::fmt;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RuntimeBindingLocation {
    Input(String),
    Context(Vec<String>),
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeInputTypeError {
    pub symbol_id: String,
    pub location: RuntimeBindingLocation,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RuntimeTerminalKind {
    InvalidValidationSummary(ModelError),
    InputType(RuntimeInputTypeError),
    StatementType(StatementTypeError),
    Nso(NsoCodecError),
    ContractEncoding(nsl_contract::ContractEncodingError),
    Encoding(ResultEncodingError),
    Contract(String),
    Cancelled,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeEvidence {
    pub checks: Vec<CheckResult>,
    pub outputs: Vec<EmitRecord>,
    pub resources: ResourceUsage,
    pub data_completeness: Completeness,
    pub current_node_id: Option<String>,
}
#[derive(Clone, Eq, PartialEq)]
pub struct RuntimeTerminalError {
    pub kind: RuntimeTerminalKind,
    pub evidence: Box<RuntimeEvidence>,
}
impl fmt::Debug for RuntimeTerminalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RuntimeTerminalError")
            .field("kind", &self.kind)
            .field("check_count", &self.evidence.checks.len())
            .field("output_count", &self.evidence.outputs.len())
            .field("resources", &self.evidence.resources)
            .finish()
    }
}
impl fmt::Display for RuntimeTerminalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match &self.kind {
            RuntimeTerminalKind::InvalidValidationSummary(_) => {
                "runtime validation summary could not be constructed"
            }
            RuntimeTerminalKind::InputType(_) => "runtime input/context type mismatch",
            RuntimeTerminalKind::StatementType(_) => "runtime statement type mismatch",
            RuntimeTerminalKind::Nso(_)
            | RuntimeTerminalKind::Encoding(_)
            | RuntimeTerminalKind::ContractEncoding(_) => "runtime encoding limit or model failure",
            RuntimeTerminalKind::Contract(_) => "invalid controlled execution request",
            RuntimeTerminalKind::Cancelled => "runtime execution cancelled",
        })
    }
}
impl std::error::Error for RuntimeTerminalError {}
impl RuntimeTerminalError {
    pub(super) fn empty(kind: RuntimeTerminalKind) -> Self {
        Self {
            kind,
            evidence: Box::new(RuntimeEvidence {
                checks: vec![],
                outputs: vec![],
                resources: ResourceUsage::default(),
                data_completeness: Completeness::Unknown,
                current_node_id: None,
            }),
        }
    }
    pub(super) fn capture<C: RuntimeClock>(
        kind: RuntimeTerminalKind,
        ctx: &ExecutionContext<C>,
    ) -> Self {
        Self {
            kind,
            evidence: Box::new(RuntimeEvidence {
                checks: ctx.checks.clone(),
                outputs: ctx.outputs.clone(),
                resources: ctx.usage(),
                data_completeness: ctx.data_completeness(),
                current_node_id: ctx.current_node_id.clone(),
            }),
        }
    }
}

pub(super) struct Failure {
    pub status: ExecutionStatus,
    pub error: RuntimeErrorInfo,
    pub unexpected: bool,
}
fn failure(code: &str, category: &str, message: String, status: ExecutionStatus) -> Failure {
    Failure {
        status,
        error: RuntimeErrorInfo {
            code: code.into(),
            category: category.into(),
            message,
            node_id: None,
            detail_code: None,
        },
        unexpected: false,
    }
}
fn runtime(message: String) -> Failure {
    failure("NSL-E8001", "RUNTIME", message, ExecutionStatus::Failed)
}
fn unexpected() -> Failure {
    let mut f = failure(
        "NSL-E8002",
        "RUNTIME",
        "An unexpected runtime error occurred.".into(),
        ExecutionStatus::Failed,
    );
    f.unexpected = true;
    f
}
fn binding(e: BindingError) -> Failure {
    let mut f = runtime(e.to_string());
    f.error.detail_code = e.detail_code().map(Into::into);
    f
}
fn expression(e: ExpressionRuntimeError) -> Failure {
    match e {
        ExpressionRuntimeError::Evaluation(e) => {
            let mut f = runtime(e.to_string());
            f.error.node_id = Some(e.node_id);
            f.error.detail_code = Some(e.detail_code.into());
            f
        }
        ExpressionRuntimeError::Contract(s) | ExpressionRuntimeError::Money { message: s, .. } => {
            runtime(s)
        }
        ExpressionRuntimeError::Binding(e) => binding(e),
        ExpressionRuntimeError::Limit(e) => failure(
            "NSL-E6001",
            "RESOURCE",
            e.to_string(),
            ExecutionStatus::LimitExceeded,
        ),
        _ => unexpected(),
    }
}
fn read(e: ReadExecutionError) -> Result<Failure, RuntimeTerminalKind> {
    Ok(match e {
        ReadExecutionError::Expression(e) => expression(e),
        ReadExecutionError::Authorization(e) => failure(
            "NSL-E5201",
            "AUTHORIZATION",
            e.message,
            ExecutionStatus::Failed,
        ),
        ReadExecutionError::Encoding(e) => return Err(RuntimeTerminalKind::Encoding(e)),
        ReadExecutionError::Tool(ToolCallError::Encoding(e)) => {
            return Err(RuntimeTerminalKind::Encoding(e));
        }
        ReadExecutionError::Tool(ToolCallError::Cancelled) => {
            return Err(RuntimeTerminalKind::Cancelled);
        }
        ReadExecutionError::Tool(ToolCallError::Execution(e)) => {
            let mut f = failure(
                if e.code == "TOOL_CONTRACT_MISMATCH" {
                    "NSL-E4001"
                } else {
                    "NSL-E4101"
                },
                "TOOL",
                e.message,
                ExecutionStatus::ToolError,
            );
            f.error.detail_code = Some(e.code);
            f
        }
        _ => unexpected(),
    })
}
pub(super) fn project(e: StatementExecutionError) -> Result<Failure, RuntimeTerminalKind> {
    Ok(match e {
        StatementExecutionError::Type(e) => return Err(RuntimeTerminalKind::StatementType(e)),
        StatementExecutionError::Read(e)
        | StatementExecutionError::Check(CheckExecutionError::Read(e)) => return read(e),
        StatementExecutionError::Binding(e)
        | StatementExecutionError::Check(CheckExecutionError::Binding(e)) => binding(e),
        StatementExecutionError::Check(CheckExecutionError::Evaluation(e)) => {
            runtime(e.to_string())
        }
        StatementExecutionError::Contract(s) => runtime(s),
        StatementExecutionError::Limit(e) => failure(
            "NSL-E6001",
            "RESOURCE",
            e.to_string(),
            ExecutionStatus::LimitExceeded,
        ),
        StatementExecutionError::StatementLimit(e) => failure(
            "NSL-E6001",
            "RESOURCE",
            e.to_string(),
            ExecutionStatus::LimitExceeded,
        ),
        _ => unexpected(),
    })
}
