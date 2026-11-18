//! Strict WRITE safety over approval-checked AST and resolved required contracts.

mod contract;

use std::{collections::HashMap, fmt};

use nsl_contract::ToolContract;
use nsl_source::ast::{ExpressionId, ExpressionKind};
use nsl_source::{SourceFile, SourcePosition, SourceSpan};

use crate::{approvals::ApprovalBindingAnalysis, diagnostics::SourceDiagnosticContext};
use contract::ContractSafety;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WriteSafetyDiagnosticKind {
    TooManyWrites,
    WriteInForeach,
    UndeclaredTool,
    WriteCapability,
    UnsupportedOperation,
    AtomicityRequired,
    IdempotencyRequired,
    RiskMetadataRequired,
}

impl WriteSafetyDiagnosticKind {
    pub const fn code(self) -> &'static str {
        match self {
            Self::TooManyWrites => "NSL-E5202",
            Self::WriteInForeach => "NSL-E5201",
            Self::UndeclaredTool => "NSL-E2201",
            Self::WriteCapability => "NSL-E5401",
            Self::UnsupportedOperation => "NSL-E5404",
            Self::AtomicityRequired => "NSL-E5402",
            Self::IdempotencyRequired => "NSL-E5403",
            Self::RiskMetadataRequired => "NSL-E5405",
        }
    }

    fn message(self, tool: &str) -> String {
        match self {
            Self::TooManyWrites => "only one WRITE is allowed per skill in NSL-0.2-STRICT".into(),
            Self::WriteInForeach => "WRITE is not allowed inside FOREACH in NSL-0.2-STRICT".into(),
            Self::UndeclaredTool => format!("tool not declared in requires: {tool}"),
            Self::WriteCapability => format!("WRITE requires a WRITE tool contract: {tool}"),
            Self::UnsupportedOperation => format!("unsupported WRITE operation: {tool}"),
            Self::AtomicityRequired => format!("WRITE tool must be ATOMIC: {tool}"),
            Self::IdempotencyRequired => format!("WRITE tool must require idempotency: {tool}"),
            Self::RiskMetadataRequired => format!("WRITE tool risk metadata is invalid: {tool}"),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteSafetyDiagnostic {
    pub kind: WriteSafetyDiagnosticKind,
    pub message: String,
    pub span: SourceSpan,
    pub logical_path: Option<String>,
    pub snippet: Option<String>,
}

impl WriteSafetyDiagnostic {
    pub fn position(&self) -> Option<SourcePosition> {
        self.logical_path.as_ref().map(|_| self.span.start())
    }
}

impl fmt::Display for WriteSafetyDiagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.kind.code(), self.message)
    }
}

impl std::error::Error for WriteSafetyDiagnostic {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum WriteSafetyError {
    Diagnostic(Box<WriteSafetyDiagnostic>),
    InvalidApprovalAnalysis,
    InvalidWriteExpression(ExpressionId),
}

impl fmt::Display for WriteSafetyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Diagnostic(error) => error.fmt(f),
            Self::InvalidApprovalAnalysis => {
                f.write_str("approval analysis has no originating AST")
            }
            Self::InvalidWriteExpression(_) => {
                f.write_str("invalid WRITE expression in approval analysis")
            }
        }
    }
}

impl std::error::Error for WriteSafetyError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Diagnostic(error) => Some(error.as_ref()),
            Self::InvalidApprovalAnalysis | Self::InvalidWriteExpression(_) => None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteSafetyAnalysis {
    pub write_node_count: usize,
    pub approval_node_count: usize,
    pub approval_write_mapping_count: usize,
    pub unused_approval_count: usize,
    pub max_write_calls: usize,
    pub writes_inside_loop: bool,
    pub all_writes_approved_exactly_once: bool,
    pub all_write_operations_supported: bool,
    pub all_write_tools_atomic: bool,
    pub all_write_tools_idempotent: bool,
    pub all_write_risk_metadata_present: bool,
    pub requires_verified_package_for_write: bool,
}

pub struct WriteSafetyAnalyzer<'source> {
    diagnostics: SourceDiagnosticContext<'source>,
}

impl<'source> WriteSafetyAnalyzer<'source> {
    pub fn new(sources: impl IntoIterator<Item = &'source SourceFile>) -> Self {
        Self {
            diagnostics: SourceDiagnosticContext::new(sources),
        }
    }

    /// Contracts must be the exact, already-resolved `requires` declarations,
    /// as in the Oracle pipeline. This stage neither hashes contracts nor calls
    /// a provider and has no principal/tenant/entitlement input or relaxations.
    pub fn analyze(
        &self,
        approvals: &ApprovalBindingAnalysis<'_>,
        contracts_by_id: &HashMap<&str, &ToolContract>,
    ) -> Result<WriteSafetyAnalysis, WriteSafetyError> {
        let skill = approvals
            .skill()
            .ok_or(WriteSafetyError::InvalidApprovalAnalysis)?;
        let write = |id| {
            let expression = skill
                .expression(id)
                .ok_or(WriteSafetyError::InvalidWriteExpression(id))?;
            let ExpressionKind::Write { tool_id, .. } = &expression.kind else {
                return Err(WriteSafetyError::InvalidWriteExpression(id));
            };
            Ok((tool_id.as_str(), &expression.span))
        };
        let count = approvals.bindings().len();
        if count > 1 {
            let (_, span) = write(approvals.bindings()[1].write)?;
            return Err(self.error(WriteSafetyDiagnosticKind::TooManyWrites, "", span));
        }
        if let Some(binding) = approvals
            .bindings()
            .iter()
            .find(|binding| binding.inside_foreach)
        {
            let (_, span) = write(binding.write)?;
            return Err(self.error(WriteSafetyDiagnosticKind::WriteInForeach, "", span));
        }
        for binding in approvals.bindings() {
            let (tool_id, span) = write(binding.write)?;
            let tool = contracts_by_id.get(tool_id).ok_or_else(|| {
                self.error(WriteSafetyDiagnosticKind::UndeclaredTool, tool_id, span)
            })?;
            if let Some(kind) = ContractSafety::from(*tool).violation() {
                return Err(self.error(kind, tool_id, span));
            }
        }
        Ok(WriteSafetyAnalysis {
            write_node_count: count,
            approval_node_count: approvals.approvals().len(),
            approval_write_mapping_count: count,
            unused_approval_count: 0,
            max_write_calls: count,
            writes_inside_loop: false,
            all_writes_approved_exactly_once: true,
            all_write_operations_supported: true,
            all_write_tools_atomic: true,
            all_write_tools_idempotent: true,
            all_write_risk_metadata_present: true,
            requires_verified_package_for_write: count > 0,
        })
    }

    fn error(
        &self,
        kind: WriteSafetyDiagnosticKind,
        tool_id: &str,
        span: &SourceSpan,
    ) -> WriteSafetyError {
        let (logical_path, snippet) = self.diagnostics.location(span);
        WriteSafetyError::Diagnostic(Box::new(WriteSafetyDiagnostic {
            kind,
            message: nsl_core::redact_text(&kind.message(tool_id)),
            span: span.clone(),
            logical_path,
            snippet,
        }))
    }
}
