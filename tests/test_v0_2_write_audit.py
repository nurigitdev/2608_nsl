from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
import json

import pytest

from nsl.audit import AuditRecorder, InMemoryAuditSink
from nsl.audit_persistence import AuditIntegrityError, JsonlAuditStore
from nsl.core import (
    ApprovalRequestId,
    BusinessOperationId,
    CommitState,
    Completeness,
    DataClassification,
    ExecutionStatus,
    IdempotencyKey,
    PayloadHash,
    VerificationStatus,
)
from nsl.data_protection import CredentialMaterialError
from nsl.runtime_models import ExecutionResult, ResourceUsage
from nsl.security import DataHandlingPolicy
from nsl.verification import VerificationResult
from nsl.vertical_slice import build_principal
from nsl.write_audit import (
    MAX_AUDIT_EVIDENCE_REFS,
    ProtectedWriteAuditEvidence,
    WriteAuditError,
    WriteAuditEventType,
    WriteAuditIdentity,
    WriteAuditRecorder,
)
from nsl.write_execution import WriteOutcomeIdentity


SEMANTIC_HASH = "sha256:" + "a" * 64


def _outcome_identity(**changes: object) -> WriteOutcomeIdentity:
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "business_operation_id": BusinessOperationId("business-0058"),
        "invocation_id": "invoke-0058",
        "node_id": "write0001",
        "tool_id": "FINANCE.CREATE_DRAFT",
        "idempotency_key": IdempotencyKey("idem:sha256:" + "b" * 64),
        "payload_hash": PayloadHash("sha256:" + "c" * 64),
        "approval_request_id": ApprovalRequestId("approval-request-0058"),
        "write_intent_hash": "sha256:" + "d" * 64,
    }
    values.update(changes)
    return WriteOutcomeIdentity(**values)  # type: ignore[arg-type]


def _identity(*, full: bool = True, **changes: object) -> WriteAuditIdentity:
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "business_operation_id": BusinessOperationId("business-0058"),
        "node_id": "write0001",
        "approval_request_id": (
            ApprovalRequestId("approval-request-0058") if full else None
        ),
        "idempotency_key": (
            IdempotencyKey("idem:sha256:" + "b" * 64) if full else None
        ),
        "payload_hash": PayloadHash("sha256:" + "c" * 64),
        "write_intent_hash": "sha256:" + "d" * 64,
    }
    values.update(changes)
    return WriteAuditIdentity(**values)  # type: ignore[arg-type]


def _evidence(**changes: object) -> ProtectedWriteAuditEvidence:
    values: dict[str, object] = {
        "data_classification": DataClassification.CONFIDENTIAL,
        "protected_payload_ref": "snapshot:write:0058",
        "display_safe_summary": {
            "document_type": "expense-draft",
            "item_count": 2,
            "exchange_rate": 1.25,
            "approved": True,
            "labels": ["finance", None],
        },
        "evidence_refs": ("approval:evidence:0058",),
        "outcome_status": "RECORDED",
    }
    values.update(changes)
    return ProtectedWriteAuditEvidence(**values)  # type: ignore[arg-type]


def _recorder(sink, *, semantic_hash: str = SEMANTIC_HASH) -> WriteAuditRecorder:
    return WriteAuditRecorder(
        AuditRecorder(
            sink,
            DataHandlingPolicy(
                max_trace_classification=DataClassification.INTERNAL,
                audit_retention_days=31,
            ),
            execution_id="exec-0058",
            skill_id="FINANCE.CREATE_EXPENSE_DRAFT",
            skill_version="2.0.0",
            semantic_hash=semantic_hash,
            principal=build_principal(tenant_id="tenant-a"),
        )
    )


