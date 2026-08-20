from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path

import pytest

from nsl.audit import (
    AUDIT_SCHEMA_VERSION,
    RUNTIME_VERSION,
    AuditEvent,
    AuditRecorder,
    InMemoryAuditSink,
    InMemorySnapshotStore,
)
from nsl.audit_persistence import (
    AuditIntegrityError,
    AuditPersistenceError,
    JsonlAuditStore,
)
from nsl.compiler import NslCompiler
from nsl.core import DataClassification, ExecutionStatus
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.security import DataHandlingPolicy
from nsl.tools import ToolExecutionError
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)
POLICY = DataHandlingPolicy(
    max_trace_classification=DataClassification.INTERNAL,
    snapshot_retention_days=30,
)


def _runtime_fixture():
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    return catalog, skill, RuntimeEngine(catalog)


def _request(execution_id: str, principal=None) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=principal or build_principal(),
        data_policy=POLICY,
    )


@pytest.fixture
def persisted_execution(tmp_path):
    catalog, skill, engine = _runtime_fixture()
    store = JsonlAuditStore(tmp_path / "evidence.jsonl")
    snapshots = InMemorySnapshotStore()
    result = asyncio.run(
        engine.execute(
            skill,
            _request("exec-aud-evidence"),
            build_mock_executor(catalog),
            store,
            snapshots,
        )
    )
    events = JsonlAuditStore(store.path).read_execution(
        tenant_id="tenant-nex", execution_id="exec-aud-evidence"
    )
    return result, store.path, events


def test_aud_001_every_execution_outcome_is_persisted(tmp_path) -> None:
    catalog, skill, engine = _runtime_fixture()
    store = JsonlAuditStore(tmp_path / "audit" / "events.jsonl")

    completed = asyncio.run(
        engine.execute(
            skill,
            _request("exec-aud-completed"),
            build_mock_executor(catalog),
            store,
        )
    )
    rejected = asyncio.run(
        engine.execute(
            skill,
            _request("exec-aud-rejected", build_principal(verified=False)),
            build_mock_executor(catalog),
            store,
        )
    )

    class FailingExecutor:
        async def execute(self, request):
            raise ToolExecutionError("UPSTREAM_FAILURE", "upstream failed")

    failed = asyncio.run(
        engine.execute(
            skill,
            _request("exec-aud-failed"),
            FailingExecutor(),
            store,
        )
    )

    assert completed.status is ExecutionStatus.COMPLETED
    assert rejected.status is ExecutionStatus.FAILED
    assert failed.status is ExecutionStatus.TOOL_ERROR

    reopened = JsonlAuditStore(store.path)
    expected_terminals = {
        "exec-aud-completed": "EXECUTION_COMPLETED",
        "exec-aud-rejected": "EXECUTION_REJECTED",
        "exec-aud-failed": "EXECUTION_FAILED",
    }
    for execution_id, terminal in expected_terminals.items():
        events = reopened.read_execution(
            tenant_id="tenant-nex", execution_id=execution_id
        )
        assert events
        assert events[-1].event_type == terminal
        assert [event.sequence for event in events] == list(
            range(1, len(events) + 1)
        )
        assert events[0].previous_event_hash is None
        assert all(
            current.previous_event_hash == previous.event_hash
            for previous, current in zip(events, events[1:])
        )


def test_aud_002_skill_identity_is_recorded_for_success_and_rejection() -> None:
    catalog, skill, engine = _runtime_fixture()
    audit = InMemoryAuditSink()
    asyncio.run(
        engine.execute(
            skill,
            _request("exec-aud-identity"),
            build_mock_executor(catalog),
            audit,
        )
    )

    assert audit.events
    assert all(event.skill_id == skill.skill_id for event in audit.events)
    assert all(event.skill_version == skill.skill_version for event in audit.events)
    started = audit.events[0]
    assert started.payload["skill_id"] == skill.skill_id
    assert started.payload["skill_version"] == skill.skill_version


def test_aud_003_nso_semantic_hash_is_recorded_on_every_event() -> None:
    catalog, skill, engine = _runtime_fixture()
    audit = InMemoryAuditSink()
    asyncio.run(
        engine.execute(
            skill,
            _request("exec-aud-hash"),
            build_mock_executor(catalog),
            audit,
        )
    )

    assert all(event.semantic_hash == skill.semantic_hash for event in audit.events)
    assert audit.events[0].payload["semantic_hash"] == skill.semantic_hash


