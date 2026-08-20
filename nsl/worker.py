from __future__ import annotations

from enum import StrEnum

from .audit import AuditSink, SnapshotStore
from .integration import (
    NullProgressSink,
    ProgressEvent,
    ProgressSink,
    ProgressState,
    RuntimeResultEnvelope,
    SkillExecutionJob,
    SkillResolutionError,
    SkillResolver,
)
from .runtime import RuntimeEngine
from .tools import ToolExecutionPort


class WorkerBoundaryCode(StrEnum):
    INVALID_DEPENDENCY = "INVALID_WORKER_DEPENDENCY"
    INVALID_JOB = "INVALID_WORKER_JOB"
    SKILL_RESOLUTION_FAILED = "SKILL_RESOLUTION_FAILED"
    SEMANTIC_IDENTITY_MISMATCH = "SEMANTIC_IDENTITY_MISMATCH"
    PROGRESS_DELIVERY_FAILED = "PROGRESS_DELIVERY_FAILED"


class WorkerEvidencePolicy(StrEnum):
    OPTIONAL = "OPTIONAL"
    SNAPSHOT_REQUIRED = "SNAPSHOT_REQUIRED"


class WorkerBoundaryError(RuntimeError):
    def __init__(self, code: WorkerBoundaryCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class SkillExecutionWorker:
    """Framework-neutral in-process boundary used by a NeX-AE Worker."""

    def __init__(
        self,
        *,
        runtime: RuntimeEngine,
        resolver: SkillResolver,
        tools: ToolExecutionPort,
        audit_sink: AuditSink,
        snapshot_store: SnapshotStore | None = None,
        progress_sink: ProgressSink | None = None,
        evidence_policy: WorkerEvidencePolicy = WorkerEvidencePolicy.OPTIONAL,
    ) -> None:
        if not isinstance(runtime, RuntimeEngine):
            raise WorkerBoundaryError(
                WorkerBoundaryCode.INVALID_DEPENDENCY,
                "Worker requires RuntimeEngine",
            )
        if not isinstance(resolver, SkillResolver):
            raise WorkerBoundaryError(
                WorkerBoundaryCode.INVALID_DEPENDENCY,
                "Worker requires SkillResolver",
            )
        if not isinstance(tools, ToolExecutionPort):
            raise WorkerBoundaryError(
                WorkerBoundaryCode.INVALID_DEPENDENCY,
                "Worker requires ToolExecutionPort",
            )
        if not callable(getattr(audit_sink, "append", None)):
            raise WorkerBoundaryError(
                WorkerBoundaryCode.INVALID_DEPENDENCY,
                "Worker requires AuditSink",
            )
        if snapshot_store is not None and not all(
            callable(getattr(snapshot_store, method, None))
            for method in ("put", "get")
        ):
            raise WorkerBoundaryError(
                WorkerBoundaryCode.INVALID_DEPENDENCY,
                "Worker snapshot store does not implement the port",
            )
        if not isinstance(evidence_policy, WorkerEvidencePolicy):
            raise WorkerBoundaryError(
                WorkerBoundaryCode.INVALID_DEPENDENCY,
                "Worker evidence policy is invalid",
            )
        if (
            evidence_policy is WorkerEvidencePolicy.SNAPSHOT_REQUIRED
            and snapshot_store is None
        ):
            raise WorkerBoundaryError(
                WorkerBoundaryCode.INVALID_DEPENDENCY,
                "Certified Worker requires a snapshot store",
            )
        if progress_sink is not None and not isinstance(progress_sink, ProgressSink):
            raise WorkerBoundaryError(
                WorkerBoundaryCode.INVALID_DEPENDENCY,
                "Worker progress sink does not implement the port",
            )
        self.runtime = runtime
        self.resolver = resolver
        self.tools = tools
        self.audit_sink = audit_sink
        self.snapshot_store = snapshot_store
        self.progress_sink = progress_sink or NullProgressSink()
        self.evidence_policy = evidence_policy

    async def execute(self, job: SkillExecutionJob) -> RuntimeResultEnvelope:
        if not isinstance(job, SkillExecutionJob):
            raise WorkerBoundaryError(
                WorkerBoundaryCode.INVALID_JOB,
                "Worker requires a validated SkillExecutionJob",
            )
        sequence = 1
        await self._publish(job, sequence, ProgressState.STARTED)
        try:
            resolved = self.resolver.resolve(job.skill_id, job.skill_version)
        except SkillResolutionError as error:
            await self._publish(
                job,
                sequence + 1,
                ProgressState.FAILED,
                error_code=WorkerBoundaryCode.SKILL_RESOLUTION_FAILED.value,
            )
            raise WorkerBoundaryError(
                WorkerBoundaryCode.SKILL_RESOLUTION_FAILED,
                "Worker could not resolve a verified Skill",
            ) from error
        if resolved.skill.semantic_hash != job.expected_semantic_hash:
            await self._publish(
                job,
                sequence + 1,
                ProgressState.FAILED,
                error_code=WorkerBoundaryCode.SEMANTIC_IDENTITY_MISMATCH.value,
            )
            raise WorkerBoundaryError(
                WorkerBoundaryCode.SEMANTIC_IDENTITY_MISMATCH,
                "resolved Skill semantic identity differs from the job",
            )
        sequence += 1
        await self._publish(job, sequence, ProgressState.SKILL_RESOLVED)
        sequence += 1
        await self._publish(job, sequence, ProgressState.RUNNING)
        result = await self.runtime.execute(
            resolved.skill,
            job.to_runtime_request(),
            self.tools,
            self.audit_sink,
            self.snapshot_store,
        )
        sequence += 1
        state = (
            ProgressState.COMPLETED
            if result.status.value == "COMPLETED"
            else ProgressState.FAILED
        )
        await self._publish(
            job,
            sequence,
            state,
            runtime_status=result.status.value,
            error_code=(
                None
                if state is ProgressState.COMPLETED
                else (result.error.code if result.error is not None else "RUNTIME_FAILED")
            ),
        )
        return RuntimeResultEnvelope(result)

    async def _publish(
        self,
        job: SkillExecutionJob,
        sequence: int,
        state: ProgressState,
        *,
        runtime_status: str | None = None,
        error_code: str | None = None,
    ) -> None:
        event = ProgressEvent(
            execution_id=job.execution_id,
            sequence=sequence,
            state=state,
            skill_id=job.skill_id,
            skill_version=job.skill_version,
            runtime_status=runtime_status,
            error_code=error_code,
        )
        try:
            await self.progress_sink.publish(event)
        except Exception as error:
            raise WorkerBoundaryError(
                WorkerBoundaryCode.PROGRESS_DELIVERY_FAILED,
                "Worker progress delivery failed",
            ) from error
