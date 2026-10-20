from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from nsl.approval import ApprovalEvidence
from nsl.audit import SnapshotRef
from nsl.core import (
    ApprovalDecision,
    ApprovalRequestId,
    BusinessOperationId,
    CommitState,
    DataClassification,
    IdempotencyKey,
    PayloadHash,
    ValueEnvelope,
    VerificationStatus,
    domain,
)
from nsl.protected_snapshots import (
    ProtectedSnapshotBlob,
    ProtectedSnapshotStore,
    SnapshotProtectionError,
)
from nsl.verification import VerificationResult
from nsl.validation import (
    BusinessPreconditionEvidence,
    BusinessPreconditionMechanism,
)
from nsl.vertical_slice import build_principal
from nsl.write_execution import (
    WriteAttemptRecord,
    WriteCallRequest,
    WriteOutcomeIdentity,
    WriteOutcomeRecord,
)
from nsl.write_intent import compute_payload_hash, compute_write_intent_hash
from nsl.write_replay import (
    MAX_WRITE_REPLAY_RECORDS,
    WRITE_REPLAY_SCHEMA_VERSION,
    ProtectedWriteReplayBundle,
    WriteReplayError,
    WriteReplayEvidenceComposer,
    WriteReplayEvidenceKind,
    WriteReplayEvidenceLoader,
    WriteReplayIdentity,
    WriteReplaySnapshotRecord,
    _validate_domain_value,
)
from nsl.write_replay_execution import (
    MAX_REPLAY_WRITE_CALLS,
    MockWriteExecutor,
    RecordedWriteExchange,
    RecordedWriteReplayExecutor,
    ReplayControlEvidenceProvider,
    ReplaySideEffectError,
    ReplayWriteDecision,
    ReplayWriteSession,
    ReplayWriteSource,
    _StrictRecordedWriteExecutor,
)


NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
SEMANTIC_HASH = "sha256:" + "1" * 64
PAYLOAD_HASH = PayloadHash("sha256:" + "2" * 64)
INTENT_HASH = "sha256:" + "3" * 64
IDEMPOTENCY_KEY = IdempotencyKey("idem:sha256:" + "4" * 64)


class VaultProtector:
    def __init__(self) -> None:
        self.values: dict[bytes, object] = {}
        self.seal_calls: list[tuple[str, DataClassification]] = []
        self.unseal_calls: list[tuple[str, DataClassification]] = []

    def seal(self, value, *, tenant_id, classification):
        token = f"ciphertext-{len(self.values) + 1}".encode("ascii")
        self.values[token] = deepcopy(value)
        self.seal_calls.append((tenant_id, classification))
        return ProtectedSnapshotBlob(token, "TEST-AEAD", "test-key-0059")

    def unseal(self, blob, *, tenant_id, classification):
        self.unseal_calls.append((tenant_id, classification))
        return deepcopy(self.values[blob.ciphertext])


def _identity(*, with_idempotency: bool = True, **changes) -> WriteReplayIdentity:
    values = {
        "tenant_id": "tenant-a",
        "execution_id": "exec-0059",
        "business_operation_id": BusinessOperationId("business-0059"),
        "skill_semantic_hash": SEMANTIC_HASH,
        "node_id": "write0001",
        "approval_request_id": ApprovalRequestId("approval-request-0059"),
        "payload_hash": PAYLOAD_HASH,
        "write_intent_hash": INTENT_HASH,
        "idempotency_key": IDEMPOTENCY_KEY if with_idempotency else None,
    }
    values.update(changes)
    return WriteReplayIdentity(**values)


def _approval(
    decision: ApprovalDecision = ApprovalDecision.APPROVED,
    **changes,
) -> ApprovalEvidence:
    values = {
        "tenant_id": "tenant-a",
        "execution_id": "exec-0059",
        "business_operation_id": BusinessOperationId("business-0059"),
        "approval_id": "approval-create",
        "approval_request_id": ApprovalRequestId("approval-request-0059"),
        "skill_semantic_hash": SEMANTIC_HASH,
        "decision": decision,
        "approver_id": "manager-0059",
        "decided_at": NOW,
        "expires_at": None,
        "payload_hash": PAYLOAD_HASH,
        "write_intent_hash": INTENT_HASH,
        "evidence_integrity": "approval:evidence:0059",
    }
    values.update(changes)
    return ApprovalEvidence(**values)


def _write_request(**changes) -> WriteAttemptRecord:
    values = {
        "tenant_id": "tenant-a",
        "execution_id": "exec-0059",
        "business_operation_id": BusinessOperationId("business-0059"),
        "invocation_id": "invoke-0059",
        "node_id": "write0001",
        "tool_id": "FINANCE.CREATE_DRAFT",
        "contract_hash": "sha256:" + "5" * 64,
        "approval_request_id": ApprovalRequestId("approval-request-0059"),
        "idempotency_key": IDEMPOTENCY_KEY,
        "payload_hash": PAYLOAD_HASH,
        "write_intent_hash": INTENT_HASH,
        "precondition_evidence_id": "precondition-0059",
        "requested_at": NOW,
    }
    values.update(changes)
    return WriteAttemptRecord(**values)