def test_aud_004_runtime_version_and_schema_are_recorded() -> None:
    catalog, skill, engine = _runtime_fixture()
    audit = InMemoryAuditSink()
    asyncio.run(
        engine.execute(
            skill,
            _request("exec-aud-runtime-version"),
            build_mock_executor(catalog),
            audit,
        )
    )

    assert all(event.schema_version == AUDIT_SCHEMA_VERSION for event in audit.events)
    assert all(event.runtime_version == RUNTIME_VERSION for event in audit.events)
    assert audit.events[0].payload["runtime_version"] == RUNTIME_VERSION


def test_aud_005_input_and_context_evidence_is_persisted(
    persisted_execution,
) -> None:
    result, path, events = persisted_execution
    started = next(event for event in events if event.event_type == "EXECUTION_STARTED")

    assert result.status is ExecutionStatus.COMPLETED
    for name in ("input", "context"):
        evidence = started.payload[name]
        assert evidence["snapshot_ref"].startswith("snapshot-")
        assert evidence["value_hash"].startswith("sha256:")
        assert evidence["classification"] in {
            DataClassification.PUBLIC.value,
            DataClassification.INTERNAL.value,
        }
    persisted_text = path.read_text(encoding="utf-8")
    assert "TEAM-FINANCE" not in persisted_text


def test_aud_006_tool_invocation_input_evidence_is_persisted(
    persisted_execution,
) -> None:
    _, _, events = persisted_execution
    started = [event for event in events if event.event_type == "TOOL_STARTED"]

    assert len(started) == 2
    for event in started:
        evidence = event.payload["input"]
        assert evidence["argument_names"]
        assert evidence["argument_hash"] == event.payload["argument_hash"]
        assert evidence["snapshot_ref"].startswith("snapshot-")
    assert [event.payload["input"]["classification"] for event in started] == [
        DataClassification.INTERNAL.value,
        DataClassification.CONFIDENTIAL.value,
    ]


def test_aud_007_tool_result_snapshot_reference_is_persisted(
    persisted_execution,
) -> None:
    _, _, events = persisted_execution
    completed = [event for event in events if event.event_type == "TOOL_COMPLETED"]

    assert len(completed) == 2
    assert all(event.payload["snapshot_ref"] for event in completed)
    assert all(
        event.payload["output"]["snapshot_ref"] == event.payload["snapshot_ref"]
        for event in completed
    )


def test_aud_008_tool_result_hash_is_persisted(persisted_execution) -> None:
    _, _, events = persisted_execution
    completed = [event for event in events if event.event_type == "TOOL_COMPLETED"]

    assert completed
    for event in completed:
        assert event.payload["result_hash"].startswith("sha256:")
        assert event.payload["output"]["result_hash"] == event.payload["result_hash"]


def test_aud_009_check_result_is_persisted(persisted_execution) -> None:
    result, _, events = persisted_execution
    checks = [event for event in events if event.event_type == "CHECK_COMPLETED"]

    assert len(checks) == len(result.checks) == 1
    assert checks[0].payload["check_id"] == result.checks[0].check_id
    assert checks[0].payload["status"] == result.checks[0].status.value
    assert checks[0].payload["condition_node_id"]
    assert checks[0].payload["presence"] == result.checks[0].presence.value
    assert checks[0].payload["completeness"] == result.checks[0].completeness.value


def test_aud_010_emit_result_uses_protected_snapshot_evidence(
    persisted_execution,
) -> None:
    result, path, events = persisted_execution
    emits = [event for event in events if event.event_type == "EMIT_COMPLETED"]

    assert len(emits) == len(result.outputs) == 1
    payload = emits[0].payload
    assert payload["redacted"] is True
    assert payload["value_hash"].startswith("sha256:")
    assert payload["snapshot_ref"].startswith("snapshot-")
    assert payload["snapshot_hash"].startswith("sha256:")
    assert payload["snapshot_classification"] == "CONFIDENTIAL"
    assert payload["field_names"] == [
        "parent_project",
        "budget",
        "spent",
        "remaining",
        "status",
    ]
    persisted_text = path.read_text(encoding="utf-8")
    assert "87500000" not in persisted_text
    assert "100000000" not in persisted_text


