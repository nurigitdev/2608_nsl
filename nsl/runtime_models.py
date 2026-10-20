from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic_ns
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .core import (
    ApprovalStatus,
    BusinessOperationId,
    CheckStatus,
    Completeness,
    DataClassification,
    ExecutionStatus,
    IdempotencyKey,
    PayloadHash,
    ValueEnvelope,
    highest_classification,
)
from .data_protection import ensure_no_credential_material
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


def _required_identity(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    ensure_no_credential_material(value, name)
    return value


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    execution_id: str
    business_operation_id: BusinessOperationId | None = None

    def __post_init__(self) -> None:
        _required_identity(self.execution_id, "ExecutionIdentity.execution_id")
        if self.business_operation_id is not None:
            _required_identity(
                self.business_operation_id,
                "ExecutionIdentity.business_operation_id",
            )


@dataclass(frozen=True, slots=True)
class ExecutionSecurityContext:
    principal: ExecutionPrincipal
    data_policy: DataHandlingPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.principal, ExecutionPrincipal):
            raise TypeError("principal must be an ExecutionPrincipal")
        if not isinstance(self.data_policy, DataHandlingPolicy):
            raise TypeError("data_policy must be a DataHandlingPolicy")

    @property
    def tenant_id(self) -> str:
        return self.principal.tenant_id


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    execution_id: str
    inputs: Mapping[str, Any]
    runtime_context: Mapping[str, Any]
    principal: ExecutionPrincipal
    data_policy: DataHandlingPolicy = DataHandlingPolicy()
    business_operation_id: BusinessOperationId | None = None

    def __post_init__(self) -> None:
        _required_identity(
            self.execution_id,
            "ExecutionRequest.execution_id",
        )
        if self.business_operation_id is not None:
            _required_identity(
                self.business_operation_id,
                "ExecutionRequest.business_operation_id",
            )
        if not isinstance(self.inputs, Mapping):
            raise ValueError("inputs must be a mapping")
        if not isinstance(self.runtime_context, Mapping):
            raise ValueError("runtime_context must be a mapping")
        ensure_no_credential_material(self.inputs, "ExecutionRequest.inputs")
        ensure_no_credential_material(
            self.runtime_context, "ExecutionRequest.runtime_context"
        )

    @property
    def identity(self) -> ExecutionIdentity:
        return ExecutionIdentity(
            self.execution_id,
            self.business_operation_id,
        )

    @property
    def security_context(self) -> ExecutionSecurityContext:
        return ExecutionSecurityContext(self.principal, self.data_policy)


def highest_declared_classification(specs: tuple[Any, ...]) -> DataClassification:
    classification = DataClassification.PUBLIC
    for spec in specs:
        classification = highest_classification(classification, spec.classification)
    return classification


def project_declared_inputs(
    skill: SkillObject,
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        spec.name: inputs[spec.name]
        for spec in skill.inputs
        if spec.name in inputs
    }


def project_declared_context(
    skill: SkillObject,
    runtime_context: Mapping[str, Any],
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for spec in skill.contexts:
        value: Any = runtime_context
        try:
            for part in spec.path:
                value = value[part]
        except (KeyError, TypeError):
            continue

        target = projected
        for part in spec.path[:-1]:
            target = target.setdefault(part, {})
        target[spec.path[-1]] = value
    return projected


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    status: CheckStatus
    severity: str
    message: str
    condition_node_id: str
    presence: Presence
    completeness: Completeness
    provenance_refs: tuple[str, ...]
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class EmitRecord:
    fields: Mapping[str, ValueEnvelope]

    def __post_init__(self) -> None:
        copied = dict(self.fields)
        if not all(
            isinstance(name, str)
            and bool(name)
            and isinstance(value, ValueEnvelope)
            for name, value in copied.items()
        ):
            raise ValueError("emit fields must map names to ValueEnvelope values")
        object.__setattr__(self, "fields", MappingProxyType(copied))

    @property
    def values(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {name: field.value for name, field in self.fields.items()}
        )

    @property
    def classifications(self) -> Mapping[str, DataClassification]:
        return MappingProxyType(
            {name: field.classification for name, field in self.fields.items()}
        )


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
    completeness: Completeness
    checks: tuple[CheckResult, ...]
    outputs: tuple[EmitRecord, ...]
    resources: ResourceUsage
    error: RuntimeErrorInfo | None = None

    def to_data(self) -> dict[str, Any]:
        from .result_codec import execution_result_to_data

        return execution_result_to_data(self)

    def to_json(self) -> str:
        from .result_codec import execution_result_to_json

        return execution_result_to_json(self)

    def semantic_view(self) -> dict[str, Any]:
        from .result_codec import semantic_result_view

        return semantic_result_view(self)


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
class ApprovalState:
    records: dict[str, ApprovalStatus] = field(default_factory=dict)


@dataclass(slots=True)
class WriteJournalState:
    entries: list[object] = field(default_factory=list)


@dataclass(slots=True)
class IdempotencyState:
    keys_by_write_node: dict[str, IdempotencyKey] = field(default_factory=dict)
    payload_hashes_by_write_node: dict[str, PayloadHash] = field(
        default_factory=dict
    )


@dataclass(slots=True)
class CheckpointState:
    checkpoint_version: int = 0
    last_completed_node_id: str | None = None
    suspended_at_node_id: str | None = None
    trace_sequence: int = 0
    approval_request_published: bool = False
    write_request_durable: bool = False


@dataclass(slots=True)
class ExecutionContext:
    skill: SkillObject
    request: ExecutionRequest
    runtime_clock: RuntimeClock = field(default_factory=SystemRuntimeClock)
    identity: ExecutionIdentity = field(init=False)
    security_context: ExecutionSecurityContext = field(init=False)
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
    data_completeness: Completeness = Completeness.COMPLETE
    invocation_counter: int = 0
    foreach_depth: int = 0
    current_node_id: str | None = None
    approvals: ApprovalState = field(default_factory=ApprovalState)
    writes: WriteJournalState = field(default_factory=WriteJournalState)
    idempotency: IdempotencyState = field(default_factory=IdempotencyState)
    checkpoint: CheckpointState = field(default_factory=CheckpointState)
    resource_guard: ResourceGuard = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.identity = self.request.identity
        self.security_context = self.request.security_context
        self.resource_guard = ResourceGuard(
            self.skill.limits, self.resources, self.runtime_clock
        )

    @property
    def tenant_id(self) -> str:
        return self.security_context.tenant_id

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

    def observe_completeness(self, completeness: Completeness) -> None:
        if completeness is Completeness.UNKNOWN:
            self.data_completeness = Completeness.UNKNOWN
        elif (
            completeness is Completeness.PARTIAL
            and self.data_completeness is Completeness.COMPLETE
        ):
            self.data_completeness = Completeness.PARTIAL

    def result_completeness(
        self, status: ExecutionStatus
    ) -> Completeness:
        if status is ExecutionStatus.COMPLETED:
            return self.data_completeness
        if self.checks or self.outputs:
            return Completeness.PARTIAL
        return Completeness.UNKNOWN

    def usage(self) -> ResourceUsage:
        return ResourceUsage(
            self.resources.tool_calls,
            self.resources.loop_iterations,
            self.resources.emitted_rows,
            self.resources.max_collection_size_seen,
        )


_ExecutionContext = ExecutionContext
