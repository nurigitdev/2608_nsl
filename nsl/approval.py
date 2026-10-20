from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .checkpoints import CheckpointCoordinator, ExecutionCheckpoint
from .core import (
    ApprovalDecision,
    ApprovalEvidenceValidation,
    ApprovalRequestId,
    ApprovalStatus,
    BusinessOperationId,
    DataClassification,
    ExecutionStatus,
    PayloadHash,
    RiskLevel,
)
from .data_protection import ensure_no_credential_material
from .ir import SkillObject
from .runtime_models import (
    ApprovalSummary,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    RuntimeClock,
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


class ResumeClaimError(RuntimeError):
    pass


@runtime_checkable
class ResumeCheckpointPort(Protocol):
    async def claim(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> ExecutionCheckpoint: ...

    async def release(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> None: ...

    async def consume(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> None: ...


@runtime_checkable
class ApprovalClock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class ApprovalEvidenceVerifier(Protocol):
    def verify(self, evidence: ApprovalEvidence) -> bool: ...


@dataclass(frozen=True, slots=True)
class RecordedApprovalClock:
    recorded_at: datetime

    def __post_init__(self) -> None:
        _utc_instant(
            self.recorded_at,
            "RecordedApprovalClock.recorded_at",
            optional=False,
        )

    def now(self) -> datetime:
        return self.recorded_at


@dataclass(frozen=True, slots=True)
class ResumeToken:
    execution_id: str
    business_operation_id: BusinessOperationId
    checkpoint_version: int
    approval_evidence: ApprovalEvidence

    def __post_init__(self) -> None:
        _required_text(self.execution_id, "ResumeToken.execution_id")
        _required_text(
            self.business_operation_id,
            "ResumeToken.business_operation_id",
        )
        if (
            not isinstance(self.checkpoint_version, int)
            or isinstance(self.checkpoint_version, bool)
            or self.checkpoint_version <= 0
        ):
            raise ValueError("ResumeToken.checkpoint_version must be positive")
        if not isinstance(self.approval_evidence, ApprovalEvidence):
            raise TypeError(
                "ResumeToken.approval_evidence must be ApprovalEvidence"
            )


@dataclass(frozen=True, slots=True)
class ApprovalResumeBindings:
    tenant_id: str
    execution_id: str
    business_operation_id: BusinessOperationId
    approval_id: str
    approval_request_id: ApprovalRequestId
    skill_semantic_hash: str
    payload_hash: PayloadHash
    write_intent_hash: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "execution_id",
            "business_operation_id",
            "approval_id",
            "approval_request_id",
            "write_intent_hash",
        ):
            _required_text(getattr(self, name), f"ApprovalResumeBindings.{name}")
        _sha256(
            self.skill_semantic_hash,
            "ApprovalResumeBindings.skill_semantic_hash",
        )
        _sha256(
            self.payload_hash,
            "ApprovalResumeBindings.payload_hash",
        )


@dataclass(frozen=True, slots=True)
class ApprovalValidationResult:
    validation: ApprovalEvidenceValidation
    approval_status: ApprovalStatus
    reason_code: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.validation, ApprovalEvidenceValidation):
            raise TypeError(
                "validation must be an ApprovalEvidenceValidation"
            )
        if not isinstance(self.approval_status, ApprovalStatus):
            raise TypeError("approval_status must be an ApprovalStatus")
        if self.reason_code is not None:
            _required_text(
                self.reason_code,
                "ApprovalValidationResult.reason_code",
            )

    @property
    def approval_gate_passed(self) -> bool:
        return (
            self.validation is ApprovalEvidenceValidation.VALID
            and self.approval_status is ApprovalStatus.APPROVED
        )

    @property
    def requires_reapproval(self) -> bool:
        return self.approval_status is ApprovalStatus.EXPIRED


class ApprovalEvidenceValidator:
    def validate(
        self,
        evidence: ApprovalEvidence,
        bindings: ApprovalResumeBindings,
        verifier: ApprovalEvidenceVerifier,
        clock: ApprovalClock,
    ) -> ApprovalValidationResult:
        if not isinstance(evidence, ApprovalEvidence):
            raise TypeError("evidence must be ApprovalEvidence")
        if not isinstance(bindings, ApprovalResumeBindings):
            raise TypeError("bindings must be ApprovalResumeBindings")
        if not isinstance(verifier, ApprovalEvidenceVerifier):
            raise TypeError(
                "verifier must implement ApprovalEvidenceVerifier"
            )
        if not isinstance(clock, ApprovalClock):
            raise TypeError("clock must implement ApprovalClock")

        verified = verifier.verify(evidence)
        if not isinstance(verified, bool):
            raise TypeError("ApprovalEvidenceVerifier.verify must return bool")
        if not verified:
            return self._blocked(
                ApprovalEvidenceValidation.INVALID,
                "APPROVAL_EVIDENCE_INVALID",
            )

        mismatch_checks = (
            (
                evidence.business_operation_id
                != bindings.business_operation_id,
                ApprovalEvidenceValidation.BUSINESS_OPERATION_MISMATCH,
                "APPROVAL_BUSINESS_OPERATION_MISMATCH",
            ),
            (
                evidence.approval_request_id
                != bindings.approval_request_id,
                ApprovalEvidenceValidation.APPROVAL_REQUEST_MISMATCH,
                "APPROVAL_REQUEST_MISMATCH",
            ),
            (
                evidence.write_intent_hash != bindings.write_intent_hash,
                ApprovalEvidenceValidation.INTENT_MISMATCH,
                "APPROVAL_INTENT_MISMATCH",
            ),
            (
                evidence.payload_hash != bindings.payload_hash,
                ApprovalEvidenceValidation.PAYLOAD_MISMATCH,
                "APPROVAL_PAYLOAD_MISMATCH",
            ),
        )
        for mismatched, validation, reason_code in mismatch_checks:
            if mismatched:
                return self._blocked(validation, reason_code)

        if (
            evidence.tenant_id != bindings.tenant_id
            or evidence.execution_id != bindings.execution_id
            or evidence.approval_id != bindings.approval_id
            or evidence.skill_semantic_hash
            != bindings.skill_semantic_hash
        ):
            return self._blocked(
                ApprovalEvidenceValidation.INVALID,
                "APPROVAL_EVIDENCE_BINDING_MISMATCH",
            )

        now = _utc_instant(
            clock.now(),
            "ApprovalClock.now",
            optional=False,
        )
        if evidence.expires_at is not None and now >= evidence.expires_at:
            return ApprovalValidationResult(
                ApprovalEvidenceValidation.EXPIRED,
                ApprovalStatus.EXPIRED,
                "APPROVAL_EXPIRED",
            )
        if evidence.decision is ApprovalDecision.REJECTED:
            return ApprovalValidationResult(
                ApprovalEvidenceValidation.VALID,
                ApprovalStatus.REJECTED,
                "APPROVAL_REJECTED",
            )
        return ApprovalValidationResult(
            ApprovalEvidenceValidation.VALID,
            ApprovalStatus.APPROVED,
            None,
        )

    @staticmethod
    def _blocked(
        validation: ApprovalEvidenceValidation,
        reason_code: str,
    ) -> ApprovalValidationResult:
        return ApprovalValidationResult(
            validation,
            ApprovalStatus.PENDING,
            reason_code,
        )


@dataclass(frozen=True, slots=True)
class ApprovalResumeOutcome:
    context: ExecutionContext
    checkpoint: ExecutionCheckpoint
    claim_id: str
    validation: ApprovalValidationResult
    claim_consumed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if not isinstance(self.checkpoint, ExecutionCheckpoint):
            raise TypeError("checkpoint must be an ExecutionCheckpoint")
        _required_text(self.claim_id, "ApprovalResumeOutcome.claim_id")
        if not isinstance(self.validation, ApprovalValidationResult):
            raise TypeError("validation must be ApprovalValidationResult")
        if not isinstance(self.claim_consumed, bool):
            raise TypeError("claim_consumed must be bool")


class ApprovalResumeCoordinator:
    __slots__ = ("_checkpoints", "_validator")

    def __init__(
        self,
        checkpoints: CheckpointCoordinator | None = None,
        validator: ApprovalEvidenceValidator | None = None,
    ) -> None:
        self._checkpoints = checkpoints or CheckpointCoordinator()
        self._validator = validator or ApprovalEvidenceValidator()

    async def resume(
        self,
        *,
        token: ResumeToken,
        skill: SkillObject,
        request: ExecutionRequest,
        approval_id: str,
        write_node_id: str,
        current_write_intent_hash: str,
        current_payload_hash: PayloadHash,
        claim_id: str,
        checkpoint_port: ResumeCheckpointPort,
        verifier: ApprovalEvidenceVerifier,
        clock: ApprovalClock,
        runtime_clock: RuntimeClock | None = None,
    ) -> ApprovalResumeOutcome:
        self._validate_contracts(
            token,
            skill,
            request,
            checkpoint_port,
            verifier,
            clock,
        )
        _required_text(approval_id, "approval_id")
        _required_text(write_node_id, "write_node_id")
        _required_text(current_write_intent_hash, "current_write_intent_hash")
        _sha256(current_payload_hash, "current_payload_hash")
        _required_text(claim_id, "claim_id")
        self._validate_token_request(token, request)

        checkpoint = await checkpoint_port.claim(
            token.execution_id,
            token.checkpoint_version,
            claim_id,
        )
        try:
            context = self._checkpoints.restore(
                checkpoint,
                skill,
                request,
                runtime_clock,
            )
            bindings = self._bindings(
                context,
                approval_id,
                write_node_id,
                current_write_intent_hash,
                current_payload_hash,
            )
            validation = self._validator.validate(
                token.approval_evidence,
                bindings,
                verifier,
                clock,
            )
        except Exception:
            await checkpoint_port.release(
                token.execution_id,
                token.checkpoint_version,
                claim_id,
            )
            raise

        if validation.validation in {
            ApprovalEvidenceValidation.INVALID,
            ApprovalEvidenceValidation.INTENT_MISMATCH,
            ApprovalEvidenceValidation.PAYLOAD_MISMATCH,
            ApprovalEvidenceValidation.BUSINESS_OPERATION_MISMATCH,
            ApprovalEvidenceValidation.APPROVAL_REQUEST_MISMATCH,
        }:
            await checkpoint_port.release(
                token.execution_id,
                token.checkpoint_version,
                claim_id,
            )
            consumed = False
        else:
            context.approvals.records[approval_id] = validation.approval_status
            await checkpoint_port.consume(
                token.execution_id,
                token.checkpoint_version,
                claim_id,
            )
            consumed = True

        return ApprovalResumeOutcome(
            context,
            checkpoint,
            claim_id,
            validation,
            consumed,
        )

    @staticmethod
    def _validate_contracts(
        token: object,
        skill: object,
        request: object,
        checkpoint_port: object,
        verifier: object,
        clock: object,
    ) -> None:
        contracts = (
            (token, ResumeToken, "token must be ResumeToken"),
            (skill, SkillObject, "skill must be SkillObject"),
            (request, ExecutionRequest, "request must be ExecutionRequest"),
            (
                checkpoint_port,
                ResumeCheckpointPort,
                "checkpoint_port must implement ResumeCheckpointPort",
            ),
            (
                verifier,
                ApprovalEvidenceVerifier,
                "verifier must implement ApprovalEvidenceVerifier",
            ),
            (clock, ApprovalClock, "clock must implement ApprovalClock"),
        )
        for value, expected, message in contracts:
            if not isinstance(value, expected):
                raise TypeError(message)

    @staticmethod
    def _validate_token_request(
        token: ResumeToken,
        request: ExecutionRequest,
    ) -> None:
        if token.execution_id != request.execution_id:
            raise ValueError("ResumeToken.execution_id binding mismatch")
        if token.business_operation_id != request.business_operation_id:
            raise ValueError(
                "ResumeToken.business_operation_id binding mismatch"
            )

    @staticmethod
    def _bindings(
        context: ExecutionContext,
        approval_id: str,
        write_node_id: str,
        current_write_intent_hash: str,
        current_payload_hash: PayloadHash,
    ) -> ApprovalResumeBindings:
        if context.approvals.records.get(approval_id) is not ApprovalStatus.PENDING:
            raise ValueError("checkpoint approval is not pending")
        approval_request_id = (
            context.checkpoint.approval_request_ids_by_write_node.get(
                write_node_id
            )
        )
        if approval_request_id is None:
            raise ValueError("checkpoint approval request binding is missing")
        return ApprovalResumeBindings(
            tenant_id=context.tenant_id,
            execution_id=context.identity.execution_id,
            business_operation_id=context.identity.business_operation_id,
            approval_id=approval_id,
            approval_request_id=ApprovalRequestId(approval_request_id),
            skill_semantic_hash=context.skill.semantic_hash,
            payload_hash=current_payload_hash,
            write_intent_hash=current_write_intent_hash,
        )
