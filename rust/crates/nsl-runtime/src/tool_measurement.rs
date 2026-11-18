use crate::{
    CancellationSignal, RuntimeClock, ToolCallError, ToolCallRequest, ToolExecutionFuture,
    ToolExecutionPort,
    data::{bigint, blank, integer},
};
use nsl_core::Integer;
use std::sync::Mutex;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ToolExecutionOutcome {
    Returned,
    Error,
    Cancelled,
}
impl ToolExecutionOutcome {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Returned => "RETURNED",
            Self::Error => "ERROR",
            Self::Cancelled => "CANCELLED",
        }
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolExecutionMeasurement {
    pub execution_id: String,
    pub invocation_id: String,
    pub node_id: String,
    pub tool_id: String,
    pub tool_version: String,
    pub tenant_id: String,
    pub outcome: ToolExecutionOutcome,
    pub duration_ns: Integer,
}
impl ToolExecutionMeasurement {
    pub fn validate(&self) -> Result<(), ToolCallError> {
        if [
            &self.execution_id,
            &self.invocation_id,
            &self.node_id,
            &self.tool_id,
            &self.tool_version,
            &self.tenant_id,
        ]
        .iter()
        .any(|id| blank(id))
        {
            return Err(ToolCallError::Measurement(
                "tool measurement identifiers must be non-empty".into(),
            ));
        }
        if bigint(&self.duration_ns) < 0.into() {
            return Err(ToolCallError::Measurement(
                "tool measurement duration_ns must be non-negative".into(),
            ));
        }
        Ok(())
    }
    pub fn duration_ms(&self) -> Integer {
        integer(bigint(&self.duration_ns) / 1_000_000)
    }
}
pub trait ToolMeasurementSink: Send + Sync {
    fn record(&self, measurement: ToolExecutionMeasurement) -> Result<(), ToolCallError>;
}
#[derive(Default)]
pub struct NullToolMeasurementSink;
impl ToolMeasurementSink for NullToolMeasurementSink {
    fn record(&self, _measurement: ToolExecutionMeasurement) -> Result<(), ToolCallError> {
        Ok(())
    }
}
#[derive(Default)]
pub struct InMemoryToolMeasurementSink {
    measurements: Mutex<Vec<ToolExecutionMeasurement>>,
}
impl InMemoryToolMeasurementSink {
    pub fn measurements(&self) -> Vec<ToolExecutionMeasurement> {
        self.measurements.lock().expect("measurement lock").clone()
    }
}
impl ToolMeasurementSink for InMemoryToolMeasurementSink {
    fn record(&self, measurement: ToolExecutionMeasurement) -> Result<(), ToolCallError> {
        self.measurements
            .lock()
            .expect("measurement lock")
            .push(measurement);
        Ok(())
    }
}
pub struct MeasuringToolExecutor<P, C, S> {
    pub delegate: P,
    pub clock: C,
    pub sink: S,
}
impl<P: ToolExecutionPort, C: RuntimeClock + Send + Sync, S: ToolMeasurementSink> ToolExecutionPort
    for MeasuringToolExecutor<P, C, S>
{
    fn execute<'a>(
        &'a self,
        request: &'a ToolCallRequest,
        cancellation: CancellationSignal,
    ) -> ToolExecutionFuture<'a> {
        Box::pin(async move {
            let started = bigint(&self.clock.monotonic_ns());
            let result = self.delegate.execute(request, cancellation).await;
            let outcome = match &result {
                Ok(_) => ToolExecutionOutcome::Returned,
                Err(ToolCallError::Cancelled) => ToolExecutionOutcome::Cancelled,
                Err(_) => ToolExecutionOutcome::Error,
            };
            let measurement = ToolExecutionMeasurement {
                execution_id: request.execution_id.clone(),
                invocation_id: request.invocation_id.clone(),
                node_id: request.node_id.clone(),
                tool_id: request.tool_id.clone(),
                tool_version: request.tool_version.clone(),
                tenant_id: request.principal.tenant_id.clone(),
                outcome,
                duration_ns: integer((bigint(&self.clock.monotonic_ns()) - started).max(0.into())),
            };
            measurement.validate()?;
            // Oracle finally semantics: a failing sink overrides prior results.
            self.sink.record(measurement)?;
            result
        })
    }
}
