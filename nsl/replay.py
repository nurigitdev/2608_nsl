from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .audit import InMemorySnapshotStore, SnapshotRef, value_hash
from .core import DataClassification
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


class RecordingToolExecutor:
    def __init__(
        self,
        delegate: ToolExecutionPort,
        snapshots: InMemorySnapshotStore,
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
        snapshots: InMemorySnapshotStore,
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
    semantic_hash: str,
    inputs: dict[str, Any],
    runtime_context: dict[str, Any],
    principal: ExecutionPrincipal,
    policy: DataHandlingPolicy,
    recorder: RecordingToolExecutor,
    snapshots: InMemorySnapshotStore,
) -> ReplayBundle:
    inputs_ref = snapshots.put(
        principal.tenant_id,
        inputs,
        DataClassification.INTERNAL,
        policy.snapshot_retention_days,
    )
    context_ref = snapshots.put(
        principal.tenant_id,
        runtime_context,
        DataClassification.INTERNAL,
        policy.snapshot_retention_days,
    )
    return ReplayBundle(
        semantic_hash,
        principal.tenant_id,
        inputs_ref,
        context_ref,
        tuple(recorder.calls),
    )


def load_replay_inputs(
    bundle: ReplayBundle,
    snapshots: InMemorySnapshotStore,
    principal: ExecutionPrincipal,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if bundle.tenant_id != principal.tenant_id:
        raise PermissionError("cross-tenant replay is forbidden")
    inputs = snapshots.get(bundle.inputs_ref, principal)
    runtime_context = snapshots.get(bundle.context_ref, principal)
    return inputs, runtime_context


def _argument_hash(request: ToolCallRequest) -> str:
    return value_hash(
        {name: envelope.value for name, envelope in request.arguments.items()}
    )
