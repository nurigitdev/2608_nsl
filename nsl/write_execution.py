from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Generic, Mapping, Protocol, TypeAlias, TypeVar

from .core import (
    ApprovalRequestId,
    BusinessOperationId,
    CommitState,
    ExecutionStatus,
    IdempotencyKey,
    PayloadHash,
    PreWriteEligibility,
    ToolCapability,
    ValueEnvelope,
)
from .data_protection import ensure_no_credential_material
from .idempotency import IdempotencyReservation
from .ir import canonical_json
from .preflight import (
    ExecutionTrustProfile,
    RuntimePreflightValidator,
    TrustPreflightError,
)
from .runtime_models import ExecutionContext, WriteSummary
from .trust import VerifiedSkillArtifact
from .validation import (
    BusinessPreconditionEvidence,
    WriteContractEvidence,
    WriteEligibilityGate,
    WriteGuardDecision,
)
from .write_intent import WriteIntent, compute_payload_hash


_WRITE_REQUESTED_DOMAIN = b"NSL-WRITE-REQUESTED-V1\0"
_OutcomeT = TypeVar("_OutcomeT")


class WriteExecutionError(RuntimeError):
    """Controlled failure at the durable WRITE invocation boundary."""


class ProviderInvocationUncertainError(WriteExecutionError):
    """Provider invocation ended without a trustworthy commit outcome."""

    detail_code = "PROVIDER_INVOCATION_UNCERTAIN"

    def __init__(self, cause: BaseException) -> None:
        if isinstance(cause, asyncio.CancelledError):
            reason_code = "PROVIDER_CANCELLED"
        elif isinstance(cause, TimeoutError):
            reason_code = "PROVIDER_TIMEOUT"
        elif isinstance(cause, ConnectionError):
            reason_code = "PROVIDER_CONNECTION_LOST"
        else:
            reason_code = "PROVIDER_EXCEPTION"
        self.cause = cause
        self.reason_code = reason_code
        super().__init__(
            "provider invocation did not return a trustworthy commit outcome"
        )


class WriteEvidenceError(WriteExecutionError):
    detail_code = "WRITE_REQUEST_EVIDENCE_INVALID"


class WriteOutcomeError(WriteExecutionError):
    detail_code = "WRITE_OUTCOME_EVIDENCE_INVALID"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteExecutionError(f"{name} must be a non-empty string")
    ensure_no_credential_material(value, name)
    return value


