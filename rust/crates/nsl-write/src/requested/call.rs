use super::{WriteExecutionError, WriteExecutionErrorKind, error, hash, key, required};
use crate::{
    BusinessPreconditionEvidence, IdempotencyDisposition, IdempotencyReservation,
    WriteContractEvidence, WriteIntent, WriteSnapshotLimits, compute_payload_hash,
};
use nsl_core::{IdempotencyKey, PayloadHash, ToolCapability, ValueEnvelope};
use std::{collections::BTreeMap, fmt};
#[derive(Clone, Eq, PartialEq)]
pub struct WriteCallRequestSpec {
    pub tenant_id: String,
    pub execution_id: String,
    pub business_operation_id: String,
    pub invocation_id: String,
    pub node_id: String,
    pub tool_ref: String,
    pub tool_id: String,
    pub contract_hash: String,
    pub arguments: BTreeMap<String, ValueEnvelope>,
    pub idempotency_key: String,
    pub payload_hash: String,
    pub approval_id: String,
    pub approval_request_id: String,
    pub write_intent_hash: String,
    pub business_preconditions: BusinessPreconditionEvidence,
    pub trace_refs: Vec<String>,
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteCallRequest {
    spec: WriteCallRequestSpec,
    key: IdempotencyKey,
    payload: PayloadHash,
}
fn traces(values: &[String]) -> Result<(), WriteExecutionError> {
    for (i, s) in values.iter().enumerate() {
        required(s, &format!("$.trace_refs[{i}]"))?;
    }
    Ok(())
}
impl WriteCallRequest {
    /// Borrowed admission: bound value/TypeRef/provenance graphs before cloning.
    /// Scalar identity and flat trace string rules retain the Oracle's semantics.
    pub fn new(
        s: &WriteCallRequestSpec,
        limits: WriteSnapshotLimits,
    ) -> Result<Self, WriteExecutionError> {
        for (name, value) in [
            ("tenant_id", &s.tenant_id),
            ("execution_id", &s.execution_id),
            ("business_operation_id", &s.business_operation_id),
            ("invocation_id", &s.invocation_id),
            ("node_id", &s.node_id),
            ("tool_ref", &s.tool_ref),
            ("tool_id", &s.tool_id),
            ("approval_id", &s.approval_id),
            ("approval_request_id", &s.approval_request_id),
        ] {
            required(value, &format!("$.{name}"))?;
        }
        hash(&s.contract_hash, "$.contract_hash")?;
        let payload = hash(&s.payload_hash, "$.payload_hash")?;
        hash(&s.write_intent_hash, "$.write_intent_hash")?;
        let key = key(&s.idempotency_key, "$.idempotency_key")?;
        // _freeze_arguments checks nonblank names, without credential-screening
        // the names or treating envelope metadata as payload hash material.
        if s.arguments.keys().any(|name| {
            name.trim_matches(|c: char| c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}'))
                .is_empty()
        }) {
            return Err(error(
                WriteExecutionErrorKind::Model,
                "$.arguments",
                "argument names must be nonempty",
            ));
        }
        crate::snapshot::check(&s.arguments, limits.metadata)?;
        if compute_payload_hash(&s.arguments, limits.canonical)? != payload {
            return Err(error(
                WriteExecutionErrorKind::Model,
                "$.payload_hash",
                "payload hash does not match arguments",
            ));
        }
        traces(&s.trace_refs)?;
        Ok(Self {
            spec: s.clone(),
            key,
            payload,
        })
    }
    pub fn metadata(&self) -> &WriteCallRequestSpec {
        &self.spec
    }
    pub fn arguments(&self) -> &BTreeMap<String, ValueEnvelope> {
        &self.spec.arguments
    }
    pub fn idempotency_key(&self) -> &IdempotencyKey {
        &self.key
    }
    pub fn payload_hash(&self) -> &PayloadHash {
        &self.payload
    }
}
/// A CREATED-only, non-Clone preparation. Standalone WriteCallRequest admission
/// does not mint this token, nor can an existing result/pending reservation do so.
pub struct PreparedWriteCall {
    request: Box<WriteCallRequest>,
}
impl PreparedWriteCall {
    pub fn request(&self) -> &WriteCallRequest {
        &self.request
    }
    pub(super) fn into_request(self) -> WriteCallRequest {
        *self.request
    }
}
#[derive(Debug)]
pub enum WriteCallPreparation {
    Created(PreparedWriteCall),
    Reused(IdempotencyReservation),
    Pending(IdempotencyReservation),
}
#[derive(Clone, Copy)]
pub struct WriteCallContext<'a> {
    pub tenant_id: &'a str,
    pub execution_id: &'a str,
    pub business_operation_id: Option<&'a str>,
}
pub struct WriteCallBuildRequest<'a> {
    pub intent: &'a WriteIntent,
    pub context: WriteCallContext<'a>,
    pub contract_evidence: &'a WriteContractEvidence,
    pub reservation: &'a IdempotencyReservation,
    pub approval_request_id: &'a str,
    pub invocation_id: &'a str,
    pub business_preconditions: &'a BusinessPreconditionEvidence,
    pub trace_refs: &'a [String],
}
#[derive(Clone, Copy, Debug, Default)]
pub struct WriteCallRequestBuilder;
impl WriteCallRequestBuilder {
    pub fn build(
        &self,
        r: WriteCallBuildRequest<'_>,
        limits: WriteSnapshotLimits,
    ) -> Result<WriteCallPreparation, WriteExecutionError> {
        let i = r.intent.metadata();
        let c = r.contract_evidence.metadata();
        let d = r.reservation.evidence().metadata();
        let b = r.business_preconditions.metadata();
        let binding = |path, msg| error(WriteExecutionErrorKind::Binding, path, msg);
        if r.context.tenant_id != i.tenant_id {
            return Err(binding(
                "$.context.tenant_id",
                "WRITE tenant binding mismatch",
            ));
        }
        if r.context.business_operation_id != Some(i.business_operation_id.as_str()) {
            return Err(binding(
                "$.context.business_operation_id",
                "WRITE business operation binding mismatch",
            ));
        }
        if c.capability != ToolCapability::Write || c.tool_id != i.tool_id {
            return Err(binding(
                "$.contract_evidence",
                "canonical WRITE Tool binding mismatch",
            ));
        }
        if d.tenant_id != i.tenant_id
            || d.business_operation_id != i.business_operation_id
            || d.write_node_id != i.node_id
            || d.tool_id != i.tool_id
            || d.contract_hash != c.contract_hash
            || d.payload_hash != i.payload_hash
        {
            return Err(binding(
                "$.reservation.evidence",
                "idempotency reservation binding mismatch",
            ));
        }
        if b.tenant_id != i.tenant_id
            || b.business_operation_id != i.business_operation_id
            || b.write_node_id != i.node_id
            || b.tool_id != i.tool_id
            || b.contract_hash != c.contract_hash
            || b.payload_hash != i.payload_hash
            || b.write_intent_hash != i.write_intent_hash
            || !b.provider_atomic_enforcement
        {
            return Err(binding(
                "$.business_preconditions",
                "business precondition binding mismatch",
            ));
        }
        for (path, value) in [
            ("$.execution_id", r.context.execution_id),
            ("$.invocation_id", r.invocation_id),
            ("$.approval_request_id", r.approval_request_id),
        ] {
            required(value, path)?;
        }
        traces(r.trace_refs)?;
        match r.reservation.disposition() {
            IdempotencyDisposition::Conflict => {
                return Err(error(
                    WriteExecutionErrorKind::Conflict,
                    "$.reservation.disposition",
                    "idempotency reservation conflicts with payload",
                ));
            }
            IdempotencyDisposition::Reused => {
                return Ok(if r.reservation.business_result_ref().is_some() {
                    WriteCallPreparation::Reused(r.reservation.clone())
                } else {
                    WriteCallPreparation::Pending(r.reservation.clone())
                });
            }
            IdempotencyDisposition::Created => {}
        }
        // The validated intent is borrowed. Check the caller's new snapshot profile
        // before building an owned Spec and before its recursive argument clone.
        crate::snapshot::check(r.intent.canonical_arguments(), limits.metadata)?;
        crate::canonical_write_arguments(r.intent.canonical_arguments(), limits.canonical)?;
        let s = WriteCallRequestSpec {
            tenant_id: i.tenant_id.clone(),
            execution_id: r.context.execution_id.into(),
            business_operation_id: i.business_operation_id.clone(),
            invocation_id: r.invocation_id.into(),
            node_id: i.node_id.clone(),
            tool_ref: i.tool_ref.clone(),
            tool_id: i.tool_id.clone(),
            contract_hash: c.contract_hash.clone(),
            arguments: r.intent.canonical_arguments().clone(),
            idempotency_key: d.idempotency_key.clone(),
            payload_hash: i.payload_hash.clone(),
            approval_id: i.approval_id.clone(),
            approval_request_id: r.approval_request_id.into(),
            write_intent_hash: i.write_intent_hash.clone(),
            business_preconditions: r.business_preconditions.clone(),
            trace_refs: r.trace_refs.to_vec(),
        };
        Ok(WriteCallPreparation::Created(PreparedWriteCall {
            request: Box::new(WriteCallRequest::new(&s, limits)?),
        }))
    }
}
macro_rules! safe_debug {($($ty:ty),*)=>{$(impl fmt::Debug for $ty {fn fmt(&self,f:&mut fmt::Formatter<'_>)->fmt::Result {f.debug_struct(stringify!($ty)).finish_non_exhaustive()}})*};}
safe_debug!(WriteCallRequestSpec, WriteCallRequest, PreparedWriteCall);
