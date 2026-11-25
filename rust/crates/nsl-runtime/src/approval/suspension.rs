use super::ApprovalRequest;
use crate::{
    ApprovalSummary, CheckpointCoordinator, CheckpointError, CheckpointErrorKind,
    ExecutionCheckpoint, ExecutionContext, ExecutionResult, RuntimeClock, SuspensionInfo,
    ValidationSummary, ValidationSummaryStatus,
};
use nsl_core::{ApprovalStatus, Completeness, ExecutionStatus, PreWriteEligibility};
use std::{fmt, future::Future, pin::Pin};

/// Trusted host snapshot, not an authorization proof or permission to execute WRITE.
/// Full WriteGuard policy evaluation remains a separate boundary.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PreWriteEvidence {
    pub validation: ValidationSummary,
    pub approval_status: ApprovalStatus,
    pub approval_evidence_valid: bool,
    pub runtime_preflight_passed: bool,
    pub current_policy_allowed: bool,
    pub tool_preconditions_passed: bool,
    pub business_preconditions_passed: bool,
    pub intent_binding_valid: bool,
    pub idempotency_ready: bool,
    pub durability_ready: bool,
    pub trust_valid: bool,
    pub replay_live_write_allowed: bool,
}
impl PreWriteEvidence {
    /// Oracle defaults deny every control. The host must explicitly supply evidence.
    pub fn new(validation: ValidationSummary) -> Self {
        Self {
            validation,
            approval_status: ApprovalStatus::NotRequested,
            approval_evidence_valid: false,
            runtime_preflight_passed: false,
            current_policy_allowed: false,
            tool_preconditions_passed: false,
            business_preconditions_passed: false,
            intent_binding_valid: false,
            idempotency_ready: false,
            durability_ready: false,
            trust_valid: false,
            replay_live_write_allowed: false,
        }
    }
    fn requires_approval(&self) -> bool {
        matches!(
            self.approval_status,
            ApprovalStatus::NotRequested | ApprovalStatus::Pending | ApprovalStatus::Expired
        ) && [
            self.runtime_preflight_passed,
            self.trust_valid,
            self.current_policy_allowed,
            self.tool_preconditions_passed,
            self.business_preconditions_passed,
            self.intent_binding_valid,
            self.idempotency_ready,
            self.durability_ready,
            self.replay_live_write_allowed,
        ]
        .into_iter()
        .all(|v| v)
    }
}

/// Opaque failure: transport credentials, endpoint details and error chains stay
/// inside the injected adapter. An error does not imply the operation was undone.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SuspensionPortError;
pub type SuspensionPortFuture<'a> =
    Pin<Box<dyn Future<Output = Result<(), SuspensionPortError>> + Send + 'a>>;
