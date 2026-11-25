use super::{WriteGuardError, WriteGuardErrorKind, error};
use nsl_core::{ApprovalStatus, PreWriteEligibility, ValueData};
use std::str::FromStr;
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WriteValidationStatus {
    AllPass,
    HasFail,
    HasUnknown,
    HasFailAndUnknown,
    NoCheck,
}
impl WriteValidationStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::AllPass => "ALL_PASS",
            Self::HasFail => "HAS_FAIL",
            Self::HasUnknown => "HAS_UNKNOWN",
            Self::HasFailAndUnknown => "HAS_FAIL_AND_UNKNOWN",
            Self::NoCheck => "NO_CHECK",
        }
    }
}
impl FromStr for WriteValidationStatus {
    type Err = WriteGuardError;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "ALL_PASS" => Ok(Self::AllPass),
            "HAS_FAIL" => Ok(Self::HasFail),
            "HAS_UNKNOWN" => Ok(Self::HasUnknown),
            "HAS_FAIL_AND_UNKNOWN" => Ok(Self::HasFailAndUnknown),
            "NO_CHECK" => Ok(Self::NoCheck),
            _ => Err(error(
                WriteGuardErrorKind::Model,
                "$.execution_evidence.validation.status",
                "unknown validation summary status",
            )),
        }
    }
}
/// Small value projection of Runtime PreWriteEvidence. These booleans are host
/// snapshots, never standalone authentication/approval/durability proofs.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteExecutionEvidence {
    pub validation: WriteValidationStatus,
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
impl WriteExecutionEvidence {
    pub fn new(validation: WriteValidationStatus) -> Self {
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
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteGuardDecision {
    eligibility: PreWriteEligibility,
    reason_code: Option<String>,
}
impl WriteGuardDecision {
    pub fn new(
        eligibility: PreWriteEligibility,
        reason_code: Option<String>,
    ) -> Result<Self, WriteGuardError> {
        if let Some(r) = &reason_code
            && r.trim_matches(|c: char| c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}'))
                .is_empty()
        {
            return Err(error(
                WriteGuardErrorKind::Model,
                "$.decision.reason_code",
                "reason code must be nonempty",
            ));
        }
        Ok(Self {
            eligibility,
            reason_code,
        })
    }
    pub(super) fn blocked(reason: &str) -> Self {
        Self {
            eligibility: PreWriteEligibility::Blocked,
            reason_code: Some(reason.into()),
        }
    }
    pub fn eligibility(&self) -> PreWriteEligibility {
        self.eligibility
    }
    pub fn reason_code(&self) -> Option<&str> {
        self.reason_code.as_deref()
    }
    pub fn to_data(&self) -> ValueData {
        super::models::data([
            (
                "eligibility",
                super::models::text(self.eligibility.as_str()),
            ),
            (
                "reason_code",
                self.reason_code
                    .as_ref()
                    .map_or(ValueData::Null, super::models::text),
            ),
        ])
    }
}
#[derive(Clone, Copy, Debug, Default)]
pub struct PreWriteEvaluator;
impl PreWriteEvaluator {
    pub fn evaluate(&self, e: &WriteExecutionEvidence) -> WriteGuardDecision {
        let block = WriteGuardDecision::blocked;
        match e.validation {
            WriteValidationStatus::HasFailAndUnknown => {
                return block("REQUIRED_CHECK_FAILED_AND_UNKNOWN");
            }
            WriteValidationStatus::HasFail => return block("REQUIRED_CHECK_FAILED"),
            WriteValidationStatus::HasUnknown => return block("REQUIRED_CHECK_UNKNOWN"),
            _ => {}
        }
        if e.approval_status == ApprovalStatus::Rejected {
            return block("APPROVAL_REJECTED");
        }
        if e.approval_status == ApprovalStatus::Approved && !e.approval_evidence_valid {
            return block("APPROVAL_EVIDENCE_INVALID");
        }
        for (allowed, reason) in [
            (e.runtime_preflight_passed, "RUNTIME_PREFLIGHT_FAILED"),
            (e.trust_valid, "PACKAGE_TRUST_INVALID"),
            (e.current_policy_allowed, "POLICY_DENIED"),
            (e.tool_preconditions_passed, "TOOL_PRECONDITION_FAILED"),
            (
                e.business_preconditions_passed,
                "BUSINESS_PRECONDITION_FAILED",
            ),
            (e.intent_binding_valid, "WRITE_BINDING_INVALID"),
            (e.idempotency_ready, "IDEMPOTENCY_NOT_READY"),
            (e.durability_ready, "DURABILITY_NOT_READY"),
            (e.replay_live_write_allowed, "REPLAY_LIVE_WRITE_FORBIDDEN"),
        ] {
            if !allowed {
                return block(reason);
            }
        }
        if matches!(
            e.approval_status,
            ApprovalStatus::NotRequested | ApprovalStatus::Pending | ApprovalStatus::Expired
        ) {
            return WriteGuardDecision {
                eligibility: PreWriteEligibility::RequiresApproval,
                reason_code: Some("APPROVAL_REQUIRED".into()),
            };
        }
        WriteGuardDecision {
            eligibility: PreWriteEligibility::Allowed,
            reason_code: None,
        }
    }
}