def _sha256_text(value: object, name: str) -> str:
    text = _required_text(value, name)
    digest = text.removeprefix("sha256:")
    if (
        not text.startswith("sha256:")
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise WriteExecutionError(f"{name} must be a lowercase SHA-256 value")
    return text


def _utc_instant(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise WriteExecutionError(f"{name} must be timezone-aware UTC")
    if value.utcoffset() != UTC.utcoffset(value):
        raise WriteExecutionError(f"{name} must use UTC")
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _freeze_arguments(
    arguments: Mapping[str, ValueEnvelope],
) -> Mapping[str, ValueEnvelope]:
    if not isinstance(arguments, Mapping):
        raise TypeError("arguments must be a mapping")
    if not all(
        isinstance(name, str)
        and bool(name.strip())
        and isinstance(envelope, ValueEnvelope)
        for name, envelope in arguments.items()
    ):
        raise TypeError("arguments must map non-empty names to ValueEnvelope")
    return MappingProxyType(
        {
            name: ValueEnvelope(
                value=_freeze_value(envelope.value),
                type_info=envelope.type_info,
                presence=envelope.presence,
                completeness=envelope.completeness,
                classification=envelope.classification,
                provenance_refs=tuple(envelope.provenance_refs),
            )
            for name, envelope in arguments.items()
        }
    )


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class WriteCallRequest:
    tenant_id: str
    execution_id: str
    business_operation_id: BusinessOperationId
    invocation_id: str
    node_id: str
    tool_ref: str
    tool_id: str
    contract_hash: str
    arguments: Mapping[str, ValueEnvelope]
    idempotency_key: IdempotencyKey
    payload_hash: PayloadHash
    approval_id: str
    approval_request_id: ApprovalRequestId
    write_intent_hash: str
    business_preconditions: BusinessPreconditionEvidence
    trace_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "execution_id",
            "business_operation_id",
            "invocation_id",
            "node_id",
            "tool_ref",
            "tool_id",
            "approval_id",
            "approval_request_id",
        ):
            _required_text(getattr(self, name), f"WriteCallRequest.{name}")
        _sha256_text(self.contract_hash, "WriteCallRequest.contract_hash")
        _sha256_text(self.payload_hash, "WriteCallRequest.payload_hash")
        _sha256_text(
            self.write_intent_hash,
            "WriteCallRequest.write_intent_hash",
        )
        key = _required_text(
            self.idempotency_key,
            "WriteCallRequest.idempotency_key",
        )
        if not key.startswith("idem:sha256:") or len(key) != 76:
            raise WriteExecutionError(
                "WriteCallRequest.idempotency_key must be an idem SHA-256 value"
            )
        frozen_arguments = _freeze_arguments(self.arguments)
        if compute_payload_hash(frozen_arguments) != self.payload_hash:
            raise WriteExecutionError(
                "WriteCallRequest.payload_hash does not match arguments"
            )
        object.__setattr__(self, "arguments", frozen_arguments)
        if not isinstance(
            self.business_preconditions,
            BusinessPreconditionEvidence,
        ):
            raise TypeError(
                "business_preconditions must be BusinessPreconditionEvidence"
            )
        if not isinstance(self.trace_refs, tuple) or not all(
            isinstance(item, str) and bool(item.strip())
            for item in self.trace_refs
        ):
            raise TypeError("trace_refs must be a tuple of non-empty strings")
        ensure_no_credential_material(self.trace_refs, "WriteCallRequest.trace_refs")


class WriteCallRequestBuilder:
    __slots__ = ()

    def build(
        self,
        *,
        intent: WriteIntent,
        context: ExecutionContext,
        contract_evidence: WriteContractEvidence,
        reservation: IdempotencyReservation,
        approval_request_id: ApprovalRequestId,
        invocation_id: str,
        business_preconditions: BusinessPreconditionEvidence,
        trace_refs: tuple[str, ...] = (),
    ) -> WriteCallRequest:
        if not isinstance(intent, WriteIntent):
            raise TypeError("intent must be a WriteIntent")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if not isinstance(contract_evidence, WriteContractEvidence):
            raise TypeError("contract_evidence must be WriteContractEvidence")
        if not isinstance(reservation, IdempotencyReservation):
            raise TypeError("reservation must be an IdempotencyReservation")
        if not isinstance(
            business_preconditions,
            BusinessPreconditionEvidence,
        ):
            raise TypeError(
                "business_preconditions must be BusinessPreconditionEvidence"
            )
        if context.tenant_id != intent.tenant_id:
            raise WriteExecutionError("WRITE tenant binding mismatch")
        if context.identity.business_operation_id != intent.business_operation_id:
            raise WriteExecutionError("WRITE business operation binding mismatch")
        if (
            contract_evidence.capability is not ToolCapability.WRITE
            or contract_evidence.tool_id != intent.tool_id
        ):
            raise WriteExecutionError("canonical WRITE Tool binding mismatch")
        durable = reservation.evidence
        if (
            durable.tenant_id != intent.tenant_id
            or durable.business_operation_id != intent.business_operation_id
            or durable.write_node_id != intent.node_id
            or durable.tool_id != intent.tool_id
            or durable.contract_hash != contract_evidence.contract_hash
            or durable.payload_hash != intent.payload_hash
        ):
            raise WriteExecutionError("idempotency reservation binding mismatch")
        if not self._precondition_matches(
            business_preconditions,
            intent,
            contract_evidence,
        ):
            raise WriteExecutionError("business precondition binding mismatch")
        return WriteCallRequest(
            tenant_id=intent.tenant_id,
            execution_id=context.identity.execution_id,
            business_operation_id=intent.business_operation_id,
            invocation_id=invocation_id,
            node_id=intent.node_id,
            tool_ref=intent.tool_ref,
            tool_id=intent.tool_id,
            contract_hash=contract_evidence.contract_hash,
            arguments=intent.canonical_arguments,
            idempotency_key=durable.idempotency_key,
            payload_hash=intent.payload_hash,
            approval_id=intent.approval_id,
            approval_request_id=approval_request_id,
            write_intent_hash=intent.write_intent_hash,
            business_preconditions=business_preconditions,
            trace_refs=trace_refs,
        )

    @staticmethod
    def _precondition_matches(
        evidence: BusinessPreconditionEvidence,
        intent: WriteIntent,
        contract: WriteContractEvidence,
    ) -> bool:
        return (
            evidence.tenant_id == intent.tenant_id
            and evidence.business_operation_id == intent.business_operation_id
            and evidence.write_node_id == intent.node_id
            and evidence.tool_id == intent.tool_id == contract.tool_id
            and evidence.contract_hash == contract.contract_hash
            and evidence.payload_hash == intent.payload_hash
            and evidence.write_intent_hash == intent.write_intent_hash
            and evidence.provider_atomic_enforcement
        )


@dataclass(frozen=True, slots=True)
class WriteAttemptRecord:
    tenant_id: str
    execution_id: str
    business_operation_id: BusinessOperationId
    invocation_id: str
    node_id: str
    tool_id: str
    contract_hash: str
    approval_request_id: ApprovalRequestId
    idempotency_key: IdempotencyKey
    payload_hash: PayloadHash
    write_intent_hash: str
    precondition_evidence_id: str
    requested_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "execution_id",
            "business_operation_id",
            "invocation_id",
            "node_id",
            "tool_id",
            "approval_request_id",
            "precondition_evidence_id",
        ):
            _required_text(getattr(self, name), f"WriteAttemptRecord.{name}")
        _sha256_text(self.contract_hash, "WriteAttemptRecord.contract_hash")
        _sha256_text(self.payload_hash, "WriteAttemptRecord.payload_hash")
        _sha256_text(
            self.write_intent_hash,
            "WriteAttemptRecord.write_intent_hash",
        )
        key = _required_text(
            self.idempotency_key,
            "WriteAttemptRecord.idempotency_key",
        )
        if not key.startswith("idem:sha256:") or len(key) != 76:
            raise WriteExecutionError(
                "WriteAttemptRecord.idempotency_key must be an idem SHA-256 value"
            )
        _utc_instant(self.requested_at, "WriteAttemptRecord.requested_at")

    @classmethod
    def from_request(
        cls,
        request: WriteCallRequest,
        requested_at: datetime,
    ) -> WriteAttemptRecord:
        if not isinstance(request, WriteCallRequest):
            raise TypeError("request must be a WriteCallRequest")
        return cls(
            tenant_id=request.tenant_id,
            execution_id=request.execution_id,
            business_operation_id=request.business_operation_id,
            invocation_id=request.invocation_id,
            node_id=request.node_id,
            tool_id=request.tool_id,
            contract_hash=request.contract_hash,
            approval_request_id=request.approval_request_id,
            idempotency_key=request.idempotency_key,
            payload_hash=request.payload_hash,
            write_intent_hash=request.write_intent_hash,
            precondition_evidence_id=request.business_preconditions.evidence_id,
            requested_at=requested_at,
        )

    @property
    def record_hash(self) -> str:
        payload = {
            "tenant_id": self.tenant_id,
            "execution_id": self.execution_id,
            "business_operation_id": self.business_operation_id,
            "invocation_id": self.invocation_id,
            "node_id": self.node_id,
            "tool_id": self.tool_id,
            "contract_hash": self.contract_hash,
            "approval_request_id": self.approval_request_id,
            "idempotency_key": self.idempotency_key,
            "payload_hash": self.payload_hash,
            "write_intent_hash": self.write_intent_hash,
            "precondition_evidence_id": self.precondition_evidence_id,
            "requested_at": self.requested_at.isoformat(),
        }
        digest = sha256(
            _WRITE_REQUESTED_DOMAIN + canonical_json(payload)
        ).hexdigest()
        return "sha256:" + digest