def test_aud_011_error_location_and_cause_are_persisted(tmp_path) -> None:
    catalog, skill, engine = _runtime_fixture()
    store = JsonlAuditStore(tmp_path / "failed.jsonl")

    class FailingExecutor:
        async def execute(self, request):
            raise ToolExecutionError("UPSTREAM_FAILURE", "upstream failed")

    result = asyncio.run(
        engine.execute(
            skill,
            _request("exec-aud-error"),
            FailingExecutor(),
            store,
        )
    )
    events = store.read_execution(
        tenant_id="tenant-nex", execution_id="exec-aud-error"
    )
    tool_failed = next(event for event in events if event.event_type == "TOOL_FAILED")
    execution_failed = events[-1]

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert tool_failed.payload["node_id"]
    assert tool_failed.payload["cause"] == {
        "error_code": "UPSTREAM_FAILURE",
        "detail_code": "UPSTREAM_FAILURE",
    }
    assert execution_failed.event_type == "EXECUTION_FAILED"
    assert execution_failed.payload["location"]["phase"] == "TOOL"
    assert execution_failed.payload["location"]["node_id"]
    assert execution_failed.payload["cause"] == {
        "error_code": "NSL-E4101",
        "detail_code": "UPSTREAM_FAILURE",
    }
    assert result.error.node_id is None


def test_aud_011_preflight_error_records_phase_when_node_is_unavailable() -> None:
    catalog, skill, engine = _runtime_fixture()
    audit = InMemoryAuditSink()
    tampered = replace(skill, semantic_hash="sha256:tampered")

    result = asyncio.run(
        engine.execute(
            tampered,
            _request("exec-aud-preflight-error"),
            build_mock_executor(catalog),
            audit,
        )
    )
    failed = audit.events[-1]

    assert result.status is ExecutionStatus.FAILED
    assert failed.payload["location"] == {"phase": "RUNTIME", "node_id": None}
    assert failed.payload["cause"]["error_code"] == "NSL-E8001"


def test_aud_014_principal_and_allow_decisions_are_persisted(
    persisted_execution,
) -> None:
    _, _, events = persisted_execution
    started = next(event for event in events if event.event_type == "EXECUTION_STARTED")
    completed = events[-1]
    tool_events = [
        event
        for event in events
        if event.event_type in {"TOOL_STARTED", "TOOL_COMPLETED"}
    ]

    assert all(event.tenant_id == "tenant-nex" for event in events)
    assert all(event.subject_id == "user-finance-001" for event in events)
    assert all(event.auth_context_ref == "auth-context-demo-001" for event in events)
    skill_decision = started.payload["authorization_decision_ref"]
    assert skill_decision.startswith("authz-")
    assert completed.payload["authorization_decision_ref"] == skill_decision
    assert tool_events
    assert all(
        event.payload["authorization_decision_ref"].startswith("authz-")
        for event in tool_events
    )


def test_aud_014_deny_decisions_are_persisted_for_skill_and_tool() -> None:
    catalog, skill, engine = _runtime_fixture()

    principal = build_principal()
    no_skill_scope = replace(
        principal,
        scopes=principal.scopes - {"nsl:skill:execute"},
    )
    skill_audit = InMemoryAuditSink()
    skill_result = asyncio.run(
        engine.execute(
            skill,
            _request("exec-aud-skill-deny", no_skill_scope),
            build_mock_executor(catalog),
            skill_audit,
        )
    )
    rejected = skill_audit.events[-1]

    assert skill_result.status is ExecutionStatus.FAILED
    assert rejected.payload["authorization_decision_status"] == "DENY"
    assert rejected.payload["authorization_decision_ref"].startswith("authz-")
    assert rejected.tenant_id == principal.tenant_id
    assert rejected.subject_id == principal.subject_id

    tool_audit = InMemoryAuditSink()
    tool_result = asyncio.run(
        engine.execute(
            skill,
            _request(
                "exec-aud-tool-deny",
                build_principal(include_tool_scope=False),
            ),
            build_mock_executor(catalog),
            tool_audit,
        )
    )
    failed = tool_audit.events[-1]

    assert tool_result.status is ExecutionStatus.FAILED
    assert failed.payload["authorization_decision_ref"].startswith("authz-")
    assert failed.payload["location"]["phase"] == "AUTHORIZATION"


