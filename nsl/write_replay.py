from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any

from .approval import ApprovalEvidence
from .audit import SnapshotRef
from .core import (
    ApprovalDecision,
    ApprovalRequestId,
    BusinessOperationId,
    CommitState,
    DataClassification,
    IdempotencyKey,
    PayloadHash,
)
from .data_protection import ensure_no_credential_material
from .ir import canonical_json
from .protected_snapshots import (
    PROTECTED_CLASSIFICATIONS,
    ProtectedSnapshotStore,
)
from .security import ExecutionPrincipal
from .verification import VerificationResult
from .write_execution import (
    WriteAttemptRecord,
    WriteOutcomeIdentity,
    WriteOutcomeRecord,
)


WRITE_REPLAY_SCHEMA_VERSION = "1.0"
MAX_WRITE_REPLAY_RECORDS = 4


class WriteReplayError(RuntimeError):
    """Invalid protected v0.2 WRITE Replay evidence."""


class WriteReplayEvidenceKind(StrEnum):
    APPROVAL = "APPROVAL"
    WRITE_REQUEST = "WRITE_REQUEST"
    COMMIT_OUTCOME = "COMMIT_OUTCOME"
    VERIFICATION_OUTCOME = "VERIFICATION_OUTCOME"


_KIND_ORDER = {
    kind: index for index, kind in enumerate(WriteReplayEvidenceKind, start=1)
}
_ALLOWED_KIND_SEQUENCES = frozenset(
    {
        (WriteReplayEvidenceKind.APPROVAL,),
        (
            WriteReplayEvidenceKind.APPROVAL,
            WriteReplayEvidenceKind.WRITE_REQUEST,
            WriteReplayEvidenceKind.COMMIT_OUTCOME,
        ),
        tuple(WriteReplayEvidenceKind),
    }
)


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteReplayError(f"{name} must be a non-empty string")
    ensure_no_credential_material(value, name)
    return value


def _sha256_text(value: object, name: str) -> str:
    text = _required_text(value, name)
    if (
        not text.startswith("sha256:")
        or len(text) != 71
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise WriteReplayError(f"{name} must be a lowercase SHA-256 value")
    return text


def _snapshot_ref_data(reference: SnapshotRef) -> dict[str, Any]:
    return {
        "snapshot_id": reference.snapshot_id,
        "tenant_id": reference.tenant_id,
        "classification": reference.classification.value,
        "value_hash": reference.value_hash,
        "expires_at": reference.expires_at,
    }


@dataclass(frozen=True, slots=True)
class WriteReplayIdentity:
    tenant_id: str
    execution_id: str
    business_operation_id: BusinessOperationId
    skill_semantic_hash: str
    node_id: str
    approval_request_id: ApprovalRequestId
    payload_hash: PayloadHash
    write_intent_hash: str
    idempotency_key: IdempotencyKey | None

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "execution_id",
            "business_operation_id",
            "node_id",
            "approval_request_id",
        ):
            _required_text(getattr(self, name), f"WriteReplayIdentity.{name}")
        _sha256_text(
            self.skill_semantic_hash,
            "WriteReplayIdentity.skill_semantic_hash",
        )
        _sha256_text(self.payload_hash, "WriteReplayIdentity.payload_hash")
        _sha256_text(
            self.write_intent_hash,
            "WriteReplayIdentity.write_intent_hash",
        )
        if self.idempotency_key is not None:
            key = _required_text(
                self.idempotency_key,
                "WriteReplayIdentity.idempotency_key",
            )
            if not key.startswith("idem:sha256:") or len(key) != 76:
                raise WriteReplayError(
                    "WriteReplayIdentity.idempotency_key must be an idem SHA-256 value"
                )

    def to_data(self) -> dict[str, str | None]:
        return {
            "tenant_id": self.tenant_id,
            "execution_id": self.execution_id,
            "business_operation_id": self.business_operation_id,
            "skill_semantic_hash": self.skill_semantic_hash,
            "node_id": self.node_id,
            "approval_request_id": self.approval_request_id,
            "payload_hash": self.payload_hash,
            "write_intent_hash": self.write_intent_hash,
            "idempotency_key": self.idempotency_key,
        }