@dataclass(frozen=True, slots=True)
class WriteRequestedReceipt:
    record_hash: str
    durable_ref: str
    persisted_at: datetime
    durable: bool

    def __post_init__(self) -> None:
        _sha256_text(self.record_hash, "WriteRequestedReceipt.record_hash")
        _required_text(self.durable_ref, "WriteRequestedReceipt.durable_ref")
        _utc_instant(self.persisted_at, "WriteRequestedReceipt.persisted_at")
        if type(self.durable) is not bool:
            raise TypeError("WriteRequestedReceipt.durable must be Bool")


class WriteEvidencePort(Protocol):
    async def record_requested(
        self,
        record: WriteAttemptRecord,
    ) -> WriteRequestedReceipt: ...


class WriteExecutionPort(Protocol[_OutcomeT]):
    async def execute(self, request: WriteCallRequest) -> _OutcomeT: ...


class WriteExecutionCoordinator(Generic[_OutcomeT]):
    __slots__ = ("_write_port", "_evidence_port")

    def __init__(
        self,
        write_port: WriteExecutionPort[_OutcomeT],
        evidence_port: WriteEvidencePort,
    ) -> None:
        if not hasattr(write_port, "execute"):
            raise TypeError("write_port must provide execute()")
        if not hasattr(evidence_port, "record_requested"):
            raise TypeError("evidence_port must provide record_requested()")
        self._write_port = write_port
        self._evidence_port = evidence_port

    async def execute(
        self,
        *,
        request: WriteCallRequest,
        decision: WriteGuardDecision,
        context: ExecutionContext,
        requested_at: datetime,
    ) -> _OutcomeT:
        if not isinstance(request, WriteCallRequest):
            raise TypeError("request must be a WriteCallRequest")
        if not isinstance(decision, WriteGuardDecision):
            raise TypeError("decision must be a WriteGuardDecision")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        WriteEligibilityGate.require_allowed(decision)
        if request.execution_id != context.identity.execution_id:
            raise WriteExecutionError("WRITE execution identity mismatch")
        if request.tenant_id != context.tenant_id:
            raise WriteExecutionError("WRITE execution tenant mismatch")
        if request.business_operation_id != context.identity.business_operation_id:
            raise WriteExecutionError("WRITE execution business operation mismatch")

        record = WriteAttemptRecord.from_request(request, requested_at)
        receipt = await self._evidence_port.record_requested(record)
        if not isinstance(receipt, WriteRequestedReceipt):
            raise WriteEvidenceError(
                "WRITE_REQUESTED persistence returned an invalid receipt"
            )
        if receipt.record_hash != record.record_hash:
            raise WriteEvidenceError("WRITE_REQUESTED receipt binding mismatch")
        if not receipt.durable:
            raise WriteEvidenceError("WRITE_REQUESTED evidence is not durable")
        if receipt.persisted_at < record.requested_at:
            raise WriteEvidenceError(
                "WRITE_REQUESTED receipt predates the request"
            )
        context.checkpoint.write_request_durable = True
        try:
            return await self._write_port.execute(request)
        except asyncio.CancelledError as error:
            raise ProviderInvocationUncertainError(error) from None
        except Exception as error:
            raise ProviderInvocationUncertainError(error) from error


