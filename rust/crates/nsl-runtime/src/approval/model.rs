use super::{ApprovalModelError, SafeApprovalSummary, required};
use crate::data::{optional, text};
use nsl_core::{
    ApprovalDecision, ApprovalRequestId, BusinessOperationId, DataClassification, PayloadHash,
    RiskLevel, UtcInstant, ValueData,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalBindingsSpec {
    pub tenant_id: String,
    pub execution_id: String,
    pub business_operation_id: String,
    pub approval_id: String,
    pub approval_request_id: String,
    pub skill_semantic_hash: String,
    pub payload_hash: String,
    pub write_intent_hash: String,
}
/// The eight approval identities are kept together, separately from any human
/// decision or opaque authenticity proof. All fields are immutable after admission.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalBindings {
    tenant_id: String,
    execution_id: String,
    business_operation_id: BusinessOperationId,
    approval_id: String,
    approval_request_id: ApprovalRequestId,
    skill_semantic_hash: PayloadHash,
    payload_hash: PayloadHash,
    write_intent_hash: String,
}
impl ApprovalBindings {
    pub fn new(s: ApprovalBindingsSpec) -> Result<Self, ApprovalModelError> {
        Self::named(s, "ApprovalBindings", &[], &[])
    }
    fn named(
        s: ApprovalBindingsSpec,
        owner: &str,
        before_intent: &[(&str, &str)],
        after_intent: &[(&str, &str)],
    ) -> Result<Self, ApprovalModelError> {
        for (name, value) in [
            ("tenant_id", s.tenant_id.as_str()),
            ("execution_id", s.execution_id.as_str()),
            ("business_operation_id", s.business_operation_id.as_str()),
            ("approval_id", s.approval_id.as_str()),
            ("approval_request_id", s.approval_request_id.as_str()),
        ]
        .into_iter()
        .chain(before_intent.iter().copied())
        .chain(std::iter::once((
            "write_intent_hash",
            s.write_intent_hash.as_str(),
        )))
        .chain(after_intent.iter().copied())
        {
            required(value, &format!("{owner}.{name}"))?;
        }
        let hash = |value: &str, field: &str| -> Result<PayloadHash, ApprovalModelError> {
            let field = format!("{owner}.{field}");
            required(value, &field)?;
            value
                .parse()
                .map_err(|_| ApprovalModelError::Hash { field })
        };
        let skill_semantic_hash = hash(&s.skill_semantic_hash, "skill_semantic_hash")?;
        let payload_hash = hash(&s.payload_hash, "payload_hash")?;
        Ok(Self {
            tenant_id: s.tenant_id,
            execution_id: s.execution_id,
            business_operation_id: s
                .business_operation_id
                .parse()
                .expect("required identifier"),
            approval_id: s.approval_id,
            approval_request_id: s.approval_request_id.parse().expect("required identifier"),
            skill_semantic_hash,
            payload_hash,
            write_intent_hash: s.write_intent_hash,
        })
    }
    pub fn tenant_id(&self) -> &str {
        &self.tenant_id
    }
    pub fn execution_id(&self) -> &str {
        &self.execution_id
    }
    pub fn business_operation_id(&self) -> &BusinessOperationId {
        &self.business_operation_id
    }
    pub fn approval_id(&self) -> &str {
        &self.approval_id
    }
    pub fn approval_request_id(&self) -> &ApprovalRequestId {
        &self.approval_request_id
    }
    pub fn skill_semantic_hash(&self) -> &PayloadHash {
        &self.skill_semantic_hash
    }
    pub fn payload_hash(&self) -> &PayloadHash {
        &self.payload_hash
    }
    /// The Oracle constructor admits an opaque nonempty, credential-free token.
    /// Canonical WriteIntent construction/hashing is R8-S01, not this model.
    pub fn write_intent_hash(&self) -> &str {
        &self.write_intent_hash
    }
    fn fields(&self) -> [(&'static str, ValueData); 8] {
        [
            ("tenant_id", text(&self.tenant_id)),
            ("execution_id", text(&self.execution_id)),
            (
                "business_operation_id",
                text(self.business_operation_id.as_str()),
            ),
            ("approval_id", text(&self.approval_id)),
            (
                "approval_request_id",
                text(self.approval_request_id.as_str()),
            ),
            (
                "skill_semantic_hash",
                text(&self.skill_semantic_hash.to_string()),
            ),
            ("payload_hash", text(&self.payload_hash.to_string())),
            ("write_intent_hash", text(&self.write_intent_hash)),
        ]
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ApprovalBindingMismatch {
    BusinessOperation,
    ApprovalRequest,
    WriteIntent,
    Payload,
    Identity,
}
impl ApprovalBindingMismatch {
    pub const fn reason_code(self) -> &'static str {
        match self {
            Self::BusinessOperation => "APPROVAL_BUSINESS_OPERATION_MISMATCH",
            Self::ApprovalRequest => "APPROVAL_REQUEST_MISMATCH",
            Self::WriteIntent => "APPROVAL_INTENT_MISMATCH",
            Self::Payload => "APPROVAL_PAYLOAD_MISMATCH",
            Self::Identity => "APPROVAL_EVIDENCE_BINDING_MISMATCH",
        }
    }
}
impl std::fmt::Display for ApprovalBindingMismatch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.reason_code())
    }
}
impl std::error::Error for ApprovalBindingMismatch {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalRequestSpec {
    pub bindings: ApprovalBindingsSpec,
    pub skill_id: String,
    pub skill_version: String,
    pub write_node_id: String,
    pub tool_id: String,
    pub risk_level: RiskLevel,
    pub message: String,
    pub expires_at: Option<UtcInstant>,
    pub display_safe_summary: SafeApprovalSummary,
    pub protected_payload_ref: Option<String>,
    pub data_classification: DataClassification,
}
#[derive(Clone, Eq, PartialEq)]
pub struct ApprovalRequest {
    bindings: ApprovalBindings,
    spec: ApprovalRequestSpec,
}
impl std::fmt::Debug for ApprovalRequest {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("ApprovalRequest { .. }")
    }
}
impl ApprovalRequest {
    pub fn new(s: ApprovalRequestSpec) -> Result<Self, ApprovalModelError> {
        // Separate static approval_id from per-execution approval_request_id.
        let bindings = ApprovalBindings::named(
            s.bindings.clone(),
            "ApprovalRequest",
            &[
                ("skill_id", &s.skill_id),
                ("skill_version", &s.skill_version),
                ("write_node_id", &s.write_node_id),
                ("tool_id", &s.tool_id),
            ],
            &[("message", &s.message)],
        )?;
        if let Some(value) = &s.protected_payload_ref {
            required(value, "ApprovalRequest.protected_payload_ref")?;
        }
        Ok(Self { bindings, spec: s })
    }
    pub fn bindings(&self) -> &ApprovalBindings {
        &self.bindings
    }
    pub fn metadata(&self) -> &ApprovalRequestSpec {
        &self.spec
    }
    pub fn is_expired_at(&self, now: UtcInstant) -> bool {
        self.spec.expires_at.is_some_and(|expiry| now >= expiry)
    }
    pub fn to_data(&self) -> ValueData {
        let mut fields = self
            .bindings
            .fields()
            .into_iter()
            .collect::<std::collections::BTreeMap<_, _>>();
        fields.extend([
            ("skill_id", text(&self.spec.skill_id)),
            ("skill_version", text(&self.spec.skill_version)),
            ("write_node_id", text(&self.spec.write_node_id)),
            ("tool_id", text(&self.spec.tool_id)),
            ("risk_level", text(self.spec.risk_level.as_str())),
            ("message", text(&self.spec.message)),
            (
                "expires_at",
                self.spec
                    .expires_at
                    .map_or(ValueData::Null, |v| text(&v.to_string())),
            ),
            (
                "display_safe_summary",
                self.spec.display_safe_summary.data().clone(),
            ),
            (
                "protected_payload_ref",
                optional(self.spec.protected_payload_ref.as_deref()),
            ),
            (
                "data_classification",
                text(self.spec.data_classification.as_str()),
            ),
        ]);
        ValueData::Object(fields.into_iter().map(|(k, v)| (k.into(), v)).collect())
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalEvidenceSpec {
    pub bindings: ApprovalBindingsSpec,
    pub decision: ApprovalDecision,
    pub approver_id: String,
    pub decided_at: UtcInstant,
    pub expires_at: Option<UtcInstant>,
    pub evidence_integrity: String,
}
#[derive(Clone, Eq, PartialEq)]
pub struct ApprovalEvidence {
    bindings: ApprovalBindings,
    spec: ApprovalEvidenceSpec,
}
impl std::fmt::Debug for ApprovalEvidence {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("ApprovalEvidence { .. }")
    }
}
impl ApprovalEvidence {
    pub fn new(s: ApprovalEvidenceSpec) -> Result<Self, ApprovalModelError> {
        let bindings = ApprovalBindings::named(
            s.bindings.clone(),
            "ApprovalEvidence",
            &[("approver_id", &s.approver_id)],
            &[("evidence_integrity", &s.evidence_integrity)],
        )?;
        if s.expires_at.is_some_and(|expiry| expiry < s.decided_at) {
            return Err(ApprovalModelError::ExpiryBeforeDecision);
        }
        Ok(Self { bindings, spec: s })
    }
    pub fn bindings(&self) -> &ApprovalBindings {
        &self.bindings
    }
    pub fn metadata(&self) -> &ApprovalEvidenceSpec {
        &self.spec
    }
    /// Structural comparison only: it never authenticates a signature, approves
    /// an actor, authorizes a WRITE or consumes resume state (R7-S04).
    pub fn check_bindings(
        &self,
        expected: &ApprovalBindings,
    ) -> Result<(), ApprovalBindingMismatch> {
        let actual = &self.bindings;
        for (mismatch, kind) in [
            (
                actual.business_operation_id != expected.business_operation_id,
                ApprovalBindingMismatch::BusinessOperation,
            ),
            (
                actual.approval_request_id != expected.approval_request_id,
                ApprovalBindingMismatch::ApprovalRequest,
            ),
            (
                actual.write_intent_hash != expected.write_intent_hash,
                ApprovalBindingMismatch::WriteIntent,
            ),
            (
                actual.payload_hash != expected.payload_hash,
                ApprovalBindingMismatch::Payload,
            ),
        ] {
            if mismatch {
                return Err(kind);
            }
        }
        if actual.tenant_id != expected.tenant_id
            || actual.execution_id != expected.execution_id
            || actual.approval_id != expected.approval_id
            || actual.skill_semantic_hash != expected.skill_semantic_hash
        {
            return Err(ApprovalBindingMismatch::Identity);
        }
        Ok(())
    }
    pub fn is_expired_at(&self, now: UtcInstant) -> bool {
        self.spec.expires_at.is_some_and(|expiry| now >= expiry)
    }
    pub fn to_data(&self) -> ValueData {
        let mut fields = self
            .bindings
            .fields()
            .into_iter()
            .collect::<std::collections::BTreeMap<_, _>>();
        fields.extend([
            ("decision", text(self.spec.decision.as_str())),
            ("approver_id", text(&self.spec.approver_id)),
            ("decided_at", text(&self.spec.decided_at.to_string())),
            (
                "expires_at",
                self.spec
                    .expires_at
                    .map_or(ValueData::Null, |v| text(&v.to_string())),
            ),
            ("evidence_integrity", text(&self.spec.evidence_integrity)),
        ]);
        ValueData::Object(fields.into_iter().map(|(k, v)| (k.into(), v)).collect())
    }
}