def _outcome_identity(**changes) -> WriteOutcomeIdentity:
    values = {
        "tenant_id": "tenant-a",
        "business_operation_id": BusinessOperationId("business-0059"),
        "invocation_id": "invoke-0059",
        "node_id": "write0001",
        "tool_id": "FINANCE.CREATE_DRAFT",
        "idempotency_key": IDEMPOTENCY_KEY,
        "payload_hash": PAYLOAD_HASH,
        "approval_request_id": ApprovalRequestId("approval-request-0059"),
        "write_intent_hash": INTENT_HASH,
    }
    values.update(changes)
    return WriteOutcomeIdentity(**values)


def _commit(
    state: CommitState = CommitState.COMMITTED,
    **changes,
) -> WriteOutcomeRecord:
    request = _write_request()
    values = {
        "requested_record_hash": request.record_hash,
        "identity": _outcome_identity(),
        "commit_state": state,
        "reason_code": None if state is CommitState.COMMITTED else "OUTCOME_UNKNOWN",
        "provider_transaction_id": "provider-tx-0059",
        "business_reference": "DRAFT-0059" if state is CommitState.COMMITTED else None,
        "evidence_refs": ("commit:evidence:0059",),
        "recorded_at": NOW,
    }
    values.update(changes)
    return WriteOutcomeRecord(**values)


def _verification(
    status: VerificationStatus = VerificationStatus.VERIFIED,
    **changes,
) -> VerificationResult:
    values = {
        "identity": _outcome_identity(),
        "commit_state": CommitState.COMMITTED,
        "status": status,
        "business_reference": "DRAFT-0059",
        "verification_tool_id": "FINANCE.GET_DRAFT",
        "reason_code": None,
        "evidence_ref": "verification:evidence:0059",
        "observed_at": NOW,
    }
    if status is VerificationStatus.NOT_REQUESTED:
        values.update(
            verification_tool_id=None,
            evidence_ref=None,
            observed_at=None,
        )
    values.update(changes)
    return VerificationResult(**values)


def _components():
    protector = VaultProtector()
    snapshots = ProtectedSnapshotStore(protector, lambda: NOW)
    composer = WriteReplayEvidenceComposer(snapshots, retention_days=30)
    loader = WriteReplayEvidenceLoader(snapshots)
    return protector, snapshots, composer, loader


def _committed_bundle():
    protector, snapshots, composer, loader = _components()
    bundle = composer.compose(
        _identity(),
        approval=_approval(),
        write_request=_write_request(),
        commit_outcome=_commit(),
        verification_outcome=_verification(),
    )
    return protector, snapshots, loader, bundle


def test_rpl_202_complete_evidence_is_sealed_and_rehydrated_without_live_calls() -> None:
    protector, _, loader, bundle = _committed_bundle()

    assert bundle.schema_version == WRITE_REPLAY_SCHEMA_VERSION
    assert len(bundle.records) == MAX_WRITE_REPLAY_RECORDS
    assert tuple(record.kind for record in bundle.records) == tuple(
        WriteReplayEvidenceKind
    )
    assert all(
        classification is DataClassification.CONFIDENTIAL
        for _, classification in protector.seal_calls
    )
    assert len(protector.seal_calls) == 4
    assert "DRAFT-0059" not in str(bundle.to_data())

    loaded = loader.load(bundle, build_principal(tenant_id="tenant-a"))

    assert loaded.approval == _approval()
    assert loaded.write_request == _write_request()
    assert loaded.commit_outcome == _commit()
    assert loaded.verification_outcome == _verification()
    assert len(protector.unseal_calls) == 4


def test_rejected_approval_uses_single_record_and_no_write_identity() -> None:
    protector, _, composer, loader = _components()
    bundle = composer.compose(
        _identity(with_idempotency=False),
        approval=_approval(ApprovalDecision.REJECTED),
    )

    assert [record.kind for record in bundle.records] == [
        WriteReplayEvidenceKind.APPROVAL
    ]
    loaded = loader.load(bundle, build_principal(tenant_id="tenant-a"))
    assert loaded.approval.decision is ApprovalDecision.REJECTED
    assert loaded.write_request is None
    assert loaded.commit_outcome is None
    assert loaded.verification_outcome is None
    assert len(protector.seal_calls) == 1


@pytest.mark.parametrize(
    "state",
    [CommitState.NOT_COMMITTED, CommitState.UNKNOWN_COMMIT_STATE],
)
def test_noncommitted_outcomes_are_rehydrated_exactly_without_verification(state) -> None:
    _, _, composer, loader = _components()
    outcome = _commit(state)
    bundle = composer.compose(
        _identity(),
        approval=_approval(),
        write_request=_write_request(),
        commit_outcome=outcome,
    )

    loaded = loader.load(bundle, build_principal(tenant_id="tenant-a"))
    assert loaded.commit_outcome == outcome
    assert loaded.commit_outcome.commit_state is state
    assert loaded.verification_outcome is None


