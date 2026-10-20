from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from .audit import AuditRecorder
from .core import (
    ApprovalRequestId,
    BusinessOperationId,
    DataClassification,
    IdempotencyKey,
    PayloadHash,
    VerificationStatus,
)
from .data_protection import ensure_no_credential_material
from .runtime_models import VerificationSummary
from .verification import VerificationResult
from .write_execution import WriteOutcomeIdentity


MAX_AUDIT_EVIDENCE_REFS = 64


class WriteAuditError(ValueError):
    """Invalid protected WRITE audit evidence or event binding."""


class WriteAuditEventType(StrEnum):
    WRITE_INTENT_CREATED = "WRITE_INTENT_CREATED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    EXECUTION_SUSPENDED = "EXECUTION_SUSPENDED"
    APPROVAL_APPROVED = "APPROVAL_APPROVED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    EXECUTION_RESUMED = "EXECUTION_RESUMED"
    WRITE_REQUESTED = "WRITE_REQUESTED"
    WRITE_COMMIT_CONFIRMED = "WRITE_COMMIT_CONFIRMED"
    WRITE_COMMIT_UNKNOWN = "WRITE_COMMIT_UNKNOWN"
    WRITE_VERIFIED = "WRITE_VERIFIED"
    WRITE_VERIFICATION_FAILED = "WRITE_VERIFICATION_FAILED"


_APPROVAL_BOUND_EVENTS = frozenset(
    {
        WriteAuditEventType.APPROVAL_REQUESTED,
        WriteAuditEventType.EXECUTION_SUSPENDED,
        WriteAuditEventType.APPROVAL_APPROVED,
        WriteAuditEventType.APPROVAL_REJECTED,
        WriteAuditEventType.EXECUTION_RESUMED,
    }
)
_IDEMPOTENCY_BOUND_EVENTS = frozenset(
    {
        WriteAuditEventType.WRITE_REQUESTED,
        WriteAuditEventType.WRITE_COMMIT_CONFIRMED,
        WriteAuditEventType.WRITE_COMMIT_UNKNOWN,
        WriteAuditEventType.WRITE_VERIFIED,
        WriteAuditEventType.WRITE_VERIFICATION_FAILED,
    }
)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteAuditError(f"{name} must be a non-empty string")
    ensure_no_credential_material(value, name)
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _optional_hash(value: object, name: str) -> str | None:
    text = _optional_text(value, name)
    if text is None:
        return None
    if (
        not text.startswith("sha256:")
        or len(text) != 71
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise WriteAuditError(f"{name} must be a lowercase SHA-256 value")
    return text


def _freeze_summary(value: object, path: str) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise WriteAuditError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise WriteAuditError(f"{path} keys must be non-empty strings")
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
class WriteAuditIdentity:
    tenant_id: str
    business_operation_id: BusinessOperationId
    node_id: str
    approval_request_id: ApprovalRequestId | None = None
    idempotency_key: IdempotencyKey | None = None
    payload_hash: PayloadHash | None = None
    write_intent_hash: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.tenant_id, "WriteAuditIdentity.tenant_id")
        _required_text(
            self.business_operation_id,
            "WriteAuditIdentity.business_operation_id",
        )
        _required_text(self.node_id, "WriteAuditIdentity.node_id")
        _optional_text(
            self.approval_request_id,
            "WriteAuditIdentity.approval_request_id",
        )
        key = _optional_text(
            self.idempotency_key,
            "WriteAuditIdentity.idempotency_key",
        )
        if key is not None and (
            not key.startswith("idem:sha256:") or len(key) != 76
        ):
            raise WriteAuditError(
                "WriteAuditIdentity.idempotency_key must be an idem SHA-256 value"
            )
        _optional_hash(self.payload_hash, "WriteAuditIdentity.payload_hash")
        _optional_hash(
            self.write_intent_hash,
            "WriteAuditIdentity.write_intent_hash",
        )

    @classmethod
    def from_write_outcome(
        cls,
        identity: WriteOutcomeIdentity,
    ) -> WriteAuditIdentity:
        if not isinstance(identity, WriteOutcomeIdentity):
            raise TypeError("identity must be a WriteOutcomeIdentity")
        return cls(
            tenant_id=identity.tenant_id,
            business_operation_id=identity.business_operation_id,
            node_id=identity.node_id,
            approval_request_id=identity.approval_request_id,
            idempotency_key=identity.idempotency_key,
            payload_hash=identity.payload_hash,
            write_intent_hash=identity.write_intent_hash,
        )

    def to_data(self) -> dict[str, str | None]:
        return {
            "business_operation_id": self.business_operation_id,
            "approval_request_id": self.approval_request_id,
            "node_id": self.node_id,
            "idempotency_key": self.idempotency_key,
            "payload_hash": self.payload_hash,
            "write_intent_hash": self.write_intent_hash,
        }