class TrustedWriteExecutionCoordinator(Generic[_OutcomeT]):
    """Canonical Production WRITE boundary: trust first, durability second."""

    __slots__ = ("_delegate", "_preflight")

    def __init__(
        self,
        delegate: WriteExecutionCoordinator[_OutcomeT],
        preflight: RuntimePreflightValidator | None = None,
    ) -> None:
        if not isinstance(delegate, WriteExecutionCoordinator):
            raise TypeError("delegate must be a WriteExecutionCoordinator")
        if preflight is not None and not isinstance(
            preflight,
            RuntimePreflightValidator,
        ):
            raise TypeError("preflight must be a RuntimePreflightValidator")
        self._delegate = delegate
        self._preflight = preflight or RuntimePreflightValidator()

    async def execute(
        self,
        *,
        artifact: VerifiedSkillArtifact,
        profile: ExecutionTrustProfile,
        trust_checked_at: datetime,
        request: WriteCallRequest,
        decision: WriteGuardDecision,
        context: ExecutionContext,
        requested_at: datetime,
    ) -> _OutcomeT:
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        evidence = self._preflight.validate(
            artifact,
            profile,
            now=trust_checked_at,
        )
        if evidence.tenant_id != context.tenant_id:
            raise TrustPreflightError(
                "TRUST_TENANT_BINDING_MISMATCH",
                "trust profile tenant does not match the execution context",
            )
        if evidence.skill_semantic_hash != context.skill.semantic_hash:
            raise TrustPreflightError(
                "TRUST_SKILL_BINDING_MISMATCH",
                "trusted Skill does not match the execution context",
            )
        return await self._delegate.execute(
            request=request,
            decision=decision,
            context=context,
            requested_at=requested_at,
        )


