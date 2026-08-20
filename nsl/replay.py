from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

from .audit import (
    RUNTIME_VERSION,
    AuditSink,
    SnapshotRef,
    SnapshotStore,
    value_hash,
)
from .core import DataClassification
from .ir import SkillObject
from .runtime_models import (
    ExecutionRequest,
    ExecutionResult,
    highest_declared_classification,
    project_declared_context,
    project_declared_inputs,
)
from .security import DataHandlingPolicy, ExecutionPrincipal
from .tools import (
    ToolCallRequest,
    ToolExecutionPort,
    ToolResultEnvelope,
)


@dataclass(frozen=True, slots=True)
class RecordedToolCall:
    tool_id: str
    tool_version: str
    argument_hash: str
    result_ref: SnapshotRef


@dataclass(frozen=True, slots=True)
class ReplayBundle:
    semantic_hash: str
    tenant_id: str
    inputs_ref: SnapshotRef
    context_ref: SnapshotRef
    tool_calls: tuple[RecordedToolCall, ...]
    original_execution_id: str = "unknown"
    runtime_version: str = RUNTIME_VERSION
    original_result_ref: SnapshotRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.semantic_hash, str) or not self.semantic_hash:
            raise ValueError("replay semantic_hash must be non-empty")
        if not isinstance(self.tenant_id, str) or not self.tenant_id:
            raise ValueError("replay tenant_id must be non-empty")
        if (
            not isinstance(self.original_execution_id, str)
            or not self.original_execution_id
        ):
            raise ValueError("replay original_execution_id must be non-empty")
        if not isinstance(self.runtime_version, str) or not self.runtime_version:
            raise ValueError("replay runtime_version must be non-empty")
        references = [self.inputs_ref, self.context_ref]
        references.extend(call.result_ref for call in self.tool_calls)
        if self.original_result_ref is not None:
            references.append(self.original_result_ref)
        if any(reference.tenant_id != self.tenant_id for reference in references):
            raise ValueError("replay bundle contains a cross-tenant snapshot reference")


@dataclass(frozen=True, slots=True)
class ReplayExecution:
    result: ExecutionResult
    tool_call_count: int


@dataclass(frozen=True, slots=True)
class ReplayDifference:
    path: str
    original: Any
    replayed: Any


@dataclass(frozen=True, slots=True)
class ReplayReport:
    execution: ReplayExecution
    matches: bool
    differences: tuple[ReplayDifference, ...]
    original_runtime_version: str
    replay_runtime_version: str
    runtime_version_changed: bool


class ReplayRuntime(Protocol):
    async def execute(
        self,
        skill: SkillObject,
        request: ExecutionRequest,
        tools: ToolExecutionPort,
        audit_sink: AuditSink,
        snapshot_store: SnapshotStore | None = None,
    ) -> ExecutionResult:
        ...


class RecordingToolExecutor:
    def __init__(
        self,
        delegate: ToolExecutionPort,
        snapshots: SnapshotStore,
        policy: DataHandlingPolicy,
    ) -> None:
        self.delegate = delegate
        self.snapshots = snapshots
        self.policy = policy
        self.calls: list[RecordedToolCall] = []

    async def execute(self, request: ToolCallRequest) -> ToolResultEnvelope:
        result = await self.delegate.execute(request)
        result_ref = self.snapshots.put(
            tenant_id=request.principal.tenant_id,
            value=result,
            classification=result.classification,
            retention_days=self.policy.snapshot_retention_days,
            hash_material={
                "tool_id": result.tool_id,
                "tool_version": result.tool_version,
                "value": result.value,
                "presence": result.presence.value,
                "completeness": result.completeness.value,
                "result_hash": result.result_hash,
            },
        )
        self.calls.append(
            RecordedToolCall(
                tool_id=request.tool_id,
                tool_version=request.tool_version,
                argument_hash=_argument_hash(request),
                result_ref=result_ref,
            )
        )
        return replace(result, snapshot_ref=result_ref.snapshot_id)


class ReplayToolExecutor:
    def __init__(
        self,
        calls: tuple[RecordedToolCall, ...],
        snapshots: SnapshotStore,
    ) -> None:
        self.calls = calls
        self.snapshots = snapshots
        self.index = 0
        self.call_count = 0

    async def execute(self, request: ToolCallRequest) -> ToolResultEnvelope:
        self.call_count += 1
        if self.index >= len(self.calls):
            raise RuntimeError("replay requested more tool calls than recorded")
        expected = self.calls[self.index]
        self.index += 1
        if (
            expected.tool_id != request.tool_id
            or expected.tool_version != request.tool_version
            or expected.argument_hash != _argument_hash(request)
        ):
            raise RuntimeError("replay tool call does not match recorded invocation")
        result = self.snapshots.get(expected.result_ref, request.principal)
        if not isinstance(result, ToolResultEnvelope):
            raise TypeError("invalid tool result snapshot")
        return replace(
            result,
            invocation_id=request.invocation_id,
            snapshot_ref=expected.result_ref.snapshot_id,
        )

    def assert_consumed(self) -> None:
        if self.index != len(self.calls):
            raise RuntimeError("replay did not consume all recorded tool calls")