@dataclass(frozen=True, slots=True)
class ProtectedWriteAuditEvidence:
    data_classification: DataClassification
    protected_payload_ref: str | None = None
    display_safe_summary: Mapping[str, object] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    outcome_status: str | None = None
    reason_code: str | None = None
    business_reference: str | None = None
    provider_transaction_id: str | None = None
    verification_tool_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.data_classification, DataClassification):
            raise TypeError("data_classification must be a DataClassification")
        for name in (
            "protected_payload_ref",
            "outcome_status",
            "reason_code",
            "business_reference",
            "provider_transaction_id",
            "verification_tool_id",
        ):
            _optional_text(getattr(self, name), name)
        if not isinstance(self.display_safe_summary, Mapping):
            raise TypeError("display_safe_summary must be a mapping")
        ensure_no_credential_material(
            self.display_safe_summary,
            "display_safe_summary",
        )
        object.__setattr__(
            self,
            "display_safe_summary",
            _freeze_summary(self.display_safe_summary, "display_safe_summary"),
        )
        if (
            not isinstance(self.evidence_refs, tuple)
            or not all(
                isinstance(item, str) and bool(item.strip())
                for item in self.evidence_refs
            )
        ):
            raise TypeError("evidence_refs must be a tuple of non-empty strings")
        if len(self.evidence_refs) > MAX_AUDIT_EVIDENCE_REFS:
            raise WriteAuditError(
                f"evidence_refs cannot exceed {MAX_AUDIT_EVIDENCE_REFS} items"
            )
        ensure_no_credential_material(self.evidence_refs, "evidence_refs")

    def to_data(self) -> dict[str, Any]:
        return {
            "data_classification": self.data_classification.value,
            "protected_payload_ref": self.protected_payload_ref,
            "display_safe_summary": _summary_to_data(
                self.display_safe_summary
            ),
            "evidence_refs": list(self.evidence_refs),
            "outcome_status": self.outcome_status,
            "reason_code": self.reason_code,
            "business_reference": self.business_reference,
            "provider_transaction_id": self.provider_transaction_id,
            "verification_tool_id": self.verification_tool_id,
        }


class WriteAuditRecorder:
    """Persists v0.2 WRITE events through the existing Audit sink boundary."""

    __slots__ = ("_recorder",)

    def __init__(self, recorder: AuditRecorder) -> None:
        if not isinstance(recorder, AuditRecorder):
            raise TypeError("recorder must be an AuditRecorder")
        _required_text(recorder.execution_id, "AuditRecorder.execution_id")
        _optional_hash(recorder.semantic_hash, "AuditRecorder.semantic_hash")
        if recorder.tenant_id is None:
            raise WriteAuditError("v0.2 WRITE audit requires a tenant identity")
        self._recorder = recorder

    def record(
        self,
        event_type: WriteAuditEventType,
        identity: WriteAuditIdentity,
        evidence: ProtectedWriteAuditEvidence,
    ) -> None:
        if not isinstance(event_type, WriteAuditEventType):
            raise TypeError("event_type must be a WriteAuditEventType")
        if not isinstance(identity, WriteAuditIdentity):
            raise TypeError("identity must be a WriteAuditIdentity")
        if not isinstance(evidence, ProtectedWriteAuditEvidence):
            raise TypeError("evidence must be ProtectedWriteAuditEvidence")
        if identity.tenant_id != self._recorder.tenant_id:
            raise WriteAuditError("audit identity tenant does not match recorder")
        self._validate_event_bindings(event_type, identity)
        payload = {
            **identity.to_data(),
            "skill_semantic_hash": self._recorder.semantic_hash,
            **evidence.to_data(),
        }
        self._recorder.emit(
            event_type.value,
            payload,
            evidence.data_classification,
            redacted_metadata=payload,
        )

    def record_verification(
        self,
        result: VerificationResult,
        *,
        data_classification: DataClassification,
        display_safe_summary: Mapping[str, object] | None = None,
        protected_payload_ref: str | None = None,
    ) -> VerificationSummary:
        if not isinstance(result, VerificationResult):
            raise TypeError("result must be a VerificationResult")
        summary = result.to_summary()
        if result.status is VerificationStatus.NOT_REQUESTED:
            return summary
        event_type = (
            WriteAuditEventType.WRITE_VERIFIED
            if result.status is VerificationStatus.VERIFIED
            else WriteAuditEventType.WRITE_VERIFICATION_FAILED
        )
        evidence_refs = (
            () if result.evidence_ref is None else (result.evidence_ref,)
        )
        self.record(
            event_type,
            WriteAuditIdentity.from_write_outcome(result.identity),
            ProtectedWriteAuditEvidence(
                data_classification=data_classification,
                protected_payload_ref=protected_payload_ref,
                display_safe_summary=display_safe_summary or {},
                evidence_refs=evidence_refs,
                outcome_status=result.status.value,
                reason_code=result.reason_code,
                business_reference=result.business_reference,
                verification_tool_id=result.verification_tool_id,
            ),
        )
        return summary

    @staticmethod
    def _validate_event_bindings(
        event_type: WriteAuditEventType,
        identity: WriteAuditIdentity,
    ) -> None:
        if identity.payload_hash is None or identity.write_intent_hash is None:
            raise WriteAuditError(
                f"{event_type.value} requires payload and write intent hashes"
            )
        if (
            event_type in _APPROVAL_BOUND_EVENTS
            and identity.approval_request_id is None
        ):
            raise WriteAuditError(
                f"{event_type.value} requires approval_request_id"
            )
        if event_type in _IDEMPOTENCY_BOUND_EVENTS:
            if identity.approval_request_id is None:
                raise WriteAuditError(
                    f"{event_type.value} requires approval_request_id"
                )
            if identity.idempotency_key is None:
                raise WriteAuditError(
                    f"{event_type.value} requires idempotency_key"
                )
