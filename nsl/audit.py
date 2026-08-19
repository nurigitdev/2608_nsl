from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol

from .core import (
    DataClassification,
    classification_allows,
    encode_value,
)
from .data_protection import redact_data
from .ir import canonical_json
from .security import DataHandlingPolicy, ExecutionPrincipal


@dataclass(frozen=True, slots=True)
class AuditEvent:
    sequence: int
    event_type: str
    classification: DataClassification
    payload: dict[str, Any]


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


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self.events.append(event)


class AuditRecorder:
    def __init__(self, sink: AuditSink, policy: DataHandlingPolicy) -> None:
        self.sink = sink
        self.policy = policy
        self.sequence = 0

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        classification: DataClassification = DataClassification.INTERNAL,
    ) -> None:
        self.sequence += 1
        if classification_allows(
            classification, self.policy.max_trace_classification
        ):
            safe_payload = encode_value(redact_data(payload))
        else:
            safe_payload = {
                "redacted": True,
                "value_hash": value_hash(payload),
            }
        self.sink.append(
            AuditEvent(
                sequence=self.sequence,
                event_type=event_type,
                classification=classification,
                payload=safe_payload,
            )
        )


def value_hash(value: Any) -> str:
    return "sha256:" + sha256(canonical_json(encode_value(value))).hexdigest()


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    snapshot_id: str
    tenant_id: str
    classification: DataClassification
    value_hash: str


@dataclass(slots=True)
class _StoredSnapshot:
    tenant_id: str
    classification: DataClassification
    value: Any
    retention_days: int


class InMemorySnapshotStore:
    """Development-only snapshot store with tenant and scope checks."""

    def __init__(self) -> None:
        self._items: dict[str, _StoredSnapshot] = {}
        self._counter = 0

    def put(
        self,
        tenant_id: str,
        value: Any,
        classification: DataClassification,
        retention_days: int,
        hash_material: Any | None = None,
    ) -> SnapshotRef:
        self._counter += 1
        snapshot_id = f"snapshot-{self._counter:06d}"
        digest = value_hash(value if hash_material is None else hash_material)
        self._items[snapshot_id] = _StoredSnapshot(
            tenant_id=tenant_id,
            classification=classification,
            value=deepcopy(value),
            retention_days=retention_days,
        )
        return SnapshotRef(snapshot_id, tenant_id, classification, digest)

    def get(self, reference: SnapshotRef, principal: ExecutionPrincipal) -> Any:
        if "nsl:replay:read" not in principal.scopes:
            raise PermissionError("nsl:replay:read scope is required")
        try:
            item = self._items[reference.snapshot_id]
        except KeyError as error:
            raise KeyError(f"snapshot not found: {reference.snapshot_id}") from error
        if item.tenant_id != principal.tenant_id or reference.tenant_id != principal.tenant_id:
            raise PermissionError("cross-tenant snapshot access is forbidden")
        return deepcopy(item.value)
