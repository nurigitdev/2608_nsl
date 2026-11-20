pub(crate) mod state;
mod wire;
use crate::{ExecutionContext, ExecutionRequest, RuntimeClock, SystemRuntimeClock};
use nsl_core::{JsonLimit, JsonLimits, ValueData};
use nsl_ir::SkillObject;
use std::{fmt, sync::Arc};
pub use wire::{CheckpointSpec, ExecutionCheckpoint};

/// Historical Oracle checkpoint runtime identity, distinct from package 0.2.1.
pub const CHECKPOINT_RUNTIME_VERSION: &str = "0.1.0";
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CheckpointLimits {
    pub outer: JsonLimits,
    pub state: JsonLimits,
    pub max_decimal_bytes: usize,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CheckpointErrorKind {
    Invalid,
    Type,
    Integrity,
    Binding,
    Credential,
    Limit(JsonLimit),
    Json,
    Encoding,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CheckpointError {
    pub kind: CheckpointErrorKind,
    pub path: String,
    pub message: &'static str,
    pub offset: Option<usize>,
}
impl fmt::Display for CheckpointError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.path, self.message)
    }
}
impl std::error::Error for CheckpointError {}
pub(super) fn error(path: &str, message: &'static str) -> CheckpointError {
    CheckpointError {
        kind: CheckpointErrorKind::Invalid,
        path: nsl_core::redact_text(path),
        message,
        offset: None,
    }
}
pub(super) fn typed(path: &str, message: &'static str) -> CheckpointError {
    CheckpointError {
        kind: CheckpointErrorKind::Type,
        ..error(path, message)
    }
}
pub(super) fn limit(path: &str, k: JsonLimit) -> CheckpointError {
    CheckpointError {
        kind: CheckpointErrorKind::Limit(k),
        ..error(path, "checkpoint limit exceeded")
    }
}
pub(super) type Result<T> = std::result::Result<T, CheckpointError>;
#[derive(Clone, Debug)]
pub struct CheckpointCoordinator {
    runtime_version: String,
    limits: CheckpointLimits,
}
impl CheckpointCoordinator {
    pub fn new(runtime_version: String, limits: CheckpointLimits) -> Result<Self> {
        wire::required(
            &ValueData::String(runtime_version.clone()),
            "runtime_version",
        )?;
        Ok(Self {
            runtime_version,
            limits,
        })
    }
    pub fn for_oracle(limits: CheckpointLimits) -> Self {
        Self {
            runtime_version: CHECKPOINT_RUNTIME_VERSION.into(),
            limits,
        }
    }
    pub fn capture<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
    ) -> Result<ExecutionCheckpoint> {
        let business = ctx.identity().business_operation_id().ok_or_else(|| {
            error(
                "$.business_operation_id",
                "checkpoint requires a business operation identity",
            )
        })?;
        let version =
            crate::data::integer(crate::data::bigint(&ctx.checkpoint.checkpoint_version) + 1);
        for field in [
            ctx.identity().execution_id(),
            business,
            ctx.skill().skill_id.as_str(),
            ctx.skill().skill_version.as_str(),
            ctx.skill().semantic_hash.as_str(),
            self.runtime_version.as_str(),
        ] {
            if field.len() > self.limits.outer.max_bytes {
                return Err(limit("$", JsonLimit::Bytes));
            }
        }
        let data = state::encode(ctx, &version, self.limits)?;
        let payload = wire::canonical(&data, self.limits.state, "$.state_payload")?;
        let cp = ExecutionCheckpoint::create(
            CheckpointSpec {
                execution_id: ctx.identity().execution_id().into(),
                business_operation_id: business.into(),
                checkpoint_version: version.clone(),
                skill_id: ctx.skill().skill_id.clone(),
                skill_version: ctx.skill().skill_version.clone(),
                semantic_hash: ctx.skill().semantic_hash.to_string(),
                ir_version: ctx.skill().ir_version.as_str().into(),
                runtime_version: self.runtime_version.clone(),
                state_payload: payload,
            },
            self.limits,
        )?;
        // Validate native capture using the same state boundary as external restore.
        state::decode(&data, &cp, ctx.request(), self.limits)?;
        ctx.checkpoint.checkpoint_version = version;
        Ok(cp)
    }
    pub fn restore<C: RuntimeClock>(
        &self,
        cp: &ExecutionCheckpoint,
        skill: Arc<SkillObject>,
        request: ExecutionRequest,
        clock: C,
    ) -> Result<ExecutionContext<C>> {
        cp.verify(self.limits)?;
        let s = cp.metadata();
        for (field, actual, expected) in [
            (
                "execution_id",
                s.execution_id.as_str(),
                Some(request.identity().execution_id()),
            ),
            (
                "business_operation_id",
                s.business_operation_id.as_str(),
                request.identity().business_operation_id(),
            ),
            (
                "skill_id",
                s.skill_id.as_str(),
                Some(skill.skill_id.as_str()),
            ),
            (
                "skill_version",
                s.skill_version.as_str(),
                Some(skill.skill_version.as_str()),
            ),
            (
                "semantic_hash",
                s.semantic_hash.as_str(),
                Some(skill.semantic_hash.as_str()),
            ),
            (
                "ir_version",
                s.ir_version.as_str(),
                Some(skill.ir_version.as_str()),
            ),
            (
                "runtime_version",
                s.runtime_version.as_str(),
                Some(self.runtime_version.as_str()),
            ),
        ] {
            if Some(actual) != expected {
                return Err(CheckpointError {
                    kind: CheckpointErrorKind::Binding,
                    ..error(&format!("$.{field}"), "checkpoint binding mismatch")
                });
            }
        }
        let data = wire::parse(cp.state_payload(), self.limits.state, "$.state_payload")?;
        let restored = state::decode(&data, cp, &request, self.limits)?;
        let mut ctx = ExecutionContext::with_clock(skill, request, clock);
        ctx.install_checkpoint_state(restored);
        Ok(ctx)
    }
    pub fn restore_with_system_clock(
        &self,
        cp: &ExecutionCheckpoint,
        skill: Arc<SkillObject>,
        request: ExecutionRequest,
    ) -> Result<ExecutionContext> {
        self.restore(cp, skill, request, SystemRuntimeClock::default())
    }
}