def _verification(status: VerificationStatus) -> VerificationResult:
    values: dict[str, object] = {
        "identity": _outcome_identity(),
        "commit_state": CommitState.COMMITTED,
        "status": status,
        "business_reference": "DRAFT-0058",
        "verification_tool_id": "FINANCE.GET_DRAFT",
        "reason_code": None,
        "evidence_ref": "verification:evidence:0058",
        "observed_at": datetime(2026, 10, 20, 8, 30, tzinfo=UTC),
    }
    if status in {VerificationStatus.FAILED, VerificationStatus.UNKNOWN}:
        values["reason_code"] = "VALUE_MISMATCH"
    if status is VerificationStatus.UNKNOWN:
        values["evidence_ref"] = None
        values["observed_at"] = None
    if status is VerificationStatus.NOT_REQUESTED:
        values.update(
            verification_tool_id=None,
            evidence_ref=None,
            observed_at=None,
        )
    return VerificationResult(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "expected_event"),
    [
        (VerificationStatus.VERIFIED, "WRITE_VERIFIED"),
        (VerificationStatus.FAILED, "WRITE_VERIFICATION_FAILED"),
        (VerificationStatus.UNKNOWN, "WRITE_VERIFICATION_FAILED"),
    ],
)
def test_vfy_004_typed_verification_is_persisted_in_result_and_audit(
    tmp_path,
    status,
    expected_event,
) -> None:
    store = JsonlAuditStore(tmp_path / "audit" / "write.jsonl")
    summary = _recorder(store).record_verification(
        _verification(status),
        data_classification=DataClassification.CONFIDENTIAL,
        display_safe_summary={"document_type": "expense-draft"},
        protected_payload_ref="snapshot:write:0058",
    )
    result = ExecutionResult(
        execution_id="exec-0058",
        skill_id="FINANCE.CREATE_EXPENSE_DRAFT",
        skill_version="2.0.0",
        semantic_hash=SEMANTIC_HASH,
        status=ExecutionStatus.COMPLETED,
        completeness=Completeness.COMPLETE,
        checks=(),
        outputs=(),
        resources=ResourceUsage(1, 0, 0, 1),
        verification=summary,
    )

    persisted = JsonlAuditStore(store.path).read_execution(
        tenant_id="tenant-a",
        execution_id="exec-0058",
    )
    assert result.to_data()["verification"]["status"] == status.value
    assert summary.status is status
    assert persisted[0].event_type == expected_event
    assert persisted[0].payload["outcome_status"] == status.value
    assert persisted[0].payload["skill_semantic_hash"] == SEMANTIC_HASH
    assert persisted[0].payload["redacted"] is True


def test_not_requested_verification_returns_typed_result_without_audit() -> None:
    sink = InMemoryAuditSink()
    summary = _recorder(sink).record_verification(
        _verification(VerificationStatus.NOT_REQUESTED),
        data_classification=DataClassification.INTERNAL,
    )

    assert summary.status is VerificationStatus.NOT_REQUESTED
    assert sink.events == []


def test_srs_section_23_complete_event_taxonomy_is_durable_and_hash_chained(
    tmp_path,
) -> None:
    store = JsonlAuditStore(tmp_path / "events.jsonl")
    recorder = _recorder(store)
    full = _identity()
    intent = _identity(full=False)

    for event_type in WriteAuditEventType:
        recorder.record(
            event_type,
            intent
            if event_type is WriteAuditEventType.WRITE_INTENT_CREATED
            else full,
            _evidence(),
        )

    events = JsonlAuditStore(store.path).read_execution(
        tenant_id="tenant-a",
        execution_id="exec-0058",
    )
    assert [event.event_type for event in events] == [
        event.value for event in WriteAuditEventType
    ]
    assert [event.sequence for event in events] == list(range(1, 12))
    assert events[0].previous_event_hash is None
    assert all(
        current.previous_event_hash == previous.event_hash
        for previous, current in zip(events, events[1:])
    )
    for event in events:
        assert event.execution_id == "exec-0058"
        assert event.semantic_hash == SEMANTIC_HASH
        assert event.payload["business_operation_id"] == "business-0058"
        assert event.payload["node_id"] == "write0001"
        assert event.payload["payload_hash"] == "sha256:" + "c" * 64
        assert event.payload["write_intent_hash"] == "sha256:" + "d" * 64


