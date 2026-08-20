from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Protocol

from .audit import SnapshotRef, value_hash
from .core import DataClassification
from .security import ExecutionPrincipal


PROTECTED_CLASSIFICATIONS = frozenset(
    {DataClassification.CONFIDENTIAL, DataClassification.RESTRICTED}
)


class SnapshotProtectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProtectedSnapshotBlob:
    ciphertext: bytes
    algorithm: str
    key_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.ciphertext, bytes) or not self.ciphertext:
            raise ValueError("protected snapshot ciphertext must be non-empty bytes")
        if (
            not isinstance(self.algorithm, str)
            or not self.algorithm.strip()
            or self.algorithm.upper() == "NONE"
        ):
            raise ValueError("protected snapshot algorithm must identify encryption")
        if not isinstance(self.key_id, str) or not self.key_id.strip():
            raise ValueError("protected snapshot key_id must be non-empty")


class SnapshotProtectionPort(Protocol):
    def seal(
        self,
        value: Any,
        *,
        tenant_id: str,
        classification: DataClassification,
    ) -> ProtectedSnapshotBlob:
        ...

    def unseal(
        self,
        blob: ProtectedSnapshotBlob,
        *,
        tenant_id: str,
        classification: DataClassification,
    ) -> Any:
        ...


@dataclass(slots=True)
class _ProtectedStoredSnapshot:
    tenant_id: str
    classification: DataClassification
    reference_hash: str
    integrity_hash: str
    retention_days: int
    expires_at: datetime
    plaintext: Any | None
    protected_blob: ProtectedSnapshotBlob | None


class ProtectedSnapshotStore:
    """SnapshotStore adapter that keeps high-classification values sealed."""

    def __init__(
        self,
        protector: SnapshotProtectionPort,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.protector = protector
        self._items: dict[str, _ProtectedStoredSnapshot] = {}
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

        protected_blob = None
        plaintext = None
        if classification in PROTECTED_CLASSIFICATIONS:
            try:
                protected_blob = self.protector.seal(
                    value,
                    tenant_id=tenant_id,
                    classification=classification,
                )
            except Exception as error:
                raise SnapshotProtectionError("failed to protect snapshot") from error
            if not isinstance(protected_blob, ProtectedSnapshotBlob):
                raise SnapshotProtectionError(
                    "snapshot protector returned an invalid protected blob"
                )
        else:
            plaintext = deepcopy(value)

        self._counter += 1
        snapshot_id = f"protected-snapshot-{self._counter:06d}"
        expires_at = self._now() + timedelta(days=retention_days)
        reference_hash = value_hash(
            value if hash_material is None else hash_material
        )
        self._items[snapshot_id] = _ProtectedStoredSnapshot(
            tenant_id=tenant_id,
            classification=classification,
            reference_hash=reference_hash,
            integrity_hash=value_hash(value),
            retention_days=retention_days,
            expires_at=expires_at,
            plaintext=plaintext,
            protected_blob=protected_blob,
        )
        return SnapshotRef(
            snapshot_id,
            tenant_id,
            classification,
            reference_hash,
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

        if item.classification in PROTECTED_CLASSIFICATIONS:
            if item.protected_blob is None or item.plaintext is not None:
                raise SnapshotProtectionError("protected snapshot storage invariant failed")
            try:
                value = self.protector.unseal(
                    item.protected_blob,
                    tenant_id=item.tenant_id,
                    classification=item.classification,
                )
            except Exception as error:
                raise SnapshotProtectionError("failed to unprotect snapshot") from error
        else:
            if item.protected_blob is not None:
                raise SnapshotProtectionError("plaintext snapshot storage invariant failed")
            value = deepcopy(item.plaintext)

        if value_hash(value) != item.integrity_hash:
            raise SnapshotProtectionError("snapshot plaintext integrity mismatch")
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
        self,
        reference: SnapshotRef,
        item: _ProtectedStoredSnapshot,
    ) -> None:
        if (
            reference.classification is not item.classification
            or reference.value_hash != item.reference_hash
            or reference.expires_at != item.expires_at.isoformat()
        ):
            raise SnapshotProtectionError("snapshot reference metadata mismatch")

    def _now(self) -> datetime:
        now = self._now_provider()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("snapshot clock must return a timezone-aware datetime")
        return now
