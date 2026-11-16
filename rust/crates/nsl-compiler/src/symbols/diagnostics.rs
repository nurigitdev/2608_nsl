use std::{collections::HashMap, fmt};

use nsl_source::ast::{ExpressionId, StatementId};
use nsl_source::{SourceFile, SourceId, SourcePosition, SourceSpan};

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
        write!(f, "{}: {message}: {}", self.kind.code(), self.name)
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

pub(super) struct SourceDiagnosticContext<'a>(HashMap<&'a SourceId, &'a SourceFile>);

impl<'a> SourceDiagnosticContext<'a> {
    pub(super) fn new(sources: impl IntoIterator<Item = &'a SourceFile>) -> Self {
        Self(
            sources
                .into_iter()
                .map(|source| (source.source_id(), source))
                .collect(),
        )
    }

    pub(super) fn error(
        &self,
        kind: SymbolDiagnosticKind,
        name: &str,
        span: &SourceSpan,
    ) -> SymbolResolutionError {
        let source = self.0.get(span.source_id());
        SymbolResolutionError::Diagnostic(Box::new(SymbolDiagnostic {
            kind,
            name: name.into(),
            span: span.clone(),
            logical_path: source.map(|source| source.logical_path().to_owned()),
            snippet: source
                .and_then(|source| source_line(source.text(), span.start().line()))
                .map(str::to_owned),
        }))
    }
}

// The Oracle indexes Python splitlines() for diagnostic snippets.
fn source_line(text: &str, mut line: usize) -> Option<&str> {
    let mut start = 0;
    for (offset, ch) in text.char_indices() {
        if offset < start
            || !matches!(
                ch,
                '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}'
                    ..='\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
            )
        {
            continue;
        }
        if line == 1 {
            return Some(&text[start..offset]);
        }
        start = offset + ch.len_utf8();
        if ch == '\r' && text.as_bytes().get(start) == Some(&b'\n') {
            start += 1;
        }
        line -= 1;
    }
    (line == 1 && start < text.len()).then(|| &text[start..])
}
