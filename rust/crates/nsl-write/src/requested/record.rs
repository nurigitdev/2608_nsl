use super::{WriteExecutionError, hash, key, required};
use crate::WriteCallRequest;
use nsl_core::{PayloadHash, UtcInstant, ValueData, canonical_json};
use sha2::{Digest, Sha256};
use std::fmt;
fn text(s: impl Into<String>) -> ValueData {
    ValueData::String(s.into())
}
fn data(items: impl IntoIterator<Item = (&'static str, ValueData)>) -> ValueData {
    ValueData::Object(items.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteAttemptRecordSpec {
    pub tenant_id: String,
    pub execution_id: String,
    pub business_operation_id: String,
    pub invocation_id: String,
    pub node_id: String,
    pub tool_id: String,
    pub contract_hash: String,
    pub approval_request_id: String,
    pub idempotency_key: String,
    pub payload_hash: String,
    pub write_intent_hash: String,
    pub precondition_evidence_id: String,
    pub requested_at: UtcInstant,
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteAttemptRecord {
    spec: WriteAttemptRecordSpec,
    hash: PayloadHash,
    canonical: Vec<u8>,
}
impl WriteAttemptRecord {
    pub fn new(s: WriteAttemptRecordSpec) -> Result<Self, WriteExecutionError> {
        for (name, value) in [
            ("tenant_id", &s.tenant_id),
            ("execution_id", &s.execution_id),
            ("business_operation_id", &s.business_operation_id),
            ("invocation_id", &s.invocation_id),
            ("node_id", &s.node_id),
            ("tool_id", &s.tool_id),
            ("approval_request_id", &s.approval_request_id),
            ("precondition_evidence_id", &s.precondition_evidence_id),
        ] {
            required(value, &format!("$.record.{name}"))?;
        }
        for (name, value) in [
            ("contract_hash", &s.contract_hash),
            ("payload_hash", &s.payload_hash),
            ("write_intent_hash", &s.write_intent_hash),
        ] {
            hash(value, &format!("$.record.{name}"))?;
        }
        key(&s.idempotency_key, "$.record.idempotency_key")?;
        let canonical = canonical_json(&Self::data(&s)).expect("string-only record");
        let mut h = Sha256::new();
        h.update(b"NSL-WRITE-REQUESTED-V1\0");
        h.update(&canonical);
        Ok(Self {
            spec: s,
            hash: PayloadHash::from_digest(h.finalize().into()),
            canonical,
        })
    }
    pub fn from_request(request: &WriteCallRequest, requested_at: UtcInstant) -> Self {
        let s = request.metadata();
        Self::new(WriteAttemptRecordSpec {
            tenant_id: s.tenant_id.clone(),
            execution_id: s.execution_id.clone(),
            business_operation_id: s.business_operation_id.clone(),
            invocation_id: s.invocation_id.clone(),
            node_id: s.node_id.clone(),
            tool_id: s.tool_id.clone(),
            contract_hash: s.contract_hash.clone(),
            approval_request_id: s.approval_request_id.clone(),
            idempotency_key: s.idempotency_key.clone(),
            payload_hash: s.payload_hash.clone(),
            write_intent_hash: s.write_intent_hash.clone(),
            precondition_evidence_id: s.business_preconditions.metadata().evidence_id.clone(),
            requested_at,
        })
        .expect("validated request identity")
    }
    fn data(s: &WriteAttemptRecordSpec) -> ValueData {
        data([
            ("tenant_id", text(&s.tenant_id)),
            ("execution_id", text(&s.execution_id)),
            ("business_operation_id", text(&s.business_operation_id)),
            ("invocation_id", text(&s.invocation_id)),
            ("node_id", text(&s.node_id)),
            ("tool_id", text(&s.tool_id)),
            ("contract_hash", text(&s.contract_hash)),
            ("approval_request_id", text(&s.approval_request_id)),
            ("idempotency_key", text(&s.idempotency_key)),
            ("payload_hash", text(&s.payload_hash)),
            ("write_intent_hash", text(&s.write_intent_hash)),
            (
                "precondition_evidence_id",
                text(&s.precondition_evidence_id),
            ),
            ("requested_at", text(s.requested_at.to_string())),
        ])
    }
    pub fn to_data(&self) -> ValueData {
        Self::data(&self.spec)
    }
    pub fn metadata(&self) -> &WriteAttemptRecordSpec {
        &self.spec
    }
    pub fn canonical_bytes(&self) -> &[u8] {
        &self.canonical
    }
    pub fn record_hash(&self) -> &PayloadHash {
        &self.hash
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteRequestedReceiptSpec {
    pub record_hash: String,
    pub durable_ref: String,
    pub persisted_at: UtcInstant,
    pub durable: bool,
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteRequestedReceipt(WriteRequestedReceiptSpec);
impl WriteRequestedReceipt {
    pub fn new(s: WriteRequestedReceiptSpec) -> Result<Self, WriteExecutionError> {
        hash(&s.record_hash, "$.receipt.record_hash")?;
        required(&s.durable_ref, "$.receipt.durable_ref")?;
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &WriteRequestedReceiptSpec {
        &self.0
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.0;
        data([
            ("record_hash", text(&s.record_hash)),
            ("durable_ref", text(&s.durable_ref)),
            ("persisted_at", text(s.persisted_at.to_string())),
            ("durable", ValueData::Bool(s.durable)),
        ])
    }
}
macro_rules! safe_debug {($($ty:ty),*)=>{$(impl fmt::Debug for $ty {fn fmt(&self,f:&mut fmt::Formatter<'_>)->fmt::Result {f.debug_struct(stringify!($ty)).finish_non_exhaustive()}})*};}
safe_debug!(
    WriteAttemptRecordSpec,
    WriteAttemptRecord,
    WriteRequestedReceiptSpec,
    WriteRequestedReceipt
);
