#![forbid(unsafe_code)]
mod canonical;
mod execution;
mod guard;
mod idempotency;
mod intent;
mod requested;
mod snapshot;
mod verification;
pub use canonical::{canonical_write_arguments, compute_payload_hash, compute_write_intent_hash};
pub use execution::*;
pub use guard::*;
pub use idempotency::*;
pub use intent::{
    ApprovalIdentityEvidence, WriteIntent, WriteIntentBuilder, WriteIntentContext, WriteIntentSpec,
};
use nsl_core::{JsonLimit, JsonLimits};
pub use requested::*;
use std::fmt;
pub use verification::*;
/// Inclusive canonical output limits, with the argument object at depth 0.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WriteLimits {
    pub canonical: JsonLimits,
    pub max_decimal_bytes: usize,
}
/// Independent retained envelope metadata budget. No metadata enters hash bytes.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SnapshotMetadataLimits {
    pub max_depth: usize,
    pub max_nodes: usize,
    pub max_text_bytes: usize,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WriteSnapshotLimits {
    pub canonical: WriteLimits,
    pub metadata: SnapshotMetadataLimits,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WriteIntentErrorKind {
    Type,
    Identity,
    HashMismatch,
    Limit(JsonLimit),
    IntegerTooWide,
    ApprovalRefreshRequired,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteIntentError {
    pub kind: WriteIntentErrorKind,
    pub path: String,
    pub message: &'static str,
}
impl WriteIntentError {
    pub fn detail_code(&self) -> Option<&'static str> {
        if self.kind == WriteIntentErrorKind::ApprovalRefreshRequired {
            Some("NEW_APPROVAL_REQUIRED")
        } else {
            None
        }
    }
}
impl fmt::Display for WriteIntentError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.path, self.message)
    }
}
impl std::error::Error for WriteIntentError {}
fn error(kind: WriteIntentErrorKind, path: &str, message: &'static str) -> WriteIntentError {
    WriteIntentError {
        kind,
        path: nsl_core::redact_text(path),
        message,
    }
}
fn required(value: &str, path: &str) -> Result<(), WriteIntentError> {
    // Match Python whitespace, including its four additional control separators.
    if value
        .trim_matches(|c: char| c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}'))
        .is_empty()
    {
        return Err(error(
            WriteIntentErrorKind::Identity,
            path,
            "expected a nonempty string",
        ));
    }
    nsl_core::ensure_no_credential_material(&nsl_core::ValueData::String(value.into())).map_err(
        |_| {
            error(
                WriteIntentErrorKind::Identity,
                path,
                "credential material is forbidden",
            )
        },
    )
}
fn hash(value: &str, path: &str) -> Result<nsl_core::PayloadHash, WriteIntentError> {
    required(value, path)?;
    value.parse().map_err(|_| {
        error(
            WriteIntentErrorKind::Identity,
            path,
            "expected a lowercase SHA-256 value",
        )
    })
}