def test_manifest_is_deterministic_for_the_same_ordered_snapshot_records() -> None:
    _, _, _, bundle = _committed_bundle()
    rebuilt = ProtectedWriteReplayBundle.create(
        bundle.source_identity,
        bundle.records,
    )

    assert rebuilt.manifest_hash == bundle.manifest_hash
    assert rebuilt.to_data() == bundle.to_data()
    with pytest.raises(FrozenInstanceError):
        bundle.manifest_hash = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("approval", "write", "commit", "verification", "message"),
    [
        (_approval(), None, None, None, "requires WRITE request"),
        (_approval(), _write_request(), None, None, "requires WRITE request"),
        (
            _approval(ApprovalDecision.REJECTED),
            _write_request(),
            _commit(),
            None,
            "rejected approval",
        ),
        (_approval(), _write_request(), _commit(), None, "requires a verification"),
        (
            _approval(),
            _write_request(),
            _commit(CommitState.UNKNOWN_COMMIT_STATE),
            _verification(),
            "non-committed",
        ),
    ],
)
def test_partial_or_inconsistent_lifecycle_is_rejected_before_snapshot_writes(
    approval,
    write,
    commit,
    verification,
    message,
) -> None:
    protector, _, composer, _ = _components()
    with pytest.raises(WriteReplayError, match=message):
        composer.compose(
            _identity(),
            approval=approval,
            write_request=write,
            commit_outcome=commit,
            verification_outcome=verification,
        )
    assert protector.seal_calls == []


def test_approved_write_requires_idempotency_and_request_hash_binding() -> None:
    _, _, composer, _ = _components()
    with pytest.raises(WriteReplayError, match="idempotency_key"):
        composer.compose(
            _identity(with_idempotency=False),
            approval=_approval(),
            write_request=_write_request(),
            commit_outcome=_commit(),
            verification_outcome=_verification(),
        )
    with pytest.raises(WriteReplayError, match="recorded WRITE request"):
        composer.compose(
            _identity(),
            approval=_approval(),
            write_request=_write_request(),
            commit_outcome=_commit(requested_record_hash="sha256:" + "9" * 64),
            verification_outcome=_verification(),
        )


@pytest.mark.parametrize(
    ("identity_changes", "message"),
    [
        ({"tenant_id": ""}, "tenant_id"),
        ({"execution_id": ""}, "execution_id"),
        ({"business_operation_id": ""}, "business_operation_id"),
        ({"skill_semantic_hash": "sha256:bad"}, "lowercase SHA-256"),
        ({"node_id": ""}, "node_id"),
        ({"approval_request_id": ""}, "approval_request_id"),
        ({"payload_hash": "sha256:" + "A" * 64}, "lowercase SHA-256"),
        ({"write_intent_hash": "sha256:bad"}, "lowercase SHA-256"),
        ({"idempotency_key": "idem:bad"}, "idem SHA-256"),
    ],
)
def test_replay_identity_rejects_malformed_boundaries(identity_changes, message) -> None:
    with pytest.raises(WriteReplayError, match=message):
        _identity(**identity_changes)


@pytest.mark.parametrize(
    ("value_name", "changed_value"),
    [
        ("approval", _approval(tenant_id="tenant-b")),
        ("write", _write_request(node_id="write0002")),
        (
            "commit",
            _commit(identity=_outcome_identity(payload_hash=PayloadHash("sha256:" + "8" * 64))),
        ),
        (
            "verification",
            _verification(identity=_outcome_identity(write_intent_hash="sha256:" + "8" * 64)),
        ),
    ],
)
def test_each_snapshot_type_must_match_the_complete_source_identity(
    value_name,
    changed_value,
) -> None:
    values = {
        "approval": _approval(),
        "write_request": _write_request(),
        "commit_outcome": _commit(),
        "verification_outcome": _verification(),
    }
    values[
        {
            "approval": "approval",
            "write": "write_request",
            "commit": "commit_outcome",
            "verification": "verification_outcome",
        }[value_name]
    ] = changed_value
    _, _, composer, _ = _components()

    with pytest.raises(WriteReplayError, match="identity mismatch"):
        composer.compose(_identity(), **values)