@dataclass(frozen=True, slots=True)
class WriteOutcomeIdentity:
    tenant_id: str
    business_operation_id: BusinessOperationId
    invocation_id: str
    node_id: str
    tool_id: str
    idempotency_key: IdempotencyKey
    payload_hash: PayloadHash
    approval_request_id: ApprovalRequestId
    write_intent_hash: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "business_operation_id",
            "invocation_id",
            "node_id",
            "tool_id",
            "approval_request_id",
        ):
            _required_text(getattr(self, name), f"WriteOutcomeIdentity.{name}")
        key = _required_text(
            self.idempotency_key,
            "WriteOutcomeIdentity.idempotency_key",
        )
        if not key.startswith("idem:sha256:") or len(key) != 76:
            raise WriteOutcomeError(
                "WriteOutcomeIdentity.idempotency_key must be an idem SHA-256 value"
            )
        _sha256_text(self.payload_hash, "WriteOutcomeIdentity.payload_hash")
        _sha256_text(
            self.write_intent_hash,
            "WriteOutcomeIdentity.write_intent_hash",
        )

    @classmethod
    def from_request(cls, request: WriteCallRequest) -> WriteOutcomeIdentity:
        if not isinstance(request, WriteCallRequest):
            raise TypeError("request must be a WriteCallRequest")
        return cls(
            tenant_id=request.tenant_id,
            business_operation_id=request.business_operation_id,
            invocation_id=request.invocation_id,
            node_id=request.node_id,
            tool_id=request.tool_id,
            idempotency_key=request.idempotency_key,
            payload_hash=request.payload_hash,
            approval_request_id=request.approval_request_id,
            write_intent_hash=request.write_intent_hash,
        )


@dataclass(frozen=True, slots=True)
class WriteResultEnvelope:
    identity: WriteOutcomeIdentity
    value: ValueEnvelope
    business_reference: str
    provider_transaction_id: str | None
    committed_at: datetime
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, WriteOutcomeIdentity):
            raise TypeError("identity must be a WriteOutcomeIdentity")
        if not isinstance(self.value, ValueEnvelope):
            raise TypeError("value must be a ValueEnvelope")
        _required_text(
            self.business_reference,
            "WriteResultEnvelope.business_reference",
        )
        _optional_text(
            self.provider_transaction_id,
            "WriteResultEnvelope.provider_transaction_id",
        )
        _utc_instant(self.committed_at, "WriteResultEnvelope.committed_at")
        _validate_evidence_refs(self.evidence_refs, "WriteResultEnvelope")

    @property
    def commit_state(self) -> CommitState:
        return CommitState.COMMITTED


@dataclass(frozen=True, slots=True)
class WriteCommitted:
    result: WriteResultEnvelope

    def __post_init__(self) -> None:
        if not isinstance(self.result, WriteResultEnvelope):
            raise TypeError("result must be a WriteResultEnvelope")

    @property
    def identity(self) -> WriteOutcomeIdentity:
        return self.result.identity

    @property
    def commit_state(self) -> CommitState:
        return CommitState.COMMITTED


@dataclass(frozen=True, slots=True)
class WriteNotCommitted:
    identity: WriteOutcomeIdentity
    reason_code: str
    message: str
    provider_transaction_id: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, WriteOutcomeIdentity):
            raise TypeError("identity must be a WriteOutcomeIdentity")
        _required_text(self.reason_code, "WriteNotCommitted.reason_code")
        _required_text(self.message, "WriteNotCommitted.message")
        _optional_text(
            self.provider_transaction_id,
            "WriteNotCommitted.provider_transaction_id",
        )
        _validate_evidence_refs(self.evidence_refs, "WriteNotCommitted")

    @property
    def commit_state(self) -> CommitState:
        return CommitState.NOT_COMMITTED


