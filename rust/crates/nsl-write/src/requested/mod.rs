mod call;
mod record;
use crate::{WriteGuardDecision, WriteIntentError, WriteIntentErrorKind, require_write_allowed};
pub use call::*;
use nsl_core::{PayloadHash, UtcInstant};
pub use record::*;
use std::{fmt, future::Future, pin::Pin};
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WriteExecutionErrorKind {
    Model,
    Binding,
    Conflict,
    Snapshot(WriteIntentErrorKind),
    Evidence,
    Port,
    NotAllowed,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteExecutionError {
    pub kind: WriteExecutionErrorKind,
    pub path: String,
    pub message: &'static str,
}
impl WriteExecutionError {
    pub fn detail_code(&self) -> Option<&'static str> {
        match self.kind {
            WriteExecutionErrorKind::Evidence => Some("WRITE_REQUEST_EVIDENCE_INVALID"),
            WriteExecutionErrorKind::Conflict => Some("IDEMPOTENCY_CONFLICT"),
            _ => None,
        }
    }
}
impl fmt::Display for WriteExecutionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.path, self.message)
    }
}
impl std::error::Error for WriteExecutionError {}
fn error(kind: WriteExecutionErrorKind, path: &str, message: &'static str) -> WriteExecutionError {
    WriteExecutionError {
        kind,
        path: nsl_core::redact_text(path),
        message,
    }
}
fn required(s: &str, path: &str) -> Result<(), WriteExecutionError> {
    crate::required(s, path).map_err(|_| {
        error(
            WriteExecutionErrorKind::Model,
            path,
            "expected nonempty credential-free identity",
        )
    })
}
fn hash(s: &str, path: &str) -> Result<PayloadHash, WriteExecutionError> {
    required(s, path)?;
    crate::hash(s, path).map_err(|_| {
        error(
            WriteExecutionErrorKind::Model,
            path,
            "expected lowercase SHA-256 identity",
        )
    })
}
fn key(s: &str, path: &str) -> Result<nsl_core::IdempotencyKey, WriteExecutionError> {
    required(s, path)?;
    s.parse().map_err(|_| {
        error(
            WriteExecutionErrorKind::Model,
            path,
            "expected idem SHA-256 prefix and 64-character suffix",
        )
    })
}
impl From<WriteIntentError> for WriteExecutionError {
    fn from(e: WriteIntentError) -> Self {
        error(
            WriteExecutionErrorKind::Snapshot(e.kind),
            &e.path,
            e.message,
        )
    }
}
/// Trusted host adapter: durably persist this entire record before acknowledging
/// its hash. An error/drop can happen after acceptance; it never proves rollback.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WriteEvidencePortError;
pub type WriteEvidenceFuture<'a> = Pin<
    Box<dyn Future<Output = Result<WriteRequestedReceipt, WriteEvidencePortError>> + Send + 'a>,
>;
pub trait WriteEvidencePort: Send + Sync {
    fn record_requested<'a>(&'a self, record: &'a WriteAttemptRecord) -> WriteEvidenceFuture<'a>;
}
/// Existing Runtime identity/checkpoint projection. A previously true flag is not
/// a substitute for the current request's matching durable acknowledgement.
pub struct WriteEvidenceContext<'a> {
    pub tenant_id: &'a str,
    pub execution_id: &'a str,
    pub business_operation_id: Option<&'a str>,
    pub write_request_durable: &'a mut bool,
}
/// Only this coordinator can construct the non-Clone durable handoff. Provider
/// orchestration in R9 consumes it; a raw receipt/checkpoint bool is not a permit.
pub struct DurableWriteRequest {
    request: WriteCallRequest,
    record: WriteAttemptRecord,
    receipt: WriteRequestedReceipt,
}
impl DurableWriteRequest {
    pub fn request(&self) -> &WriteCallRequest {
        &self.request
    }
    pub fn record(&self) -> &WriteAttemptRecord {
        &self.record
    }
    pub fn receipt(&self) -> &WriteRequestedReceipt {
        &self.receipt
    }
}
impl fmt::Debug for DurableWriteRequest {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("DurableWriteRequest")
            .finish_non_exhaustive()
    }
}
pub struct WriteRequestedCoordinator<'a> {
    port: &'a dyn WriteEvidencePort,
}
impl<'a> WriteRequestedCoordinator<'a> {
    pub fn new(port: &'a dyn WriteEvidencePort) -> Self {
        Self { port }
    }
    /// Accepts only a CREATED preparation. REUSED/PENDING have no prepared-call
    /// token, and CONFLICT fails before persistence. No business provider is called.
    pub async fn persist(
        &self,
        prepared: PreparedWriteCall,
        decision: &WriteGuardDecision,
        context: WriteEvidenceContext<'_>,
        requested_at: UtcInstant,
    ) -> Result<DurableWriteRequest, WriteExecutionError> {
        require_write_allowed(decision).map_err(|_| {
            error(
                WriteExecutionErrorKind::NotAllowed,
                "$.decision",
                "WRITE eligibility is not ALLOWED",
            )
        })?;
        let request = prepared.into_request();
        let s = request.metadata();
        for (mismatch, path) in [
            (
                s.execution_id != context.execution_id,
                "$.context.execution_id",
            ),
            (s.tenant_id != context.tenant_id, "$.context.tenant_id"),
            (
                Some(s.business_operation_id.as_str()) != context.business_operation_id,
                "$.context.business_operation_id",
            ),
        ] {
            if mismatch {
                return Err(error(
                    WriteExecutionErrorKind::Binding,
                    path,
                    "WRITE execution identity mismatch",
                ));
            }
        }
        let record = WriteAttemptRecord::from_request(&request, requested_at);
        let receipt = self.port.record_requested(&record).await.map_err(|_| {
            error(
                WriteExecutionErrorKind::Port,
                "$.receipt",
                "WRITE_REQUESTED durable storage failed",
            )
        })?;
        let reject = |message| error(WriteExecutionErrorKind::Evidence, "$.receipt", message);
        if receipt.metadata().record_hash != record.record_hash().as_str() {
            return Err(reject("WRITE_REQUESTED receipt binding mismatch"));
        }
        if !receipt.metadata().durable {
            return Err(reject("WRITE_REQUESTED evidence is not durable"));
        }
        if receipt.metadata().persisted_at < record.metadata().requested_at {
            return Err(reject("WRITE_REQUESTED receipt predates request"));
        }
        *context.write_request_durable = true;
        Ok(DurableWriteRequest {
            request,
            record,
            receipt,
        })
    }
}