def test_generic_audit_contains_only_protected_write_metadata(tmp_path) -> None:
    store = JsonlAuditStore(tmp_path / "protected.jsonl")
    _recorder(store).record(
        WriteAuditEventType.WRITE_REQUESTED,
        _identity(),
        _evidence(),
    )

    persisted = store.path.read_text(encoding="utf-8")
    payload = json.loads(persisted)["event"]["payload"]
    assert "raw_payload" not in payload
    assert "bank_account" not in persisted
    assert payload["protected_payload_ref"] == "snapshot:write:0058"
    assert payload["data_classification"] == "CONFIDENTIAL"
    assert payload["display_safe_summary"]["item_count"] == 2
    with pytest.raises(TypeError, match="unexpected keyword"):
        ProtectedWriteAuditEvidence(  # type: ignore[call-arg]
            data_classification=DataClassification.INTERNAL,
            raw_payload={"bank_account": "110-000-000"},
        )


def test_audit_identity_is_immutable_and_detached_from_write_outcome() -> None:
    source = _outcome_identity()
    identity = WriteAuditIdentity.from_write_outcome(source)

    assert identity.to_data() == {
        "business_operation_id": "business-0058",
        "approval_request_id": "approval-request-0058",
        "node_id": "write0001",
        "idempotency_key": "idem:sha256:" + "b" * 64,
        "payload_hash": "sha256:" + "c" * 64,
        "write_intent_hash": "sha256:" + "d" * 64,
    }
    with pytest.raises(FrozenInstanceError):
        identity.node_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError, match="WriteOutcomeIdentity"):
        WriteAuditIdentity.from_write_outcome(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"tenant_id": ""}, "tenant_id"),
        ({"business_operation_id": ""}, "business_operation_id"),
        ({"node_id": ""}, "node_id"),
        ({"approval_request_id": ""}, "approval_request_id"),
        ({"idempotency_key": "idem:bad"}, "idem SHA-256"),
        ({"payload_hash": "sha256:bad"}, "lowercase SHA-256"),
        ({"write_intent_hash": "sha256:" + "A" * 64}, "lowercase SHA-256"),
    ],
)
def test_write_audit_identity_rejects_malformed_boundaries(changes, message) -> None:
    with pytest.raises(WriteAuditError, match=message):
        _identity(**changes)


@pytest.mark.parametrize(
    ("event_type", "identity", "message"),
    [
        (
            WriteAuditEventType.WRITE_INTENT_CREATED,
            _identity(payload_hash=None),
            "payload and write intent hashes",
        ),
        (
            WriteAuditEventType.APPROVAL_REQUESTED,
            _identity(approval_request_id=None),
            "approval_request_id",
        ),
        (
            WriteAuditEventType.WRITE_REQUESTED,
            _identity(idempotency_key=None),
            "idempotency_key",
        ),
        (
            WriteAuditEventType.WRITE_COMMIT_CONFIRMED,
            _identity(approval_request_id=None),
            "approval_request_id",
        ),
    ],
)
def test_event_binding_fails_closed_when_required_identity_is_missing(
    event_type,
    identity,
    message,
) -> None:
    with pytest.raises(WriteAuditError, match=message):
        _recorder(InMemoryAuditSink()).record(
            event_type,
            identity,
            _evidence(),
        )


def test_protected_evidence_boundary_and_worst_case_reference_count() -> None:
    maximum = tuple(f"evidence:{index}" for index in range(MAX_AUDIT_EVIDENCE_REFS))
    evidence = _evidence(evidence_refs=maximum)

    assert len(evidence.to_data()["evidence_refs"]) == MAX_AUDIT_EVIDENCE_REFS
    with pytest.raises(WriteAuditError, match="cannot exceed"):
        _evidence(evidence_refs=maximum + ("over-limit",))
    with pytest.raises(TypeError, match="tuple"):
        _evidence(evidence_refs=["not-a-tuple"])
    with pytest.raises(TypeError, match="tuple"):
        _evidence(evidence_refs=("",))
    with pytest.raises(CredentialMaterialError):
        _evidence(evidence_refs=("authorization: Bearer abc",))
    with pytest.raises(TypeError, match="DataClassification"):
        _evidence(data_classification="CONFIDENTIAL")


