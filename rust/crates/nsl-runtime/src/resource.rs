use crate::data::{bigint, integer, number, object};
use nsl_core::{Integer, ValueData};
use nsl_ir::ResourceLimits;
use num_bigint::BigInt;
use std::{fmt, time::Instant};

/// Trusted host port for monotonic nanoseconds. Request data and wall-clock
/// timestamps do not participate in duration accounting. Test clocks may
/// supply exact signed integers to reproduce frozen component observations.
pub trait RuntimeClock {
    fn monotonic_ns(&self) -> Integer;
}
impl<T: RuntimeClock + ?Sized> RuntimeClock for &T {
    fn monotonic_ns(&self) -> Integer {
        (**self).monotonic_ns()
    }
}
#[derive(Debug)]
pub struct SystemRuntimeClock {
    origin: Instant,
}
impl Default for SystemRuntimeClock {
    fn default() -> Self {
        Self {
            origin: Instant::now(),
        }
    }
}
impl RuntimeClock for SystemRuntimeClock {
    fn monotonic_ns(&self) -> Integer {
        integer(self.origin.elapsed().as_nanos())
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResourceUsage {
    pub tool_calls: Integer,
    pub loop_iterations: Integer,
    pub emitted_rows: Integer,
    pub max_collection_size_seen: Integer,
}
impl Default for ResourceUsage {
    fn default() -> Self {
        Self {
            tool_calls: 0.into(),
            loop_iterations: 0.into(),
            emitted_rows: 0.into(),
            max_collection_size_seen: 0.into(),
        }
    }
}
impl ResourceUsage {
    pub fn to_data(&self) -> ValueData {
        object([
            ("tool_calls", number(&self.tool_calls)),
            ("loop_iterations", number(&self.loop_iterations)),
            ("emitted_rows", number(&self.emitted_rows)),
            (
                "max_collection_size_seen",
                number(&self.max_collection_size_seen),
            ),
        ])
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ResourceLimit {
    Duration,
    ToolCalls,
    LoopIterations,
    EmittedRows,
    CollectionSize,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LimitExceeded(pub ResourceLimit);
impl fmt::Display for LimitExceeded {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self.0 {
            ResourceLimit::Duration => "execution duration limit exceeded",
            ResourceLimit::ToolCalls => "tool call limit exceeded",
            ResourceLimit::LoopIterations => "loop iteration limit exceeded",
            ResourceLimit::EmittedRows => "emitted row limit exceeded",
            ResourceLimit::CollectionSize => "collection size limit exceeded",
        })
    }
}
impl std::error::Error for LimitExceeded {}

pub struct ResourceGuard<C: RuntimeClock = SystemRuntimeClock> {
    limits: ResourceLimits,
    meter: ResourceUsage,
    clock: C,
    started_ns: BigInt,
}
impl<C: RuntimeClock> ResourceGuard<C> {
    pub fn new(limits: ResourceLimits, clock: C) -> Self {
        Self::with_usage(limits, ResourceUsage::default(), clock)
    }
    pub fn with_usage(limits: ResourceLimits, meter: ResourceUsage, clock: C) -> Self {
        let started_ns = bigint(&clock.monotonic_ns());
        Self {
            limits,
            meter,
            clock,
            started_ns,
        }
    }
    fn remaining_ns(&self) -> BigInt {
        bigint(&self.limits.duration_ms) * 1_000_000
            - (bigint(&self.clock.monotonic_ns()) - &self.started_ns)
    }
    pub fn check_deadline(&self) -> Result<(), LimitExceeded> {
        if self.remaining_ns() <= 0.into() {
            Err(LimitExceeded(ResourceLimit::Duration))
        } else {
            Ok(())
        }
    }
    pub fn remaining_duration_ms(&self) -> Result<Integer, LimitExceeded> {
        let n = self.remaining_ns();
        if n <= 0.into() {
            return Err(LimitExceeded(ResourceLimit::Duration));
        }
        Ok(integer((n + 999_999) / 1_000_000))
    }
    fn consume(
        current: &mut Integer,
        limit: &Integer,
        kind: ResourceLimit,
    ) -> Result<(), LimitExceeded> {
        let n = bigint(current);
        if n >= bigint(limit) {
            return Err(LimitExceeded(kind));
        }
        *current = integer(n + 1);
        Ok(())
    }
    pub fn before_tool_call(&mut self) -> Result<(), LimitExceeded> {
        self.check_deadline()?;
        Self::consume(
            &mut self.meter.tool_calls,
            &self.limits.tool_calls,
            ResourceLimit::ToolCalls,
        )
    }
    pub fn before_loop_iteration(&mut self) -> Result<(), LimitExceeded> {
        self.check_deadline()?;
        Self::consume(
            &mut self.meter.loop_iterations,
            &self.limits.loop_iterations,
            ResourceLimit::LoopIterations,
        )
    }
    pub fn before_emit(&self) -> Result<(), LimitExceeded> {
        self.check_deadline()?;
        if bigint(&self.meter.emitted_rows) >= bigint(&self.limits.emitted_rows) {
            return Err(LimitExceeded(ResourceLimit::EmittedRows));
        }
        Ok(())
    }
    pub fn record_emit(&mut self) {
        self.meter.emitted_rows = integer(bigint(&self.meter.emitted_rows) + 1);
    }
    pub fn check_collection(&self, size: &Integer) -> Result<(), LimitExceeded> {
        self.check_deadline()?;
        if bigint(size) > bigint(&self.limits.collection_size) {
            return Err(LimitExceeded(ResourceLimit::CollectionSize));
        }
        Ok(())
    }
    pub fn observe_collection(&mut self, size: Integer) -> Result<(), LimitExceeded> {
        if bigint(&size) > bigint(&self.meter.max_collection_size_seen) {
            self.meter.max_collection_size_seen = size.clone();
        }
        self.check_collection(&size)
    }
    pub(crate) fn checkpoint_usage(&self) -> &ResourceUsage {
        &self.meter
    }
    pub(crate) fn install_checkpoint_usage(&mut self, usage: ResourceUsage) {
        self.meter = usage;
    }
    pub fn usage(&self) -> ResourceUsage {
        self.meter.clone()
    }
    pub fn limits(&self) -> &ResourceLimits {
        &self.limits
    }
}