def test_bundle_rejects_empty_over_limit_duplicate_reordered_and_partial_sets() -> None:
    _, _, _, bundle = _committed_bundle()
    identity = bundle.source_identity

    with pytest.raises(WriteReplayError, match="requires evidence"):
        ProtectedWriteReplayBundle.create(identity, ())
    with pytest.raises(WriteReplayError, match="cannot exceed"):
        ProtectedWriteReplayBundle.create(
            identity,
            bundle.records + (bundle.records[0],),
        )

    approval = bundle.records[0]
    duplicate = WriteReplaySnapshotRecord.create(
        2,
        WriteReplayEvidenceKind.APPROVAL,
        identity,
        approval.snapshot_ref,
    )
    with pytest.raises(WriteReplayError, match="unique"):
        ProtectedWriteReplayBundle.create(identity, (approval, duplicate))

    commit = WriteReplaySnapshotRecord.create(
        2,
        WriteReplayEvidenceKind.COMMIT_OUTCOME,
        identity,
        bundle.records[2].snapshot_ref,
    )
    write = WriteReplaySnapshotRecord.create(
        3,
        WriteReplayEvidenceKind.WRITE_REQUEST,
        identity,
        bundle.records[1].snapshot_ref,
    )
    with pytest.raises(WriteReplayError, match="ordering"):
        ProtectedWriteReplayBundle.create(identity, (approval, commit, write))

    partial = WriteReplaySnapshotRecord.create(
        2,
        WriteReplayEvidenceKind.WRITE_REQUEST,
        identity,
        bundle.records[1].snapshot_ref,
    )
    with pytest.raises(WriteReplayError, match="incomplete"):
        ProtectedWriteReplayBundle.create(identity, (approval, partial))


def test_bundle_rejects_sequence_identity_record_and_manifest_tampering() -> None:
    _, _, _, bundle = _committed_bundle()
    identity = bundle.source_identity
    first = bundle.records[0]

    with pytest.raises(WriteReplayError, match="contiguous"):
        ProtectedWriteReplayBundle.create(
            identity,
            (
                WriteReplaySnapshotRecord.create(
                    2,
                    first.kind,
                    identity,
                    first.snapshot_ref,
                ),
            ),
        )
    other_identity = replace(identity, execution_id="exec-other")
    other_record = WriteReplaySnapshotRecord.create(
        1,
        first.kind,
        other_identity,
        first.snapshot_ref,
    )
    with pytest.raises(WriteReplayError, match="identity does not match"):
        ProtectedWriteReplayBundle.create(identity, (other_record,))
    with pytest.raises(WriteReplayError, match="record hash mismatch"):
        replace(first, record_hash="sha256:" + "0" * 64)
    with pytest.raises(WriteReplayError, match="manifest hash mismatch"):
        replace(bundle, manifest_hash="sha256:" + "0" * 64)
    with pytest.raises(WriteReplayError, match="unsupported"):
        replace(bundle, schema_version="2.0")


def test_snapshot_record_rejects_invalid_metadata_and_unprotected_refs() -> None:
    identity = _identity()
    protected_ref = SnapshotRef(
        "snapshot-1",
        "tenant-a",
        DataClassification.CONFIDENTIAL,
        "sha256:" + "5" * 64,
        NOW.isoformat(),
    )
    with pytest.raises(WriteReplayError, match="positive integer"):
        WriteReplaySnapshotRecord.create(
            0,
            WriteReplayEvidenceKind.APPROVAL,
            identity,
            protected_ref,
        )
    with pytest.raises(TypeError, match="kind"):
        WriteReplaySnapshotRecord.create(  # type: ignore[arg-type]
            1,
            "APPROVAL",
            identity,
            protected_ref,
        )
    with pytest.raises(TypeError, match="identity"):
        WriteReplaySnapshotRecord.create(  # type: ignore[arg-type]
            1,
            WriteReplayEvidenceKind.APPROVAL,
            object(),
            protected_ref,
        )
    with pytest.raises(TypeError, match="snapshot_ref"):
        WriteReplaySnapshotRecord.create(  # type: ignore[arg-type]
            1,
            WriteReplayEvidenceKind.APPROVAL,
            identity,
            object(),
        )
    for changed_ref, message in (
        (replace(protected_ref, tenant_id="tenant-b"), "cross-tenant"),
        (
            replace(protected_ref, classification=DataClassification.INTERNAL),
            "protected snapshot",
        ),
        (replace(protected_ref, value_hash="sha256:bad"), "lowercase SHA-256"),
    ):
        with pytest.raises(WriteReplayError, match=message):
            WriteReplaySnapshotRecord.create(
                1,
                WriteReplayEvidenceKind.APPROVAL,
                identity,
                changed_ref,
            )


def test_loader_fails_closed_for_missing_wrong_type_and_tampered_snapshot() -> None:
    protector, snapshots, loader, bundle = _committed_bundle()
    principal = build_principal(tenant_id="tenant-a")
    first = bundle.records[0]
    missing_ref = replace(first.snapshot_ref, snapshot_id="missing")
    missing_record = WriteReplaySnapshotRecord.create(
        1,
        first.kind,
        bundle.source_identity,
        missing_ref,
    )
    missing_bundle = ProtectedWriteReplayBundle.create(
        bundle.source_identity,
        (missing_record,),
    )
    with pytest.raises(KeyError, match="snapshot not found"):
        loader.load(missing_bundle, principal)

    wrong_ref = snapshots.put(
        "tenant-a",
        _approval(),
        DataClassification.CONFIDENTIAL,
        30,
    )
    wrong_record = WriteReplaySnapshotRecord.create(
        2,
        WriteReplayEvidenceKind.WRITE_REQUEST,
        bundle.source_identity,
        wrong_ref,
    )
    wrong_bundle = ProtectedWriteReplayBundle.create(
        bundle.source_identity,
        (bundle.records[0], wrong_record, bundle.records[2], bundle.records[3]),
    )
    with pytest.raises(TypeError, match="WriteAttemptRecord"):
        loader.load(wrong_bundle, principal)

    blob = snapshots._items[first.snapshot_ref.snapshot_id].protected_blob
    assert blob is not None
    protector.values[blob.ciphertext] = _approval(approver_id="tampered")
    with pytest.raises(SnapshotProtectionError, match="integrity mismatch"):
        loader.load(bundle, principal)


