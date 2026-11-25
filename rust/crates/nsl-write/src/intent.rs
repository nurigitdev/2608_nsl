use crate::{
    WriteIntentError, WriteIntentErrorKind, WriteSnapshotLimits, canonical,
    canonical_write_arguments, compute_write_intent_hash, error, hash, required,
};
use nsl_core::{BusinessOperationId, PayloadHash, ToolCapability, ValueEnvelope};
use nsl_ir::{Expression, ExpressionKind, SkillObject};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
};
#[derive(Clone, Eq, PartialEq)]
pub struct WriteIntentSpec {
    pub tenant_id: String,
    pub business_operation_id: String,
    pub node_id: String,
    pub tool_ref: String,
    pub tool_id: String,
    pub approval_id: String,
    pub skill_semantic_hash: String,
    pub canonical_arguments: BTreeMap<String, ValueEnvelope>,
    pub payload_hash: String,
    pub write_intent_hash: String,
}
impl fmt::Debug for WriteIntentSpec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WriteIntentSpec").finish_non_exhaustive()
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteIntent {
    spec: WriteIntentSpec,
    business: BusinessOperationId,
    payload: PayloadHash,
    intent: PayloadHash,
    canonical_bytes: Vec<u8>,
}
impl fmt::Debug for WriteIntent {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WriteIntent").finish_non_exhaustive()
    }
}
/// Hash identity only. Authentication/TTL/current authority remains a guard concern.
pub trait ApprovalIdentityEvidence {
    fn payload_hash(&self) -> &str;
    fn write_intent_hash(&self) -> &str;
}
impl WriteIntent {
    pub fn new(
        spec: &WriteIntentSpec,
        limits: WriteSnapshotLimits,
    ) -> Result<Self, WriteIntentError> {
        crate::snapshot::check(&spec.canonical_arguments, limits.metadata)?;
        for (field, value) in [
            ("tenant_id", &spec.tenant_id),
            ("business_operation_id", &spec.business_operation_id),
            ("node_id", &spec.node_id),
            ("tool_ref", &spec.tool_ref),
            ("tool_id", &spec.tool_id),
            ("approval_id", &spec.approval_id),
        ] {
            required(value, &format!("$.{field}"))?;
        }
        hash(&spec.skill_semantic_hash, "$.skill_semantic_hash")?;
        let payload = hash(&spec.payload_hash, "$.payload_hash")?;
        let intent = hash(&spec.write_intent_hash, "$.write_intent_hash")?;
        let canonical_bytes =
            canonical_write_arguments(&spec.canonical_arguments, limits.canonical)?;
        if payload != canonical::payload_hash(&canonical_bytes) {
            return Err(error(
                WriteIntentErrorKind::HashMismatch,
                "$.payload_hash",
                "payload hash does not match canonical arguments",
            ));
        }
        if intent
            != compute_write_intent_hash(
                &spec.skill_semantic_hash,
                &spec.node_id,
                &spec.tool_id,
                &spec.payload_hash,
            )?
        {
            return Err(error(
                WriteIntentErrorKind::HashMismatch,
                "$.write_intent_hash",
                "write intent hash does not match intent semantics",
            ));
        }
        let business = spec
            .business_operation_id
            .parse()
            .expect("required identity");
        Ok(Self {
            spec: spec.clone(),
            business,
            payload,
            intent,
            canonical_bytes,
        })
    }
    pub fn metadata(&self) -> &WriteIntentSpec {
        &self.spec
    }
    pub fn canonical_arguments(&self) -> &BTreeMap<String, ValueEnvelope> {
        &self.spec.canonical_arguments
    }
    pub fn canonical_bytes(&self) -> &[u8] {
        &self.canonical_bytes
    }
    pub fn business_operation_id(&self) -> &BusinessOperationId {
        &self.business
    }
    pub fn payload_hash(&self) -> &PayloadHash {
        &self.payload
    }
    pub fn write_intent_hash(&self) -> &PayloadHash {
        &self.intent
    }
    pub fn require_approval_identity(
        &self,
        evidence: &dyn ApprovalIdentityEvidence,
    ) -> Result<(), WriteIntentError> {
        let payload = hash(evidence.payload_hash(), "$.approval.payload_hash")?;
        let intent = hash(evidence.write_intent_hash(), "$.approval.write_intent_hash")?;
        if payload != self.payload || intent != self.intent {
            return Err(error(
                WriteIntentErrorKind::ApprovalRefreshRequired,
                "$.approval",
                "WRITE intent changed after approval; a new Approval Request is required",
            ));
        }
        Ok(())
    }
}
/// Borrowed Runtime projection: nsl-write does not depend on nsl-runtime.
pub struct WriteIntentContext<'a> {
    pub tenant_id: &'a str,
    pub business_operation_id: Option<&'a str>,
    pub skill: &'a SkillObject,
}
#[derive(Clone, Copy, Debug, Default)]
pub struct WriteIntentBuilder;
impl WriteIntentBuilder {
    pub fn build(
        &self,
        expression: &Expression,
        arguments: &BTreeMap<String, ValueEnvelope>,
        ctx: WriteIntentContext<'_>,
        limits: WriteSnapshotLimits,
    ) -> Result<WriteIntent, WriteIntentError> {
        let ExpressionKind::Write(write) = &expression.kind else {
            return Err(error(
                WriteIntentErrorKind::Type,
                "$.expression",
                "expected a WRITE expression",
            ));
        };
        let business = ctx.business_operation_id.ok_or_else(|| {
            error(
                WriteIntentErrorKind::Identity,
                "$.business_operation_id",
                "BusinessOperationId is required to build a WriteIntent",
            )
        })?;
        let mut required = ctx
            .skill
            .required_tools
            .iter()
            .filter(|t| t.tool_ref == write.tool_ref);
        let tool = required.next().ok_or_else(|| {
            error(
                WriteIntentErrorKind::Identity,
                "$.tool_ref",
                "WRITE must resolve exactly one declared tool contract",
            )
        })?;
        if required.next().is_some() {
            return Err(error(
                WriteIntentErrorKind::Identity,
                "$.tool_ref",
                "WRITE must resolve exactly one declared tool contract",
            ));
        }
        if tool.kind.capability() != ToolCapability::Write {
            return Err(error(
                WriteIntentErrorKind::Identity,
                "$.tool_ref",
                "WRITE requires a WRITE-capable tool contract",
            ));
        }
        let expected = write
            .arguments
            .iter()
            .map(|a| a.name.as_str())
            .collect::<BTreeSet<_>>();
        if arguments.len() != write.arguments.len()
            || arguments
                .keys()
                .map(String::as_str)
                .collect::<BTreeSet<_>>()
                != expected
        {
            return Err(error(
                WriteIntentErrorKind::Identity,
                "$.arguments",
                "evaluated WRITE arguments do not match the IR expression",
            ));
        }
        // Bound borrowed values before freezing/cloning the argument graph.
        crate::snapshot::check(arguments, limits.metadata)?;
        let canonical = canonical_write_arguments(arguments, limits.canonical)?;
        let payload = canonical::payload_hash(&canonical);
        let intent = compute_write_intent_hash(
            ctx.skill.semantic_hash.as_str(),
            &expression.node_id,
            &tool.tool_id,
            payload.as_str(),
        )?;
        WriteIntent::new(
            &WriteIntentSpec {
                tenant_id: ctx.tenant_id.into(),
                business_operation_id: business.into(),
                node_id: expression.node_id.clone(),
                tool_ref: write.tool_ref.clone(),
                tool_id: tool.tool_id.clone(),
                approval_id: write.approval_ref.clone(),
                skill_semantic_hash: ctx.skill.semantic_hash.to_string(),
                canonical_arguments: arguments.clone(),
                payload_hash: payload.to_string(),
                write_intent_hash: intent.to_string(),
            },
            limits,
        )
    }
}