def _record_material(
    sequence: int,
    kind: WriteReplayEvidenceKind,
    identity: WriteReplayIdentity,
    snapshot_ref: SnapshotRef,
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "kind": kind.value,
        "identity": identity.to_data(),
        "snapshot_ref": _snapshot_ref_data(snapshot_ref),
    }


def _validate_record_components(
    sequence: object,
    kind: object,
    identity: object,
    snapshot_ref: object,
) -> None:
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence <= 0
    ):
        raise WriteReplayError("record sequence must be a positive integer")
    if not isinstance(kind, WriteReplayEvidenceKind):
        raise TypeError("record kind must be a WriteReplayEvidenceKind")
    if not isinstance(identity, WriteReplayIdentity):
        raise TypeError("record identity must be a WriteReplayIdentity")
    if not isinstance(snapshot_ref, SnapshotRef):
        raise TypeError("record snapshot_ref must be a SnapshotRef")


@dataclass(frozen=True, slots=True)
class WriteReplaySnapshotRecord:
    sequence: int
    kind: WriteReplayEvidenceKind
    identity: WriteReplayIdentity
    snapshot_ref: SnapshotRef
    record_hash: str

    def __post_init__(self) -> None:
        _validate_record_components(
            self.sequence,
            self.kind,
            self.identity,
            self.snapshot_ref,
        )
        if self.snapshot_ref.tenant_id != self.identity.tenant_id:
            raise WriteReplayError("record contains a cross-tenant snapshot reference")
        if self.snapshot_ref.classification not in PROTECTED_CLASSIFICATIONS:
            raise WriteReplayError("Replay evidence must use a protected snapshot")
        _sha256_text(self.snapshot_ref.value_hash, "snapshot_ref.value_hash")
        expected = self.compute_hash(
            self.sequence,
            self.kind,
            self.identity,
            self.snapshot_ref,
        )
        if self.record_hash != expected:
            raise WriteReplayError("Replay evidence record hash mismatch")

    @classmethod
    def create(
        cls,
        sequence: int,
        kind: WriteReplayEvidenceKind,
        identity: WriteReplayIdentity,
        snapshot_ref: SnapshotRef,
    ) -> WriteReplaySnapshotRecord:
        _validate_record_components(sequence, kind, identity, snapshot_ref)
        return cls(
            sequence,
            kind,
            identity,
            snapshot_ref,
            cls.compute_hash(sequence, kind, identity, snapshot_ref),
        )

    @staticmethod
    def compute_hash(
        sequence: int,
        kind: WriteReplayEvidenceKind,
        identity: WriteReplayIdentity,
        snapshot_ref: SnapshotRef,
    ) -> str:
        return "sha256:" + sha256(
            b"NSL-WRITE-REPLAY-RECORD-V1\0"
            + canonical_json(
                _record_material(sequence, kind, identity, snapshot_ref)
            )
        ).hexdigest()

    def to_data(self) -> dict[str, Any]:
        return {
            **_record_material(
                self.sequence,
                self.kind,
                self.identity,
                self.snapshot_ref,
            ),
            "record_hash": self.record_hash,
        }


def _bundle_material(
    schema_version: str,
    identity: WriteReplayIdentity,
    records: tuple[WriteReplaySnapshotRecord, ...],
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "source_identity": identity.to_data(),
        "records": [record.to_data() for record in records],
    }