def test_loader_enforces_principal_type_verification_scope_and_tenant() -> None:
    _, _, loader, bundle = _committed_bundle()
    with pytest.raises(TypeError, match="bundle"):
        loader.load(object(), build_principal())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="principal"):
        loader.load(bundle, object())  # type: ignore[arg-type]
    with pytest.raises(PermissionError, match="cross-tenant"):
        loader.load(bundle, build_principal(tenant_id="tenant-b"))
    with pytest.raises(PermissionError, match="replay:read"):
        loader.load(
            bundle,
            build_principal(tenant_id="tenant-a", include_replay_scope=False),
        )


@pytest.mark.parametrize(
    ("snapshots", "retention", "classification", "exception", "message"),
    [
        (object(), 30, DataClassification.CONFIDENTIAL, TypeError, "ProtectedSnapshotStore"),
        (None, 30, DataClassification.CONFIDENTIAL, TypeError, "ProtectedSnapshotStore"),
        ("valid", 0, DataClassification.CONFIDENTIAL, WriteReplayError, "positive"),
        ("valid", True, DataClassification.CONFIDENTIAL, WriteReplayError, "positive"),
        ("valid", 30, DataClassification.INTERNAL, WriteReplayError, "protected"),
    ],
)
def test_composer_constructor_rejects_invalid_resource_boundaries(
    snapshots,
    retention,
    classification,
    exception,
    message,
) -> None:
    selected = (
        ProtectedSnapshotStore(VaultProtector(), lambda: NOW)
        if snapshots == "valid"
        else snapshots
    )
    with pytest.raises(exception, match=message):
        WriteReplayEvidenceComposer(
            selected,  # type: ignore[arg-type]
            retention_days=retention,
            classification=classification,
        )


def test_loader_and_composer_require_exact_public_contract_types() -> None:
    snapshots = ProtectedSnapshotStore(VaultProtector(), lambda: NOW)
    composer = WriteReplayEvidenceComposer(snapshots, retention_days=1)
    with pytest.raises(TypeError, match="identity"):
        composer.compose(object(), approval=_approval())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ProtectedSnapshotStore"):
        WriteReplayEvidenceLoader(object())  # type: ignore[arg-type]


def test_bundle_contract_rejects_malformed_container_types() -> None:
    _, _, _, bundle = _committed_bundle()
    with pytest.raises(TypeError, match="source_identity"):
        replace(bundle, source_identity=object())
    with pytest.raises(TypeError, match="tuple"):
        replace(bundle, records=list(bundle.records))


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        (WriteReplayEvidenceKind.APPROVAL, "ApprovalEvidence"),
        (WriteReplayEvidenceKind.WRITE_REQUEST, "WriteAttemptRecord"),
        (WriteReplayEvidenceKind.COMMIT_OUTCOME, "WriteOutcomeRecord"),
        (WriteReplayEvidenceKind.VERIFICATION_OUTCOME, "VerificationResult"),
    ],
)
def test_snapshot_kind_rejects_wrong_domain_value_type(kind, message) -> None:
    with pytest.raises(TypeError, match=message):
        _validate_domain_value(kind, object(), _identity())

    with pytest.raises(TypeError, match="unsupported"):
        _validate_domain_value("UNKNOWN", object(), _identity())  # type: ignore[arg-type]


