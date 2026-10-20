from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Generic, Mapping, Protocol, TypeVar

from .core import (
    ApprovalRequestId,
    BusinessOperationId,
    IdempotencyKey,
    PayloadHash,
    PreWriteEligibility,
    ToolCapability,
    ValueEnvelope,
)
from .data_protection import ensure_no_credential_material
from .idempotency import IdempotencyReservation
from .ir import canonical_json
from .runtime_models import ExecutionContext
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


class WriteEvidenceError(WriteExecutionError):
    detail_code = "WRITE_REQUEST_EVIDENCE_INVALID"


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
        return await self._write_port.execute(request)
