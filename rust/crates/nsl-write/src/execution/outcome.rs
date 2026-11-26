use super::{WriteExecutionOutcome, WriteInvocation, WriteOutcomeIdentity};
use crate::{
    WriteExecutionError, WriteExecutionErrorKind,
    requested::{hash, required},
};
use nsl_core::{CommitState, PayloadHash, UtcInstant, ValueData, canonical_json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fmt, future::Future, pin::Pin};

fn text(value: impl Into<String>) -> ValueData {
    ValueData::String(value.into())
}
fn object(
    values: impl IntoIterator<Item = (&'static str, ValueData)>,
) -> BTreeMap<String, ValueData> {
    values.into_iter().map(|(k, v)| (k.into(), v)).collect()
}
fn data(values: impl IntoIterator<Item = (&'static str, ValueData)>) -> ValueData {
    ValueData::Object(object(values))
}
fn optional(value: &Option<String>, path: &str) -> Result<(), WriteExecutionError> {
    if let Some(value) = value {
        required(value, path)?;
    }
    Ok(())
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteOutcomeRecordSpec {
    pub requested_record_hash: String,
    pub identity: WriteOutcomeIdentity,
    pub commit_state: CommitState,
    pub reason_code: Option<String>,
    pub provider_transaction_id: Option<String>,
    pub business_reference: Option<String>,
    pub evidence_refs: Vec<String>,
    pub recorded_at: UtcInstant,
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteOutcomeRecord {
    spec: WriteOutcomeRecordSpec,
    canonical: Vec<u8>,
    hash: PayloadHash,
}
impl WriteOutcomeRecord {
    pub fn new(s: WriteOutcomeRecordSpec) -> Result<Self, WriteExecutionError> {
        hash(&s.requested_record_hash, "$.record.requested_record_hash")?;
        for (name, value) in [
            ("reason_code", &s.reason_code),
            ("provider_transaction_id", &s.provider_transaction_id),
            ("business_reference", &s.business_reference),
        ] {
            optional(value, &format!("$.record.{name}"))?;
        }
        for (i, value) in s.evidence_refs.iter().enumerate() {
            required(value, &format!("$.record.evidence_refs[{i}]"))?;
        }
        let canonical =
            canonical_json(&Self::canonical_data(&s)).expect("flat string-only outcome metadata");
        let mut h = Sha256::new();
        h.update(b"NSL-WRITE-OUTCOME-V1\0");
        h.update(&canonical);
        Ok(Self {
            spec: s,
            canonical,
            hash: PayloadHash::from_digest(h.finalize().into()),
        })
    }
    /// Derive linkage from the private invocation, never an arbitrary caller hash.
    fn from_invocation(invocation: &WriteInvocation, recorded_at: UtcInstant) -> Self {
        let (reason_code, provider_transaction_id, business_reference, evidence_refs) =
            match invocation.outcome() {
                WriteExecutionOutcome::Committed(v) => {
                    let s = v.metadata();
                    (
                        None,
                        s.provider_transaction_id.clone(),
                        Some(s.business_reference.clone()),
                        s.evidence_refs.clone(),
                    )
                }
                WriteExecutionOutcome::NotCommitted(v) => {
                    let s = v.metadata();
                    (
                        Some(s.reason_code.clone()),
                        s.provider_transaction_id.clone(),
                        None,
                        s.evidence_refs.clone(),
                    )
                }
                WriteExecutionOutcome::Unknown(v) => {
                    let s = v.metadata();
                    (
                        Some(s.reason_code.clone()),
                        s.provider_transaction_id.clone(),
                        None,
                        s.evidence_refs.clone(),
                    )
                }
            };
        Self::new(WriteOutcomeRecordSpec {
            requested_record_hash: invocation.requested_record().record_hash().to_string(),
            identity: invocation.outcome().identity().clone(),
            commit_state: invocation.outcome().commit_state(),
            reason_code,
            provider_transaction_id,
            business_reference,
            evidence_refs,
            recorded_at,
        })
        .expect("validated invocation metadata")
    }
    fn identity_fields(identity: &WriteOutcomeIdentity) -> BTreeMap<String, ValueData> {
        let s = identity.metadata();
        object([
            ("tenant_id", text(&s.tenant_id)),
            ("business_operation_id", text(&s.business_operation_id)),
            ("invocation_id", text(&s.invocation_id)),
            ("node_id", text(&s.node_id)),
            ("tool_id", text(&s.tool_id)),
            ("idempotency_key", text(&s.idempotency_key)),
            ("payload_hash", text(&s.payload_hash)),
            ("approval_request_id", text(&s.approval_request_id)),
            ("write_intent_hash", text(&s.write_intent_hash)),
        ])
    }
    fn fields(s: &WriteOutcomeRecordSpec) -> BTreeMap<String, ValueData> {
        let optional = |v: &Option<String>| v.as_ref().map(text).unwrap_or(ValueData::Null);
        object([
            ("requested_record_hash", text(&s.requested_record_hash)),
            ("commit_state", text(s.commit_state.as_str())),
            ("reason_code", optional(&s.reason_code)),
            (
                "provider_transaction_id",
                optional(&s.provider_transaction_id),
            ),
            ("business_reference", optional(&s.business_reference)),
            (
                "evidence_refs",
                ValueData::Array(s.evidence_refs.iter().map(text).collect()),
            ),
            ("recorded_at", text(s.recorded_at.to_string())),
        ])
    }
    fn canonical_data(s: &WriteOutcomeRecordSpec) -> ValueData {
        let mut fields = Self::fields(s);
        fields.extend(Self::identity_fields(&s.identity));
        ValueData::Object(fields)
    }
    /// Oracle dataclass/journal projection; hash input instead flattens identity.
    pub fn to_data(&self) -> ValueData {
        let mut fields = Self::fields(&self.spec);
        fields.insert(
            "identity".into(),
            ValueData::Object(Self::identity_fields(&self.spec.identity)),
        );
        ValueData::Object(fields)
    }
    pub fn metadata(&self) -> &WriteOutcomeRecordSpec {
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
pub struct WriteOutcomeReceiptSpec {
    pub record_hash: String,
    pub durable_ref: String,
    pub persisted_at: UtcInstant,
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteOutcomeReceipt(WriteOutcomeReceiptSpec);
impl WriteOutcomeReceipt {
    pub fn new(s: WriteOutcomeReceiptSpec) -> Result<Self, WriteExecutionError> {
        hash(&s.record_hash, "$.receipt.record_hash")?;
        required(&s.durable_ref, "$.receipt.durable_ref")?;
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &WriteOutcomeReceiptSpec {
        &self.0
    }
    pub fn to_data(&self) -> ValueData {
        data([
            ("record_hash", text(&self.0.record_hash)),
            ("durable_ref", text(&self.0.durable_ref)),
            ("persisted_at", text(self.0.persisted_at.to_string())),
        ])
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteJournalEntry {
    record: WriteOutcomeRecord,
    durable_ref: String,
}
impl WriteJournalEntry {
    pub fn new(
        record: WriteOutcomeRecord,
        durable_ref: String,
    ) -> Result<Self, WriteExecutionError> {
        required(&durable_ref, "$.journal.durable_ref")?;
        Ok(Self {
            record,
            durable_ref,
        })
    }
    pub fn record(&self) -> &WriteOutcomeRecord {
        &self.record
    }
    pub fn durable_ref(&self) -> &str {
        &self.durable_ref
    }
    pub fn to_data(&self) -> ValueData {
        data([
            ("record", self.record.to_data()),
            ("durable_ref", text(&self.durable_ref)),
        ])
    }
}
/// Trusted storage adapter: durably persist the complete record before issuing
/// its matching acknowledgement. Error/drop can occur after durable acceptance.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WriteOutcomeEvidencePortError {
    Storage,
    MalformedReceipt,
}
pub type WriteOutcomeEvidenceFuture<'a> = Pin<
    Box<
        dyn Future<Output = Result<WriteOutcomeReceipt, WriteOutcomeEvidencePortError>> + Send + 'a,
    >,
>;
pub trait WriteOutcomeEvidencePort: Send + Sync {
    fn record_outcome<'a>(
        &'a self,
        record: &'a WriteOutcomeRecord,
    ) -> WriteOutcomeEvidenceFuture<'a>;
}
/// Borrow the Runtime-owned maps/journal directly without a reverse dependency.
pub struct WriteOutcomeContext<'a> {
    pub tenant_id: &'a str,
    pub execution_id: &'a str,
    pub business_operation_id: Option<&'a str>,
    pub keys_by_write_node: &'a BTreeMap<String, String>,
    pub payload_hashes_by_write_node: &'a BTreeMap<String, String>,
    pub journal: &'a mut Vec<ValueData>,
}
/// An acknowledgement-validated observation. This is not final Runtime success
/// or a substitute for R9-S04 post-WRITE verification.
pub struct PersistedWriteOutcome {
    invocation: WriteInvocation,
    entry: WriteJournalEntry,
    receipt: WriteOutcomeReceipt,
}
impl PersistedWriteOutcome {
    pub fn invocation(&self) -> &WriteInvocation {
        &self.invocation
    }
    pub fn entry(&self) -> &WriteJournalEntry {
        &self.entry
    }
    pub fn receipt(&self) -> &WriteOutcomeReceipt {
        &self.receipt
    }
}
/// Preserve the original observation on admission/storage/receipt failure. It
/// contains no reusable provider permit and cannot authorize a business retry.
pub struct WriteOutcomePersistenceError {
    pub error: WriteExecutionError,
    invocation: Box<WriteInvocation>,
}
impl WriteOutcomePersistenceError {
    pub fn invocation(&self) -> &WriteInvocation {
        &self.invocation
    }
}
impl fmt::Display for WriteOutcomePersistenceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.error.fmt(f)
    }
}
impl std::error::Error for WriteOutcomePersistenceError {}
fn rejected(path: &str, message: &'static str) -> WriteExecutionError {
    WriteExecutionError {
        kind: WriteExecutionErrorKind::Outcome,
        path: path.into(),
        message,
    }
}
pub struct WriteOutcomeCoordinator<'a> {
    port: &'a dyn WriteOutcomeEvidencePort,
}
impl<'a> WriteOutcomeCoordinator<'a> {
    pub fn new(port: &'a dyn WriteOutcomeEvidencePort) -> Self {
        Self { port }
    }
    pub async fn persist(
        &self,
        invocation: WriteInvocation,
        context: WriteOutcomeContext<'_>,
        recorded_at: UtcInstant,
    ) -> Result<PersistedWriteOutcome, WriteOutcomePersistenceError> {
        let s = invocation.request().metadata();
        let expected = WriteOutcomeIdentity::from_request(invocation.request());
        let admission = if invocation.outcome().identity() != &expected {
            Some(("$.outcome.identity", "provider outcome identity mismatch"))
        } else if s.execution_id != context.execution_id {
            Some(("$.context.execution_id", "WRITE outcome execution mismatch"))
        } else if s.tenant_id != context.tenant_id {
            Some(("$.context.tenant_id", "WRITE outcome tenant mismatch"))
        } else if Some(s.business_operation_id.as_str()) != context.business_operation_id {
            Some((
                "$.context.business_operation_id",
                "WRITE outcome business operation mismatch",
            ))
        } else if context.keys_by_write_node.get(&s.node_id) != Some(&s.idempotency_key)
            || context.payload_hashes_by_write_node.get(&s.node_id) != Some(&s.payload_hash)
        {
            Some((
                "$.context.idempotency",
                "WRITE outcome idempotency state mismatch",
            ))
        } else {
            None
        };
        if let Some((path, message)) = admission {
            return Err(WriteOutcomePersistenceError {
                error: rejected(path, message),
                invocation: Box::new(invocation),
            });
        }
        let record = WriteOutcomeRecord::from_invocation(&invocation, recorded_at);
        let receipt = match self.port.record_outcome(&record).await {
            Ok(receipt) => receipt,
            Err(e) => {
                return Err(WriteOutcomePersistenceError {
                    error: if e == WriteOutcomeEvidencePortError::MalformedReceipt {
                        rejected(
                            "$.receipt",
                            "outcome persistence returned an invalid receipt",
                        )
                    } else {
                        WriteExecutionError {
                            kind: WriteExecutionErrorKind::Port,
                            path: "$.receipt".into(),
                            message: "WRITE outcome durable storage failed",
                        }
                    },
                    invocation: Box::new(invocation),
                });
            }
        };
        if receipt.metadata().record_hash != record.record_hash().as_str() {
            return Err(WriteOutcomePersistenceError {
                error: rejected("$.receipt.record_hash", "outcome receipt binding mismatch"),
                invocation: Box::new(invocation),
            });
        }
        if receipt.metadata().persisted_at < record.metadata().recorded_at {
            return Err(WriteOutcomePersistenceError {
                error: rejected(
                    "$.receipt.persisted_at",
                    "outcome receipt predates the outcome",
                ),
                invocation: Box::new(invocation),
            });
        }
        let entry = WriteJournalEntry::new(record, receipt.metadata().durable_ref.clone())
            .expect("validated receipt reference");
        context.journal.push(entry.to_data());
        Ok(PersistedWriteOutcome {
            invocation,
            entry,
            receipt,
        })
    }
}
macro_rules! safe_debug {($($ty:ty),*)=>{$(impl fmt::Debug for $ty {fn fmt(&self,f:&mut fmt::Formatter<'_>)->fmt::Result {f.debug_struct(stringify!($ty)).finish_non_exhaustive()}})*};}
safe_debug!(
    WriteOutcomeRecordSpec,
    WriteOutcomeRecord,
    WriteOutcomeReceiptSpec,
    WriteOutcomeReceipt,
    WriteJournalEntry,
    PersistedWriteOutcome,
    WriteOutcomePersistenceError
);
