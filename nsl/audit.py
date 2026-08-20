from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Callable, Protocol

from .core import (
    DataClassification,
    classification_allows,
    encode_value,
)
from .data_protection import redact_data, redact_text
from .ir import canonical_json
from .security import DataHandlingPolicy, ExecutionPrincipal


AUDIT_SCHEMA_VERSION = "1.1"
RUNTIME_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    schema_version: str
    execution_id: str
    sequence: int
    event_type: str
    skill_id: str
    skill_version: str
    semantic_hash: str
    runtime_version: str
    tenant_id: str | None
    subject_id: str | None
    auth_context_ref: str | None
    retention_days: int
    classification: DataClassification
    payload: dict[str, Any]
    previous_event_hash: str | None
    event_hash: str

    @classmethod
    def create(
        cls,
        *,
        execution_id: str,
        sequence: int,
        event_type: str,
        skill_id: str,
        skill_version: str,
        semantic_hash: str,
        runtime_version: str,
        tenant_id: str | None,
        subject_id: str | None,
        auth_context_ref: str | None,
        classification: DataClassification,
        payload: dict[str, Any],
        previous_event_hash: str | None,
        retention_days: int = 90,
    ) -> AuditEvent:
        if (
            not isinstance(retention_days, int)
            or isinstance(retention_days, bool)
            or retention_days <= 0
        ):
            raise ValueError("audit event retention_days must be positive")
        hash_data = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "execution_id": execution_id,
            "sequence": sequence,
            "event_type": event_type,
            "skill_id": skill_id,
            "skill_version": skill_version,
            "semantic_hash": semantic_hash,
            "runtime_version": runtime_version,
            "tenant_id": tenant_id,
            "subject_id": subject_id,
            "auth_context_ref": auth_context_ref,
            "retention_days": retention_days,
            "classification": classification.value,
            "payload": payload,
            "previous_event_hash": previous_event_hash,
        }
        return cls(
            schema_version=AUDIT_SCHEMA_VERSION,
            execution_id=execution_id,
            sequence=sequence,
            event_type=event_type,
            skill_id=skill_id,
            skill_version=skill_version,
            semantic_hash=semantic_hash,
            runtime_version=runtime_version,
            tenant_id=tenant_id,
            subject_id=subject_id,
            auth_context_ref=auth_context_ref,
            retention_days=retention_days,
            classification=classification,
            payload=payload,
            previous_event_hash=previous_event_hash,
            event_hash=value_hash(hash_data),
        )

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> AuditEvent:
        expected_fields = {
            "schema_version",
            "execution_id",
            "sequence",
            "event_type",
            "skill_id",
            "skill_version",
            "semantic_hash",
            "runtime_version",
            "tenant_id",
            "subject_id",
            "auth_context_ref",
            "retention_days",
            "classification",
            "payload",
            "previous_event_hash",
            "event_hash",
        }
        if not isinstance(data, dict) or set(data) != expected_fields:
            raise ValueError("invalid audit event schema")
        if data["schema_version"] != AUDIT_SCHEMA_VERSION:
            raise ValueError("unsupported audit event schema version")
        for field_name in (
            "execution_id",
            "event_type",
            "skill_id",
            "skill_version",
            "semantic_hash",
            "runtime_version",
            "event_hash",
        ):
            if not isinstance(data[field_name], str) or not data[field_name]:
                raise ValueError(f"audit event {field_name} must be non-empty")
        for field_name in (
            "tenant_id",
            "subject_id",
            "auth_context_ref",
            "previous_event_hash",
        ):
            if data[field_name] is not None and (
                not isinstance(data[field_name], str) or not data[field_name]
            ):
                raise ValueError(f"audit event {field_name} must be null or non-empty")
        if (
            not isinstance(data["sequence"], int)
            or isinstance(data["sequence"], bool)
            or data["sequence"] <= 0
        ):
            raise ValueError("audit event sequence must be a positive integer")
        if not isinstance(data["payload"], dict):
            raise ValueError("audit event payload must be an object")
        try:
            classification = DataClassification(data["classification"])
        except (TypeError, ValueError) as error:
            raise ValueError("invalid audit event classification") from error
        event = cls(
            schema_version=data["schema_version"],
            execution_id=data["execution_id"],
            sequence=data["sequence"],
            event_type=data["event_type"],
            skill_id=data["skill_id"],
            skill_version=data["skill_version"],
            semantic_hash=data["semantic_hash"],
            runtime_version=data["runtime_version"],
            tenant_id=data["tenant_id"],
            subject_id=data["subject_id"],
            auth_context_ref=data["auth_context_ref"],
            retention_days=data["retention_days"],
            classification=classification,
            payload=deepcopy(data["payload"]),
            previous_event_hash=data["previous_event_hash"],
            event_hash=data["event_hash"],
        )
        event.verify()
        return event

    def to_data(self) -> dict[str, Any]:
        return {
            **self._hash_data(),
            "event_hash": self.event_hash,
        }

    def verify(self) -> None:
        if self.schema_version != AUDIT_SCHEMA_VERSION:
            raise ValueError("unsupported audit event schema version")
        if (
            not isinstance(self.retention_days, int)
            or isinstance(self.retention_days, bool)
            or self.retention_days <= 0
        ):
            raise ValueError("audit event retention_days must be positive")
        if self.event_hash != value_hash(self._hash_data()):
            raise ValueError("audit event hash mismatch")

    def _hash_data(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "semantic_hash": self.semantic_hash,
            "runtime_version": self.runtime_version,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "auth_context_ref": self.auth_context_ref,
            "retention_days": self.retention_days,
            "classification": self.classification.value,
            "payload": deepcopy(self.payload),
            "previous_event_hash": self.previous_event_hash,
        }