def _call_request(
    invocation_id: str = "invoke-0060",
    *,
    idempotency_key: IdempotencyKey | None = None,
) -> WriteCallRequest:
    arguments = {
        "detail": ValueEnvelope.complete(
            {"description": "KTX", "amount": Decimal("84000")},
            domain("DraftPayload"),
            DataClassification.CONFIDENTIAL,
        )
    }
    payload_hash = compute_payload_hash(arguments)
    intent_hash = compute_write_intent_hash(
        skill_semantic_hash=SEMANTIC_HASH,
        write_node_id="write0001",
        tool_id="FINANCE.CREATE_DRAFT",
        payload_hash=payload_hash,
    )
    contract_hash = "sha256:" + "6" * 64
    precondition = BusinessPreconditionEvidence(
        evidence_id="precondition-0060",
        tenant_id="tenant-a",
        business_operation_id=BusinessOperationId("business-0060"),
        write_node_id="write0001",
        tool_id="FINANCE.CREATE_DRAFT",
        contract_hash=contract_hash,
        payload_hash=payload_hash,
        write_intent_hash=intent_hash,
        mechanism=BusinessPreconditionMechanism.ETAG,
        evidence_ref="etag:opaque:0060",
        observed_at=NOW,
        expires_at=datetime(2026, 10, 20, 10, 0, tzinfo=UTC),
        provider_atomic_enforcement=True,
    )
    return WriteCallRequest(
        tenant_id="tenant-a",
        execution_id="exec-0060",
        business_operation_id=BusinessOperationId("business-0060"),
        invocation_id=invocation_id,
        node_id="write0001",
        tool_ref="write_tool",
        tool_id="FINANCE.CREATE_DRAFT",
        contract_hash=contract_hash,
        arguments=arguments,
        idempotency_key=(
            idempotency_key
            or IdempotencyKey("idem:sha256:" + "7" * 64)
        ),
        payload_hash=payload_hash,
        approval_id="APPROVE_DRAFT",
        approval_request_id=ApprovalRequestId("approval-request-0060"),
        write_intent_hash=intent_hash,
        business_preconditions=precondition,
        trace_refs=("trace:0060",),
    )


def _exchange(
    request: WriteCallRequest | None = None,
    *,
    state: CommitState = CommitState.COMMITTED,
    reason_code: str | None = None,
) -> RecordedWriteExchange:
    selected = request or _call_request()
    attempt = WriteAttemptRecord.from_request(selected, NOW)
    outcome = WriteOutcomeRecord(
        requested_record_hash=attempt.record_hash,
        identity=WriteOutcomeIdentity.from_request(selected),
        commit_state=state,
        reason_code=(
            reason_code
            if reason_code is not None
            else (
                None
                if state is CommitState.COMMITTED
                else "PROVIDER_REJECTED"
            )
        ),
        provider_transaction_id="provider-tx-0060",
        business_reference=(
            "DRAFT-0060" if state is CommitState.COMMITTED else None
        ),
        evidence_refs=("commit:evidence:0060",),
        recorded_at=NOW,
    )
    return RecordedWriteExchange(attempt, outcome)


def _scenario_bundle(
    *,
    approval_decision: ApprovalDecision = ApprovalDecision.APPROVED,
    commit_state: CommitState = CommitState.COMMITTED,
    verification_status: VerificationStatus = VerificationStatus.VERIFIED,
    verification_reason: str | None = None,
):
    request = _call_request()
    exchange = _exchange(request, state=commit_state)
    outcome_identity = exchange.outcome.identity
    identity = WriteReplayIdentity(
        tenant_id=request.tenant_id,
        execution_id=request.execution_id,
        business_operation_id=request.business_operation_id,
        skill_semantic_hash=SEMANTIC_HASH,
        node_id=request.node_id,
        approval_request_id=request.approval_request_id,
        payload_hash=request.payload_hash,
        write_intent_hash=request.write_intent_hash,
        idempotency_key=(
            None
            if approval_decision is ApprovalDecision.REJECTED
            else request.idempotency_key
        ),
    )
    approval = ApprovalEvidence(
        tenant_id=request.tenant_id,
        execution_id=request.execution_id,
        business_operation_id=request.business_operation_id,
        approval_id=request.approval_id,
        approval_request_id=request.approval_request_id,
        skill_semantic_hash=SEMANTIC_HASH,
        decision=approval_decision,
        approver_id="manager-0060",
        decided_at=NOW,
        expires_at=None,
        payload_hash=request.payload_hash,
        write_intent_hash=request.write_intent_hash,
        evidence_integrity="approval:evidence:0060",
    )
    protector = VaultProtector()
    snapshots = ProtectedSnapshotStore(protector, lambda: NOW)
    composer = WriteReplayEvidenceComposer(snapshots, retention_days=30)
    if approval_decision is ApprovalDecision.REJECTED:
        bundle = composer.compose(identity, approval=approval)
    else:
        verification = None
        if commit_state is CommitState.COMMITTED:
            verification = VerificationResult(
                identity=outcome_identity,
                commit_state=CommitState.COMMITTED,
                status=verification_status,
                business_reference="DRAFT-0060",
                verification_tool_id=(
                    None
                    if verification_status is VerificationStatus.NOT_REQUESTED
                    else "FINANCE.GET_DRAFT"
                ),
                reason_code=verification_reason,
                evidence_ref=(
                    "verification:evidence:0060"
                    if verification_status
                    in {VerificationStatus.VERIFIED, VerificationStatus.FAILED}
                    else None
                ),
                observed_at=(
                    NOW
                    if verification_status
                    in {VerificationStatus.VERIFIED, VerificationStatus.FAILED}
                    else None
                ),
            )
        bundle = composer.compose(
            identity,
            approval=approval,
            write_request=exchange.request,
            commit_outcome=exchange.outcome,
            verification_outcome=verification,
        )
    return (
        request,
        bundle,
        WriteReplayEvidenceLoader(snapshots),
        protector,
    )


