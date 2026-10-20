from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from .core import BusinessOperationId, IdempotencyKey, PayloadHash, ToolCapability
from .data_protection import ensure_no_credential_material
from .ir import canonical_json
from .runtime_models import ExecutionContext
from .tools import ToolContract
from .write_intent import WriteIntent


_IDEMPOTENCY_KEY_DOMAIN = b"NSL-IDEMPOTENCY-KEY-V1\0"
_REQUIRED_SCOPE = "TOOL_AND_TENANT"
_REQUIRED_DUPLICATE_BEHAVIOR = "RETURN_ORIGINAL_RESULT"
_REQUIRED_CONFLICT_BEHAVIOR = "IDEMPOTENCY_CONFLICT"


class IdempotencyError(ValueError):
    """Base class for controlled idempotency contract failures."""


class IdempotencyRetentionError(IdempotencyError):
    detail_code = "IDEMPOTENCY_RETENTION_UNAVAILABLE"


class IdempotencyConflictError(IdempotencyError):
    detail_code = "IDEMPOTENCY_CONFLICT"


class IdempotencyStateError(IdempotencyError):
    detail_code = "IDEMPOTENCY_STATE_INVALID"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdempotencyError(f"{name} must be a non-empty string")
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
        raise IdempotencyError(f"{name} must be a lowercase SHA-256 value")
    return text