@pytest.mark.parametrize(
    "summary",
    [
        {"": "invalid-key"},
        {1: "invalid-key-type"},
        {"amount": float("nan")},
        {"amount": float("inf")},
    ],
)
def test_display_safe_summary_rejects_non_json_or_nonfinite_values(summary) -> None:
    with pytest.raises(WriteAuditError):
        _evidence(display_safe_summary=summary)
    with pytest.raises(TypeError, match="non-JSON-safe"):
        _evidence(display_safe_summary={"value": object()})
    with pytest.raises(TypeError, match="mapping"):
        _evidence(display_safe_summary=())


def test_credential_material_is_rejected_from_every_generic_evidence_surface() -> None:
    for changes in (
        {"protected_payload_ref": "authorization: Bearer abc"},
        {"display_safe_summary": {"password": "secret-value"}},
        {"reason_code": "token=abc"},
    ):
        with pytest.raises(CredentialMaterialError):
            _evidence(**changes)


def test_recorder_rejects_tenant_mismatch_invalid_contracts_and_bad_hash() -> None:
    sink = InMemoryAuditSink()
    recorder = _recorder(sink)

    with pytest.raises(WriteAuditError, match="tenant"):
        recorder.record(
            WriteAuditEventType.WRITE_REQUESTED,
            _identity(tenant_id="tenant-b"),
            _evidence(),
        )
    for arguments in (
        ("WRITE_REQUESTED", _identity(), _evidence()),
        (WriteAuditEventType.WRITE_REQUESTED, object(), _evidence()),
        (WriteAuditEventType.WRITE_REQUESTED, _identity(), object()),
    ):
        with pytest.raises(TypeError):
            recorder.record(*arguments)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="VerificationResult"):
        recorder.record_verification(  # type: ignore[arg-type]
            object(),
            data_classification=DataClassification.INTERNAL,
        )
    with pytest.raises(WriteAuditError, match="lowercase SHA-256"):
        _recorder(sink, semantic_hash="unknown")
    with pytest.raises(TypeError, match="AuditRecorder"):
        WriteAuditRecorder(object())  # type: ignore[arg-type]


def test_recorder_requires_tenant_scoped_principal() -> None:
    recorder = AuditRecorder(
        InMemoryAuditSink(),
        DataHandlingPolicy(),
        execution_id="exec-0058",
        semantic_hash=SEMANTIC_HASH,
    )
    with pytest.raises(WriteAuditError, match="tenant identity"):
        WriteAuditRecorder(recorder)


def test_persistence_failure_does_not_advance_sequence_or_publish_summary() -> None:
    class FailingSink:
        def append(self, event) -> None:
            raise OSError("storage unavailable")

    base = AuditRecorder(
        FailingSink(),
        DataHandlingPolicy(),
        execution_id="exec-0058",
        skill_id="FINANCE.CREATE_EXPENSE_DRAFT",
        skill_version="2.0.0",
        semantic_hash=SEMANTIC_HASH,
        principal=build_principal(tenant_id="tenant-a"),
    )
    recorder = WriteAuditRecorder(base)

    with pytest.raises(OSError, match="storage unavailable"):
        recorder.record_verification(
            _verification(VerificationStatus.VERIFIED),
            data_classification=DataClassification.INTERNAL,
        )
    assert base.sequence == 0
    assert base.previous_event_hash is None


def test_persisted_v02_audit_tampering_is_detected_after_restart(tmp_path) -> None:
    store = JsonlAuditStore(tmp_path / "tampered.jsonl")
    _recorder(store).record(
        WriteAuditEventType.WRITE_REQUESTED,
        _identity(),
        _evidence(),
    )
    data = json.loads(store.path.read_text(encoding="utf-8"))
    data["event"]["payload"]["node_id"] = "tampered"
    store.path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(AuditIntegrityError, match="invalid audit record"):
        JsonlAuditStore(store.path)