@pytest.mark.parametrize(
    (
        "approval_decision",
        "commit_state",
        "verification_status",
        "verification_reason",
    ),
    [
        (
            ApprovalDecision.APPROVED,
            CommitState.COMMITTED,
            VerificationStatus.VERIFIED,
            None,
        ),
        (
            ApprovalDecision.REJECTED,
            CommitState.NOT_COMMITTED,
            VerificationStatus.NOT_REQUESTED,
            None,
        ),
        (
            ApprovalDecision.APPROVED,
            CommitState.NOT_COMMITTED,
            VerificationStatus.NOT_REQUESTED,
            None,
        ),
        (
            ApprovalDecision.APPROVED,
            CommitState.UNKNOWN_COMMIT_STATE,
            VerificationStatus.NOT_REQUESTED,
            None,
        ),
        (
            ApprovalDecision.APPROVED,
            CommitState.COMMITTED,
            VerificationStatus.FAILED,
            "VALUE_MISMATCH",
        ),
        (
            ApprovalDecision.APPROVED,
            CommitState.COMMITTED,
            VerificationStatus.UNKNOWN,
            "PARTIAL_DATA",
        ),
        (
            ApprovalDecision.APPROVED,
            CommitState.COMMITTED,
            VerificationStatus.UNKNOWN,
            "VERIFICATION_DEPENDENCY_FAILURE",
        ),
    ],
)
def test_rpl_201_203_repeated_replay_has_zero_live_write_calls(
    approval_decision,
    commit_state,
    verification_status,
    verification_reason,
) -> None:
    class InstrumentedLiveWriteProvider:
        def __init__(self) -> None:
            self.call_count = 0

        async def execute(self, request) -> object:
            self.call_count += 1
            raise AssertionError("Live WRITE must never be called")

    live_provider = InstrumentedLiveWriteProvider()
    request, bundle, loader, _ = _scenario_bundle(
        approval_decision=approval_decision,
        commit_state=commit_state,
        verification_status=verification_status,
        verification_reason=verification_reason,
    )
    decisions = []
    for _ in range(2):
        session = ReplayWriteSession.open(
            bundle,
            loader,
            build_principal(tenant_id="tenant-a"),
        )
        assert session.approval().decision is approval_decision
        if approval_decision is ApprovalDecision.APPROVED:
            decision = asyncio.run(session.execute_write(request))
            decisions.append(decision)
            assert decision.outcome.commit_state is commit_state
            assert decision.outcome.identity.idempotency_key == request.idempotency_key
            assert decision.automatic_retry_allowed is False
            assert decision.side_effect_performed is False
            assert decision.reconciliation_required is (
                commit_state is CommitState.UNKNOWN_COMMIT_STATE
            )
            verification = session.verification()
            if commit_state is CommitState.COMMITTED:
                assert verification is not None
                assert verification.status is verification_status
            else:
                assert verification is None
        else:
            assert session.verification() is None
        session.assert_complete()

    assert live_provider.call_count == 0
    assert not decisions or decisions[0] == decisions[-1]


def test_replay_session_cannot_receive_or_retain_a_live_write_provider() -> None:
    request, bundle, loader, _ = _scenario_bundle()
    evidence = loader.load(bundle, build_principal(tenant_id="tenant-a"))

    class LiveWriteProvider:
        async def execute(self, request):
            raise AssertionError

    live = LiveWriteProvider()
    with pytest.raises(TypeError):
        ReplayWriteSession(evidence, live)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="RehydratedWriteReplayEvidence"):
        RecordedWriteReplayExecutor(live)  # type: ignore[arg-type]
    session = ReplayWriteSession(evidence)
    assert not hasattr(session, "delegate")
    assert not hasattr(session, "write_port")
    assert session.write_call_count == 0
    assert asyncio.run(session.execute_write(request)).source is ReplayWriteSource.RECORDED
    assert session.write_call_count == 1


def test_mock_executor_requires_exact_order_and_fails_closed_after_mismatch() -> None:
    first_request = _call_request("invoke-0060-a")
    second_request = _call_request("invoke-0060-b")
    first = _exchange(first_request)
    second = _exchange(second_request)

    executor = MockWriteExecutor((first, second))
    with pytest.raises(ReplaySideEffectError, match="invocation order"):
        asyncio.run(executor.execute(second_request))
    with pytest.raises(ReplaySideEffectError, match="failed closed"):
        asyncio.run(executor.execute(first_request))
    with pytest.raises(ReplaySideEffectError, match="failed closed"):
        executor.assert_consumed()