def test_aud_012_013_persistent_audit_keeps_only_protected_evidence(
    persisted_execution,
) -> None:
    _, path, events = persisted_execution
    protected = [
        event
        for event in events
        if event.classification
        in {DataClassification.CONFIDENTIAL, DataClassification.RESTRICTED}
    ]
    persisted_text = path.read_text(encoding="utf-8")

    assert protected
    assert all(event.payload["redacted"] is True for event in protected)
    assert all(event.payload["snapshot_ref"] for event in protected)
    assert all(event.payload["snapshot_hash"].startswith("sha256:") for event in protected)
    assert "PRJ-PARENT-001" not in persisted_text
    assert "87500000" not in persisted_text


def test_jsonl_store_is_tenant_scoped_and_validates_query_boundaries(tmp_path) -> None:
    store = JsonlAuditStore(tmp_path / "events.jsonl")
    recorder = AuditRecorder(
        store,
        POLICY,
        execution_id="exec-query",
        principal=build_principal(),
    )
    recorder.emit("EXECUTION_STARTED", {"state": "started"})

    assert len(
        store.read_execution(tenant_id="tenant-nex", execution_id="exec-query")
    ) == 1
    assert not store.read_execution(
        tenant_id="tenant-other", execution_id="exec-query"
    )
    with pytest.raises(ValueError, match="tenant_id"):
        store.read_execution(tenant_id="", execution_id="exec-query")
    with pytest.raises(ValueError, match="execution_id"):
        store.read_execution(tenant_id="tenant-nex", execution_id="")


def test_jsonl_store_rejects_invalid_append_and_tampering(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    store = JsonlAuditStore(path)
    with pytest.raises(TypeError, match="AuditEvent"):
        store.append(object())

    recorder = AuditRecorder(store, POLICY, execution_id="exec-integrity")
    recorder.emit("EXECUTION_STARTED", {"state": "started"})
    data = json.loads(path.read_text(encoding="utf-8"))
    data["event"]["payload"]["state"] = "tampered"
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(AuditIntegrityError, match="invalid audit record"):
        JsonlAuditStore(path)


def test_audit_event_decoder_rejects_schema_and_value_boundaries() -> None:
    sink = InMemoryAuditSink()
    AuditRecorder(sink, POLICY).emit("TEST", {"value": 1})
    valid = sink.events[0].to_data()

    mutations = (
        ({**valid, "extra": True}, "schema"),
        ({**valid, "schema_version": "2.0"}, "schema version"),
        ({**valid, "execution_id": ""}, "execution_id"),
        ({**valid, "tenant_id": ""}, "tenant_id"),
        ({**valid, "sequence": 0}, "sequence"),
        ({**valid, "payload": []}, "payload"),
        ({**valid, "classification": "SECRET"}, "classification"),
    )
    for data, message in mutations:
        with pytest.raises(ValueError, match=message):
            AuditEvent.from_data(data)

    event = sink.events[0]
    with pytest.raises(ValueError, match="schema version"):
        replace(event, schema_version="2.0").verify()
    with pytest.raises(ValueError, match="retention_days"):
        replace(event, retention_days=0).verify()
    with pytest.raises(ValueError, match="retention_days"):
        AuditEvent.create(
            execution_id=event.execution_id,
            sequence=event.sequence,
            event_type=event.event_type,
            skill_id=event.skill_id,
            skill_version=event.skill_version,
            semantic_hash=event.semantic_hash,
            runtime_version=event.runtime_version,
            tenant_id=event.tenant_id,
            subject_id=event.subject_id,
            auth_context_ref=event.auth_context_ref,
            classification=event.classification,
            payload=event.payload,
            previous_event_hash=event.previous_event_hash,
            retention_days=False,
        )


def test_jsonl_store_reports_empty_truncated_and_unreadable_records(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "events.jsonl"
    assert JsonlAuditStore(path).read_execution(
        tenant_id="tenant-nex", execution_id="missing"
    ) == ()

    path.write_bytes(b"\n")
    with pytest.raises(AuditIntegrityError, match="empty audit record"):
        JsonlAuditStore(path)

    path.write_bytes(b"{")
    with pytest.raises(AuditIntegrityError, match="invalid audit record"):
        JsonlAuditStore(path)

    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError()))
    with pytest.raises(AuditPersistenceError, match="failed to read"):
        JsonlAuditStore(path)


