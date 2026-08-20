from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol, runtime_checkable

from .data_protection import CredentialMaterialError, ensure_no_credential_material
from .integration import IntegrationContractError, SkillExecutionJob


class DispatchStatus(StrEnum):
    QUEUED = "QUEUED"


@dataclass(frozen=True, slots=True)
class JobDispatchReceipt:
    job_id: str
    execution_id: str
    status: DispatchStatus

    def __post_init__(self) -> None:
        _safe_dispatch_id(self.job_id, "dispatch job_id")
        _safe_dispatch_id(self.execution_id, "dispatch execution_id")
        if not isinstance(self.status, DispatchStatus):
            raise IntegrationContractError("dispatch status is invalid")

    def to_data(self) -> dict[str, str]:
        return {
            "job_id": self.job_id,
            "execution_id": self.execution_id,
            "status": self.status.value,
        }


@runtime_checkable
class JobDispatcher(Protocol):
    async def submit(self, job: SkillExecutionJob) -> JobDispatchReceipt:
        ...


class InMemoryJobDispatcher:
    """Contract fake that queues canonical jobs without invoking the Runtime."""

    def __init__(self) -> None:
        self._pending: dict[str, bytes] = {}

    async def submit(self, job: SkillExecutionJob) -> JobDispatchReceipt:
        if not isinstance(job, SkillExecutionJob):
            raise IntegrationContractError(
                "dispatcher requires a validated SkillExecutionJob"
            )
        if job.execution_id in self._pending:
            raise IntegrationContractError("duplicate execution_id is forbidden")
        payload = job.to_bytes()
        job_id = "job-" + sha256(payload).hexdigest()[:24]
        self._pending[job.execution_id] = payload
        return JobDispatchReceipt(job_id, job.execution_id, DispatchStatus.QUEUED)

    @property
    def pending_jobs(self) -> tuple[bytes, ...]:
        return tuple(self._pending.values())


def _safe_dispatch_id(value: object, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or len(value) > 256
    ):
        raise IntegrationContractError(
            f"{field} must be non-empty ASCII up to 256 bytes"
        )
    try:
        ensure_no_credential_material(value, field)
    except CredentialMaterialError as error:
        raise IntegrationContractError(
            f"credential material is forbidden in {field}"
        ) from error
    return value
