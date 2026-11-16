use std::fmt;

use nsl_ir::NsoCodecError;
use nsl_source::{ParseError, SourceFile, SourceId, SourceSpan};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IncludeDiagnosticKind {
    Cycle,
    PathOutsideRoot,
    DepthLimit,
    FileLimit,
    BundleSizeLimit,
    ToolVersionConflict,
    DuplicateContext,
    DuplicateLimits,
    ResolutionFailed,
    RequiredLimitsMissing,
}

impl IncludeDiagnosticKind {
    pub const fn code(self) -> &'static str {
        use IncludeDiagnosticKind::*;
        match self {
            Cycle => "NSL-E2301",
            PathOutsideRoot => "NSL-E2302",
            DepthLimit => "NSL-E2303",
            FileLimit => "NSL-E2304",
            BundleSizeLimit => "NSL-E2305",
            ToolVersionConflict => "NSL-E2306",
            DuplicateContext => "NSL-E2307",
            DuplicateLimits => "NSL-E2308",
            ResolutionFailed => "NSL-E2309",
            RequiredLimitsMissing => "NSL-E2310",
        }
    }

    const fn message(self) -> &'static str {
        use IncludeDiagnosticKind::*;
        match self {
            Cycle => "circular include dependency",
            PathOutsideRoot => "invalid include path or resolver path mismatch",
            DepthLimit => "maximum include depth exceeded",
            FileLimit => "maximum include file count exceeded",
            BundleSizeLimit => "maximum source bundle size exceeded",
            ToolVersionConflict => "conflicting include tool versions",
            DuplicateContext => "duplicate include context",
            DuplicateLimits => "duplicate include limits",
            ResolutionFailed => "include source could not be resolved",
            RequiredLimitsMissing => "composed skill must define exactly one limits block",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IncludeDiagnostic {
    pub kind: IncludeDiagnosticKind,
    pub source_id: SourceId,
    pub logical_path: String,
    pub span: Option<SourceSpan>,
}

impl fmt::Display for IncludeDiagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.kind.code())?;
        if let Some(span) = &self.span {
            let start = span.start();
            write!(f, " at {}:{}", start.line(), start.column())?;
        }
        write!(f, ": {}", self.kind.message())
    }
}

impl std::error::Error for IncludeDiagnostic {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IncludeIdentityConflictKind {
    PathRebound,
    SourceIdRebound,
    ContentChanged,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IncludeIdentityConflict {
    pub kind: IncludeIdentityConflictKind,
    pub span: SourceSpan,
}

impl fmt::Display for IncludeIdentityConflict {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        use IncludeIdentityConflictKind::*;
        f.write_str(match self.kind {
            PathRebound => "include path resolved to a different source ID",
            SourceIdRebound => "include source ID resolved to a different path",
            ContentChanged => "include source content changed during resolution",
        })
    }
}

impl std::error::Error for IncludeIdentityConflict {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct IncludeResolveError;

impl fmt::Display for IncludeResolveError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("include source could not be resolved")
    }
}

impl std::error::Error for IncludeResolveError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum IncludeError {
    Diagnostic(IncludeDiagnostic),
    IdentityConflict(IncludeIdentityConflict),
    Parse {
        logical_path: String,
        error: ParseError,
    },
    Manifest(NsoCodecError),
}

impl fmt::Display for IncludeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Diagnostic(error) => error.fmt(f),
            Self::IdentityConflict(error) => error.fmt(f),
            Self::Parse { error, .. } => error.fmt(f),
            Self::Manifest(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for IncludeError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Diagnostic(error) => Some(error),
            Self::IdentityConflict(error) => Some(error),
            Self::Parse { error, .. } => Some(error),
            Self::Manifest(error) => Some(error),
        }
    }
}

pub(super) fn diagnostic(
    kind: IncludeDiagnosticKind,
    source: &SourceFile,
    span: Option<&SourceSpan>,
) -> IncludeError {
    IncludeError::Diagnostic(IncludeDiagnostic {
        kind,
        source_id: source.source_id().clone(),
        logical_path: source.logical_path().to_owned(),
        span: span.cloned(),
    })
}
