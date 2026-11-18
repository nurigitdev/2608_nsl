use std::fmt;

use nsl_source::ast::{ExpressionId, StatementId};
use nsl_source::{SourcePosition, SourceSpan};

pub(super) use crate::diagnostics::SourceDiagnosticContext;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SymbolDiagnosticKind {
    UnknownIdentifier,
    DuplicateSymbol,
    DuplicateApproval,
}

impl SymbolDiagnosticKind {
    pub const fn code(self) -> &'static str {
        match self {
            Self::UnknownIdentifier => "NSL-E2001",
            Self::DuplicateSymbol | Self::DuplicateApproval => "NSL-E2002",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SymbolDiagnostic {
    pub kind: SymbolDiagnosticKind,
    pub name: String,
    pub span: SourceSpan,
    pub logical_path: Option<String>,
    pub snippet: Option<String>,
}

impl SymbolDiagnostic {
    pub fn position(&self) -> Option<SourcePosition> {
        self.logical_path.as_ref().map(|_| self.span.start())
    }
}

impl fmt::Display for SymbolDiagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = match self.kind {
            SymbolDiagnosticKind::UnknownIdentifier => "unknown identifier",
            SymbolDiagnosticKind::DuplicateSymbol => "duplicate symbol or shadowing is forbidden",
            SymbolDiagnosticKind::DuplicateApproval => "duplicate approval ID",
        };
        write!(
            f,
            "{}: {}",
            self.kind.code(),
            nsl_core::redact_text(&format!("{message}: {}", self.name))
        )
    }
}

impl std::error::Error for SymbolDiagnostic {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SymbolResolutionError {
    Diagnostic(Box<SymbolDiagnostic>),
    InvalidStatement(StatementId),
    InvalidExpression(ExpressionId),
}

impl fmt::Display for SymbolResolutionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Diagnostic(error) => error.fmt(f),
            Self::InvalidStatement(_) => f.write_str("invalid statement reference in skill AST"),
            Self::InvalidExpression(_) => f.write_str("invalid expression reference in skill AST"),
        }
    }
}

impl std::error::Error for SymbolResolutionError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Diagnostic(error) => Some(error.as_ref()),
            Self::InvalidStatement(_) | Self::InvalidExpression(_) => None,
        }
    }
}

impl SourceDiagnosticContext<'_> {
    pub(crate) fn error(
        &self,
        kind: SymbolDiagnosticKind,
        name: &str,
        span: &SourceSpan,
    ) -> SymbolResolutionError {
        let (logical_path, snippet) = self.location(span);
        SymbolResolutionError::Diagnostic(Box::new(SymbolDiagnostic {
            kind,
            name: name.into(),
            span: span.clone(),
            logical_path,
            snippet,
        }))
    }
}