@dataclass(frozen=True, slots=True)
class ProtectedWriteReplayBundle:
    schema_version: str
    source_identity: WriteReplayIdentity
    records: tuple[WriteReplaySnapshotRecord, ...]
    manifest_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != WRITE_REPLAY_SCHEMA_VERSION:
            raise WriteReplayError("unsupported WRITE Replay schema version")
        if not isinstance(self.source_identity, WriteReplayIdentity):
            raise TypeError("source_identity must be a WriteReplayIdentity")
        if not isinstance(self.records, tuple) or not all(
            isinstance(record, WriteReplaySnapshotRecord)
            for record in self.records
        ):
            raise TypeError("records must be a tuple of Replay snapshot records")
        if not self.records:
            raise WriteReplayError("WRITE Replay bundle requires evidence records")
        if len(self.records) > MAX_WRITE_REPLAY_RECORDS:
            raise WriteReplayError(
                f"WRITE Replay bundle cannot exceed {MAX_WRITE_REPLAY_RECORDS} records"
            )
        if tuple(record.sequence for record in self.records) != tuple(
            range(1, len(self.records) + 1)
        ):
            raise WriteReplayError("Replay evidence sequence is not contiguous")
        kinds = tuple(record.kind for record in self.records)
        if len(set(kinds)) != len(kinds):
            raise WriteReplayError("Replay evidence kinds must be unique")
        if tuple(sorted(kinds, key=_KIND_ORDER.__getitem__)) != kinds:
            raise WriteReplayError("Replay evidence ordering is invalid")
        if kinds not in _ALLOWED_KIND_SEQUENCES:
            raise WriteReplayError("Replay evidence set is incomplete")
        if any(record.identity != self.source_identity for record in self.records):
            raise WriteReplayError("Replay record identity does not match bundle")
        expected = self.compute_manifest_hash(
            self.schema_version,
            self.source_identity,
            self.records,
        )
        if self.manifest_hash != expected:
            raise WriteReplayError("WRITE Replay manifest hash mismatch")

    @classmethod
    def create(
        cls,
        identity: WriteReplayIdentity,
        records: tuple[WriteReplaySnapshotRecord, ...],
    ) -> ProtectedWriteReplayBundle:
        return cls(
            WRITE_REPLAY_SCHEMA_VERSION,
            identity,
            records,
            cls.compute_manifest_hash(
                WRITE_REPLAY_SCHEMA_VERSION,
                identity,
                records,
            ),
        )

    @staticmethod
    def compute_manifest_hash(
        schema_version: str,
        identity: WriteReplayIdentity,
        records: tuple[WriteReplaySnapshotRecord, ...],
    ) -> str:
        return "sha256:" + sha256(
            b"NSL-WRITE-REPLAY-BUNDLE-V1\0"
            + canonical_json(
                _bundle_material(schema_version, identity, records)
            )
        ).hexdigest()

    def to_data(self) -> dict[str, Any]:
        return {
            **_bundle_material(
                self.schema_version,
                self.source_identity,
                self.records,
            ),
            "manifest_hash": self.manifest_hash,
        }


@dataclass(frozen=True, slots=True)
class RehydratedWriteReplayEvidence:
    approval: ApprovalEvidence
    write_request: WriteAttemptRecord | None
    commit_outcome: WriteOutcomeRecord | None
    verification_outcome: VerificationResult | None


