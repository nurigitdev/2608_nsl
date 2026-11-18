//! Approval declarations and WRITE linkage, before ordinary name/type analysis.

mod walk;

use std::{collections::HashSet, fmt};

use nsl_source::ast::{AstSkill, ExpressionId, StatementId};
use nsl_source::{SourceFile, SourceLanguageVersion, SourcePosition, SourceSpan};

use crate::diagnostics::SourceDiagnosticContext;
use crate::symbols::{ApprovalSymbol, ApprovalSymbolId, ApprovalTable};
use walk::EventKind;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ApprovalDiagnosticKind {
    DuplicateApproval,
    UnknownApproval,
    ApprovalOrder,
    MultipleWrites,
    UnusedApproval,
    ApprovalScope,
}

impl ApprovalDiagnosticKind {
    pub const fn code(self) -> &'static str {
        match self {
            Self::DuplicateApproval => "NSL-E2002",
            Self::UnknownApproval => "NSL-E5301",
            Self::ApprovalOrder => "NSL-E5302",
            Self::MultipleWrites => "NSL-E5303",
            Self::UnusedApproval => "NSL-E5304",
            Self::ApprovalScope => "NSL-E5305",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalDiagnostic {
    pub kind: ApprovalDiagnosticKind,
    pub message: String,
    pub span: SourceSpan,
    pub logical_path: Option<String>,
    pub snippet: Option<String>,
}

impl ApprovalDiagnostic {
    pub fn position(&self) -> Option<SourcePosition> {
        self.logical_path.as_ref().map(|_| self.span.start())
    }
}

impl fmt::Display for ApprovalDiagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.kind.code(), self.message)
    }
}

impl std::error::Error for ApprovalDiagnostic {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ApprovalAnalysisError {
    Diagnostic(Box<ApprovalDiagnostic>),
    InvalidStatement(StatementId),
    InvalidExpression(ExpressionId),
}

impl fmt::Display for ApprovalAnalysisError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Diagnostic(error) => error.fmt(f),
            Self::InvalidStatement(_) => f.write_str("invalid statement reference in skill AST"),
            Self::InvalidExpression(_) => f.write_str("invalid expression reference in skill AST"),
        }
    }
}

impl std::error::Error for ApprovalAnalysisError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Diagnostic(error) => Some(error.as_ref()),
            Self::InvalidStatement(_) | Self::InvalidExpression(_) => None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalDeclaration {
    pub symbol: ApprovalSymbol,
    /// One-based order among Approval and WRITE events, not all AST nodes.
    pub declaration_order: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalWriteBinding {
    pub approval: ApprovalSymbolId,
    pub write: ExpressionId,
    pub write_order: usize,
    pub inside_foreach: bool,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ApprovalBindingAnalysis<'ast> {
    origin: Option<&'ast AstSkill>,
    approvals: Vec<ApprovalDeclaration>,
    bindings: Vec<ApprovalWriteBinding>,
}

impl<'ast> ApprovalBindingAnalysis<'ast> {
    pub(crate) fn skill(&self) -> Option<&'ast AstSkill> {
        self.origin
    }
    pub fn approvals(&self) -> &[ApprovalDeclaration] {
        &self.approvals
    }

    pub fn bindings(&self) -> &[ApprovalWriteBinding] {
        &self.bindings
    }
}

pub struct ApprovalBindingAnalyzer<'source> {
    diagnostics: SourceDiagnosticContext<'source>,
}

impl<'source> ApprovalBindingAnalyzer<'source> {
    pub fn new(sources: impl IntoIterator<Item = &'source SourceFile>) -> Self {
        Self {
            diagnostics: SourceDiagnosticContext::new(sources),
        }
    }

    /// Run on a parsed/composed AST before ordinary symbol resolution. WRITE
    /// count, loop prohibition and Tool Contract safety belong to R5-S07.
    pub fn analyze<'ast>(
        &self,
        skill: &'ast AstSkill,
    ) -> Result<ApprovalBindingAnalysis<'ast>, ApprovalAnalysisError> {
        if skill.language_version != SourceLanguageVersion::V0_2 {
            return Ok(ApprovalBindingAnalysis {
                origin: Some(skill),
                ..ApprovalBindingAnalysis::default()
            });
        }
        let events = walk::events(skill)?;
        let mut table = ApprovalTable::default();
        let mut declaration_orders = Vec::new();
        // Validate all declarations before any WRITE reference, as in Oracle.
        for (index, event) in events.iter().enumerate() {
            if let EventKind::Approval {
                statement,
                name,
                span,
            } = event.kind
            {
                if event.inside_foreach {
                    return Err(self.error(
                        ApprovalDiagnosticKind::ApprovalScope,
                        "approval declarations must be in skill scope".into(),
                        span,
                    ));
                }
                if table.declare(name, statement, span).is_none() {
                    return Err(self.error(
                        ApprovalDiagnosticKind::DuplicateApproval,
                        format!("duplicate approval ID: {name}"),
                        span,
                    ));
                }
                declaration_orders.push(index + 1);
            }
        }
        let mut linked = HashSet::new();
        let mut bindings = Vec::new();
        for (index, event) in events.iter().enumerate() {
            if let EventKind::Write {
                expression,
                approval_name,
                approval_span,
            } = event.kind
            {
                let (slot, approval) = table.get(approval_name).ok_or_else(|| {
                    self.error(
                        ApprovalDiagnosticKind::UnknownApproval,
                        format!("unknown approval ID: {approval_name}"),
                        approval_span,
                    )
                })?;
                let order = index + 1;
                if declaration_orders[slot] >= order {
                    return Err(self.error(
                        ApprovalDiagnosticKind::ApprovalOrder,
                        format!("approval must be declared before WRITE: {approval_name}"),
                        approval_span,
                    ));
                }
                if !linked.insert(approval.id) {
                    return Err(self.error(
                        ApprovalDiagnosticKind::MultipleWrites,
                        format!("approval must be linked to exactly one WRITE: {approval_name}"),
                        approval_span,
                    ));
                }
                bindings.push(ApprovalWriteBinding {
                    approval: approval.id,
                    write: expression,
                    write_order: order,
                    inside_foreach: event.inside_foreach,
                });
            }
        }
        let approvals = table
            .into_symbols()
            .into_iter()
            .zip(declaration_orders)
            .map(|(symbol, declaration_order)| ApprovalDeclaration {
                symbol,
                declaration_order,
            })
            .collect::<Vec<_>>();
        for approval in &approvals {
            if !linked.contains(&approval.symbol.id) {
                return Err(self.error(
                    ApprovalDiagnosticKind::UnusedApproval,
                    format!(
                        "approval is not linked to a WRITE: {}",
                        approval.symbol.name
                    ),
                    &approval.symbol.span,
                ));
            }
        }
        Ok(ApprovalBindingAnalysis {
            origin: Some(skill),
            approvals,
            bindings,
        })
    }

    fn error(
        &self,
        kind: ApprovalDiagnosticKind,
        message: String,
        span: &SourceSpan,
    ) -> ApprovalAnalysisError {
        let (logical_path, snippet) = self.diagnostics.location(span);
        ApprovalAnalysisError::Diagnostic(Box::new(ApprovalDiagnostic {
            kind,
            message: nsl_core::redact_text(&message),
            span: span.clone(),
            logical_path,
            snippet,
        }))
    }
}
