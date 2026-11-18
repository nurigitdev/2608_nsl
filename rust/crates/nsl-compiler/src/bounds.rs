//! Exact worst-case resource counts for typed IR, followed by limit validation.

mod analyze;

use std::fmt;

use nsl_core::Integer;
use nsl_ir::{ResourceLimits, Statement, StaticAnalysis};
use nsl_source::{SourceFile, SourcePosition, SourceSpan};
use num_bigint::BigInt;

use crate::diagnostics::SourceDiagnosticContext;

const ORACLE_INTEGER_STRING_DIGITS: usize = 4300;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct UnboundedStructureError {
    pub node_id: String,
}

impl fmt::Display for UnboundedStructureError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "foreach {} requires a positive static max", self.node_id)
    }
}

impl std::error::Error for UnboundedStructureError {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BoundDiagnosticKind {
    ToolCalls,
    LoopIterations,
    EmitRecords,
    UnboundedStructure,
}

impl BoundDiagnosticKind {
    pub const fn code(self) -> &'static str {
        match self {
            Self::ToolCalls => "NSL-E6101",
            Self::LoopIterations => "NSL-E6102",
            Self::EmitRecords => "NSL-E6103",
            Self::UnboundedStructure => "NSL-E6104",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BoundDiagnostic {
    pub kind: BoundDiagnosticKind,
    pub message: String,
    pub span: SourceSpan,
    pub logical_path: Option<String>,
    pub snippet: Option<String>,
}

impl BoundDiagnostic {
    pub fn position(&self) -> Option<SourcePosition> {
        self.logical_path.as_ref().map(|_| self.span.start())
    }
}

impl fmt::Display for BoundDiagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.kind.code(), self.message)
    }
}

impl std::error::Error for BoundDiagnostic {}

/// The Oracle cannot format an over-limit diagnostic integer wider than 4300
/// digits. This is a typed abort, not an NSL semantic diagnostic (R5-S05 rule).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BoundIntegerConversionError {
    pub span: SourceSpan,
}

impl fmt::Display for BoundIntegerConversionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "static bound diagnostic integer exceeds the {ORACLE_INTEGER_STRING_DIGITS}-digit conversion limit"
        )
    }
}

impl std::error::Error for BoundIntegerConversionError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum BoundError {
    Diagnostic(Box<BoundDiagnostic>),
    IntegerConversion(Box<BoundIntegerConversionError>),
}

impl fmt::Display for BoundError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Diagnostic(error) => error.fmt(f),
            Self::IntegerConversion(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for BoundError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(match self {
            Self::Diagnostic(error) => error.as_ref(),
            Self::IntegerConversion(error) => error.as_ref(),
        })
    }
}

pub struct StaticBoundAnalyzer<'source> {
    diagnostics: SourceDiagnosticContext<'source>,
}

impl<'source> StaticBoundAnalyzer<'source> {
    pub fn new(sources: impl IntoIterator<Item = &'source SourceFile>) -> Self {
        Self {
            diagnostics: SourceDiagnosticContext::new(sources),
        }
    }

    /// Count typed IR without imposing machine integer widths or an output-digit
    /// limit. Successful results are bounded; invalid maxima are rejected.
    pub fn analyze(
        &self,
        statements: &[Statement],
    ) -> Result<StaticAnalysis, UnboundedStructureError> {
        analyze::analyze(statements)
    }

    /// Validate in Oracle order: tool calls, loop iterations, emitted records.
    /// Collection size and duration are declared Runtime limits, not inferred
    /// counts. Their admission remains in Source/NSO and enforcement in Runtime.
    pub fn check(
        &self,
        statements: &[Statement],
        limits: &ResourceLimits,
        skill_span: &SourceSpan,
        limits_span: &SourceSpan,
    ) -> Result<StaticAnalysis, BoundError> {
        let analysis = self.analyze(statements).map_err(|error| {
            self.error(
                BoundDiagnosticKind::UnboundedStructure,
                format!("static execution bound cannot be proven: {error}"),
                skill_span,
            )
        })?;
        for (actual, limit, kind, label) in [
            (
                &analysis.max_tool_calls,
                &limits.tool_calls,
                BoundDiagnosticKind::ToolCalls,
                "tool call",
            ),
            (
                &analysis.max_loop_iterations,
                &limits.loop_iterations,
                BoundDiagnosticKind::LoopIterations,
                "loop",
            ),
            (
                &analysis.max_emit_records,
                &limits.emitted_rows,
                BoundDiagnosticKind::EmitRecords,
                "emit",
            ),
        ] {
            if integer(actual) > integer(limit) {
                if [actual, limit].iter().any(|value| {
                    value.as_str().trim_start_matches('-').len() > ORACLE_INTEGER_STRING_DIGITS
                }) {
                    return Err(BoundError::IntegerConversion(Box::new(
                        BoundIntegerConversionError {
                            span: limits_span.clone(),
                        },
                    )));
                }
                return Err(self.error(
                    kind,
                    format!(
                        "static {label} bound {} exceeds declared limit {}",
                        actual.as_str(),
                        limit.as_str()
                    ),
                    limits_span,
                ));
            }
        }
        Ok(analysis)
    }

    fn error(&self, kind: BoundDiagnosticKind, message: String, span: &SourceSpan) -> BoundError {
        let (logical_path, snippet) = self.diagnostics.location(span);
        BoundError::Diagnostic(Box::new(BoundDiagnostic {
            kind,
            message,
            span: span.clone(),
            logical_path,
            snippet,
        }))
    }
}

fn integer(value: &Integer) -> BigInt {
    value.as_str().parse().expect("Core Integer is canonical")
}