class WriteReplayEvidenceComposer:
    __slots__ = ("_snapshots", "_retention_days", "_classification")

    def __init__(
        self,
        snapshots: ProtectedSnapshotStore,
        *,
        retention_days: int,
        classification: DataClassification = DataClassification.CONFIDENTIAL,
    ) -> None:
        if not isinstance(snapshots, ProtectedSnapshotStore):
            raise TypeError("snapshots must be a ProtectedSnapshotStore")
        if (
            not isinstance(retention_days, int)
            or isinstance(retention_days, bool)
            or retention_days <= 0
        ):
            raise WriteReplayError("retention_days must be positive")
        if classification not in PROTECTED_CLASSIFICATIONS:
            raise WriteReplayError(
                "WRITE Replay evidence classification must be protected"
            )
        self._snapshots = snapshots
        self._retention_days = retention_days
        self._classification = classification

    def compose(
        self,
        identity: WriteReplayIdentity,
        *,
        approval: ApprovalEvidence,
        write_request: WriteAttemptRecord | None = None,
        commit_outcome: WriteOutcomeRecord | None = None,
        verification_outcome: VerificationResult | None = None,
    ) -> ProtectedWriteReplayBundle:
        if not isinstance(identity, WriteReplayIdentity):
            raise TypeError("identity must be a WriteReplayIdentity")
        values = self._validate_lifecycle(
            identity,
            approval,
            write_request,
            commit_outcome,
            verification_outcome,
        )
        records: list[WriteReplaySnapshotRecord] = []
        for sequence, (kind, value) in enumerate(values, start=1):
            reference = self._snapshots.put(
                identity.tenant_id,
                value,
                self._classification,
                self._retention_days,
            )
            records.append(
                WriteReplaySnapshotRecord.create(
                    sequence,
                    kind,
                    identity,
                    reference,
                )
            )
        return ProtectedWriteReplayBundle.create(identity, tuple(records))

    @staticmethod
    def _validate_lifecycle(
        identity: WriteReplayIdentity,
        approval: ApprovalEvidence,
        write_request: WriteAttemptRecord | None,
        commit_outcome: WriteOutcomeRecord | None,
        verification_outcome: VerificationResult | None,
    ) -> tuple[tuple[WriteReplayEvidenceKind, object], ...]:
        _validate_domain_value(
            WriteReplayEvidenceKind.APPROVAL,
            approval,
            identity,
        )
        if approval.decision is ApprovalDecision.REJECTED:
            if any(
                value is not None
                for value in (
                    write_request,
                    commit_outcome,
                    verification_outcome,
                )
            ):
                raise WriteReplayError(
                    "rejected approval cannot contain WRITE outcomes"
                )
            return ((WriteReplayEvidenceKind.APPROVAL, approval),)
        if write_request is None or commit_outcome is None:
            raise WriteReplayError(
                "approved Replay requires WRITE request and commit outcome"
            )
        if identity.idempotency_key is None:
            raise WriteReplayError(
                "WRITE Replay identity requires idempotency_key"
            )
        _validate_domain_value(
            WriteReplayEvidenceKind.WRITE_REQUEST,
            write_request,
            identity,
        )
        _validate_domain_value(
            WriteReplayEvidenceKind.COMMIT_OUTCOME,
            commit_outcome,
            identity,
        )
        if commit_outcome.requested_record_hash != write_request.record_hash:
            raise WriteReplayError(
                "commit outcome does not bind the recorded WRITE request"
            )
        values: list[tuple[WriteReplayEvidenceKind, object]] = [
            (WriteReplayEvidenceKind.APPROVAL, approval),
            (WriteReplayEvidenceKind.WRITE_REQUEST, write_request),
            (WriteReplayEvidenceKind.COMMIT_OUTCOME, commit_outcome),
        ]
        if commit_outcome.commit_state is CommitState.COMMITTED:
            if verification_outcome is None:
                raise WriteReplayError(
                    "committed Replay requires a verification outcome"
                )
            _validate_domain_value(
                WriteReplayEvidenceKind.VERIFICATION_OUTCOME,
                verification_outcome,
                identity,
            )
            values.append(
                (
                    WriteReplayEvidenceKind.VERIFICATION_OUTCOME,
                    verification_outcome,
                )
            )
        elif verification_outcome is not None:
            raise WriteReplayError(
                "non-committed Replay cannot contain verification outcome"
            )
        return tuple(values)


