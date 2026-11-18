use crate::data::{bigint, blank, integer, number, object, optional, strings, text};
use crate::security::{ModelError, optional_identity, required};
use nsl_core::{
    ApprovalStatus, CheckStatus, CommitState, Completeness, DataClassification, IdempotencyKey,
    IdentityError, Integer, PayloadHash, Presence, Value, ValueData, ValueEnvelope,
    VerificationStatus,
};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CheckResult {
    pub check_id: String,
    pub status: CheckStatus,
    pub severity: String,
    pub message: String,
    pub condition_node_id: String,
    pub presence: Presence,
    pub completeness: Completeness,
    pub provenance_refs: Vec<String>,
    pub reason_code: Option<String>,
}
impl CheckResult {
    pub fn to_data(&self) -> ValueData {
        object([
            ("check_id", text(&self.check_id)),
            ("status", text(self.status.as_str())),
            ("severity", text(&self.severity)),
            ("message", text(&self.message)),
            ("condition_node_id", text(&self.condition_node_id)),
            ("presence", text(self.presence.as_str())),
            ("completeness", text(self.completeness.as_str())),
            ("provenance_refs", strings(&self.provenance_refs)),
            ("reason_code", optional(self.reason_code.as_deref())),
        ])
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ValidationSummaryStatus {
    AllPass,
    HasFail,
    HasUnknown,
    HasFailAndUnknown,
    NoCheck,
}
impl ValidationSummaryStatus {
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
fn summary_status(total: &Integer, fail: &Integer, unknown: &Integer) -> ValidationSummaryStatus {
    use ValidationSummaryStatus::*;
    if bigint(total) == 0.into() {
        NoCheck
    } else if bigint(fail) != 0.into() && bigint(unknown) != 0.into() {
        HasFailAndUnknown
    } else if bigint(fail) != 0.into() {
        HasFail
    } else if bigint(unknown) != 0.into() {
        HasUnknown
    } else {
        AllPass
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidationSummarySpec {
    pub status: ValidationSummaryStatus,
    pub total_count: Integer,
    pub pass_count: Integer,
    pub fail_count: Integer,
    pub unknown_count: Integer,
    pub failed_check_ids: Vec<String>,
    pub unknown_check_ids: Vec<String>,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidationSummary(ValidationSummarySpec);
impl ValidationSummary {
    pub fn new(spec: ValidationSummarySpec) -> Result<Self, ModelError> {
        let counts = [
            &spec.total_count,
            &spec.pass_count,
            &spec.fail_count,
            &spec.unknown_count,
        ];
        if counts.iter().any(|n| bigint(n) < 0.into()) {
            return Err(ModelError::Invalid(
                "ValidationSummary counts must be non-negative integers",
            ));
        }
        if bigint(&spec.total_count)
            != bigint(&spec.pass_count) + bigint(&spec.fail_count) + bigint(&spec.unknown_count)
        {
            return Err(ModelError::Invalid(
                "ValidationSummary counts do not add up",
            ));
        }
        if spec.status != summary_status(&spec.total_count, &spec.fail_count, &spec.unknown_count) {
            return Err(ModelError::Invalid(
                "ValidationSummary status does not match counts",
            ));
        }
        for (ids, count, message) in [
            (
                &spec.failed_check_ids,
                &spec.fail_count,
                "ValidationSummary.failed_check_ids is invalid",
            ),
            (
                &spec.unknown_check_ids,
                &spec.unknown_count,
                "ValidationSummary.unknown_check_ids is invalid",
            ),
        ] {
            if bigint(&integer(ids.len())) != bigint(count)
                || ids.windows(2).any(|p| p[0] >= p[1])
                || ids.iter().any(|s| blank(s))
            {
                return Err(ModelError::Invalid(message));
            }
        }
        let failed = spec.failed_check_ids.iter().collect::<BTreeSet<_>>();
        if spec.unknown_check_ids.iter().any(|s| failed.contains(s)) {
            return Err(ModelError::Invalid(
                "ValidationSummary check outcomes overlap",
            ));
        }
        Ok(Self(spec))
    }
    pub fn from_checks(checks: &[CheckResult]) -> Result<Self, ModelError> {
        let mut ids = BTreeSet::new();
        let mut failed = Vec::new();
        let mut unknown = Vec::new();
        let mut passes = 0usize;
        for check in checks {
            if blank(&check.check_id) || !ids.insert(&check.check_id) {
                return Err(ModelError::Invalid(
                    "check IDs must be unique non-empty strings",
                ));
            }
            match check.status {
                CheckStatus::Pass => passes += 1,
                CheckStatus::Fail => failed.push(check.check_id.clone()),
                CheckStatus::Unknown => unknown.push(check.check_id.clone()),
            }
        }
        failed.sort();
        unknown.sort();
        let total = integer(checks.len());
        let fail = integer(failed.len());
        let unknown_count = integer(unknown.len());
        Self::new(ValidationSummarySpec {
            status: summary_status(&total, &fail, &unknown_count),
            total_count: total,
            pass_count: integer(passes),
            fail_count: fail,
            unknown_count,
            failed_check_ids: failed,
            unknown_check_ids: unknown,
        })
    }
    pub fn spec(&self) -> &ValidationSummarySpec {
        &self.0
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.0;
        object([
            ("status", text(s.status.as_str())),
            ("total_count", number(&s.total_count)),
            ("pass_count", number(&s.pass_count)),
            ("fail_count", number(&s.fail_count)),
            ("unknown_count", number(&s.unknown_count)),
            ("failed_check_ids", strings(&s.failed_check_ids)),
            ("unknown_check_ids", strings(&s.unknown_check_ids)),
        ])
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EmitRecord {
    fields: Vec<(String, ValueEnvelope)>,
}
impl EmitRecord {
    pub fn new(fields: Vec<(String, ValueEnvelope)>) -> Result<Self, ModelError> {
        let mut names = BTreeSet::new();
        if fields
            .iter()
            .any(|(name, _)| name.is_empty() || !names.insert(name))
        {
            return Err(ModelError::Invalid(
                "emit fields must map names to ValueEnvelope values",
            ));
        }
        Ok(Self { fields })
    }
    pub fn fields(&self) -> &[(String, ValueEnvelope)] {
        &self.fields
    }
    pub fn values(&self) -> BTreeMap<String, Value> {
        self.fields
            .iter()
            .map(|(k, v)| (k.clone(), v.value().clone()))
            .collect()
    }
    pub fn classifications(&self) -> BTreeMap<String, DataClassification> {
        self.fields
            .iter()
            .map(|(k, v)| (k.clone(), v.classification()))
            .collect()
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeErrorInfo {
    pub code: String,
    pub category: String,
    pub message: String,
    pub node_id: Option<String>,
    pub detail_code: Option<String>,
}
impl RuntimeErrorInfo {
    pub fn to_data(&self) -> ValueData {
        object([
            ("code", text(&self.code)),
            ("category", text(&self.category)),
            ("message", text(&self.message)),
            ("node_id", optional(self.node_id.as_deref())),
            ("detail_code", optional(self.detail_code.as_deref())),
        ])
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalSummary {
    status: ApprovalStatus,
    approval_id: String,
    approval_request_id: Option<String>,
}
impl ApprovalSummary {
    pub fn new(
        status: ApprovalStatus,
        approval_id: String,
        approval_request_id: Option<String>,
    ) -> Result<Self, ModelError> {
        required(
            &approval_id,
            "ApprovalSummary.approval_id must be a non-empty string",
        )?;
        optional_identity(
            approval_request_id.as_deref(),
            "ApprovalSummary.approval_request_id must be a non-empty string",
        )?;
        Ok(Self {
            status,
            approval_id,
            approval_request_id,
        })
    }
    pub fn to_data(&self) -> ValueData {
        object([
            ("status", text(self.status.as_str())),
            ("approval_id", text(&self.approval_id)),
            (
                "approval_request_id",
                optional(self.approval_request_id.as_deref()),
            ),
        ])
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteSummarySpec {
    pub commit_state: CommitState,
    pub tool_id: String,
    pub node_id: String,
    pub idempotency_key: String,
    pub payload_hash: String,
    pub provider_transaction_id: Option<String>,
    pub business_reference: Option<String>,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteSummary(WriteSummarySpec);
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValidatedWriteIdentities {
    pub idempotency_key: IdempotencyKey,
    pub payload_hash: PayloadHash,
}
impl ValidatedWriteIdentities {
    pub fn parse(key: &str, payload: &str) -> Result<Self, IdentityError> {
        Ok(Self {
            idempotency_key: key.parse()?,
            payload_hash: payload.parse()?,
        })
    }
}
impl WriteSummary {
    pub fn new(spec: WriteSummarySpec) -> Result<Self, ModelError> {
        for (value, message) in [
            (
                &spec.tool_id,
                "WriteSummary.tool_id must be a non-empty string",
            ),
            (
                &spec.node_id,
                "WriteSummary.node_id must be a non-empty string",
            ),
            (
                &spec.idempotency_key,
                "WriteSummary.idempotency_key must be a non-empty string",
            ),
            (
                &spec.payload_hash,
                "WriteSummary.payload_hash must be a non-empty string",
            ),
        ] {
            required(value, message)?;
        }
        optional_identity(
            spec.provider_transaction_id.as_deref(),
            "WriteSummary.provider_transaction_id must be a non-empty string",
        )?;
        optional_identity(
            spec.business_reference.as_deref(),
            "WriteSummary.business_reference must be a non-empty string",
        )?;
        Ok(Self(spec))
    }
    pub fn spec(&self) -> &WriteSummarySpec {
        &self.0
    }
    pub fn validate_for_write(&self) -> Result<ValidatedWriteIdentities, IdentityError> {
        ValidatedWriteIdentities::parse(&self.0.idempotency_key, &self.0.payload_hash)
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.0;
        object([
            ("commit_state", text(s.commit_state.as_str())),
            ("tool_id", text(&s.tool_id)),
            ("node_id", text(&s.node_id)),
            ("idempotency_key", text(&s.idempotency_key)),
            ("payload_hash", text(&s.payload_hash)),
            (
                "provider_transaction_id",
                optional(s.provider_transaction_id.as_deref()),
            ),
            (
                "business_reference",
                optional(s.business_reference.as_deref()),
            ),
        ])
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct VerificationSummary {
    status: VerificationStatus,
    reason_code: Option<String>,
    evidence_ref: Option<String>,
}
impl VerificationSummary {
    pub fn new(
        status: VerificationStatus,
        reason_code: Option<String>,
        evidence_ref: Option<String>,
    ) -> Result<Self, ModelError> {
        optional_identity(
            reason_code.as_deref(),
            "VerificationSummary.reason_code must be a non-empty string",
        )?;
        optional_identity(
            evidence_ref.as_deref(),
            "VerificationSummary.evidence_ref must be a non-empty string",
        )?;
        Ok(Self {
            status,
            reason_code,
            evidence_ref,
        })
    }
    pub fn to_data(&self) -> ValueData {
        object([
            ("status", text(self.status.as_str())),
            ("reason_code", optional(self.reason_code.as_deref())),
            ("evidence_ref", optional(self.evidence_ref.as_deref())),
        ])
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SuspensionInfo {
    reason: String,
    checkpoint_version: Integer,
    approval_id: String,
    approval_request_id: String,
}
impl SuspensionInfo {
    pub fn new(
        reason: String,
        checkpoint_version: Integer,
        approval_id: String,
        approval_request_id: String,
    ) -> Result<Self, ModelError> {
        required(&reason, "SuspensionInfo.reason must be a non-empty string")?;
        if bigint(&checkpoint_version) <= 0.into() {
            return Err(ModelError::Invalid(
                "SuspensionInfo.checkpoint_version must be positive",
            ));
        }
        required(
            &approval_id,
            "SuspensionInfo.approval_id must be a non-empty string",
        )?;
        required(
            &approval_request_id,
            "SuspensionInfo.approval_request_id must be a non-empty string",
        )?;
        Ok(Self {
            reason,
            checkpoint_version,
            approval_id,
            approval_request_id,
        })
    }
    pub fn to_data(&self) -> ValueData {
        object([
            ("reason", text(&self.reason)),
            ("checkpoint_version", number(&self.checkpoint_version)),
            ("approval_id", text(&self.approval_id)),
            ("approval_request_id", text(&self.approval_request_id)),
        ])
    }
}