def _utc_instant(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise IdempotencyError(f"{name} must be timezone-aware UTC")
    if value.utcoffset() != UTC.utcoffset(value):
        raise IdempotencyError(f"{name} must use UTC")
    return value


class IdempotencyKeyFactory:
    __slots__ = ()

    def create(
        self,
        tenant_id: str,
        business_operation_id: BusinessOperationId,
        node_id: str,
    ) -> IdempotencyKey:
        identity = {
            "tenant_id": _required_text(tenant_id, "tenant_id"),
            "business_operation_id": _required_text(
                business_operation_id,
                "business_operation_id",
            ),
            "write_node_id": _required_text(node_id, "write_node_id"),
        }
        digest = sha256(
            _IDEMPOTENCY_KEY_DOMAIN + canonical_json(identity)
        ).hexdigest()
        return IdempotencyKey("idem:sha256:" + digest)


@dataclass(frozen=True, slots=True)
class IdempotencyIdentity:
    tenant_id: str
    business_operation_id: BusinessOperationId
    write_node_id: str
    idempotency_key: IdempotencyKey
    payload_hash: PayloadHash

    def __post_init__(self) -> None:
        _required_text(self.tenant_id, "IdempotencyIdentity.tenant_id")
        _required_text(
            self.business_operation_id,
            "IdempotencyIdentity.business_operation_id",
        )
        _required_text(
            self.write_node_id,
            "IdempotencyIdentity.write_node_id",
        )
        key = _required_text(
            self.idempotency_key,
            "IdempotencyIdentity.idempotency_key",
        )
        if not key.startswith("idem:sha256:") or len(key) != 76:
            raise IdempotencyError(
                "IdempotencyIdentity.idempotency_key must be an idem SHA-256 value"
            )
        _sha256_text(
            self.payload_hash,
            "IdempotencyIdentity.payload_hash",
        )


def idempotency_identity(
    intent: WriteIntent,
    key_factory: IdempotencyKeyFactory | None = None,
) -> IdempotencyIdentity:
    if not isinstance(intent, WriteIntent):
        raise TypeError("intent must be a WriteIntent")
    factory = key_factory or IdempotencyKeyFactory()
    key = factory.create(
        intent.tenant_id,
        intent.business_operation_id,
        intent.node_id,
    )
    return IdempotencyIdentity(
        tenant_id=intent.tenant_id,
        business_operation_id=intent.business_operation_id,
        write_node_id=intent.node_id,
        idempotency_key=key,
        payload_hash=intent.payload_hash,
    )


@dataclass(frozen=True, slots=True)
class ProviderIdempotencyEvidence:
    tool_id: str
    contract_hash: str
    retention_policy: str
    provider_guarantee_ref: str
    verified_at: datetime
    guaranteed_until: datetime
    durable_duplicate_guarantee: bool

    def __post_init__(self) -> None:
        for name in (
            "tool_id",
            "retention_policy",
            "provider_guarantee_ref",
        ):
            _required_text(
                getattr(self, name),
                f"ProviderIdempotencyEvidence.{name}",
            )
        _sha256_text(
            self.contract_hash,
            "ProviderIdempotencyEvidence.contract_hash",
        )
        _utc_instant(
            self.verified_at,
            "ProviderIdempotencyEvidence.verified_at",
        )
        _utc_instant(
            self.guaranteed_until,
            "ProviderIdempotencyEvidence.guaranteed_until",
        )
        if self.guaranteed_until <= self.verified_at:
            raise IdempotencyError(
                "provider idempotency guarantee must end after verification"
            )
        if type(self.durable_duplicate_guarantee) is not bool:
            raise TypeError("durable_duplicate_guarantee must be a bool")


class IdempotencyRetentionValidator:
    __slots__ = ()

    def validate(
        self,
        *,
        intent: WriteIntent,
        contract: ToolContract,
        evidence: ProviderIdempotencyEvidence,
        now: datetime,
        required_until: datetime,
    ) -> ProviderIdempotencyEvidence:
        if not isinstance(intent, WriteIntent):
            raise TypeError("intent must be a WriteIntent")
        if not isinstance(contract, ToolContract):
            raise TypeError("contract must be a ToolContract")
        if not isinstance(evidence, ProviderIdempotencyEvidence):
            raise TypeError("evidence must be ProviderIdempotencyEvidence")
        current = _utc_instant(now, "now")
        horizon = _utc_instant(required_until, "required_until")
        if horizon <= current:
            raise IdempotencyRetentionError(
                "required idempotency horizon must end after now"
            )
        if contract.tool_id != intent.tool_id or evidence.tool_id != intent.tool_id:
            raise IdempotencyRetentionError(
                "idempotency evidence tool binding mismatch"
            )
        if evidence.contract_hash != contract.contract_hash:
            raise IdempotencyRetentionError(
                "idempotency evidence contract binding mismatch"
            )
        semantics = contract.write_semantics
        if contract.capability is not ToolCapability.WRITE or semantics is None:
            raise IdempotencyRetentionError(
                "idempotency requires a WRITE Tool Contract"
            )
        idempotency = semantics.idempotency
        if (
            idempotency.required is not True
            or idempotency.scope != _REQUIRED_SCOPE
            or idempotency.duplicate_behavior != _REQUIRED_DUPLICATE_BEHAVIOR
            or idempotency.payload_conflict_behavior
            != _REQUIRED_CONFLICT_BEHAVIOR
        ):
            raise IdempotencyRetentionError(
                "Tool Contract lacks the required durable duplicate semantics"
            )
        if evidence.retention_policy != idempotency.retention:
            raise IdempotencyRetentionError(
                "idempotency retention policy binding mismatch"
            )
        if not evidence.durable_duplicate_guarantee:
            raise IdempotencyRetentionError(
                "provider durable duplicate guarantee is absent"
            )
        if evidence.verified_at > current:
            raise IdempotencyRetentionError(
                "provider idempotency evidence is from the future"
            )
        if current >= evidence.guaranteed_until:
            raise IdempotencyRetentionError(
                "provider idempotency guarantee has expired"
            )
        if evidence.guaranteed_until < horizon:
            raise IdempotencyRetentionError(
                "provider idempotency guarantee does not cover recovery horizon"
            )
        return evidence


class IdempotencyDisposition(StrEnum):
    CREATED = "CREATED"
    REUSED = "REUSED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class IdempotencyEvidenceRecord:
    tenant_id: str
    business_operation_id: BusinessOperationId
    write_node_id: str
    tool_id: str
    contract_hash: str
    idempotency_key: IdempotencyKey
    payload_hash: PayloadHash
    provider_guarantee_ref: str
    retained_until: datetime

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "business_operation_id",
            "write_node_id",
            "tool_id",
            "provider_guarantee_ref",
        ):
            _required_text(
                getattr(self, name),
                f"IdempotencyEvidenceRecord.{name}",
            )
        _sha256_text(
            self.contract_hash,
            "IdempotencyEvidenceRecord.contract_hash",
        )
        key = _required_text(
            self.idempotency_key,
            "IdempotencyEvidenceRecord.idempotency_key",
        )
        if not key.startswith("idem:sha256:") or len(key) != 76:
            raise IdempotencyError(
                "IdempotencyEvidenceRecord.idempotency_key must be an idem SHA-256 value"
            )
        _sha256_text(
            self.payload_hash,
            "IdempotencyEvidenceRecord.payload_hash",
        )
        _utc_instant(
            self.retained_until,
            "IdempotencyEvidenceRecord.retained_until",
        )


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    disposition: IdempotencyDisposition
    evidence: IdempotencyEvidenceRecord
    business_result_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, IdempotencyDisposition):
            raise TypeError("disposition must be an IdempotencyDisposition")
        if not isinstance(self.evidence, IdempotencyEvidenceRecord):
            raise TypeError("evidence must be an IdempotencyEvidenceRecord")
        if self.business_result_ref is not None:
            _required_text(
                self.business_result_ref,
                "IdempotencyReservation.business_result_ref",
            )
        if (
            self.disposition is IdempotencyDisposition.CONFLICT
            and self.business_result_ref is not None
        ):
            raise IdempotencyError(
                "a conflicting reservation cannot expose a business result"
            )