pub trait CheckpointPort: Send + Sync {
    fn save<'a>(&'a self, checkpoint: &'a ExecutionCheckpoint) -> SuspensionPortFuture<'a>;
}
/// Implementations must publish idempotently by ApprovalRequestId. No human wait.
pub trait ApprovalPort: Send + Sync {
    fn publish<'a>(&'a self, request: &'a ApprovalRequest) -> SuspensionPortFuture<'a>;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ApprovalSuspensionErrorKind {
    Binding,
    Type,
    Checkpoint(CheckpointErrorKind),
    Save,
    Publish,
}
/// Codeless component error. Paths are screened and messages never include payloads.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalSuspensionError {
    pub kind: ApprovalSuspensionErrorKind,
    pub path: String,
    pub message: &'static str,
}
impl fmt::Display for ApprovalSuspensionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.path, self.message)
    }
}
impl std::error::Error for ApprovalSuspensionError {}
fn failure(
    kind: ApprovalSuspensionErrorKind,
    path: &str,
    message: &'static str,
) -> ApprovalSuspensionError {
    ApprovalSuspensionError {
        kind,
        path: nsl_core::redact_text(path),
        message,
    }
}
impl From<CheckpointError> for ApprovalSuspensionError {
    fn from(e: CheckpointError) -> Self {
        failure(
            ApprovalSuspensionErrorKind::Checkpoint(e.kind),
            &e.path,
            e.message,
        )
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct ApprovalSuspension {
    checkpoint: ExecutionCheckpoint,
    result: ExecutionResult,
}
impl fmt::Debug for ApprovalSuspension {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ApprovalSuspension").finish_non_exhaustive()
    }
}
impl ApprovalSuspension {
    pub fn new(
        checkpoint: ExecutionCheckpoint,
        result: ExecutionResult,
    ) -> Result<Self, ApprovalSuspensionError> {
        if result.status != ExecutionStatus::WaitingForApproval {
            return Err(failure(
                ApprovalSuspensionErrorKind::Type,
                "$.result.status",
                "approval suspension must be WAITING_FOR_APPROVAL",
            ));
        }
        Ok(Self { checkpoint, result })
    }
    pub fn checkpoint(&self) -> &ExecutionCheckpoint {
        &self.checkpoint
    }
    pub fn result(&self) -> &ExecutionResult {
        &self.result
    }
}

#[derive(Clone, Debug)]
pub struct ApprovalSuspensionCoordinator {
    checkpoints: CheckpointCoordinator,
}
impl ApprovalSuspensionCoordinator {
    pub fn new(checkpoints: CheckpointCoordinator) -> Self {
        Self { checkpoints }
    }
    pub async fn suspend<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        request: &ApprovalRequest,
        checkpoint_port: &dyn CheckpointPort,
        approval_port: &dyn ApprovalPort,
        evidence: Option<&PreWriteEvidence>,
    ) -> Result<ApprovalSuspension, ApprovalSuspensionError> {
        self.validate_bindings(ctx, request)?;
        let validation = self.validated_pre_write(ctx, evidence)?;
        self.validate_retry(ctx, request)?;
        let b = request.bindings();
        let node = &request.metadata().write_node_id;
        ctx.approvals
            .records
            .insert(b.approval_id().into(), ApprovalStatus::Pending);
        ctx.checkpoint.suspended_at_node_id = Some(node.clone());
        ctx.checkpoint.approval_request_published = false;
        ctx.checkpoint
            .approval_request_ids_by_write_node
            .insert(node.clone(), b.approval_request_id().to_string());
        ctx.checkpoint
            .write_intent_hashes_by_write_node
            .insert(node.clone(), b.write_intent_hash().into());
        ctx.checkpoint
            .payload_hashes_by_write_node
            .insert(node.clone(), b.payload_hash().to_string());

        // Preserve staged state and captured version on failure/drop: an I/O error
        // cannot tell us whether a durable store/publisher accepted the request.
        let checkpoint = self.checkpoints.capture(ctx)?;
        checkpoint_port.save(&checkpoint).await.map_err(|_| {
            failure(
                ApprovalSuspensionErrorKind::Save,
                "$.checkpoint",
                "checkpoint save failed",
            )
        })?;
        approval_port.publish(request).await.map_err(|_| {
            failure(
                ApprovalSuspensionErrorKind::Publish,
                "$.approval_request",
                "approval publication failed",
            )
        })?;
        ctx.checkpoint.approval_request_published = true;
        let model_error = |_| {
            failure(
                ApprovalSuspensionErrorKind::Type,
                "$.result",
                "invalid suspension result",
            )
        };
        let result = ExecutionResult {
            execution_id: ctx.identity().execution_id().into(),
            skill_id: ctx.skill().skill_id.clone(),
            skill_version: ctx.skill().skill_version.clone(),
            semantic_hash: ctx.skill().semantic_hash.to_string(),
            status: ExecutionStatus::WaitingForApproval,
            completeness: ctx.result_completeness(ExecutionStatus::WaitingForApproval),
            checks: ctx.checks.clone(),
            outputs: ctx.outputs.clone(),
            resources: ctx.usage(),
            error: None,
            pre_write_eligibility: validation
                .as_ref()
                .map(|_| PreWriteEligibility::RequiresApproval),
            validation,
            approval: Some(
                ApprovalSummary::new(
                    ApprovalStatus::Pending,
                    b.approval_id().into(),
                    Some(b.approval_request_id().to_string()),
                )
                .map_err(model_error)?,
            ),
            suspension: Some(
                SuspensionInfo::new(
                    "APPROVAL_REQUIRED".into(),
                    checkpoint.metadata().checkpoint_version.clone(),
                    b.approval_id().into(),
                    b.approval_request_id().to_string(),
                )
                .map_err(model_error)?,
            ),
            write: None,
            verification: None,
        };
        ApprovalSuspension::new(checkpoint, result)
    }
    fn validate_bindings<C: RuntimeClock>(
        &self,
        ctx: &ExecutionContext<C>,
        request: &ApprovalRequest,
    ) -> Result<(), ApprovalSuspensionError> {
        let b = request.bindings();
        for (field, actual, expected) in [
            ("tenant_id", b.tenant_id(), Some(ctx.tenant_id())),
            (
                "execution_id",
                b.execution_id(),
                Some(ctx.identity().execution_id()),
            ),
            (
                "business_operation_id",
                b.business_operation_id().as_str(),
                ctx.identity().business_operation_id(),
            ),
            (
                "skill_id",
                request.metadata().skill_id.as_str(),
                Some(ctx.skill().skill_id.as_str()),
            ),
            (
                "skill_version",
                request.metadata().skill_version.as_str(),
                Some(ctx.skill().skill_version.as_str()),
            ),
            (
                "skill_semantic_hash",
                b.skill_semantic_hash().as_str(),
                Some(ctx.skill().semantic_hash.as_str()),
            ),
        ] {
            if Some(actual) != expected {
                return Err(failure(
                    ApprovalSuspensionErrorKind::Binding,
                    &format!("$.request.{field}"),
                    "approval request binding mismatch",
                ));
            }
        }
        Ok(())
    }
    fn validated_pre_write<C: RuntimeClock>(
        &self,
        ctx: &ExecutionContext<C>,
        evidence: Option<&PreWriteEvidence>,
    ) -> Result<Option<ValidationSummary>, ApprovalSuspensionError> {
        if ctx.skill().language_version != "0.2" {
            return Ok(None);
        }
        let err = |path, message| failure(ApprovalSuspensionErrorKind::Type, path, message);
        let evidence = evidence.ok_or_else(|| {
            err(
                "$.pre_write_evidence",
                "v0.2 approval requires PreWriteEvidence",
            )
        })?;
        let validation = ValidationSummary::from_checks(&ctx.checks)
            .map_err(|_| err("$.checks", "invalid CHECK summary"))?;
        if evidence.validation != validation {
            return Err(err(
                "$.pre_write_evidence.validation",
                "PreWriteEvidence validation binding mismatch",
            ));
        }
        if validation.spec().status != ValidationSummaryStatus::AllPass
            || ctx.data_completeness() != Completeness::Complete
        {
            return Err(err(
                "$.pre_write_evidence.validation",
                "v0.2 approval requires complete passing validation",
            ));
        }
        if !evidence.requires_approval() {
            return Err(err(
                "$.pre_write_evidence",
                "v0.2 approval requires REQUIRES_APPROVAL eligibility",
            ));
        }
        Ok(Some(validation))
    }
    fn validate_retry<C: RuntimeClock>(
        &self,
        ctx: &ExecutionContext<C>,
        request: &ApprovalRequest,
    ) -> Result<(), ApprovalSuspensionError> {
        let b = request.bindings();
        let node = &request.metadata().write_node_id;
        if let Some(prior) = ctx.checkpoint.approval_request_ids_by_write_node.get(node) {
            if prior != b.approval_request_id().as_str() {
                return Err(failure(
                    ApprovalSuspensionErrorKind::Binding,
                    &format!("$.checkpoint.approval_request_ids_by_write_node[{node:?}]"),
                    "write node is already bound to another ApprovalRequestId",
                ));
            }
            for (field, previous, incoming) in [
                (
                    "payload_hashes_by_write_node",
                    ctx.checkpoint
                        .payload_hashes_by_write_node
                        .get(node)
                        .map(String::as_str),
                    b.payload_hash().as_str(),
                ),
                (
                    "write_intent_hashes_by_write_node",
                    ctx.checkpoint
                        .write_intent_hashes_by_write_node
                        .get(node)
                        .map(String::as_str),
                    b.write_intent_hash(),
                ),
            ] {
                if previous != Some(incoming) {
                    return Err(failure(
                        ApprovalSuspensionErrorKind::Type,
                        &format!("$.checkpoint.{field}[{node:?}]"),
                        "approval retry requires the original payload and intent bindings",
                    ));
                }
            }
        }
        Ok(())
    }
}