class AuditSink(Protocol):
    def append(self, event: AuditEvent) -> None:
        ...


class SnapshotStore(Protocol):
    def put(
        self,
        tenant_id: str,
        value: Any,
        classification: DataClassification,
        retention_days: int,
        hash_material: Any | None = None,
    ) -> SnapshotRef:
        ...

    def get(self, reference: SnapshotRef, principal: ExecutionPrincipal) -> Any:
        ...

    def delete(self, reference: SnapshotRef, principal: ExecutionPrincipal) -> None:
        ...

    def purge_expired(self) -> int:
        ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.events.append(event)


class AuditRecorder:
    def __init__(
        self,
        sink: AuditSink,
        policy: DataHandlingPolicy,
        *,
        execution_id: str = "unscoped",
        skill_id: str = "unknown",
        skill_version: str = "unknown",
        semantic_hash: str = "unknown",
        runtime_version: str = RUNTIME_VERSION,
        principal: ExecutionPrincipal | None = None,
    ) -> None:
        self.sink = sink
        self.policy = policy
        self.execution_id = execution_id
        self.skill_id = skill_id
        self.skill_version = skill_version
        self.semantic_hash = semantic_hash
        self.runtime_version = runtime_version
        self.tenant_id = (
            None if principal is None else redact_text(principal.tenant_id)
        )
        self.subject_id = (
            None if principal is None else redact_text(principal.subject_id)
        )
        self.auth_context_ref = (
            None if principal is None else redact_text(principal.auth_context_ref)
        )
        self.sequence = 0
        self.previous_event_hash: str | None = None

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        classification: DataClassification = DataClassification.INTERNAL,
        *,
        secure_snapshot_ref: SnapshotRef | None = None,
        redacted_metadata: dict[str, Any] | None = None,
    ) -> None:
        next_sequence = self.sequence + 1
        if classification_allows(
            classification, self.policy.max_trace_classification
        ):
            safe_payload = encode_value(redact_data(payload))
        else:
            safe_payload = encode_value(redact_data(redacted_metadata or {}))
            safe_payload.update({
                "redacted": True,
                "value_hash": value_hash(payload),
            })
        if secure_snapshot_ref is not None:
            safe_payload.update(
                {
                    "snapshot_ref": secure_snapshot_ref.snapshot_id,
                    "snapshot_hash": secure_snapshot_ref.value_hash,
                    "snapshot_classification": (
                        secure_snapshot_ref.classification.value
                    ),
                }
            )
        event = AuditEvent.create(
            execution_id=self.execution_id,
            sequence=next_sequence,
            event_type=event_type,
            skill_id=self.skill_id,
            skill_version=self.skill_version,
            semantic_hash=self.semantic_hash,
            runtime_version=self.runtime_version,
            tenant_id=self.tenant_id,
            subject_id=self.subject_id,
            auth_context_ref=self.auth_context_ref,
            classification=classification,
            payload=safe_payload,
            previous_event_hash=self.previous_event_hash,
            retention_days=self.policy.audit_retention_days,
        )
        self.sink.append(event)
        self.sequence = next_sequence
        self.previous_event_hash = event.event_hash