@dataclass(frozen=True, slots=True)
class WriteCommitUnknown:
    identity: WriteOutcomeIdentity
    reason_code: str
    message: str
    provider_transaction_id: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, WriteOutcomeIdentity):
            raise TypeError("identity must be a WriteOutcomeIdentity")
        _required_text(self.reason_code, "WriteCommitUnknown.reason_code")
        _required_text(self.message, "WriteCommitUnknown.message")
        _optional_text(
            self.provider_transaction_id,
            "WriteCommitUnknown.provider_transaction_id",
        )
        _validate_evidence_refs(self.evidence_refs, "WriteCommitUnknown")

    @property
    def commit_state(self) -> CommitState:
        return CommitState.UNKNOWN_COMMIT_STATE


WriteExecutionOutcome: TypeAlias = (
    WriteCommitted | WriteNotCommitted | WriteCommitUnknown
)


def _validate_evidence_refs(value: object, owner: str) -> None:
    if not isinstance(value, tuple) or not all(
        isinstance(item, str) and bool(item.strip()) for item in value
    ):
        raise TypeError(f"{owner}.evidence_refs must be a tuple of non-empty strings")
    ensure_no_credential_material(value, f"{owner}.evidence_refs")


@dataclass(frozen=True, slots=True)
class WriteOutcomeRecord:
    requested_record_hash: str
    identity: WriteOutcomeIdentity
    commit_state: CommitState
    reason_code: str | None
    provider_transaction_id: str | None
    business_reference: str | None
    evidence_refs: tuple[str, ...]
    recorded_at: datetime

    def __post_init__(self) -> None:
        _sha256_text(
            self.requested_record_hash,
            "WriteOutcomeRecord.requested_record_hash",
        )
        if not isinstance(self.identity, WriteOutcomeIdentity):
            raise TypeError("identity must be a WriteOutcomeIdentity")
        if not isinstance(self.commit_state, CommitState):
            raise TypeError("commit_state must be a CommitState")
        _optional_text(self.reason_code, "WriteOutcomeRecord.reason_code")
        _optional_text(
            self.provider_transaction_id,
            "WriteOutcomeRecord.provider_transaction_id",
        )
        _optional_text(
            self.business_reference,
            "WriteOutcomeRecord.business_reference",
        )
        _validate_evidence_refs(self.evidence_refs, "WriteOutcomeRecord")
        _utc_instant(self.recorded_at, "WriteOutcomeRecord.recorded_at")

    @property
    def record_hash(self) -> str:
        payload = {
            "requested_record_hash": self.requested_record_hash,
            "tenant_id": self.identity.tenant_id,
            "business_operation_id": self.identity.business_operation_id,
            "invocation_id": self.identity.invocation_id,
            "node_id": self.identity.node_id,
            "tool_id": self.identity.tool_id,
            "idempotency_key": self.identity.idempotency_key,
            "payload_hash": self.identity.payload_hash,
            "approval_request_id": self.identity.approval_request_id,
            "write_intent_hash": self.identity.write_intent_hash,
            "commit_state": self.commit_state.value,
            "reason_code": self.reason_code,
            "provider_transaction_id": self.provider_transaction_id,
            "business_reference": self.business_reference,
            "evidence_refs": list(self.evidence_refs),
            "recorded_at": self.recorded_at.isoformat(),
        }
        return "sha256:" + sha256(
            b"NSL-WRITE-OUTCOME-V1\0" + canonical_json(payload)
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class WriteOutcomeReceipt:
    record_hash: str
    durable_ref: str
    persisted_at: datetime

    def __post_init__(self) -> None:
        _sha256_text(self.record_hash, "WriteOutcomeReceipt.record_hash")
        _required_text(self.durable_ref, "WriteOutcomeReceipt.durable_ref")
        _utc_instant(self.persisted_at, "WriteOutcomeReceipt.persisted_at")


class WriteOutcomeEvidencePort(Protocol):
    async def record_outcome(
        self,
        record: WriteOutcomeRecord,
    ) -> WriteOutcomeReceipt: ...


@dataclass(frozen=True, slots=True)
class WriteJournalEntry:
    record: WriteOutcomeRecord
    durable_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.record, WriteOutcomeRecord):
            raise TypeError("record must be a WriteOutcomeRecord")
        _required_text(self.durable_ref, "WriteJournalEntry.durable_ref")