class WriteReplayEvidenceLoader:
    __slots__ = ("_snapshots",)

    def __init__(self, snapshots: ProtectedSnapshotStore) -> None:
        if not isinstance(snapshots, ProtectedSnapshotStore):
            raise TypeError("snapshots must be a ProtectedSnapshotStore")
        self._snapshots = snapshots

    def load(
        self,
        bundle: ProtectedWriteReplayBundle,
        principal: ExecutionPrincipal,
    ) -> RehydratedWriteReplayEvidence:
        if not isinstance(bundle, ProtectedWriteReplayBundle):
            raise TypeError("bundle must be a ProtectedWriteReplayBundle")
        if not isinstance(principal, ExecutionPrincipal):
            raise TypeError("principal must be an ExecutionPrincipal")
        principal.validate(require_verified=True)
        if principal.tenant_id != bundle.source_identity.tenant_id:
            raise PermissionError("cross-tenant WRITE Replay is forbidden")
        loaded: dict[WriteReplayEvidenceKind, object] = {}
        for record in bundle.records:
            value = self._snapshots.get(record.snapshot_ref, principal)
            _validate_domain_value(record.kind, value, bundle.source_identity)
            loaded[record.kind] = value
        approval = loaded[WriteReplayEvidenceKind.APPROVAL]
        assert isinstance(approval, ApprovalEvidence)
        write_request = loaded.get(WriteReplayEvidenceKind.WRITE_REQUEST)
        commit_outcome = loaded.get(WriteReplayEvidenceKind.COMMIT_OUTCOME)
        verification = loaded.get(
            WriteReplayEvidenceKind.VERIFICATION_OUTCOME
        )
        WriteReplayEvidenceComposer._validate_lifecycle(
            bundle.source_identity,
            approval,
            write_request,
            commit_outcome,
            verification,
        )
        return RehydratedWriteReplayEvidence(
            approval=approval,
            write_request=write_request,
            commit_outcome=commit_outcome,
            verification_outcome=verification,
        )


def _validate_domain_value(
    kind: WriteReplayEvidenceKind,
    value: object,
    identity: WriteReplayIdentity,
) -> None:
    if kind is WriteReplayEvidenceKind.APPROVAL:
        if not isinstance(value, ApprovalEvidence):
            raise TypeError("approval snapshot must contain ApprovalEvidence")
        bindings = (
            value.tenant_id == identity.tenant_id,
            value.execution_id == identity.execution_id,
            value.business_operation_id == identity.business_operation_id,
            value.skill_semantic_hash == identity.skill_semantic_hash,
            value.approval_request_id == identity.approval_request_id,
            value.payload_hash == identity.payload_hash,
            value.write_intent_hash == identity.write_intent_hash,
        )
    elif kind is WriteReplayEvidenceKind.WRITE_REQUEST:
        if not isinstance(value, WriteAttemptRecord):
            raise TypeError("WRITE request snapshot must contain WriteAttemptRecord")
        bindings = (
            value.tenant_id == identity.tenant_id,
            value.execution_id == identity.execution_id,
            value.business_operation_id == identity.business_operation_id,
            value.node_id == identity.node_id,
            value.approval_request_id == identity.approval_request_id,
            value.idempotency_key == identity.idempotency_key,
            value.payload_hash == identity.payload_hash,
            value.write_intent_hash == identity.write_intent_hash,
        )
    elif kind is WriteReplayEvidenceKind.COMMIT_OUTCOME:
        if not isinstance(value, WriteOutcomeRecord):
            raise TypeError("commit snapshot must contain WriteOutcomeRecord")
        bindings = _outcome_bindings(value.identity, identity)
    elif kind is WriteReplayEvidenceKind.VERIFICATION_OUTCOME:
        if not isinstance(value, VerificationResult):
            raise TypeError(
                "verification snapshot must contain VerificationResult"
            )
        bindings = _outcome_bindings(value.identity, identity)
    else:
        raise TypeError("unsupported WRITE Replay evidence kind")
    if not all(bindings):
        raise WriteReplayError(f"{kind.value} snapshot identity mismatch")


def _outcome_bindings(
    value: WriteOutcomeIdentity,
    identity: WriteReplayIdentity,
) -> tuple[bool, ...]:
    return (
        value.tenant_id == identity.tenant_id,
        value.business_operation_id == identity.business_operation_id,
        value.node_id == identity.node_id,
        value.approval_request_id == identity.approval_request_id,
        value.idempotency_key == identity.idempotency_key,
        value.payload_hash == identity.payload_hash,
        value.write_intent_hash == identity.write_intent_hash,
    )
