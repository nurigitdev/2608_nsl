mod flow;
mod prefix;
use super::*;
use nsl_core::{ApprovalStatus, PreWriteEligibility, UtcInstant, VerificationStatus};
use nsl_write::*;
pub use prefix::ControlledPrefix;
use std::{collections::BTreeMap, fmt};

/// Injected host authority and storage. No transport or implicit permission lives
/// in Runtime. Adapters retain their existing atomicity/authority obligations.
pub struct ControlledWriteServices<'a> {
    pub policy: &'a dyn RuntimePolicyPort,
    pub idempotency: &'a dyn IdempotencyEvidencePort,
    pub requested: &'a dyn WriteEvidencePort,
    pub provider: &'a dyn WriteExecutionPort,
    pub outcomes: &'a dyn WriteOutcomeEvidencePort,
    pub verification: Option<&'a dyn PostWriteVerificationPort>,
}
/// Explicit trusted host snapshot. R10 package authentication remains host-owned;
/// these fields cannot replace approval, current policy or durable receipts.
#[derive(Clone, Copy, Debug, Default)]
pub struct ControlledHostEvidence {
    pub trust_valid: bool,
    pub replay_live_write_allowed: bool,
    pub durability_ready: bool,
    pub idempotency_ready: bool,
}
pub struct ControlledWriteOptions<'a> {
    pub now: UtcInstant,
    pub required_until: UtcInstant,
    pub requested_at: UtcInstant,
    pub recorded_at: UtcInstant,
    pub invocation_id: &'a str,
    pub trace_refs: &'a [String],
    pub business_preconditions: &'a BusinessPreconditionEvidence,
    pub provider_evidence: &'a ProviderIdempotencyEvidence,
    pub host: ControlledHostEvidence,
    pub cancellation: WriteCancellationSignal,
    pub limits: WriteSnapshotLimits,
}
/// Codeless failures preserve their component's typed path. Never infer rollback
/// from this error: outcome persistence failures retain the original invocation.
pub enum ControlledExecutionError {
    Binding { path: &'static str },
    Runtime(RuntimeTerminalError),
    Checkpoint(CheckpointError),
    Suspension(ApprovalSuspensionError),
    Intent(WriteIntentError),
    Guard(WriteGuardError),
    Idempotency(IdempotencyError),
    Write(WriteExecutionError),
    Outcome(WriteOutcomePersistenceError),
    Verification(VerificationError),
    Statement(StatementExecutionError),
    ExistingReservation(Box<IdempotencyReservation>),
}
impl fmt::Debug for ControlledExecutionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ControlledExecutionError")
            .field("path", &self.path())
            .finish_non_exhaustive()
    }
}
impl fmt::Display for ControlledExecutionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: controlled execution could not advance", self.path())
    }
}
impl std::error::Error for ControlledExecutionError {}
impl ControlledExecutionError {
    pub fn path(&self) -> &str {
        match self {
            Self::Binding { path } => path,
            Self::Checkpoint(e) => &e.path,
            Self::Suspension(e) => &e.path,
            Self::Intent(e) => &e.path,
            Self::Guard(e) => &e.path,
            Self::Idempotency(e) => &e.path,
            Self::Write(e) => &e.path,
            Self::Outcome(e) => &e.error.path,
            Self::Verification(e) => &e.path,
            Self::Runtime(_) => "$.runtime",
            Self::Statement(_) => "$.statement",
            Self::ExistingReservation(_) => "$.reservation",
        }
    }
    pub fn detail_code(&self) -> Option<&'static str> {
        None
    }
}
macro_rules! conversions {($($kind:ident($ty:ty)),+)=>{$(impl From<$ty> for ControlledExecutionError {fn from(e:$ty)->Self {Self::$kind(e)}})+};}
conversions!(
    Runtime(RuntimeTerminalError),
    Checkpoint(CheckpointError),
    Suspension(ApprovalSuspensionError),
    Intent(WriteIntentError),
    Guard(WriteGuardError),
    Idempotency(IdempotencyError),
    Write(WriteExecutionError),
    Outcome(WriteOutcomePersistenceError),
    Verification(VerificationError),
    Statement(StatementExecutionError)
);
fn binding(path: &'static str) -> ControlledExecutionError {
    ControlledExecutionError::Binding { path }
}
fn boundary(e: BoundaryError) -> ControlledExecutionError {
    match e {
        BoundaryError::Statement(e) => e.into(),
        BoundaryError::Terminal(e) => RuntimeTerminalError::empty(e).into(),
    }
}
struct Plan {
    approval: usize,
    write: usize,
}
impl Plan {
    fn new(skill: &SkillObject) -> Result<Self, ControlledExecutionError> {
        let approval =
            admission::controlled_prefix_end(skill).map_err(|_| binding("$.skill.body"))?;
        let write = skill.body.iter().position(|s| matches!(&s.kind, nsl_ir::StatementKind::Let { value, .. } if matches!(value.kind, nsl_ir::ExpressionKind::Write(_)))).expect("admitted WRITE");
        Ok(Self { approval, write })
    }
    fn expression<'s>(&self, skill: &'s SkillObject) -> (&'s str, &'s nsl_ir::Expression) {
        let nsl_ir::StatementKind::Let {
            target_symbol_id,
            value,
        } = &skill.body[self.write].kind
        else {
            unreachable!()
        };
        (target_symbol_id, value)
    }
}
/// An observed execution owns the actual context and evidence even on a later
/// failure. No public constructor or mutable access can mint final success.
pub struct ControlledExecution<C: RuntimeClock> {
    context: ExecutionContext<C>,
    result: Option<ExecutionResult>,
    persisted: Option<PersistedWriteOutcome>,
    verification: Option<VerificationResult>,
    failure: Option<ControlledExecutionError>,
}
impl<C: RuntimeClock> ControlledExecution<C> {
    pub fn context(&self) -> &ExecutionContext<C> {
        &self.context
    }
    pub fn result(&self) -> Option<&ExecutionResult> {
        self.result.as_ref()
    }
    pub fn persisted(&self) -> Option<&PersistedWriteOutcome> {
        self.persisted.as_ref()
    }
    pub fn verification(&self) -> Option<&VerificationResult> {
        self.verification.as_ref()
    }
    pub fn failure(&self) -> Option<&ControlledExecutionError> {
        self.failure.as_ref()
    }
    pub fn automatic_retry_allowed(&self) -> bool {
        false
    }
}