@dataclass(frozen=True, slots=True)
class WriteCommitDecision:
    execution_status: ExecutionStatus
    outcome: WriteExecutionOutcome
    write_summary: WriteSummary
    automatic_retry_allowed: bool
    reconciliation_required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.execution_status, ExecutionStatus):
            raise TypeError("execution_status must be an ExecutionStatus")
        if not isinstance(
            self.outcome,
            (WriteCommitted, WriteNotCommitted, WriteCommitUnknown),
        ):
            raise TypeError("outcome must be a WriteExecutionOutcome")
        if not isinstance(self.write_summary, WriteSummary):
            raise TypeError("write_summary must be a WriteSummary")
        if type(self.automatic_retry_allowed) is not bool:
            raise TypeError("automatic_retry_allowed must be Bool")
        if type(self.reconciliation_required) is not bool:
            raise TypeError("reconciliation_required must be Bool")
        if self.automatic_retry_allowed:
            raise WriteOutcomeError("automatic WRITE retry is forbidden")
        expected_reconciliation = (
            self.write_summary.commit_state is CommitState.UNKNOWN_COMMIT_STATE
        )
        if self.reconciliation_required is not expected_reconciliation:
            raise WriteOutcomeError("reconciliation flag does not match commit state")
        if self.outcome.commit_state is not self.write_summary.commit_state:
            raise WriteOutcomeError("outcome and summary commit states differ")
        expected_status = {
            CommitState.COMMITTED: ExecutionStatus.COMPLETED,
            CommitState.NOT_COMMITTED: ExecutionStatus.FAILED,
            CommitState.UNKNOWN_COMMIT_STATE: (
                ExecutionStatus.RECONCILIATION_REQUIRED
            ),
        }[self.write_summary.commit_state]
        if self.execution_status is not expected_status:
            raise WriteOutcomeError("execution status does not match commit state")


