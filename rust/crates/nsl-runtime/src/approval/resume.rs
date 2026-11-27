use super::{ApprovalBindingMismatch, ApprovalBindings, ApprovalBindingsSpec, ApprovalEvidence};
use crate::{
    CheckpointCoordinator, CheckpointError, CheckpointErrorKind, ExecutionCheckpoint,
    ExecutionContext, ExecutionRequest, RuntimeClock,
};
use nsl_core::{
    ApprovalDecision, ApprovalStatus, BusinessOperationId, Integer, PayloadHash, UtcInstant,
    ValueData,
};
use nsl_ir::SkillObject;
use std::{fmt, future::Future, pin::Pin, sync::Arc};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ResumeClaimError {
    Stale,
    Live,
    Consumed,
    Owner,
    Unavailable,
}
pub type ResumeClaimFuture<'a, T> =
    Pin<Box<dyn Future<Output = Result<T, ResumeClaimError>> + Send + 'a>>;
/// Host-owned durable adapter. claim must atomically reject stale/live/consumed
/// versions across workers; release/consume must atomically enforce claim ownership.
/// Neither an error nor cancellation proves that an operation was rolled back.
pub trait ResumeCheckpointPort: Send + Sync {
    fn claim<'a>(
        &'a self,
        execution_id: &'a str,
        version: &'a Integer,
        claim_id: &'a str,
    ) -> ResumeClaimFuture<'a, ExecutionCheckpoint>;
    fn release<'a>(
        &'a self,
        execution_id: &'a str,
        version: &'a Integer,
        claim_id: &'a str,
    ) -> ResumeClaimFuture<'a, ()>;
    fn consume<'a>(
        &'a self,
        execution_id: &'a str,
        version: &'a Integer,
        claim_id: &'a str,
    ) -> ResumeClaimFuture<'a, ()>;
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ApprovalVerifierError;
/// Authenticate the complete evidence and approver using trusted current host
/// authority. A nonempty evidence_integrity string alone is not a verified proof.
pub trait ApprovalEvidenceVerifier: Send + Sync {
    fn verify(&self, evidence: &ApprovalEvidence) -> Result<bool, ApprovalVerifierError>;
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ApprovalClockError;
pub trait ApprovalClock: Send + Sync {
    fn now(&self) -> Result<UtcInstant, ApprovalClockError>;
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RecordedApprovalClock(pub UtcInstant);
impl ApprovalClock for RecordedApprovalClock {
    fn now(&self) -> Result<UtcInstant, ApprovalClockError> {
        Ok(self.0)
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ApprovalResumeErrorKind {
    Type,
    Checkpoint(CheckpointErrorKind),
    Claim(ResumeClaimError),
    Verifier,
    Clock,
    Release(ResumeClaimError),
    Consume(ResumeClaimError),
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalResumeError {
    pub kind: ApprovalResumeErrorKind,
    pub path: String,
    pub message: &'static str,
    /// A cleanup failure preserves the original typed error/path. Recovery is a
    /// host responsibility; no unconfirmed release is reported as successful.
    pub release_failed: bool,
}
impl fmt::Display for ApprovalResumeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.path, self.message)
    }
}
impl std::error::Error for ApprovalResumeError {}
fn error(kind: ApprovalResumeErrorKind, path: &str, message: &'static str) -> ApprovalResumeError {
    ApprovalResumeError {
        kind,
        path: nsl_core::redact_text(path),
        message,
        release_failed: false,
    }
}
fn typed(path: &str, message: &'static str) -> ApprovalResumeError {
    error(ApprovalResumeErrorKind::Type, path, message)
}
fn required(text: &str, path: &str) -> Result<(), ApprovalResumeError> {
    super::required(text, path)
        .map_err(|_| typed(path, "expected a nonempty credential-free string"))
}
impl From<CheckpointError> for ApprovalResumeError {
    fn from(e: CheckpointError) -> Self {
        error(
            ApprovalResumeErrorKind::Checkpoint(e.kind),
            &e.path,
            e.message,
        )
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct ResumeToken {
    execution_id: String,
    business_operation_id: BusinessOperationId,
    checkpoint_version: Integer,
    approval_evidence: ApprovalEvidence,
}
impl fmt::Debug for ResumeToken {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ResumeToken").finish_non_exhaustive()
    }
}
impl ResumeToken {
    pub fn new(
        execution_id: String,
        business_operation_id: String,
        checkpoint_version: Integer,
        approval_evidence: ApprovalEvidence,
    ) -> Result<Self, ApprovalResumeError> {
        required(&execution_id, "$.token.execution_id")?;
        required(&business_operation_id, "$.token.business_operation_id")?;
        if crate::data::bigint(&checkpoint_version) <= 0.into() {
            return Err(typed(
                "$.token.checkpoint_version",
                "checkpoint version must be positive",
            ));
        }
        Ok(Self {
            execution_id,
            business_operation_id: business_operation_id.parse().expect("required identifier"),
            checkpoint_version,
            approval_evidence,
        })
    }
    pub fn execution_id(&self) -> &str {
        &self.execution_id
    }
    pub fn business_operation_id(&self) -> &BusinessOperationId {
        &self.business_operation_id
    }
    pub fn checkpoint_version(&self) -> &Integer {
        &self.checkpoint_version
    }
    pub fn approval_evidence(&self) -> &ApprovalEvidence {
        &self.approval_evidence
    }
}
pub type ApprovalResumeBindings = ApprovalBindings;
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ApprovalEvidenceValidation {
    Valid,
    Invalid,
    Expired,
    IntentMismatch,
    PayloadMismatch,
    BusinessOperationMismatch,
    ApprovalRequestMismatch,
}
impl ApprovalEvidenceValidation {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Valid => "VALID",
            Self::Invalid => "INVALID",
            Self::Expired => "EXPIRED",
            Self::IntentMismatch => "INTENT_MISMATCH",
            Self::PayloadMismatch => "PAYLOAD_MISMATCH",
            Self::BusinessOperationMismatch => "BUSINESS_OPERATION_MISMATCH",
            Self::ApprovalRequestMismatch => "APPROVAL_REQUEST_MISMATCH",
        }
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ApprovalValidationResult {
    validation: ApprovalEvidenceValidation,
    approval_status: ApprovalStatus,
    reason_code: Option<&'static str>,
}
impl ApprovalValidationResult {
    pub fn validation(&self) -> ApprovalEvidenceValidation {
        self.validation
    }
    pub fn approval_status(&self) -> ApprovalStatus {
        self.approval_status
    }
    pub fn reason_code(&self) -> Option<&'static str> {
        self.reason_code
    }
    pub fn approval_gate_passed(&self) -> bool {
        self.validation == ApprovalEvidenceValidation::Valid
            && self.approval_status == ApprovalStatus::Approved
    }
    pub fn requires_reapproval(&self) -> bool {
        self.approval_status == ApprovalStatus::Expired
    }
    pub fn to_data(&self) -> ValueData {
        crate::data::object([
            ("validation", crate::data::text(self.validation.as_str())),
            (
                "approval_status",
                crate::data::text(self.approval_status.as_str()),
            ),
            ("reason_code", crate::data::optional(self.reason_code)),
        ])
    }
}
fn blocked(
    validation: ApprovalEvidenceValidation,
    reason_code: &'static str,
) -> ApprovalValidationResult {
    ApprovalValidationResult {
        validation,
        approval_status: ApprovalStatus::Pending,
        reason_code: Some(reason_code),
    }
}
#[derive(Clone, Copy, Debug, Default)]
pub struct ApprovalEvidenceValidator;
impl ApprovalEvidenceValidator {
    pub fn validate(
        &self,
        evidence: &ApprovalEvidence,
        bindings: &ApprovalResumeBindings,
        verifier: &dyn ApprovalEvidenceVerifier,
        clock: &dyn ApprovalClock,
    ) -> Result<ApprovalValidationResult, ApprovalResumeError> {
        use ApprovalEvidenceValidation::*;
        if !verifier.verify(evidence).map_err(|_| {
            error(
                ApprovalResumeErrorKind::Verifier,
                "$.approval_evidence",
                "approval verifier failed",
            )
        })? {
            return Ok(blocked(Invalid, "APPROVAL_EVIDENCE_INVALID"));
        }
        if let Err(mismatch) = evidence.check_bindings(bindings) {
            let kind = match mismatch {
                ApprovalBindingMismatch::BusinessOperation => BusinessOperationMismatch,
                ApprovalBindingMismatch::ApprovalRequest => ApprovalRequestMismatch,
                ApprovalBindingMismatch::WriteIntent => IntentMismatch,
                ApprovalBindingMismatch::Payload => PayloadMismatch,
                ApprovalBindingMismatch::Identity => Invalid,
            };
            return Ok(blocked(kind, mismatch.reason_code()));
        }
        let now = clock.now().map_err(|_| {
            error(
                ApprovalResumeErrorKind::Clock,
                "$.clock.now",
                "approval clock failed",
            )
        })?;
        if evidence.is_expired_at(now) {
            return Ok(ApprovalValidationResult {
                validation: Expired,
                approval_status: ApprovalStatus::Expired,
                reason_code: Some("APPROVAL_EXPIRED"),
            });
        }
        let rejected = evidence.metadata().decision == ApprovalDecision::Rejected;
        Ok(ApprovalValidationResult {
            validation: Valid,
            approval_status: if rejected {
                ApprovalStatus::Rejected
            } else {
                ApprovalStatus::Approved
            },
            reason_code: if rejected {
                Some("APPROVAL_REJECTED")
            } else {
                None
            },
        })
    }
}

pub struct ApprovalResumeRequest<'a> {
    pub token: &'a ResumeToken,
    pub skill: Arc<SkillObject>,
    pub request: ExecutionRequest,
    pub approval_id: &'a str,
    pub write_node_id: &'a str,
    pub current_write_intent_hash: &'a str,
    pub current_payload_hash: &'a PayloadHash,
    pub claim_id: &'a str,
}
pub struct ApprovalResumeOutcome<C: RuntimeClock> {
    evidence: ApprovalEvidence,
    context: ExecutionContext<C>,
    checkpoint: ExecutionCheckpoint,
    claim_id: String,
    validation: ApprovalValidationResult,
    claim_consumed: bool,
}
impl<C: RuntimeClock> fmt::Debug for ApprovalResumeOutcome<C> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ApprovalResumeOutcome")
            .field("validation", &self.validation)
            .field("claim_consumed", &self.claim_consumed)
            .finish_non_exhaustive()
    }
}
impl<C: RuntimeClock> ApprovalResumeOutcome<C> {
    pub(crate) fn into_controlled_parts(
        self,
    ) -> (
        ExecutionContext<C>,
        ApprovalValidationResult,
        ApprovalEvidence,
        bool,
    ) {
        (
            self.context,
            self.validation,
            self.evidence,
            self.claim_consumed,
        )
    }
    pub fn context(&self) -> &ExecutionContext<C> {
        &self.context
    }
    pub fn checkpoint(&self) -> &ExecutionCheckpoint {
        &self.checkpoint
    }
    pub fn claim_id(&self) -> &str {
        &self.claim_id
    }
    pub fn validation(&self) -> ApprovalValidationResult {
        self.validation
    }
    pub fn claim_consumed(&self) -> bool {
        self.claim_consumed
    }
    /// Opens only the approval gate; later policy/WRITE guards remain mandatory.
    pub fn into_context(self) -> ExecutionContext<C> {
        self.context
    }
}
#[derive(Clone, Debug)]
pub struct ApprovalResumeCoordinator {
    checkpoints: CheckpointCoordinator,
}
impl ApprovalResumeCoordinator {
    pub fn new(checkpoints: CheckpointCoordinator) -> Self {
        Self { checkpoints }
    }
    pub async fn resume<C: RuntimeClock>(
        &self,
        input: ApprovalResumeRequest<'_>,
        port: &dyn ResumeCheckpointPort,
        verifier: &dyn ApprovalEvidenceVerifier,
        clock: &dyn ApprovalClock,
        runtime_clock: C,
    ) -> Result<ApprovalResumeOutcome<C>, ApprovalResumeError> {
        let t = input.token;
        for (value, path) in [
            (input.approval_id, "$.approval_id"),
            (input.write_node_id, "$.write_node_id"),
            (
                input.current_write_intent_hash,
                "$.current_write_intent_hash",
            ),
            (input.claim_id, "$.claim_id"),
        ] {
            required(value, path)?;
        }
        if t.execution_id() != input.request.identity().execution_id() {
            return Err(typed(
                "$.token.execution_id",
                "resume token binding mismatch",
            ));
        }
        if Some(t.business_operation_id().as_str())
            != input.request.identity().business_operation_id()
        {
            return Err(typed(
                "$.token.business_operation_id",
                "resume token binding mismatch",
            ));
        }
        input
            .request
            .security_context()
            .principal
            .validate(true)
            .map_err(|_| {
                typed(
                    "$.request.security.principal",
                    "resume requires a valid verified current principal",
                )
            })?;
        let checkpoint = port
            .claim(t.execution_id(), t.checkpoint_version(), input.claim_id)
            .await
            .map_err(|e| {
                error(
                    ApprovalResumeErrorKind::Claim(e),
                    "$.claim",
                    "checkpoint claim failed",
                )
            })?;
        let checked = (|| {
            let m = checkpoint.metadata();
            for (field, equal) in [
                ("execution_id", m.execution_id == t.execution_id()),
                (
                    "business_operation_id",
                    m.business_operation_id == t.business_operation_id().as_str(),
                ),
                (
                    "checkpoint_version",
                    &m.checkpoint_version == t.checkpoint_version(),
                ),
            ] {
                if !equal {
                    return Err(typed(
                        &format!("$.checkpoint.{field}"),
                        "claimed checkpoint does not match resume token",
                    ));
                }
            }
            let context =
                self.checkpoints
                    .restore(&checkpoint, input.skill, input.request, runtime_clock)?;
            let bindings = resume_bindings(&context, input.approval_id, input.write_node_id)?;
            let mut validation = ApprovalEvidenceValidator.validate(
                t.approval_evidence(),
                &bindings,
                verifier,
                clock,
            )?;
            if validation.approval_gate_passed() {
                if bindings.write_intent_hash() != input.current_write_intent_hash {
                    validation = blocked(
                        ApprovalEvidenceValidation::IntentMismatch,
                        "APPROVAL_INTENT_MISMATCH",
                    );
                } else if bindings.payload_hash() != input.current_payload_hash {
                    validation = blocked(
                        ApprovalEvidenceValidation::PayloadMismatch,
                        "APPROVAL_PAYLOAD_MISMATCH",
                    );
                }
            }
            Ok((context, validation))
        })();
        let (mut context, validation) = match checked {
            Ok(value) => value,
            Err(mut e) => {
                e.release_failed = port
                    .release(t.execution_id(), t.checkpoint_version(), input.claim_id)
                    .await
                    .is_err();
                return Err(e);
            }
        };
        let consumed = matches!(
            validation.validation,
            ApprovalEvidenceValidation::Valid | ApprovalEvidenceValidation::Expired
        );
        if consumed {
            context
                .approvals
                .records
                .insert(input.approval_id.into(), validation.approval_status);
            port.consume(t.execution_id(), t.checkpoint_version(), input.claim_id)
                .await
                .map_err(|e| {
                    error(
                        ApprovalResumeErrorKind::Consume(e),
                        "$.claim",
                        "checkpoint consumption failed",
                    )
                })?;
        } else {
            port.release(t.execution_id(), t.checkpoint_version(), input.claim_id)
                .await
                .map_err(|e| {
                    let mut err = error(
                        ApprovalResumeErrorKind::Release(e),
                        "$.claim",
                        "checkpoint release failed",
                    );
                    err.release_failed = true;
                    err
                })?;
        }
        Ok(ApprovalResumeOutcome {
            evidence: t.approval_evidence().clone(),
            context,
            checkpoint,
            claim_id: input.claim_id.into(),
            validation,
            claim_consumed: consumed,
        })
    }
}
fn resume_bindings<C: RuntimeClock>(
    ctx: &ExecutionContext<C>,
    approval: &str,
    node: &str,
) -> Result<ApprovalResumeBindings, ApprovalResumeError> {
    if ctx.approvals.records.get(approval) != Some(&ApprovalStatus::Pending) {
        return Err(typed(
            &format!("$.approvals[{approval:?}]"),
            "checkpoint approval is not pending",
        ));
    }
    let get = |map: &std::collections::BTreeMap<String, String>, field: &str| {
        map.get(node).cloned().ok_or_else(|| {
            typed(
                &format!("$.checkpoint.{field}[{node:?}]"),
                "checkpoint approval binding is missing",
            )
        })
    };
    let approval_request_id = get(
        &ctx.checkpoint.approval_request_ids_by_write_node,
        "approval_request_ids_by_write_node",
    )?;
    let write_intent_hash = get(
        &ctx.checkpoint.write_intent_hashes_by_write_node,
        "write_intent_hashes_by_write_node",
    )?;
    let payload_hash = get(
        &ctx.checkpoint.payload_hashes_by_write_node,
        "payload_hashes_by_write_node",
    )?;
    ApprovalBindings::new(ApprovalBindingsSpec {
        tenant_id: ctx.tenant_id().into(),
        execution_id: ctx.identity().execution_id().into(),
        business_operation_id: ctx
            .identity()
            .business_operation_id()
            .expect("checkpoint business identity")
            .into(),
        approval_id: approval.into(),
        approval_request_id,
        skill_semantic_hash: ctx.skill().semantic_hash.to_string(),
        payload_hash,
        write_intent_hash,
    })
    .map_err(|_| typed("$.checkpoint", "invalid approval bindings"))
}
