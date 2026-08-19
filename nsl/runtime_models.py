from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic_ns
from typing import Any, Mapping, Protocol

from .audit import AuditRecorder
from .core import (
    CheckStatus,
    Completeness,
    DataClassification,
    ExecutionStatus,
    ValueEnvelope,
    encode_value,
)
from .ir import ResourceLimits, SkillObject
from .security import DataHandlingPolicy, ExecutionPrincipal


class LimitExceeded(RuntimeError):
    pass


MAX_FOREACH_NESTING_DEPTH = 16
_NANOSECONDS_PER_MILLISECOND = 1_000_000
_DURATION_LIMIT_MESSAGE = "execution duration limit exceeded"


class ImmutableBindingError(RuntimeError):
    detail_code = "IMMUTABLE_BINDING_ERROR"


class RuntimeClock(Protocol):
    def monotonic_ns(self) -> int: ...


class SystemRuntimeClock:
    __slots__ = ()

    def monotonic_ns(self) -> int:
        return monotonic_ns()


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    execution_id: str
    inputs: Mapping[str, Any]
    runtime_context: Mapping[str, Any]
    principal: ExecutionPrincipal
    data_policy: DataHandlingPolicy = DataHandlingPolicy()

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, str) or not self.execution_id.strip():
            raise ValueError("execution_id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    status: CheckStatus
    severity: str
    message: str
    completeness: Completeness
    provenance_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EmitRecord:
    values: Mapping[str, Any]
    classifications: Mapping[str, DataClassification]


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    tool_calls: int
    loop_iterations: int
    emitted_rows: int
    max_collection_size_seen: int


@dataclass(frozen=True, slots=True)
class RuntimeErrorInfo:
    code: str
    category: str
    message: str
    node_id: str | None = None
    detail_code: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    execution_id: str
    skill_id: str
    skill_version: str
    semantic_hash: str
    status: ExecutionStatus
    checks: tuple[CheckResult, ...]
    outputs: tuple[EmitRecord, ...]
    resources: ResourceUsage
    error: RuntimeErrorInfo | None = None

    def semantic_view(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "semantic_hash": self.semantic_hash,
            "status": self.status.value,
            "checks": [
                {
                    "check_id": check.check_id,
                    "status": check.status.value,
                    "severity": check.severity,
                    "message": check.message,
                    "completeness": check.completeness.value,
                }
                for check in self.checks
            ],
            "outputs": [encode_value(dict(record.values)) for record in self.outputs],
            "resources": {
                "tool_calls": self.resources.tool_calls,
                "loop_iterations": self.resources.loop_iterations,
                "emitted_rows": self.resources.emitted_rows,
                "max_collection_size_seen": self.resources.max_collection_size_seen,
            },
            "error": None
            if self.error is None
            else {
                "code": self.error.code,
                "category": self.error.category,
                "message": self.error.message,
                "node_id": self.error.node_id,
                "detail_code": self.error.detail_code,
            },
        }


@dataclass(slots=True)
class _ResourceMeter:
    tool_calls: int = 0
    loop_iterations: int = 0
    emitted_rows: int = 0
    max_collection_size_seen: int = 0


@dataclass(slots=True)
class ResourceGuard:
    limits: ResourceLimits
    meter: _ResourceMeter
    clock: RuntimeClock
    started_ns: int = field(init=False)

    def __post_init__(self) -> None:
        self.started_ns = self.clock.monotonic_ns()

    def _remaining_duration_ns(self) -> int:
        elapsed_ns = self.clock.monotonic_ns() - self.started_ns
        return self.limits.duration_ms * _NANOSECONDS_PER_MILLISECOND - elapsed_ns

    def check_deadline(self) -> None:
        if self._remaining_duration_ns() <= 0:
            raise LimitExceeded(_DURATION_LIMIT_MESSAGE)

    def remaining_duration_ms(self) -> int:
        remaining_ns = self._remaining_duration_ns()
        if remaining_ns <= 0:
            raise LimitExceeded(_DURATION_LIMIT_MESSAGE)
        return (
            remaining_ns + _NANOSECONDS_PER_MILLISECOND - 1
        ) // _NANOSECONDS_PER_MILLISECOND

    @staticmethod
    def _consume(current: int, limit: int, message: str) -> int:
        if current >= limit:
            raise LimitExceeded(message)
        return current + 1

    def before_tool_call(self) -> None:
        self.check_deadline()
        self.meter.tool_calls = self._consume(
            self.meter.tool_calls,
            self.limits.tool_calls,
            "tool call limit exceeded",
        )

    def before_loop_iteration(self) -> None:
        self.check_deadline()
        self.meter.loop_iterations = self._consume(
            self.meter.loop_iterations,
            self.limits.loop_iterations,
            "loop iteration limit exceeded",
        )

    def before_emit(self) -> None:
        self.check_deadline()
        if self.meter.emitted_rows >= self.limits.emitted_rows:
            raise LimitExceeded("emitted row limit exceeded")

    def record_emit(self) -> None:
        self.meter.emitted_rows += 1

    def check_collection(self, size: int) -> None:
        self.check_deadline()
        if size > self.limits.collection_size:
            raise LimitExceeded("collection size limit exceeded")

    def observe_collection(self, size: int) -> None:
        self.meter.max_collection_size_seen = max(
            self.meter.max_collection_size_seen, size
        )
        self.check_collection(size)


@dataclass(slots=True)
class ExecutionContext:
    skill: SkillObject
    request: ExecutionRequest
    audit: AuditRecorder
    runtime_clock: RuntimeClock = field(default_factory=SystemRuntimeClock)
    input_values: dict[str, ValueEnvelope] = field(default_factory=dict)
    context_values: dict[str, ValueEnvelope] = field(default_factory=dict)
    frames: list[dict[str, ValueEnvelope]] = field(
        default_factory=lambda: [{}]
    )
    check_frames: list[dict[str, ValueEnvelope]] = field(
        default_factory=lambda: [{}]
    )
    checks: list[CheckResult] = field(default_factory=list)
    outputs: list[EmitRecord] = field(default_factory=list)
    resources: _ResourceMeter = field(default_factory=_ResourceMeter)
    invocation_counter: int = 0
    foreach_depth: int = 0
    resource_guard: ResourceGuard = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.resource_guard = ResourceGuard(
            self.skill.limits, self.resources, self.runtime_clock
        )

    def _is_bound(self, symbol_id: str) -> bool:
        return (
            symbol_id in self.input_values
            or symbol_id in self.context_values
            or any(symbol_id in frame for frame in self.frames)
            or any(symbol_id in frame for frame in self.check_frames)
        )

    def _bind_once(
        self,
        namespace: dict[str, ValueEnvelope],
        symbol_id: str,
        value: ValueEnvelope,
    ) -> None:
        if self._is_bound(symbol_id):
            raise ImmutableBindingError(f"immutable symbol already bound: {symbol_id}")
        namespace[symbol_id] = value

    def bind_input(self, symbol_id: str, value: ValueEnvelope) -> None:
        self._bind_once(self.input_values, symbol_id, value)

    def bind_context(self, symbol_id: str, value: ValueEnvelope) -> None:
        self._bind_once(self.context_values, symbol_id, value)

    def bind(self, symbol_id: str, value: ValueEnvelope) -> None:
        self._bind_once(self.frames[-1], symbol_id, value)

    def bind_check(self, symbol_id: str, value: ValueEnvelope) -> None:
        self._bind_once(self.check_frames[-1], symbol_id, value)

    def push_frame(self) -> None:
        self.frames.append({})
        self.check_frames.append({})

    def pop_frame(self) -> None:
        if len(self.frames) == 1 or len(self.check_frames) == 1:
            raise RuntimeError("cannot pop the root execution frame")
        self.frames.pop()
        self.check_frames.pop()

    def resolve(self, symbol_id: str) -> ValueEnvelope:
        for frame in reversed(self.frames):
            if symbol_id in frame:
                return frame[symbol_id]
        for frame in reversed(self.check_frames):
            if symbol_id in frame:
                return frame[symbol_id]
        for namespace in (self.context_values, self.input_values):
            if symbol_id in namespace:
                return namespace[symbol_id]
        raise RuntimeError(f"unknown symbol: {symbol_id}")

    def usage(self) -> ResourceUsage:
        return ResourceUsage(
            self.resources.tool_calls,
            self.resources.loop_iterations,
            self.resources.emitted_rows,
            self.resources.max_collection_size_seen,
        )


_ExecutionContext = ExecutionContext
