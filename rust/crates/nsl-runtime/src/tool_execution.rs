use crate::{
    ToolCallError, ToolCallRequest, ToolContractValidator, ToolExecutionError, ToolResultEnvelope,
    data::{bigint, integer},
    tool_result_hash,
};
use nsl_contract::ToolContractCatalog;
use nsl_core::{Completeness, Integer, Presence, Value};
use std::{
    collections::BTreeMap,
    future::{Future, poll_fn},
    pin::Pin,
    sync::{Arc, Mutex},
    task::Poll,
    time::Duration,
};

/// Sticky explicit cancellation. Requesting cancellation does not abort or drop
/// the provider future: its caller must await completion to observe cleanup.
#[derive(Clone, Debug)]
pub struct CancellationSignal {
    sender: tokio::sync::watch::Sender<bool>,
}
impl Default for CancellationSignal {
    fn default() -> Self {
        Self {
            sender: tokio::sync::watch::channel(false).0,
        }
    }
}
impl CancellationSignal {
    pub fn request(&self) {
        self.sender.send_replace(true);
    }
    pub fn is_requested(&self) -> bool {
        *self.sender.borrow()
    }
    pub async fn cancelled(&self) {
        let mut receiver = self.sender.subscribe();
        // self owns a sender throughout this wait, so it cannot be closed.
        let _ = receiver.wait_for(|requested| *requested).await;
    }
}

pub type ToolExecutionFuture<'a> =
    Pin<Box<dyn Future<Output = Result<ToolResultEnvelope, ToolCallError>> + Send + 'a>>;
pub trait ToolExecutionPort: Send + Sync {
    fn execute<'a>(
        &'a self,
        request: &'a ToolCallRequest,
        cancellation: CancellationSignal,
    ) -> ToolExecutionFuture<'a>;
}
impl<T: ToolExecutionPort + ?Sized> ToolExecutionPort for &T {
    fn execute<'a>(
        &'a self,
        request: &'a ToolCallRequest,
        cancellation: CancellationSignal,
    ) -> ToolExecutionFuture<'a> {
        (**self).execute(request, cancellation)
    }
}
impl<T: ToolExecutionPort + ?Sized> ToolExecutionPort for Arc<T> {
    fn execute<'a>(
        &'a self,
        request: &'a ToolCallRequest,
        cancellation: CancellationSignal,
    ) -> ToolExecutionFuture<'a> {
        (**self).execute(request, cancellation)
    }
}

/// Requires a host Tokio time driver. There is no spawn, retry or commit-state
/// inference. After expiry, wait without a second cleanup deadline, matching
/// asyncio.wait_for: suppression may return a value or raise a cleanup error.
pub async fn execute_with_timeout(
    executor: &dyn ToolExecutionPort,
    request: &ToolCallRequest,
    timeout_ms: i64,
    cancellation: CancellationSignal,
) -> Result<ToolResultEnvelope, ToolCallError> {
    let timeout_error = || {
        ToolCallError::Execution(ToolExecutionError::new(
            "TOOL_TIMEOUT",
            format!("tool timed out after {timeout_ms} ms: {}", request.tool_id),
        ))
    };
    if timeout_ms <= 0 {
        return Err(timeout_error());
    }
    let mut execution = executor.execute(request, cancellation.clone());
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
    let result = match completed {
        Some(result) => result,
        None => {
            cancellation.request();
            match execution.await {
                Err(ToolCallError::Cancelled) => return Err(timeout_error()),
                result => result,
            }
        }
    };
    match result {
        Err(ToolCallError::ProviderTimeout) => Err(timeout_error()),
        result => result,
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MockToolResult {
    pub value: Value,
    pub completeness: Completeness,
}
impl From<Value> for MockToolResult {
    fn from(value: Value) -> Self {
        Self {
            value,
            completeness: Completeness::Complete,
        }
    }
}
pub type FixtureHandler =
    Arc<dyn Fn(&BTreeMap<String, Value>) -> Result<MockToolResult, ToolCallError> + Send + Sync>;

/// In-memory deterministic fixtures only. Calls are counted after admission and
/// before handler invocation, including failures. No transport or RuntimeEngine.
pub struct MockToolExecutor {
    catalog: ToolContractCatalog,
    handlers: BTreeMap<String, FixtureHandler>,
    call_count: Mutex<Integer>,
    max_decimal_bytes: usize,
}
impl MockToolExecutor {
    pub fn new(
        catalog: ToolContractCatalog,
        handlers: BTreeMap<String, FixtureHandler>,
        max_decimal_bytes: usize,
    ) -> Self {
        Self {
            catalog,
            handlers,
            call_count: Mutex::new(0.into()),
            max_decimal_bytes,
        }
    }
    pub fn call_count(&self) -> Integer {
        self.call_count.lock().expect("call count lock").clone()
    }
}
impl ToolExecutionPort for MockToolExecutor {
    fn execute<'a>(
        &'a self,
        request: &'a ToolCallRequest,
        _cancellation: CancellationSignal,
    ) -> ToolExecutionFuture<'a> {
        Box::pin(async move {
            let contract = self
                .catalog
                .resolve(&request.tool_id, &request.tool_version)
                .map_err(|_| {
                    ToolExecutionError::new(
                        "TOOL_CONTRACT_MISMATCH",
                        format!("contract unavailable: {}", request.tool_id),
                    )
                })?;
            ToolContractValidator.validate_request(request, contract)?;
            let handler = self.handlers.get(&request.tool_id).ok_or_else(|| {
                ToolExecutionError::new(
                    "MOCK_FIXTURE_NOT_FOUND",
                    format!("no fixture for {}", request.tool_id),
                )
            })?;
            let raw_arguments = request
                .arguments
                .iter()
                .map(|(k, v)| (k.clone(), v.value().clone()))
                .collect();
            {
                let mut count = self.call_count.lock().expect("call count lock");
                *count = integer(bigint(&count) + 1);
            }
            let result = handler(&raw_arguments)?;
            ToolContractValidator.validate_result_value(&result.value, contract)?;
            // The Oracle treats {} as PRESENT; only [] and None are EMPTY.
            let presence = if matches!(&result.value, Value::Null)
                || matches!(&result.value, Value::List(items) if items.is_empty())
            {
                Presence::Empty
            } else {
                Presence::Present
            };
            let result_hash = tool_result_hash(&result.value, self.max_decimal_bytes)?.to_string();
            Ok(ToolResultEnvelope {
                invocation_id: request.invocation_id.clone(),
                tool_id: request.tool_id.clone(),
                tool_version: request.tool_version.clone(),
                value: result.value,
                type_info: contract.output_type().clone(),
                presence,
                completeness: result.completeness,
                classification: contract.output_classification(),
                result_hash,
                snapshot_ref: None,
            })
        })
    }
}