def create_replay_bundle(
    skill: SkillObject,
    request: ExecutionRequest,
    original_result: ExecutionResult,
    recorder: RecordingToolExecutor,
    snapshots: SnapshotStore,
    *,
    runtime_version: str = RUNTIME_VERSION,
) -> ReplayBundle:
    if original_result.execution_id != request.execution_id:
        raise ValueError("original result execution_id does not match request")
    if original_result.semantic_hash != skill.semantic_hash:
        raise ValueError("original result semantic hash does not match skill")
    if recorder.snapshots is not snapshots:
        raise ValueError("recorded tool calls and replay bundle must share a store")
    if recorder.policy != request.data_policy:
        raise ValueError("recording and execution data policies do not match")

    inputs = project_declared_inputs(skill, request.inputs)
    runtime_context = project_declared_context(skill, request.runtime_context)
    inputs_ref = snapshots.put(
        request.principal.tenant_id,
        inputs,
        highest_declared_classification(skill.inputs),
        request.data_policy.snapshot_retention_days,
    )
    context_ref = snapshots.put(
        request.principal.tenant_id,
        runtime_context,
        highest_declared_classification(skill.contexts),
        request.data_policy.snapshot_retention_days,
    )
    original_result_ref = snapshots.put(
        request.principal.tenant_id,
        original_result.semantic_view(),
        highest_declared_classification(
            (*skill.inputs, *skill.contexts, *skill.outputs)
        ),
        request.data_policy.snapshot_retention_days,
    )
    return ReplayBundle(
        skill.semantic_hash,
        request.principal.tenant_id,
        inputs_ref,
        context_ref,
        tuple(recorder.calls),
        original_result.execution_id,
        runtime_version,
        original_result_ref,
    )
def load_replay_inputs(
    bundle: ReplayBundle,
    snapshots: SnapshotStore,
    principal: ExecutionPrincipal,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if bundle.tenant_id != principal.tenant_id:
        raise PermissionError("cross-tenant replay is forbidden")
    inputs = snapshots.get(bundle.inputs_ref, principal)
    runtime_context = snapshots.get(bundle.context_ref, principal)
    return inputs, runtime_context


async def replay_previous_execution(
    bundle: ReplayBundle,
    skill: SkillObject,
    *,
    replay_execution_id: str,
    principal: ExecutionPrincipal,
    policy: DataHandlingPolicy,
    snapshots: SnapshotStore,
    runtime: ReplayRuntime,
    audit_sink: AuditSink,
) -> ReplayExecution:
    if not isinstance(replay_execution_id, str) or not replay_execution_id.strip():
        raise ValueError("replay_execution_id must be non-empty")
    if bundle.semantic_hash != skill.semantic_hash:
        raise ValueError("replay bundle semantic hash does not match skill")
    replay_inputs, replay_context = load_replay_inputs(
        bundle, snapshots, principal
    )
    replay_tools = ReplayToolExecutor(bundle.tool_calls, snapshots)
    request = ExecutionRequest(
        execution_id=replay_execution_id,
        inputs=replay_inputs,
        runtime_context=replay_context,
        principal=principal,
        data_policy=policy,
    )
    result = await runtime.execute(
        skill,
        request,
        replay_tools,
        audit_sink,
        snapshots,
    )
    replay_tools.assert_consumed()
    return ReplayExecution(result, replay_tools.call_count)


async def replay_and_compare(
    bundle: ReplayBundle,
    skill: SkillObject,
    *,
    replay_execution_id: str,
    principal: ExecutionPrincipal,
    policy: DataHandlingPolicy,
    snapshots: SnapshotStore,
    runtime: ReplayRuntime,
    audit_sink: AuditSink,
    runtime_version: str = RUNTIME_VERSION,
) -> ReplayReport:
    if not isinstance(runtime_version, str) or not runtime_version:
        raise ValueError("replay runtime_version must be non-empty")
    if bundle.original_result_ref is None:
        raise ValueError("replay bundle has no original result snapshot reference")
    original_view = snapshots.get(bundle.original_result_ref, principal)
    if not isinstance(original_view, dict):
        raise TypeError("invalid original result snapshot")
    execution = await replay_previous_execution(
        bundle,
        skill,
        replay_execution_id=replay_execution_id,
        principal=principal,
        policy=policy,
        snapshots=snapshots,
        runtime=runtime,
        audit_sink=audit_sink,
    )
    differences = compare_replay_values(
        original_view, execution.result.semantic_view()
    )
    return ReplayReport(
        execution=execution,
        matches=not differences,
        differences=differences,
        original_runtime_version=bundle.runtime_version,
        replay_runtime_version=runtime_version,
        runtime_version_changed=bundle.runtime_version != runtime_version,
    )


def compare_replay_values(
    original: Any,
    replayed: Any,
) -> tuple[ReplayDifference, ...]:
    differences: list[ReplayDifference] = []
    _collect_differences(original, replayed, "", differences)
    return tuple(differences)


def _collect_differences(
    original: Any,
    replayed: Any,
    path: str,
    differences: list[ReplayDifference],
) -> None:
    if isinstance(original, dict) and isinstance(replayed, dict):
        for key in sorted(set(original) | set(replayed)):
            child_path = f"{path}/{_escape_path(key)}"
            if key not in original:
                differences.append(
                    ReplayDifference(child_path, {"$missing": True}, replayed[key])
                )
            elif key not in replayed:
                differences.append(
                    ReplayDifference(child_path, original[key], {"$missing": True})
                )
            else:
                _collect_differences(
                    original[key], replayed[key], child_path, differences
                )
        return
    if isinstance(original, list) and isinstance(replayed, list):
        for index in range(max(len(original), len(replayed))):
            child_path = f"{path}/{index}"
            if index >= len(original):
                differences.append(
                    ReplayDifference(
                        child_path, {"$missing": True}, replayed[index]
                    )
                )
            elif index >= len(replayed):
                differences.append(
                    ReplayDifference(
                        child_path, original[index], {"$missing": True}
                    )
                )
            else:
                _collect_differences(
                    original[index], replayed[index], child_path, differences
                )
        return
    if original != replayed:
        differences.append(ReplayDifference(path or "/", original, replayed))


def _escape_path(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _argument_hash(request: ToolCallRequest) -> str:
    return value_hash(
        {name: envelope.value for name, envelope in request.arguments.items()}
    )
