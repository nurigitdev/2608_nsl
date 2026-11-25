use super::{WriteGuardError, WriteGuardErrorKind, error, hash, required};
use nsl_core::{RiskLevel, ToolCapability, UtcInstant, ValueData};
use std::{fmt, str::FromStr};
pub(super) fn text(s: impl Into<String>) -> ValueData {
    ValueData::String(s.into())
}
pub(super) fn data(items: impl IntoIterator<Item = (&'static str, ValueData)>) -> ValueData {
    ValueData::Object(items.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
fn fields<'a>(
    items: impl IntoIterator<Item = (&'static str, &'static str, &'a str)>,
) -> Result<(), WriteGuardError> {
    for (kind, path, value) in items {
        if kind == "hash" {
            hash(value, path)?;
        } else {
            required(value, path)?;
        }
    }
    Ok(())
}
fn action(value: &str, path: &str) -> Result<(), WriteGuardError> {
    if value != "WRITE" {
        return Err(error(
            WriteGuardErrorKind::Model,
            path,
            "policy action must be WRITE",
        ));
    }
    Ok(())
}
macro_rules! wire_enum {($name:ident, $path:literal, {$($variant:ident => $wire:literal),+})=>{
    #[derive(Clone,Copy,Debug,Eq,PartialEq)]pub enum $name {$($variant),+}
    impl $name {pub const ALL:&'static [Self]=&[$(Self::$variant),+];pub fn as_str(self)->&'static str {match self {$(Self::$variant=>$wire),+}}}
    impl FromStr for $name {type Err=WriteGuardError;fn from_str(s:&str)->Result<Self,Self::Err>{match s {$($wire=>Ok(Self::$variant),)+_=>Err(error(WriteGuardErrorKind::Model,$path,"unknown WRITE evidence enum value"))}}}
};}
wire_enum!(PolicyEffect,"$.policy_decision.effect",{Allow=>"ALLOW",Deny=>"DENY"});
wire_enum!(BusinessPreconditionMechanism,"$.business_preconditions.mechanism",{Version=>"VERSION",Revision=>"REVISION",Etag=>"ETAG",PreconditionToken=>"PRECONDITION_TOKEN",TransactionalRevalidation=>"TRANSACTIONAL_REVALIDATION",EquivalentProviderMechanism=>"EQUIVALENT_PROVIDER_MECHANISM"});
#[derive(Clone, Eq, PartialEq)]
pub struct WriteContractEvidenceSpec {
    pub tool_id: String,
    pub contract_hash: String,
    pub capability: ToolCapability,
    pub risk_level: RiskLevel,
    pub preconditions_supported: bool,
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteContractEvidence(WriteContractEvidenceSpec);
impl WriteContractEvidence {
    pub fn new(s: WriteContractEvidenceSpec) -> Result<Self, WriteGuardError> {
        required(&s.tool_id, "$.contract_evidence.tool_id")?;
        hash(&s.contract_hash, "$.contract_evidence.contract_hash")?;
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &WriteContractEvidenceSpec {
        &self.0
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.0;
        data([
            ("tool_id", text(&s.tool_id)),
            ("contract_hash", text(&s.contract_hash)),
            ("capability", text(s.capability.as_str())),
            ("risk_level", text(s.risk_level.as_str())),
            (
                "preconditions_supported",
                ValueData::Bool(s.preconditions_supported),
            ),
        ])
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct WritePolicyRequestSpec {
    pub tenant_id: String,
    pub subject_id: String,
    pub action: String,
    pub tool_id: String,
    pub risk_level: RiskLevel,
    pub business_operation_id: String,
    pub write_node_id: String,
    pub write_intent_hash: String,
}
#[derive(Clone, Eq, PartialEq)]
pub struct WritePolicyRequest(WritePolicyRequestSpec);
impl WritePolicyRequest {
    pub fn new(s: WritePolicyRequestSpec) -> Result<Self, WriteGuardError> {
        fields([
            ("text", "$.policy_request.tenant_id", s.tenant_id.as_str()),
            ("text", "$.policy_request.subject_id", s.subject_id.as_str()),
            ("text", "$.policy_request.action", s.action.as_str()),
            ("text", "$.policy_request.tool_id", s.tool_id.as_str()),
            (
                "text",
                "$.policy_request.business_operation_id",
                s.business_operation_id.as_str(),
            ),
            (
                "text",
                "$.policy_request.write_node_id",
                s.write_node_id.as_str(),
            ),
            (
                "hash",
                "$.policy_request.write_intent_hash",
                s.write_intent_hash.as_str(),
            ),
        ])?;
        action(&s.action, "$.policy_request.action")?;
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &WritePolicyRequestSpec {
        &self.0
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.0;
        data([
            ("tenant_id", text(&s.tenant_id)),
            ("subject_id", text(&s.subject_id)),
            ("action", text(&s.action)),
            ("tool_id", text(&s.tool_id)),
            ("risk_level", text(s.risk_level.as_str())),
            ("business_operation_id", text(&s.business_operation_id)),
            ("write_node_id", text(&s.write_node_id)),
            ("write_intent_hash", text(&s.write_intent_hash)),
        ])
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct PolicyDecisionSpec {
    pub decision_id: String,
    pub policy_id: String,
    pub policy_version: String,
    pub effect: PolicyEffect,
    pub tenant_id: String,
    pub subject_id: String,
    pub action: String,
    pub tool_id: String,
    pub risk_level: RiskLevel,
    pub business_operation_id: String,
    pub write_node_id: String,
    pub write_intent_hash: String,
    pub evidence_ref: String,
    pub evaluated_at: UtcInstant,
    pub expires_at: UtcInstant,
}
#[derive(Clone, Eq, PartialEq)]
pub struct PolicyDecision(PolicyDecisionSpec);
impl PolicyDecision {
    pub fn new(s: PolicyDecisionSpec) -> Result<Self, WriteGuardError> {
        fields([
            (
                "text",
                "$.policy_decision.decision_id",
                s.decision_id.as_str(),
            ),
            ("text", "$.policy_decision.policy_id", s.policy_id.as_str()),
            (
                "text",
                "$.policy_decision.policy_version",
                s.policy_version.as_str(),
            ),
            ("text", "$.policy_decision.tenant_id", s.tenant_id.as_str()),
            (
                "text",
                "$.policy_decision.subject_id",
                s.subject_id.as_str(),
            ),
            ("text", "$.policy_decision.action", s.action.as_str()),
            ("text", "$.policy_decision.tool_id", s.tool_id.as_str()),
            (
                "text",
                "$.policy_decision.business_operation_id",
                s.business_operation_id.as_str(),
            ),
            (
                "text",
                "$.policy_decision.write_node_id",
                s.write_node_id.as_str(),
            ),
            (
                "text",
                "$.policy_decision.evidence_ref",
                s.evidence_ref.as_str(),
            ),
            (
                "hash",
                "$.policy_decision.write_intent_hash",
                s.write_intent_hash.as_str(),
            ),
        ])?;
        action(&s.action, "$.policy_decision.action")?;
        if s.expires_at <= s.evaluated_at {
            return Err(error(
                WriteGuardErrorKind::Model,
                "$.policy_decision.expires_at",
                "policy decision must expire after evaluation",
            ));
        }
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &PolicyDecisionSpec {
        &self.0
    }
    pub fn matches(&self, request: &WritePolicyRequest) -> bool {
        let p = &self.0;
        let r = &request.0;
        p.tenant_id == r.tenant_id
            && p.subject_id == r.subject_id
            && p.action == r.action
            && p.tool_id == r.tool_id
            && p.risk_level == r.risk_level
            && p.business_operation_id == r.business_operation_id
            && p.write_node_id == r.write_node_id
            && p.write_intent_hash == r.write_intent_hash
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.0;
        data([
            ("decision_id", text(&s.decision_id)),
            ("policy_id", text(&s.policy_id)),
            ("policy_version", text(&s.policy_version)),
            ("effect", text(s.effect.as_str())),
            ("tenant_id", text(&s.tenant_id)),
            ("subject_id", text(&s.subject_id)),
            ("action", text(&s.action)),
            ("tool_id", text(&s.tool_id)),
            ("risk_level", text(s.risk_level.as_str())),
            ("business_operation_id", text(&s.business_operation_id)),
            ("write_node_id", text(&s.write_node_id)),
            ("write_intent_hash", text(&s.write_intent_hash)),
            ("evidence_ref", text(&s.evidence_ref)),
            ("evaluated_at", text(s.evaluated_at.to_string())),
            ("expires_at", text(s.expires_at.to_string())),
        ])
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct BusinessPreconditionEvidenceSpec {
    pub evidence_id: String,
    pub tenant_id: String,
    pub business_operation_id: String,
    pub write_node_id: String,
    pub tool_id: String,
    pub contract_hash: String,
    pub payload_hash: String,
    pub write_intent_hash: String,
    pub mechanism: BusinessPreconditionMechanism,
    pub evidence_ref: String,
    pub observed_at: UtcInstant,
    pub expires_at: UtcInstant,
    pub provider_atomic_enforcement: bool,
}
#[derive(Clone, Eq, PartialEq)]
pub struct BusinessPreconditionEvidence(BusinessPreconditionEvidenceSpec);
impl BusinessPreconditionEvidence {
    pub fn new(s: BusinessPreconditionEvidenceSpec) -> Result<Self, WriteGuardError> {
        fields([
            (
                "text",
                "$.business_preconditions.evidence_id",
                s.evidence_id.as_str(),
            ),
            (
                "text",
                "$.business_preconditions.tenant_id",
                s.tenant_id.as_str(),
            ),
            (
                "text",
                "$.business_preconditions.business_operation_id",
                s.business_operation_id.as_str(),
            ),
            (
                "text",
                "$.business_preconditions.write_node_id",
                s.write_node_id.as_str(),
            ),
            (
                "text",
                "$.business_preconditions.tool_id",
                s.tool_id.as_str(),
            ),
            (
                "text",
                "$.business_preconditions.evidence_ref",
                s.evidence_ref.as_str(),
            ),
            (
                "hash",
                "$.business_preconditions.contract_hash",
                s.contract_hash.as_str(),
            ),
            (
                "hash",
                "$.business_preconditions.payload_hash",
                s.payload_hash.as_str(),
            ),
            (
                "hash",
                "$.business_preconditions.write_intent_hash",
                s.write_intent_hash.as_str(),
            ),
        ])?;
        if s.expires_at <= s.observed_at {
            return Err(error(
                WriteGuardErrorKind::Model,
                "$.business_preconditions.expires_at",
                "precondition evidence must expire after observation",
            ));
        }
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &BusinessPreconditionEvidenceSpec {
        &self.0
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.0;
        data([
            ("evidence_id", text(&s.evidence_id)),
            ("tenant_id", text(&s.tenant_id)),
            ("business_operation_id", text(&s.business_operation_id)),
            ("write_node_id", text(&s.write_node_id)),
            ("tool_id", text(&s.tool_id)),
            ("contract_hash", text(&s.contract_hash)),
            ("payload_hash", text(&s.payload_hash)),
            ("write_intent_hash", text(&s.write_intent_hash)),
            ("mechanism", text(s.mechanism.as_str())),
            ("evidence_ref", text(&s.evidence_ref)),
            ("observed_at", text(s.observed_at.to_string())),
            ("expires_at", text(s.expires_at.to_string())),
            (
                "provider_atomic_enforcement",
                ValueData::Bool(s.provider_atomic_enforcement),
            ),
        ])
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RiskAcceptancePolicy {
    accepted_levels: Vec<RiskLevel>,
}
impl RiskAcceptancePolicy {
    pub fn new(levels: impl IntoIterator<Item = RiskLevel>) -> Result<Self, WriteGuardError> {
        // The set has at most the six Core levels, even for repeated host input.
        let mut accepted_levels = Vec::new();
        for level in levels {
            if !accepted_levels.contains(&level) {
                accepted_levels.push(level);
            }
        }
        accepted_levels.sort_by_key(|v| v.as_str());
        if accepted_levels.is_empty() {
            return Err(error(
                WriteGuardErrorKind::Model,
                "$.risk_policy.accepted_levels",
                "risk acceptance policy must contain a level",
            ));
        }
        Ok(Self { accepted_levels })
    }
    pub fn initial_v0_2() -> Self {
        Self {
            accepted_levels: vec![RiskLevel::R3],
        }
    }
    pub fn accepts(&self, risk: RiskLevel) -> bool {
        self.accepted_levels.contains(&risk)
    }
    pub fn accepted_levels(&self) -> &[RiskLevel] {
        &self.accepted_levels
    }
}
macro_rules! safe_debug {($($ty:ty),*)=>{$(impl fmt::Debug for $ty {fn fmt(&self,f:&mut fmt::Formatter<'_>)->fmt::Result {f.debug_struct(stringify!($ty)).finish_non_exhaustive()}})*};}
safe_debug!(
    WriteContractEvidenceSpec,
    WriteContractEvidence,
    WritePolicyRequestSpec,
    WritePolicyRequest,
    PolicyDecisionSpec,
    PolicyDecision,
    BusinessPreconditionEvidenceSpec,
    BusinessPreconditionEvidence
);
