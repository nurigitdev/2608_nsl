from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .audit import InMemoryAuditSink, InMemorySnapshotStore
from .ir import SkillObject
from .replay import RecordingToolExecutor
from .runtime import ExecutionRequest, ExecutionResult, RuntimeEngine
from .security import DataHandlingPolicy, ExecutionPrincipal
from .tools import FixtureHandler, MockToolExecutor, ToolContractCatalog


@dataclass(slots=True)
class CLIRunSession:
    request: ExecutionRequest
    result: ExecutionResult
    audit: InMemoryAuditSink
    snapshots: InMemorySnapshotStore
    recording: RecordingToolExecutor


async def execute_skill(
    skill: SkillObject,
    catalog: ToolContractCatalog,
    principal: ExecutionPrincipal,
    execution_id: str,
    *,
    inputs: Mapping[str, Any] | None = None,
    runtime_context: Mapping[str, Any] | None = None,
    handlers: Mapping[str, FixtureHandler] | None = None,
) -> CLIRunSession:
    policy = DataHandlingPolicy()
    request = ExecutionRequest(
        execution_id=execution_id,
        inputs={} if inputs is None else inputs,
        runtime_context={} if runtime_context is None else runtime_context,
        principal=principal,
        data_policy=policy,
    )
    audit = InMemoryAuditSink()
    snapshots = InMemorySnapshotStore()
    mock = MockToolExecutor(catalog, {} if handlers is None else handlers)
    recording = RecordingToolExecutor(mock, snapshots, policy)
    result = await RuntimeEngine(catalog).execute(
        skill, request, recording, audit, snapshots
    )
    return CLIRunSession(request, result, audit, snapshots, recording)
