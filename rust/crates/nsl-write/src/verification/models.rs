use super::{VerificationError, VerificationErrorKind, error, optional, required};
use crate::WriteOutcomeIdentity;
use nsl_core::{
    CommitState, PayloadHash, TypeRef, UtcInstant, ValueData, VerificationStatus, canonical_json,
};
use sha2::{Digest, Sha256};
use std::fmt;
fn txt(s: impl Into<String>) -> ValueData {
    ValueData::String(s.into())
}
fn object(items: impl IntoIterator<Item = (&'static str, ValueData)>) -> ValueData {
    ValueData::Object(items.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
fn opt(s: &Option<String>) -> ValueData {
    s.as_ref().map(txt).unwrap_or(ValueData::Null)
}
fn identity_data(i: &WriteOutcomeIdentity) -> ValueData {
    let s = i.metadata();
    object([
        ("tenant_id", txt(&s.tenant_id)),
        ("business_operation_id", txt(&s.business_operation_id)),
        ("invocation_id", txt(&s.invocation_id)),
        ("node_id", txt(&s.node_id)),
        ("tool_id", txt(&s.tool_id)),
        ("idempotency_key", txt(&s.idempotency_key)),
        ("payload_hash", txt(&s.payload_hash)),
        ("approval_request_id", txt(&s.approval_request_id)),
        ("write_intent_hash", txt(&s.write_intent_hash)),
    ])
}
#[derive(Eq, PartialEq)]
pub struct VerificationRequestSpec {
    pub identity: WriteOutcomeIdentity,
    pub verification_tool_id: String,
    pub business_reference: String,
    pub provider_transaction_id: Option<String>,
    pub reference_schema: Option<TypeRef>,
    pub consistency_hint: Option<String>,
}
#[derive(Eq, PartialEq)]
pub struct VerificationRequest(VerificationRequestSpec);
impl VerificationRequest {
    /// Own admitted type metadata without cloning the recursive schema graph.
    /// Encoding uses Core's external type projection (including Optional rejection).
    pub fn new(s: VerificationRequestSpec) -> Result<Self, VerificationError> {
        required(&s.verification_tool_id, "$.verification_tool_id")?;
        required(&s.business_reference, "$.business_reference")?;
        optional(&s.provider_transaction_id, "$.provider_transaction_id")?;
        optional(&s.consistency_hint, "$.consistency_hint")?;
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &VerificationRequestSpec {
        &self.0
    }
    fn schema(&self) -> Result<ValueData, VerificationError> {
        self.0
            .reference_schema
            .as_ref()
            .map(TypeRef::to_data)
            .transpose()
            .map(|v| v.unwrap_or(ValueData::Null))
            .map_err(|kind| {
                error(
                    VerificationErrorKind::Type(kind),
                    "$.reference_schema",
                    "reference schema cannot be encoded",
                )
            })
    }
    pub fn to_data(&self) -> Result<ValueData, VerificationError> {
        Ok(object([
            ("identity", identity_data(&self.0.identity)),
            ("verification_tool_id", txt(&self.0.verification_tool_id)),
            ("business_reference", txt(&self.0.business_reference)),
            (
                "provider_transaction_id",
                opt(&self.0.provider_transaction_id),
            ),
            ("reference_schema", self.schema()?),
            ("consistency_hint", opt(&self.0.consistency_hint)),
        ]))
    }
    pub fn canonical_bytes(&self) -> Result<Vec<u8>, VerificationError> {
        let s = &self.0;
        let i = s.identity.metadata();
        let value = object([
            ("tenant_id", txt(&i.tenant_id)),
            ("business_operation_id", txt(&i.business_operation_id)),
            ("invocation_id", txt(&i.invocation_id)),
            ("node_id", txt(&i.node_id)),
            ("write_tool_id", txt(&i.tool_id)),
            ("idempotency_key", txt(&i.idempotency_key)),
            ("payload_hash", txt(&i.payload_hash)),
            ("approval_request_id", txt(&i.approval_request_id)),
            ("write_intent_hash", txt(&i.write_intent_hash)),
            ("verification_tool_id", txt(&s.verification_tool_id)),
            ("business_reference", txt(&s.business_reference)),
            ("provider_transaction_id", opt(&s.provider_transaction_id)),
            ("reference_schema", self.schema()?),
            ("consistency_hint", opt(&s.consistency_hint)),
        ]);
        Ok(canonical_json(&value).expect("string-only verification identity/type document"))
    }
    pub fn request_hash(&self) -> Result<PayloadHash, VerificationError> {
        let mut h = Sha256::new();
        h.update(b"NSL-POST-WRITE-VERIFY-V1\0");
        h.update(self.canonical_bytes()?);
        Ok(PayloadHash::from_digest(h.finalize().into()))
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct VerificationObservationSpec {
    pub request_hash: String,
    pub business_reference: String,
    pub matched: bool,
    pub evidence_ref: String,
    pub observed_at: UtcInstant,
    pub reason_code: Option<String>,
}
#[derive(Clone, Eq, PartialEq)]
pub struct VerificationObservation(VerificationObservationSpec);
impl VerificationObservation {
    pub fn new(s: VerificationObservationSpec) -> Result<Self, VerificationError> {
        crate::requested::hash(&s.request_hash, "$.request_hash").map_err(|_| {
            error(
                VerificationErrorKind::Model,
                "$.request_hash",
                "expected lowercase SHA-256 identity",
            )
        })?;
        required(&s.business_reference, "$.business_reference")?;
        required(&s.evidence_ref, "$.evidence_ref")?;
        optional(&s.reason_code, "$.reason_code")?;
        if s.matched == s.reason_code.is_some() {
            return Err(error(
                VerificationErrorKind::Model,
                "$.reason_code",
                "matched/reason combination is inconsistent",
            ));
        }
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &VerificationObservationSpec {
        &self.0
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.0;
        object([
            ("request_hash", txt(&s.request_hash)),
            ("business_reference", txt(&s.business_reference)),
            ("matched", ValueData::Bool(s.matched)),
            ("evidence_ref", txt(&s.evidence_ref)),
            ("observed_at", txt(s.observed_at.to_string())),
            ("reason_code", opt(&s.reason_code)),
        ])
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct VerificationResultSpec {
    pub identity: WriteOutcomeIdentity,
    pub commit_state: CommitState,
    pub status: VerificationStatus,
    pub business_reference: String,
    pub verification_tool_id: Option<String>,
    pub reason_code: Option<String>,
    pub evidence_ref: Option<String>,
    pub observed_at: Option<UtcInstant>,
}
#[derive(Clone, Eq, PartialEq)]
pub struct VerificationResult(VerificationResultSpec);
impl VerificationResult {
    pub fn new(s: VerificationResultSpec) -> Result<Self, VerificationError> {
        let reject = |path, message| error(VerificationErrorKind::Model, path, message);
        if s.commit_state != CommitState::Committed {
            return Err(reject(
                "$.commit_state",
                "verification cannot change COMMITTED state",
            ));
        }
        required(&s.business_reference, "$.business_reference")?;
        for (path, value) in [
            ("$.verification_tool_id", &s.verification_tool_id),
            ("$.reason_code", &s.reason_code),
            ("$.evidence_ref", &s.evidence_ref),
        ] {
            optional(value, path)?;
        }
        match s.status {
            VerificationStatus::NotRequested => {
                if s.verification_tool_id.is_some()
                    || s.reason_code.is_some()
                    || s.evidence_ref.is_some()
                    || s.observed_at.is_some()
                {
                    return Err(reject(
                        "$.status",
                        "NOT_REQUESTED cannot contain outcome evidence",
                    ));
                }
            }
            status => {
                if s.verification_tool_id.is_none() {
                    return Err(reject(
                        "$.verification_tool_id",
                        "requested verification requires a tool identity",
                    ));
                }
                if status == VerificationStatus::Verified {
                    if s.reason_code.is_some()
                        || s.evidence_ref.is_none()
                        || s.observed_at.is_none()
                    {
                        return Err(reject(
                            "$.status",
                            "VERIFIED requires evidence/time without a failure reason",
                        ));
                    }
                } else if s.reason_code.is_none() {
                    return Err(reject("$.reason_code", "FAILED/UNKNOWN require a reason"));
                }
            }
        }
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &VerificationResultSpec {
        &self.0
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.0;
        object([
            ("identity", identity_data(&s.identity)),
            ("commit_state", txt(s.commit_state.as_str())),
            ("status", txt(s.status.as_str())),
            ("business_reference", txt(&s.business_reference)),
            ("verification_tool_id", opt(&s.verification_tool_id)),
            ("reason_code", opt(&s.reason_code)),
            ("evidence_ref", opt(&s.evidence_ref)),
            (
                "observed_at",
                s.observed_at
                    .map(|v| txt(v.to_string()))
                    .unwrap_or(ValueData::Null),
            ),
        ])
    }
    pub fn to_summary_data(&self) -> ValueData {
        object([
            ("status", txt(self.0.status.as_str())),
            ("reason_code", opt(&self.0.reason_code)),
            ("evidence_ref", opt(&self.0.evidence_ref)),
        ])
    }
}
macro_rules! safe_debug {($($ty:ty),*)=>{$(impl fmt::Debug for $ty {fn fmt(&self,f:&mut fmt::Formatter<'_>)->fmt::Result {f.debug_struct(stringify!($ty)).finish_non_exhaustive()}})*};}
safe_debug!(
    VerificationRequestSpec,
    VerificationRequest,
    VerificationObservationSpec,
    VerificationObservation,
    VerificationResultSpec,
    VerificationResult
);