def test_mock_executor_detects_missing_extra_and_changed_idempotency_calls() -> None:
    request = _call_request()
    exchange = _exchange(request)

    missing = MockWriteExecutor((exchange,))
    with pytest.raises(ReplaySideEffectError, match="did not consume"):
        missing.assert_consumed()

    complete = MockWriteExecutor((exchange,))
    decision = asyncio.run(complete.execute(request))
    assert decision.source is ReplayWriteSource.MOCK
    assert complete.call_count == 1
    complete.assert_consumed()
    with pytest.raises(ReplaySideEffectError, match="more WRITE calls"):
        asyncio.run(complete.execute(request))

    changed_key = _call_request(
        idempotency_key=IdempotencyKey("idem:sha256:" + "8" * 64)
    )
    mismatched = MockWriteExecutor((exchange,))
    with pytest.raises(ReplaySideEffectError, match="invocation order"):
        asyncio.run(mismatched.execute(changed_key))


def test_mock_executor_supports_zero_one_max_and_rejects_max_plus_one() -> None:
    empty = MockWriteExecutor(())
    empty.assert_consumed()

    requests = tuple(
        _call_request(f"invoke-0060-{index:03d}")
        for index in range(MAX_REPLAY_WRITE_CALLS)
    )
    exchanges = tuple(_exchange(request) for request in requests)
    maximum = MockWriteExecutor(exchanges)
    for request in requests:
        asyncio.run(maximum.execute(request))
    assert maximum.call_count == MAX_REPLAY_WRITE_CALLS
    maximum.assert_consumed()

    with pytest.raises(ReplaySideEffectError, match="cannot exceed"):
        MockWriteExecutor(exchanges + (exchanges[-1],))


def test_mock_executor_rejects_duplicate_and_malformed_exchange_sets() -> None:
    exchange = _exchange()
    with pytest.raises(ReplaySideEffectError, match="unique"):
        MockWriteExecutor((exchange, exchange))
    with pytest.raises(TypeError, match="tuple"):
        MockWriteExecutor([exchange])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="RecordedWriteExchange"):
        MockWriteExecutor((object(),))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ReplayWriteSource"):
        _StrictRecordedWriteExecutor((), "MOCK")  # type: ignore[arg-type]


def test_recorded_exchange_and_decision_reject_inconsistent_evidence() -> None:
    exchange = _exchange()
    with pytest.raises(TypeError, match="WriteAttemptRecord"):
        RecordedWriteExchange(object(), exchange.outcome)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="WriteOutcomeRecord"):
        RecordedWriteExchange(exchange.request, object())  # type: ignore[arg-type]
    with pytest.raises(ReplaySideEffectError, match="does not bind"):
        RecordedWriteExchange(
            exchange.request,
            replace(
                exchange.outcome,
                requested_record_hash="sha256:" + "9" * 64,
            ),
        )
    with pytest.raises(ReplaySideEffectError, match="identities differ"):
        RecordedWriteExchange(
            exchange.request,
            replace(
                exchange.outcome,
                identity=replace(
                    exchange.outcome.identity,
                    invocation_id="invoke-other",
                ),
            ),
        )
    with pytest.raises(TypeError, match="WriteCallRequest"):
        exchange.matches(object())  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="WriteOutcomeRecord"):
        ReplayWriteDecision(object(), ReplayWriteSource.MOCK)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ReplayWriteSource"):
        ReplayWriteDecision(exchange.outcome, "MOCK")  # type: ignore[arg-type]
    with pytest.raises(ReplaySideEffectError, match="automatic"):
        ReplayWriteDecision(
            exchange.outcome,
            ReplayWriteSource.MOCK,
            automatic_retry_allowed=True,
        )
    with pytest.raises(ReplaySideEffectError, match="side effect"):
        ReplayWriteDecision(
            exchange.outcome,
            ReplayWriteSource.MOCK,
            side_effect_performed=True,
        )


def test_recorded_executor_and_control_provider_reject_partial_or_wrong_input() -> None:
    _, bundle, loader, _ = _scenario_bundle()
    evidence = loader.load(bundle, build_principal(tenant_id="tenant-a"))
    controls = ReplayControlEvidenceProvider(evidence)
    assert controls.approval() == evidence.approval
    assert controls.verification() == evidence.verification_outcome

    partial = replace(evidence, commit_outcome=None)
    with pytest.raises(ReplaySideEffectError, match="partial"):
        RecordedWriteReplayExecutor(partial)
    for factory in (
        RecordedWriteReplayExecutor,
        ReplayControlEvidenceProvider,
        ReplayWriteSession,
    ):
        with pytest.raises(TypeError, match="RehydratedWriteReplayEvidence"):
            factory(object())  # type: ignore[arg-type]


def test_replay_session_validates_loader_and_write_request_types() -> None:
    request, bundle, loader, _ = _scenario_bundle()
    with pytest.raises(TypeError, match="WriteReplayEvidenceLoader"):
        ReplayWriteSession.open(  # type: ignore[arg-type]
            bundle,
            object(),
            build_principal(tenant_id="tenant-a"),
        )
    session = ReplayWriteSession.open(
        bundle,
        loader,
        build_principal(tenant_id="tenant-a"),
    )
    with pytest.raises(TypeError, match="WriteCallRequest"):
        asyncio.run(session.execute_write(object()))  # type: ignore[arg-type]
