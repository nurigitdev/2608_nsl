from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .checkpoints import CheckpointCoordinator, ExecutionCheckpoint
from .core import (
    ApprovalDecision,
    ApprovalRequestId,
    ApprovalStatus,
    BusinessOperationId,
    DataClassification,
    ExecutionStatus,
    PayloadHash,
    RiskLevel,
)
from .data_protection import ensure_no_credential_material
from .runtime_models import (
    ApprovalSummary,
    ExecutionContext,
    ExecutionResult,
    SuspensionInfo,
)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    ensure_no_credential_material(value, name)
    return value


def _sha256(value: object, name: str) -> str:
    text = _required_text(value, name)
    digest = text.removeprefix("sha256:")
    if (
        not text.startswith("sha256:")
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 value")
    return text


def _utc_instant(value: object, name: str, *, optional: bool) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must use UTC")
    return value


def _freeze_summary(value: object, path: str = "display_safe_summary") -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{path} keys must be non-empty strings")
            frozen[key] = _freeze_summary(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_summary(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    raise TypeError(f"{path} contains a non-JSON-safe value")


def _summary_to_data(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _summary_to_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_summary_to_data(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    tenant_id: str
    execution_id: str
    business_operation_id: BusinessOperationId
    approval_id: str
    approval_request_id: ApprovalRequestId
    skill_id: str
    skill_version: str
    skill_semantic_hash: str
    write_node_id: str
    tool_id: str
    payload_hash: PayloadHash
    write_intent_hash: str
    risk_level: RiskLevel
    message: str
    expires_at: datetime | None
    display_safe_summary: Mapping[str, object]
    protected_payload_ref: str | None
    data_classification: DataClassification

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "execution_id",
            "business_operation_id",
            "approval_id",
            "approval_request_id",
            "skill_id",
            "skill_version",
            "write_node_id",
            "tool_id",
            "write_intent_hash",
            "message",
        ):
            _required_text(getattr(self, name), f"ApprovalRequest.{name}")
        _sha256(
            self.skill_semantic_hash,
            "ApprovalRequest.skill_semantic_hash",
        )
        _sha256(self.payload_hash, "ApprovalRequest.payload_hash")
        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError("ApprovalRequest.risk_level must be a RiskLevel")
        if not isinstance(self.data_classification, DataClassification):
            raise TypeError(
                "ApprovalRequest.data_classification must be a DataClassification"
            )
        _utc_instant(
            self.expires_at,
            "ApprovalRequest.expires_at",
            optional=True,
        )
        if self.protected_payload_ref is not None:
            _required_text(
                self.protected_payload_ref,
                "ApprovalRequest.protected_payload_ref",
            )
        if not isinstance(self.display_safe_summary, Mapping):
            raise TypeError("ApprovalRequest.display_safe_summary must be a mapping")
        ensure_no_credential_material(
            self.display_safe_summary,
            "ApprovalRequest.display_safe_summary",
        )
        object.__setattr__(
            self,
            "display_safe_summary",
            _freeze_summary(self.display_safe_summary),
        )

    def to_data(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "execution_id": self.execution_id,
            "business_operation_id": self.business_operation_id,
            "approval_id": self.approval_id,
            "approval_request_id": self.approval_request_id,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "skill_semantic_hash": self.skill_semantic_hash,
            "write_node_id": self.write_node_id,
            "tool_id": self.tool_id,
            "payload_hash": self.payload_hash,
            "write_intent_hash": self.write_intent_hash,
            "risk_level": self.risk_level.value,
            "message": self.message,
            "expires_at": (
                self.expires_at.isoformat() if self.expires_at is not None else None
            ),
            "display_safe_summary": _summary_to_data(
                self.display_safe_summary
            ),
            "protected_payload_ref": self.protected_payload_ref,
            "data_classification": self.data_classification.value,
        }


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    tenant_id: str
    execution_id: str
    business_operation_id: BusinessOperationId
    approval_id: str
    approval_request_id: ApprovalRequestId
    skill_semantic_hash: str
    decision: ApprovalDecision
    approver_id: str
    decided_at: datetime
    expires_at: datetime | None
    payload_hash: PayloadHash
    write_intent_hash: str
    evidence_integrity: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "execution_id",
            "business_operation_id",
            "approval_id",
            "approval_request_id",
            "approver_id",
            "write_intent_hash",
            "evidence_integrity",
        ):
            _required_text(getattr(self, name), f"ApprovalEvidence.{name}")
        _sha256(
            self.skill_semantic_hash,
            "ApprovalEvidence.skill_semantic_hash",
        )
        _sha256(self.payload_hash, "ApprovalEvidence.payload_hash")
        if not isinstance(self.decision, ApprovalDecision):
            raise TypeError("ApprovalEvidence.decision must be an ApprovalDecision")
        _utc_instant(
            self.decided_at,
            "ApprovalEvidence.decided_at",
            optional=False,
        )
        _utc_instant(
            self.expires_at,
            "ApprovalEvidence.expires_at",
            optional=True,
        )
        if self.expires_at is not None and self.expires_at < self.decided_at:
            raise ValueError("ApprovalEvidence.expires_at precedes decided_at")

    def to_data(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "execution_id": self.execution_id,
            "business_operation_id": self.business_operation_id,
            "approval_id": self.approval_id,
            "approval_request_id": self.approval_request_id,
            "skill_semantic_hash": self.skill_semantic_hash,
            "decision": self.decision.value,
            "approver_id": self.approver_id,
            "decided_at": self.decided_at.isoformat(),
            "expires_at": (
                self.expires_at.isoformat() if self.expires_at is not None else None
            ),
            "payload_hash": self.payload_hash,
            "write_intent_hash": self.write_intent_hash,
            "evidence_integrity": self.evidence_integrity,
        }


@runtime_checkable
class CheckpointPort(Protocol):
    async def save(self, checkpoint: ExecutionCheckpoint) -> None: ...


@runtime_checkable
class ApprovalPort(Protocol):
    """Publishes requests idempotently by ApprovalRequestId."""

    async def publish(self, request: ApprovalRequest) -> None: ...


@dataclass(frozen=True, slots=True)
class ApprovalSuspension:
    checkpoint: ExecutionCheckpoint
    result: ExecutionResult

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, ExecutionCheckpoint):
            raise TypeError("checkpoint must be an ExecutionCheckpoint")
        if not isinstance(self.result, ExecutionResult):
            raise TypeError("result must be an ExecutionResult")
        if self.result.status is not ExecutionStatus.WAITING_FOR_APPROVAL:
            raise ValueError("approval suspension must be WAITING_FOR_APPROVAL")


class ApprovalSuspensionCoordinator:
    """Persists then publishes without waiting for a human decision."""

    __slots__ = ("_checkpoints",)

    def __init__(
        self,
        checkpoints: CheckpointCoordinator | None = None,
    ) -> None:
        self._checkpoints = checkpoints or CheckpointCoordinator()

    async def suspend(
        self,
        ctx: ExecutionContext,
        request: ApprovalRequest,
        checkpoint_port: CheckpointPort,
        approval_port: ApprovalPort,
    ) -> ApprovalSuspension:
        if not isinstance(ctx, ExecutionContext):
            raise TypeError("ctx must be an ExecutionContext")
        if not isinstance(request, ApprovalRequest):
            raise TypeError("request must be an ApprovalRequest")
        if not isinstance(checkpoint_port, CheckpointPort):
            raise TypeError("checkpoint_port must implement CheckpointPort")
        if not isinstance(approval_port, ApprovalPort):
            raise TypeError("approval_port must implement ApprovalPort")
        self._validate_bindings(ctx, request)
        self._stage_suspend(ctx, request)

        checkpoint = self._checkpoints.capture(ctx)
        await checkpoint_port.save(checkpoint)
        await approval_port.publish(request)
        ctx.checkpoint.approval_request_published = True

        return ApprovalSuspension(
            checkpoint=checkpoint,
            result=ExecutionResult(
                execution_id=ctx.identity.execution_id,
                skill_id=ctx.skill.skill_id,
                skill_version=ctx.skill.skill_version,
                semantic_hash=ctx.skill.semantic_hash,
                status=ExecutionStatus.WAITING_FOR_APPROVAL,
                completeness=ctx.result_completeness(
                    ExecutionStatus.WAITING_FOR_APPROVAL
                ),
                checks=tuple(ctx.checks),
                outputs=tuple(ctx.outputs),
                resources=ctx.usage(),
                approval=ApprovalSummary(
                    status=ApprovalStatus.PENDING,
                    approval_id=request.approval_id,
                    approval_request_id=request.approval_request_id,
                ),
                suspension=SuspensionInfo(
                    reason="APPROVAL_REQUIRED",
                    checkpoint_version=checkpoint.checkpoint_version,
                    approval_id=request.approval_id,
                    approval_request_id=request.approval_request_id,
                ),
            ),
        )

    @staticmethod
    def _validate_bindings(
        ctx: ExecutionContext,
        request: ApprovalRequest,
    ) -> None:
        expected = {
            "tenant_id": ctx.tenant_id,
            "execution_id": ctx.identity.execution_id,
            "business_operation_id": ctx.identity.business_operation_id,
            "skill_id": ctx.skill.skill_id,
            "skill_version": ctx.skill.skill_version,
            "skill_semantic_hash": ctx.skill.semantic_hash,
        }
        for name, value in expected.items():
            if getattr(request, name) != value:
                raise ValueError(f"ApprovalRequest.{name} binding mismatch")

    @staticmethod
    def _stage_suspend(
        ctx: ExecutionContext,
        request: ApprovalRequest,
    ) -> None:
        prior_request_id = (
            ctx.checkpoint.approval_request_ids_by_write_node.get(
                request.write_node_id
            )
        )
        if (
            prior_request_id is not None
            and prior_request_id != request.approval_request_id
        ):
            raise ValueError(
                "write node is already bound to another ApprovalRequestId"
            )
        ctx.approvals.records[request.approval_id] = ApprovalStatus.PENDING
        ctx.checkpoint.suspended_at_node_id = request.write_node_id
        ctx.checkpoint.approval_request_published = False
        ctx.checkpoint.approval_request_ids_by_write_node[
            request.write_node_id
        ] = request.approval_request_id
        ctx.checkpoint.write_intent_hashes_by_write_node[
            request.write_node_id
        ] = request.write_intent_hash
        ctx.checkpoint.payload_hashes_by_write_node[
            request.write_node_id
        ] = request.payload_hash