def test_jsonl_store_rejects_append_sequence_hash_and_io_failures(
    tmp_path, monkeypatch
) -> None:
    store = JsonlAuditStore(tmp_path / "events.jsonl")
    sequence_gap = AuditEvent.create(
        execution_id="exec-gap",
        sequence=2,
        event_type="TEST",
        skill_id="skill",
        skill_version="1",
        semantic_hash="sha256:semantic",
        runtime_version=RUNTIME_VERSION,
        tenant_id="tenant-nex",
        subject_id="subject",
        auth_context_ref="auth-ref",
        classification=DataClassification.INTERNAL,
        payload={},
        previous_event_hash=None,
    )
    with pytest.raises(AuditIntegrityError, match="sequence discontinuity"):
        store.append(sequence_gap)

    broken_chain = AuditEvent.create(
        execution_id="exec-chain",
        sequence=1,
        event_type="TEST",
        skill_id="skill",
        skill_version="1",
        semantic_hash="sha256:semantic",
        runtime_version=RUNTIME_VERSION,
        tenant_id="tenant-nex",
        subject_id="subject",
        auth_context_ref="auth-ref",
        classification=DataClassification.INTERNAL,
        payload={},
        previous_event_hash="sha256:not-genesis",
    )
    with pytest.raises(AuditIntegrityError, match="hash chain discontinuity"):
        store.append(broken_chain)

    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError()))
    recorder = AuditRecorder(store, POLICY, execution_id="exec-io")
    with pytest.raises(AuditPersistenceError, match="failed to append"):
        recorder.emit("TEST", {})
    assert recorder.sequence == 0


@pytest.mark.parametrize(
    ("first_sequence", "second_previous", "message"),
    [
        (2, None, "sequence discontinuity"),
        (1, "sha256:not-genesis", "hash chain discontinuity"),
    ],
)
def test_jsonl_store_rejects_persisted_chain_discontinuities(
    tmp_path, first_sequence, second_previous, message
) -> None:
    event = AuditEvent.create(
        execution_id="exec-load-chain",
        sequence=first_sequence,
        event_type="TEST",
        skill_id="skill",
        skill_version="1",
        semantic_hash="sha256:semantic",
        runtime_version=RUNTIME_VERSION,
        tenant_id="tenant-nex",
        subject_id="subject",
        auth_context_ref="auth-ref",
        classification=DataClassification.INTERNAL,
        payload={},
        previous_event_hash=second_previous,
    )
    path = tmp_path / "events.jsonl"
    record = {
        "storage_schema_version": "1.0",
        "stored_at": "2026-01-01T00:00:00+00:00",
        "expires_at": "2026-04-01T00:00:00+00:00",
        "event": event.to_data(),
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(AuditIntegrityError, match=message):
        JsonlAuditStore(path)


def test_jsonl_store_rejects_non_string_query_values(tmp_path) -> None:
    store = JsonlAuditStore(tmp_path / "events.jsonl")
    with pytest.raises(ValueError, match="tenant_id"):
        store.read_execution(tenant_id=None, execution_id="exec")
    with pytest.raises(ValueError, match="execution_id"):
        store.read_execution(tenant_id="tenant", execution_id=None)


def test_jsonl_store_rejects_invalid_storage_envelopes(tmp_path) -> None:
    source_path = tmp_path / "source.jsonl"
    source = JsonlAuditStore(source_path)
    AuditRecorder(source, POLICY, execution_id="exec-storage-schema").emit(
        "TEST", {}
    )
    valid = json.loads(source_path.read_text(encoding="utf-8"))
    mutations = (
        ({**valid, "extra": True}, "invalid audit record"),
        (
            {**valid, "storage_schema_version": "2.0"},
            "invalid audit record",
        ),
        (
            {**valid, "expires_at": "2026-01-01T00:00:00+00:00"},
            "invalid audit record",
        ),
        ({**valid, "stored_at": ""}, "invalid audit record"),
        ({**valid, "stored_at": "not-a-time"}, "invalid audit record"),
        (
            {**valid, "stored_at": "2026-01-01T00:00:00"},
            "invalid audit record",
        ),
    )
    for index, (data, message) in enumerate(mutations):
        path = tmp_path / f"invalid-{index}.jsonl"
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        with pytest.raises(AuditIntegrityError, match=message):
            JsonlAuditStore(path)
