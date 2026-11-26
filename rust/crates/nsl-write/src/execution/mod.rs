mod models;
mod outcome;
use crate::{DurableWriteRequest, WriteAttemptRecord, WriteCallRequest, WriteRequestedReceipt};
pub use models::*;
use nsl_core::ExecutionStatus;
pub use outcome::*;
use std::{
    fmt,
    future::{Future, poll_fn},
    pin::Pin,
    task::Poll,
    time::Duration,
};

/// Invocation-local sticky cancellation. Request it, then await invocation:
/// notification neither aborts the provider future nor asserts business rollback.
#[derive(Clone, Debug)]
pub struct WriteCancellationSignal {
    sender: tokio::sync::watch::Sender<bool>,
}
impl Default for WriteCancellationSignal {
    fn default() -> Self {
        Self {
            sender: tokio::sync::watch::channel(false).0,
        }
    }
}
impl WriteCancellationSignal {
    pub fn request(&self) {
        self.sender.send_replace(true);
    }
    pub fn is_requested(&self) -> bool {
        *self.sender.borrow()
    }
    pub async fn cancelled(&self) {
        let mut receiver = self.sender.subscribe();
        // This signal owns a sender throughout the wait; closure is impossible.
        let _ = receiver.wait_for(|requested| *requested).await;
    }
}

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
    fn execute<'a>(
        &'a self,
        request: &'a WriteCallRequest,
        cancellation: WriteCancellationSignal,
    ) -> WriteExecutionFuture<'a>;
}
/// Observed invocation, not a durable outcome or final business success. R9-S03
/// must bind/persist its identity against this request before releasing a result.
/// The consumed durable handoff cannot be extracted or invoked a second time.
pub struct WriteInvocation {
    durable: DurableWriteRequest,
    outcome: WriteExecutionOutcome,
    provider_error: Option<WriteProviderError>,
    deadline_elapsed: bool,
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
    /// Original typed provider/cleanup termination. Timeout mapping never erases
    /// a distinct cleanup error or the provider's eventual successful outcome.
    pub fn provider_error(&self) -> Option<WriteProviderError> {
        self.provider_error
    }
    pub fn deadline_elapsed(&self) -> bool {
        self.deadline_elapsed
    }
    pub fn automatic_retry_allowed(&self) -> bool {
        false
    }
    pub fn reconciliation_required(&self) -> bool {
        matches!(self.outcome, WriteExecutionOutcome::Unknown(_))
    }
    /// Recovery advice only. A definite observation still needs R9-S03 outcome
    /// admission/persistence and later verification before final success/failure.
    pub fn recovery_status(&self) -> Option<ExecutionStatus> {
        self.reconciliation_required()
            .then_some(ExecutionStatus::ReconciliationRequired)
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
    /// One awaited call after R8 admission. Cancellation is cooperative: keep
    /// awaiting to observe its cleanup, suppression result or cleanup error.
    /// Dropping this future after polling can leave a real mutation uncertain;
    /// its drop never supplies a rollback claim or a reusable handoff.
    pub async fn invoke(
        &self,
        durable: DurableWriteRequest,
        cancellation: WriteCancellationSignal,
    ) -> WriteInvocation {
        let result = self.port.execute(durable.request(), cancellation).await;
        Self::observed(durable, result, false)
    }
    /// Requires a host Tokio time driver. Match asyncio.wait_for: nonpositive
    /// budgets invoke no provider; after expiry signal once and await cleanup
    /// without a second deadline. Completion/suppression can return any explicit
    /// outcome. The timer itself never proves a commit or rollback.
    pub async fn invoke_with_timeout(
        &self,
        durable: DurableWriteRequest,
        timeout_ms: i64,
        cancellation: WriteCancellationSignal,
    ) -> WriteInvocation {
        if timeout_ms <= 0 {
            return Self::observed(durable, Err(WriteProviderError::Timeout), true);
        }
        let (result, elapsed) = {
            let mut execution = self.port.execute(durable.request(), cancellation.clone());
            let timer = tokio::time::sleep(Duration::from_millis(timeout_ms.unsigned_abs()));
            let mut timer = std::pin::pin!(timer);
            let completed = poll_fn(|cx| {
                if let Poll::Ready(result) = execution.as_mut().poll(cx) {
                    return Poll::Ready(Some(result));
                }
                if timer.as_mut().poll(cx).is_ready() {
                    Poll::Ready(None)
                } else {
                    Poll::Pending
                }
            })
            .await;
            match completed {
                Some(result) => (result, false),
                None => {
                    cancellation.request();
                    (execution.await, true)
                }
            }
        };
        Self::observed(durable, result, elapsed)
    }
    fn observed(
        durable: DurableWriteRequest,
        result: Result<WriteExecutionOutcome, WriteProviderError>,
        deadline_elapsed: bool,
    ) -> WriteInvocation {
        let provider_error = result.as_ref().err().copied();
        let outcome = match result {
            Ok(outcome) => outcome,
            Err(error) => WriteExecutionOutcome::Unknown(
                WriteCommitUnknown::new(WriteFailureSpec {
                    identity: WriteOutcomeIdentity::from_request(durable.request()),
                    reason_code: if deadline_elapsed && error == WriteProviderError::Cancelled {
                        WriteProviderError::Timeout.reason_code()
                    } else {
                        error.reason_code()
                    }
                    .into(),
                    message: "provider outcome is uncertain; reconciliation is required".into(),
                    provider_transaction_id: None,
                    evidence_refs: durable.request().metadata().trace_refs.clone(),
                })
                .expect("validated request identity/traces and fixed safe message"),
            ),
        };
        WriteInvocation {
            durable,
            outcome,
            provider_error,
            deadline_elapsed,
        }
    }
}
