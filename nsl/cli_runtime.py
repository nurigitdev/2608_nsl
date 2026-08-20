from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .audit import InMemoryAuditSink, InMemorySnapshotStore
from .ir import SkillObject
from .performance import RuntimeExecutionTiming, measure_runtime_execution
from .replay import RecordingToolExecutor
from .runtime import ExecutionRequest, ExecutionResult, RuntimeEngine
from .security import DataHandlingPolicy, ExecutionPrincipal
from .tools import (
    FixtureHandler,
    InMemoryToolMeasurementSink,
    MockToolExecutor,
    ToolContractCatalog,
)


@dataclass(slots=True)
class CLIRunSession:
    request: ExecutionRequest
    result: ExecutionResult
    audit: InMemoryAuditSink
    snapshots: InMemorySnapshotStore
    recording: RecordingToolExecutor
    timing: RuntimeExecutionTiming | None = None


def build_execution_request(
    principal: ExecutionPrincipal,
    execution_id: str,
    *,
    inputs: Mapping[str, Any] | None = None,
    runtime_context: Mapping[str, Any] | None = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={} if inputs is None else inputs,
        runtime_context={} if runtime_context is None else runtime_context,
        principal=principal,
        data_policy=DataHandlingPolicy(),
    )


async def execute_skill(
    skill: SkillObject,
    catalog: ToolContractCatalog,
    principal: ExecutionPrincipal,
    execution_id: str,
    *,
    inputs: Mapping[str, Any] | None = None,
    runtime_context: Mapping[str, Any] | None = None,
    handlers: Mapping[str, FixtureHandler] | None = None,
    measure_timing: bool = False,
) -> CLIRunSession:
    request = build_execution_request(
        principal,
        execution_id,
        inputs=inputs,
        runtime_context=runtime_context,
    )
    policy = request.data_policy
    audit = InMemoryAuditSink()
    snapshots = InMemorySnapshotStore()
    mock = MockToolExecutor(catalog, {} if handlers is None else handlers)
    recording = RecordingToolExecutor(mock, snapshots, policy)
    measurements = InMemoryToolMeasurementSink()
    engine = RuntimeEngine(
        catalog,
        tool_measurement_sink=measurements if measure_timing else None,
    )

    async def operation() -> ExecutionResult:
        return await engine.execute(skill, request, recording, audit, snapshots)

    if measure_timing:
        measured = await measure_runtime_execution(
            execution_id,
            operation,
            measurements.measurements,
        )
        result = measured.result
        timing = measured.timing
    else:
        result = await operation()
        timing = None
    return CLIRunSession(request, result, audit, snapshots, recording, timing)