class IdempotencyEvidencePort(Protocol):
    """Atomically create, reuse, or reject durable idempotency evidence."""

    async def reserve(
        self,
        evidence: IdempotencyEvidenceRecord,
    ) -> IdempotencyReservation: ...


class IdempotencyCoordinator:
    __slots__ = ("_port", "_key_factory", "_retention_validator")

    def __init__(
        self,
        port: IdempotencyEvidencePort,
        *,
        key_factory: IdempotencyKeyFactory | None = None,
        retention_validator: IdempotencyRetentionValidator | None = None,
    ) -> None:
        if not hasattr(port, "reserve"):
            raise TypeError("port must provide reserve()")
        self._port = port
        self._key_factory = key_factory or IdempotencyKeyFactory()
        self._retention_validator = (
            retention_validator or IdempotencyRetentionValidator()
        )

    async def reserve(
        self,
        *,
        intent: WriteIntent,
        context: ExecutionContext,
        contract: ToolContract,
        provider_evidence: ProviderIdempotencyEvidence,
        now: datetime,
        required_until: datetime,
    ) -> IdempotencyReservation:
        if not isinstance(intent, WriteIntent):
            raise TypeError("intent must be a WriteIntent")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if context.tenant_id != intent.tenant_id:
            raise IdempotencyStateError("idempotency tenant binding mismatch")
        context_business_id = context.identity.business_operation_id
        if context_business_id is None:
            raise IdempotencyStateError(
                "execution context lacks a business operation id"
            )
        if context_business_id != intent.business_operation_id:
            raise IdempotencyStateError(
                "idempotency business operation binding mismatch"
            )

        evidence = self._retention_validator.validate(
            intent=intent,
            contract=contract,
            evidence=provider_evidence,
            now=now,
            required_until=required_until,
        )
        identity = idempotency_identity(intent, self._key_factory)
        previous_key = context.idempotency.keys_by_write_node.get(intent.node_id)
        if previous_key is not None and previous_key != identity.idempotency_key:
            raise IdempotencyStateError(
                "checkpoint idempotency key does not match semantic identity"
            )
        previous_payload = context.idempotency.payload_hashes_by_write_node.get(
            intent.node_id
        )
        if previous_payload is not None and previous_payload != intent.payload_hash:
            raise IdempotencyConflictError(
                "idempotency key is already bound to a different payload"
            )

        record = IdempotencyEvidenceRecord(
            tenant_id=intent.tenant_id,
            business_operation_id=intent.business_operation_id,
            write_node_id=intent.node_id,
            tool_id=intent.tool_id,
            contract_hash=contract.contract_hash,
            idempotency_key=(
                previous_key
                if previous_key is not None
                else identity.idempotency_key
            ),
            payload_hash=intent.payload_hash,
            provider_guarantee_ref=evidence.provider_guarantee_ref,
            retained_until=evidence.guaranteed_until,
        )
        reservation = await self._port.reserve(record)
        if not isinstance(reservation, IdempotencyReservation):
            raise IdempotencyStateError(
                "idempotency evidence port returned an invalid reservation"
            )
        if reservation.evidence != record:
            raise IdempotencyStateError(
                "idempotency evidence port returned mismatched evidence"
            )
        if reservation.disposition is IdempotencyDisposition.CONFLICT:
            raise IdempotencyConflictError(
                "idempotency key is already bound to a different payload"
            )

        context.idempotency.keys_by_write_node[intent.node_id] = (
            record.idempotency_key
        )
        context.idempotency.payload_hashes_by_write_node[intent.node_id] = (
            record.payload_hash
        )
        return reservation
