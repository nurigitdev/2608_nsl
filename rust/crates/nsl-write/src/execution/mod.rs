mod models;
use crate::{DurableWriteRequest, WriteAttemptRecord, WriteCallRequest, WriteRequestedReceipt};
pub use models::*;
use std::{fmt, future::Future, pin::Pin};

/// A trusted adapter classifies transport/protocol termination, never business
/// rollback. Protected raw causes are retained by the host, outside public data.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WriteProviderError {
    Cancelled,
    Timeout,
    ConnectionLost,
    Exception,
    MalformedAcknowledgement,
}
impl WriteProviderError {
    pub fn reason_code(self) -> &'static str {
        match self {
            Self::Cancelled => "PROVIDER_CANCELLED",
            Self::Timeout => "PROVIDER_TIMEOUT",
            Self::ConnectionLost => "PROVIDER_CONNECTION_LOST",
            Self::Exception => "PROVIDER_EXCEPTION",
            Self::MalformedAcknowledgement => "PROVIDER_ACKNOWLEDGEMENT_MALFORMED",
        }
    }
}
pub type WriteExecutionFuture<'a> =
    Pin<Box<dyn Future<Output = Result<WriteExecutionOutcome, WriteProviderError>> + Send + 'a>>;
/// Privileged host boundary. A normal response is an explicit business outcome;
/// an error cannot assert NOT_COMMITTED. No runtime/executor type crosses it.
pub trait WriteExecutionPort: Send + Sync {
    fn execute<'a>(&'a self, request: &'a WriteCallRequest) -> WriteExecutionFuture<'a>;
}
/// Observed invocation, not a durable outcome or final business success. R9-S03
/// must bind/persist its identity against this request before releasing a result.
/// The consumed durable handoff cannot be extracted or invoked a second time.
pub struct WriteInvocation {
    durable: DurableWriteRequest,
    outcome: WriteExecutionOutcome,
}
impl WriteInvocation {
    pub fn request(&self) -> &WriteCallRequest {
        self.durable.request()
    }
    pub fn requested_record(&self) -> &WriteAttemptRecord {
        self.durable.record()
    }
    pub fn requested_receipt(&self) -> &WriteRequestedReceipt {
        self.durable.receipt()
    }
    pub fn outcome(&self) -> &WriteExecutionOutcome {
        &self.outcome
    }
}
impl fmt::Debug for WriteInvocation {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WriteInvocation").finish_non_exhaustive()
    }
}
pub struct WriteInvocationCoordinator<'a> {
    port: &'a dyn WriteExecutionPort,
}
impl<'a> WriteInvocationCoordinator<'a> {
    pub fn new(port: &'a dyn WriteExecutionPort) -> Self {
        Self { port }
    }
    /// One awaited call, after R8's current durable acknowledgement. No spawn,
    /// timer, retry, task-abort inference, outcome persistence or verification.
    /// Dropping this future after polling can leave a real mutation uncertain;
    /// its drop never supplies a rollback claim or a reusable handoff.
    pub async fn invoke(&self, durable: DurableWriteRequest) -> WriteInvocation {
        let outcome = match self.port.execute(durable.request()).await {
            Ok(outcome) => outcome,
            Err(error) => WriteExecutionOutcome::Unknown(
                WriteCommitUnknown::new(WriteFailureSpec {
                    identity: WriteOutcomeIdentity::from_request(durable.request()),
                    reason_code: error.reason_code().into(),
                    message: "provider outcome is uncertain; reconciliation is required".into(),
                    provider_transaction_id: None,
                    evidence_refs: durable.request().metadata().trace_refs.clone(),
                })
                .expect("validated request identity/traces and fixed safe message"),
            ),
        };
        WriteInvocation { durable, outcome }
    }
}