def value_hash(value: Any) -> str:
    hashable = asdict(value) if is_dataclass(value) else value
    return "sha256:" + sha256(canonical_json(encode_value(hashable))).hexdigest()


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    snapshot_id: str
    tenant_id: str
    classification: DataClassification
    value_hash: str
    expires_at: str | None = None


@dataclass(slots=True)
class _StoredSnapshot:
    tenant_id: str
    classification: DataClassification
    value: Any
    reference_hash: str
    integrity_hash: str
    retention_days: int
    expires_at: datetime


class InMemorySnapshotStore:
    """Development-only snapshot store with tenant and scope checks."""

    def __init__(self, now_provider: Callable[[], datetime] | None = None) -> None:
        self._items: dict[str, _StoredSnapshot] = {}
        self._counter = 0
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    def put(
        self,
        tenant_id: str,
        value: Any,
        classification: DataClassification,
        retention_days: int,
        hash_material: Any | None = None,
    ) -> SnapshotRef:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("snapshot tenant_id must be non-empty")
        if not isinstance(classification, DataClassification):
            raise ValueError("snapshot classification is invalid")
        if (
            not isinstance(retention_days, int)
            or isinstance(retention_days, bool)
            or retention_days <= 0
        ):
            raise ValueError("snapshot retention_days must be positive")
        self._counter += 1
        snapshot_id = f"snapshot-{self._counter:06d}"
        digest = value_hash(value if hash_material is None else hash_material)
        expires_at = self._now() + timedelta(days=retention_days)
        self._items[snapshot_id] = _StoredSnapshot(
            tenant_id=tenant_id,
            classification=classification,
            value=deepcopy(value),
            reference_hash=digest,
            integrity_hash=value_hash(value),
            retention_days=retention_days,
            expires_at=expires_at,
        )
        return SnapshotRef(
            snapshot_id,
            tenant_id,
            classification,
            digest,
            expires_at.isoformat(),
        )

    def get(self, reference: SnapshotRef, principal: ExecutionPrincipal) -> Any:
        principal.validate(require_verified=True)
        if "nsl:replay:read" not in principal.scopes:
            raise PermissionError("nsl:replay:read scope is required")
        try:
            item = self._items[reference.snapshot_id]
        except KeyError as error:
            raise KeyError(f"snapshot not found: {reference.snapshot_id}") from error
        if item.tenant_id != principal.tenant_id or reference.tenant_id != principal.tenant_id:
            raise PermissionError("cross-tenant snapshot access is forbidden")
        self._validate_reference(reference, item)
        if self._now() >= item.expires_at:
            del self._items[reference.snapshot_id]
            raise KeyError(f"snapshot expired: {reference.snapshot_id}")
        value = deepcopy(item.value)
        if value_hash(value) != item.integrity_hash:
            raise ValueError("snapshot plaintext integrity mismatch")
        return value

    def delete(self, reference: SnapshotRef, principal: ExecutionPrincipal) -> None:
        principal.validate(require_verified=True)
        if "nsl:snapshot:delete" not in principal.scopes:
            raise PermissionError("nsl:snapshot:delete scope is required")
        try:
            item = self._items[reference.snapshot_id]
        except KeyError as error:
            raise KeyError(f"snapshot not found: {reference.snapshot_id}") from error
        if item.tenant_id != principal.tenant_id or reference.tenant_id != principal.tenant_id:
            raise PermissionError("cross-tenant snapshot access is forbidden")
        self._validate_reference(reference, item)
        del self._items[reference.snapshot_id]

    def purge_expired(self) -> int:
        now = self._now()
        expired = [
            snapshot_id
            for snapshot_id, item in self._items.items()
            if now >= item.expires_at
        ]
        for snapshot_id in expired:
            del self._items[snapshot_id]
        return len(expired)

    def _validate_reference(
        self, reference: SnapshotRef, item: _StoredSnapshot
    ) -> None:
        if (
            reference.classification is not item.classification
            or reference.value_hash != item.reference_hash
            or reference.expires_at != item.expires_at.isoformat()
        ):
            raise ValueError("snapshot reference metadata mismatch")

    def _now(self) -> datetime:
        now = self._now_provider()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("snapshot clock must return a timezone-aware datetime")
        return now