class WriteOutcomeCoordinator:
    __slots__ = ("_port",)

    def __init__(self, port: WriteOutcomeEvidencePort) -> None:
        if not hasattr(port, "record_outcome"):
            raise TypeError("port must provide record_outcome()")
        self._port = port

    async def finalize(
        self,
        *,
        request: WriteCallRequest,
        context: ExecutionContext,
        requested_record_hash: str,
        outcome: WriteExecutionOutcome,
        recorded_at: datetime,
    ) -> WriteCommitDecision:
        if not isinstance(request, WriteCallRequest):
            raise TypeError("request must be a WriteCallRequest")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if not isinstance(
            outcome,
            (WriteCommitted, WriteNotCommitted, WriteCommitUnknown),
        ):
            raise TypeError("outcome must be a WriteExecutionOutcome")
        expected_identity = WriteOutcomeIdentity.from_request(request)
        if outcome.identity != expected_identity:
            raise WriteOutcomeError("provider outcome identity mismatch")
        if request.execution_id != context.identity.execution_id:
            raise WriteOutcomeError("WRITE outcome execution mismatch")
        if context.tenant_id != request.tenant_id:
            raise WriteOutcomeError("WRITE outcome tenant mismatch")
        if context.identity.business_operation_id != request.business_operation_id:
            raise WriteOutcomeError("WRITE outcome business operation mismatch")
        if (
            context.idempotency.keys_by_write_node.get(request.node_id)
            != request.idempotency_key
            or context.idempotency.payload_hashes_by_write_node.get(request.node_id)
            != request.payload_hash
        ):
            raise WriteOutcomeError("WRITE outcome idempotency state mismatch")

        record = self._record(
            requested_record_hash,
            outcome,
            recorded_at,
        )
        receipt = await self._port.record_outcome(record)
        if not isinstance(receipt, WriteOutcomeReceipt):
            raise WriteOutcomeError("outcome persistence returned an invalid receipt")
        if receipt.record_hash != record.record_hash:
            raise WriteOutcomeError("outcome receipt binding mismatch")
        if receipt.persisted_at < record.recorded_at:
            raise WriteOutcomeError("outcome receipt predates the outcome")
        context.writes.entries.append(WriteJournalEntry(record, receipt.durable_ref))
        summary = WriteSummary(
            commit_state=outcome.commit_state,
            tool_id=request.tool_id,
            node_id=request.node_id,
            idempotency_key=request.idempotency_key,
            payload_hash=request.payload_hash,
            provider_transaction_id=record.provider_transaction_id,
            business_reference=record.business_reference,
        )
        status = {
            CommitState.COMMITTED: ExecutionStatus.COMPLETED,
            CommitState.NOT_COMMITTED: ExecutionStatus.FAILED,
            CommitState.UNKNOWN_COMMIT_STATE: (
                ExecutionStatus.RECONCILIATION_REQUIRED
            ),
        }[outcome.commit_state]
        return WriteCommitDecision(
            execution_status=status,
            outcome=outcome,
            write_summary=summary,
            automatic_retry_allowed=False,
            reconciliation_required=(
                outcome.commit_state is CommitState.UNKNOWN_COMMIT_STATE
            ),
        )

    @staticmethod
    def _record(
        requested_record_hash: str,
        outcome: WriteExecutionOutcome,
        recorded_at: datetime,
    ) -> WriteOutcomeRecord:
        if isinstance(outcome, WriteCommitted):
            reason_code = None
            provider_transaction_id = outcome.result.provider_transaction_id
            business_reference = outcome.result.business_reference
            evidence_refs = outcome.result.evidence_refs
        else:
            reason_code = outcome.reason_code
            provider_transaction_id = outcome.provider_transaction_id
            business_reference = None
            evidence_refs = outcome.evidence_refs
        return WriteOutcomeRecord(
            requested_record_hash=requested_record_hash,
            identity=outcome.identity,
            commit_state=outcome.commit_state,
            reason_code=reason_code,
            provider_transaction_id=provider_transaction_id,
            business_reference=business_reference,
            evidence_refs=evidence_refs,
            recorded_at=recorded_at,
        )


class CommitAwareWriteExecutor:
    __slots__ = ("_invocation", "_outcomes")

    def __init__(
        self,
        invocation: WriteExecutionCoordinator[object],
        outcomes: WriteOutcomeCoordinator,
    ) -> None:
        if not isinstance(invocation, WriteExecutionCoordinator):
            raise TypeError("invocation must be a WriteExecutionCoordinator")
        if not isinstance(outcomes, WriteOutcomeCoordinator):
            raise TypeError("outcomes must be a WriteOutcomeCoordinator")
        self._invocation = invocation
        self._outcomes = outcomes

    async def execute(
        self,
        *,
        request: WriteCallRequest,
        decision: WriteGuardDecision,
        context: ExecutionContext,
        requested_at: datetime,
        recorded_at: datetime,
    ) -> WriteCommitDecision:
        requested_record_hash = WriteAttemptRecord.from_request(
            request,
            requested_at,
        ).record_hash
        try:
            raw_outcome = await self._invocation.execute(
                request=request,
                decision=decision,
                context=context,
                requested_at=requested_at,
            )
        except ProviderInvocationUncertainError as error:
            outcome = self._uncertain(request, error.reason_code)
        else:
            if isinstance(
                raw_outcome,
                (WriteCommitted, WriteNotCommitted, WriteCommitUnknown),
            ):
                outcome = raw_outcome
            else:
                outcome = self._uncertain(
                    request,
                    "PROVIDER_ACKNOWLEDGEMENT_MALFORMED",
                )
        return await self._outcomes.finalize(
            request=request,
            context=context,
            requested_record_hash=requested_record_hash,
            outcome=outcome,
            recorded_at=recorded_at,
        )

    @staticmethod
    def _uncertain(
        request: WriteCallRequest,
        reason_code: str,
    ) -> WriteCommitUnknown:
        return WriteCommitUnknown(
            identity=WriteOutcomeIdentity.from_request(request),
            reason_code=reason_code,
            message="provider outcome is uncertain; reconciliation is required",
            evidence_refs=request.trace_refs,
        )
