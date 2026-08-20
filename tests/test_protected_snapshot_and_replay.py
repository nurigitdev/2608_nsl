from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
import asyncio

import pytest

from nsl.audit import (
    AuditRecorder,
    InMemoryAuditSink,
    InMemorySnapshotStore,
    SnapshotRef,
    value_hash,
)
from nsl.audit_persistence import AuditPersistenceError, JsonlAuditStore
from nsl.core import DataClassification
from nsl.compiler import NslCompiler
from nsl.protected_snapshots import (
    ProtectedSnapshotBlob,
    ProtectedSnapshotStore,
    SnapshotProtectionError,
)
from nsl.replay import (
    RecordingToolExecutor,
    ReplayBundle,
    compare_replay_values,
    create_replay_bundle,
    replay_and_compare,
    replay_previous_execution,
)
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.security import AuthorizationError, DataHandlingPolicy
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


class VaultProtector:
    def __init__(self) -> None:
        self.values: dict[bytes, object] = {}
        self.seal_calls: list[tuple[str, DataClassification]] = []
        self.unseal_calls: list[tuple[str, DataClassification]] = []

    def seal(self, value, *, tenant_id, classification):
        token = f"ciphertext-{len(self.values) + 1}".encode("ascii")
        self.values[token] = deepcopy(value)
        self.seal_calls.append((tenant_id, classification))
        return ProtectedSnapshotBlob(token, "TEST-AEAD", "test-key-1")

    def unseal(self, blob, *, tenant_id, classification):
        self.unseal_calls.append((tenant_id, classification))
        return deepcopy(self.values[blob.ciphertext])


def lifecycle_store(kind: str, clock: MutableClock):
    if kind == "memory":
        return InMemorySnapshotStore(clock)
    return ProtectedSnapshotStore(VaultProtector(), clock)


@pytest.fixture
def recorded_replay():
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    runtime = RuntimeEngine(catalog)
    principal = build_principal()
    policy = DataHandlingPolicy()
    snapshots = InMemorySnapshotStore()
    provider = build_mock_executor(catalog)
    recorder = RecordingToolExecutor(provider, snapshots, policy)
    request = ExecutionRequest(
        execution_id="exec-original",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=principal,
        data_policy=policy,
    )
    original = asyncio.run(
        runtime.execute(
            skill,
            request,
            recorder,
            InMemoryAuditSink(),
            snapshots,
        )
    )
    bundle = create_replay_bundle(
        skill,
        request,
        original,
        recorder,
        snapshots,
    )
    return {
        "skill": skill,
        "runtime": runtime,
        "principal": principal,
        "policy": policy,
        "snapshots": snapshots,
        "provider": provider,
        "request": request,
        "original": original,
        "bundle": bundle,
    }


@pytest.mark.parametrize(
    "classification",
    [DataClassification.CONFIDENTIAL, DataClassification.RESTRICTED],
)
def test_sec_018_protected_snapshots_are_sealed_at_rest(classification) -> None:
    protector = VaultProtector()
    store = ProtectedSnapshotStore(protector)
    secret = {"project": "PRJ-SECRET", "amount": 100_000_000}

    reference = store.put("tenant-nex", secret, classification, 30)

    assert protector.seal_calls == [("tenant-nex", classification)]
    assert "PRJ-SECRET" not in repr(store._items)
    assert store._items[reference.snapshot_id].plaintext is None
    assert store.get(reference, build_principal()) == secret
    assert protector.unseal_calls == [("tenant-nex", classification)]


@pytest.mark.parametrize(
    "classification",
    [DataClassification.PUBLIC, DataClassification.INTERNAL],
)
def test_unprotected_snapshot_classes_do_not_use_protector(classification) -> None:
    protector = VaultProtector()
    store = ProtectedSnapshotStore(protector)
    value = {"year": 2026}

    reference = store.put("tenant-nex", value, classification, 30)
    value["year"] = 2030

    assert protector.seal_calls == []
    assert store.get(reference, build_principal()) == {"year": 2026}
    assert protector.unseal_calls == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"tenant_id": ""}, "tenant_id"),
        ({"classification": "CONFIDENTIAL"}, "classification"),
        ({"retention_days": 0}, "retention_days"),
        ({"retention_days": True}, "retention_days"),
    ],
)
def test_protected_snapshot_put_rejects_invalid_boundaries(kwargs, message) -> None:
    arguments = {
        "tenant_id": "tenant-nex",
        "value": {"secret": 1},
        "classification": DataClassification.CONFIDENTIAL,
        "retention_days": 30,
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError, match=message):
        ProtectedSnapshotStore(VaultProtector()).put(**arguments)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"ciphertext": b""}, "ciphertext"),
        ({"algorithm": "NONE"}, "algorithm"),
        ({"algorithm": ""}, "algorithm"),
        ({"key_id": ""}, "key_id"),
    ],
)
def test_protected_blob_rejects_unprotected_metadata(kwargs, message) -> None:
    arguments = {
        "ciphertext": b"ciphertext",
        "algorithm": "TEST-AEAD",
        "key_id": "key-1",
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError, match=message):
        ProtectedSnapshotBlob(**arguments)


def test_protected_snapshot_enforces_scope_tenant_and_reference_integrity() -> None:
    store = ProtectedSnapshotStore(VaultProtector())
    reference = store.put(
        "tenant-nex",
        {"secret": 1},
        DataClassification.CONFIDENTIAL,
        30,
    )

    with pytest.raises(PermissionError, match="nsl:replay:read"):
        store.get(reference, replace(build_principal(), scopes=frozenset()))
    with pytest.raises(AuthorizationError, match="verified"):
        store.get(reference, build_principal(verified=False))
    with pytest.raises(PermissionError, match="cross-tenant"):
        store.get(reference, build_principal(tenant_id="tenant-other"))
    with pytest.raises(KeyError, match="snapshot not found"):
        store.get(
            replace(reference, snapshot_id="missing"),
            build_principal(),
        )
    with pytest.raises(SnapshotProtectionError, match="metadata mismatch"):
        store.get(
            replace(reference, value_hash="sha256:tampered"),
            build_principal(),
        )
    with pytest.raises(SnapshotProtectionError, match="metadata mismatch"):
        store.get(
            replace(reference, classification=DataClassification.RESTRICTED),
            build_principal(),
        )


def test_protected_snapshot_wraps_protector_and_storage_failures() -> None:
    class FailingSeal:
        def seal(self, value, *, tenant_id, classification):
            raise RuntimeError("kms unavailable")

    with pytest.raises(SnapshotProtectionError, match="failed to protect"):
        ProtectedSnapshotStore(FailingSeal()).put(
            "tenant-nex",
            {"secret": 1},
            DataClassification.CONFIDENTIAL,
            30,
        )

    class InvalidSeal:
        def seal(self, value, *, tenant_id, classification):
            return b"not-a-protected-blob"

    with pytest.raises(SnapshotProtectionError, match="invalid protected blob"):
        ProtectedSnapshotStore(InvalidSeal()).put(
            "tenant-nex",
            {"secret": 1},
            DataClassification.CONFIDENTIAL,
            30,
        )

    class FailingUnseal(VaultProtector):
        def unseal(self, blob, *, tenant_id, classification):
            raise RuntimeError("kms unavailable")

    failing_unseal = ProtectedSnapshotStore(FailingUnseal())
    reference = failing_unseal.put(
        "tenant-nex",
        {"secret": 1},
        DataClassification.CONFIDENTIAL,
        30,
    )
    with pytest.raises(SnapshotProtectionError, match="failed to unprotect"):
        failing_unseal.get(reference, build_principal())


def test_protected_snapshot_detects_plaintext_and_storage_invariant_tampering() -> None:
    protector = VaultProtector()
    store = ProtectedSnapshotStore(protector)
    protected_ref = store.put(
        "tenant-nex",
        {"secret": 1},
        DataClassification.CONFIDENTIAL,
        30,
    )
    protected_item = store._items[protected_ref.snapshot_id]
    protected_item.plaintext = {"secret": 1}
    with pytest.raises(SnapshotProtectionError, match="storage invariant"):
        store.get(protected_ref, build_principal())

    plain_ref = store.put(
        "tenant-nex",
        {"year": 2026},
        DataClassification.INTERNAL,
        30,
    )
    plain_item = store._items[plain_ref.snapshot_id]
    plain_item.protected_blob = ProtectedSnapshotBlob(
        b"unexpected", "TEST-AEAD", "key-1"
    )
    with pytest.raises(SnapshotProtectionError, match="storage invariant"):
        store.get(plain_ref, build_principal())

    integrity_store = ProtectedSnapshotStore(VaultProtector())
    integrity_ref = integrity_store.put(
        "tenant-nex",
        {"secret": 1},
        DataClassification.CONFIDENTIAL,
        30,
    )
    integrity_store._items[integrity_ref.snapshot_id].integrity_hash = value_hash(
        {"secret": 2}
    )
    with pytest.raises(SnapshotProtectionError, match="plaintext integrity"):
        integrity_store.get(integrity_ref, build_principal())


@pytest.mark.parametrize("kind", ["memory", "protected"])
def test_sec_020_snapshot_retention_enforces_exact_expiration_boundary(kind) -> None:
    clock = MutableClock()
    store = lifecycle_store(kind, clock)
    reference = store.put(
        "tenant-nex",
        {"secret": 1},
        DataClassification.CONFIDENTIAL,
        1,
    )

    assert reference.expires_at == "2026-01-02T00:00:00+00:00"
    clock.advance(days=1, microseconds=-1)
    assert store.get(reference, build_principal()) == {"secret": 1}
    clock.advance(microseconds=1)
    with pytest.raises(KeyError, match="snapshot expired"):
        store.get(reference, build_principal())
    with pytest.raises(KeyError, match="snapshot not found"):
        store.get(reference, build_principal())


@pytest.mark.parametrize("kind", ["memory", "protected"])
def test_sec_020_snapshot_delete_requires_scope_tenant_and_valid_reference(kind) -> None:
    clock = MutableClock()
    store = lifecycle_store(kind, clock)
    reference = store.put(
        "tenant-nex",
        {"secret": 1},
        DataClassification.CONFIDENTIAL,
        30,
    )
    principal = build_principal()

    with pytest.raises(PermissionError, match="nsl:snapshot:delete"):
        store.delete(reference, principal)
    delete_principal = replace(
        principal,
        scopes=principal.scopes | {"nsl:snapshot:delete"},
    )
    with pytest.raises(AuthorizationError, match="verified"):
        store.delete(
            reference,
            replace(
                build_principal(verified=False),
                scopes=delete_principal.scopes,
            ),
        )
    with pytest.raises(PermissionError, match="cross-tenant"):
        store.delete(
            reference,
            replace(delete_principal, tenant_id="tenant-other"),
        )
    error_type = ValueError if kind == "memory" else SnapshotProtectionError
    with pytest.raises(error_type, match="metadata mismatch"):
        store.delete(
            replace(reference, expires_at="2030-01-01T00:00:00+00:00"),
            delete_principal,
        )

    store.delete(reference, delete_principal)
    with pytest.raises(KeyError, match="snapshot not found"):
        store.delete(reference, delete_principal)


@pytest.mark.parametrize("kind", ["memory", "protected"])
def test_sec_020_snapshot_purge_deletes_only_expired_values(kind) -> None:
    clock = MutableClock()
    store = lifecycle_store(kind, clock)
    first = store.put(
        "tenant-nex",
        {"value": 1},
        DataClassification.CONFIDENTIAL,
        1,
    )
    second = store.put(
        "tenant-nex",
        {"value": 2},
        DataClassification.RESTRICTED,
        2,
    )

    assert store.purge_expired() == 0
    clock.advance(days=1)
    assert store.purge_expired() == 1
    assert store.get(second, build_principal()) == {"value": 2}
    with pytest.raises(KeyError, match="snapshot not found"):
        store.get(first, build_principal())
    clock.advance(days=1)
    assert store.purge_expired() == 1
    assert store.purge_expired() == 0


@pytest.mark.parametrize("kind", ["memory", "protected"])
def test_snapshot_lifecycle_rejects_invalid_clock(kind) -> None:
    store = lifecycle_store(kind, MutableClock())
    store._now_provider = lambda: datetime(2026, 1, 1)

    with pytest.raises(ValueError, match="timezone-aware"):
        store.put(
            "tenant-nex",
            {"value": 1},
            DataClassification.INTERNAL,
            1,
        )


def test_in_memory_snapshot_rejects_invalid_put_reference_and_plaintext() -> None:
    clock = MutableClock()
    store = InMemorySnapshotStore(clock)
    invalid_arguments = (
        ("", DataClassification.INTERNAL, 1, "tenant_id"),
        ("tenant-nex", "INTERNAL", 1, "classification"),
        ("tenant-nex", DataClassification.INTERNAL, 0, "retention_days"),
        ("tenant-nex", DataClassification.INTERNAL, True, "retention_days"),
    )
    for tenant_id, classification, retention_days, message in invalid_arguments:
        with pytest.raises(ValueError, match=message):
            store.put(
                tenant_id,
                {"value": 1},
                classification,
                retention_days,
            )

    reference = store.put(
        "tenant-nex",
        {"value": 1},
        DataClassification.INTERNAL,
        1,
    )
    with pytest.raises(ValueError, match="metadata mismatch"):
        store.get(
            replace(reference, value_hash="sha256:tampered"),
            build_principal(),
        )
    store._items[reference.snapshot_id].integrity_hash = value_hash({"value": 2})
    with pytest.raises(ValueError, match="plaintext integrity"):
        store.get(reference, build_principal())


def test_sec_020_audit_execution_delete_and_retention_purge(tmp_path) -> None:
    clock = MutableClock()
    path = tmp_path / "audit.jsonl"
    store = JsonlAuditStore(path, clock)
    first = AuditRecorder(
        store,
        DataHandlingPolicy(audit_retention_days=1),
        execution_id="exec-first",
        principal=build_principal(),
    )
    first.emit("EXECUTION_STARTED", {})
    clock.advance(hours=12)
    first.emit("EXECUTION_COMPLETED", {})

    second = AuditRecorder(
        store,
        DataHandlingPolicy(audit_retention_days=3),
        execution_id="exec-second",
        principal=build_principal(),
    )
    second.emit("EXECUTION_STARTED", {})
    second.emit("EXECUTION_COMPLETED", {})

    clock.advance(hours=12)
    assert store.purge_expired(clock.current) == 0
    assert len(
        store.read_execution(tenant_id="tenant-nex", execution_id="exec-first")
    ) == 2

    clock.advance(hours=12)
    assert store.purge_expired(clock.current) == 2
    assert not store.read_execution(
        tenant_id="tenant-nex", execution_id="exec-first"
    )
    assert len(
        store.read_execution(tenant_id="tenant-nex", execution_id="exec-second")
    ) == 2
    assert store.delete_execution(
        tenant_id="tenant-other", execution_id="exec-second"
    ) == 0
    assert store.delete_execution(
        tenant_id="tenant-nex", execution_id="exec-second"
    ) == 2
    assert path.read_bytes() == b""


def test_audit_retention_rejects_invalid_boundaries_and_rewrite_failure(
    tmp_path, monkeypatch
) -> None:
    clock = MutableClock()
    path = tmp_path / "audit.jsonl"
    store = JsonlAuditStore(path, clock)
    recorder = AuditRecorder(
        store,
        DataHandlingPolicy(audit_retention_days=1),
        execution_id="exec-delete",
        principal=build_principal(),
    )
    recorder.emit("EXECUTION_STARTED", {})

    with pytest.raises(ValueError, match="timezone-aware"):
        store.purge_expired(datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="tenant_id"):
        store.delete_execution(tenant_id="", execution_id="exec-delete")
    with pytest.raises(ValueError, match="execution_id"):
        store.delete_execution(tenant_id="tenant-nex", execution_id="")

    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda self, value: (_ for _ in ()).throw(OSError()),
    )
    with pytest.raises(AuditPersistenceError, match="failed to rewrite"):
        store.delete_execution(
            tenant_id="tenant-nex", execution_id="exec-delete"
        )


def test_audit_event_rejects_invalid_clock_and_retention_boundaries(tmp_path) -> None:
    with pytest.raises(ValueError, match="audit_retention_days"):
        DataHandlingPolicy(audit_retention_days=0)
    recorder = AuditRecorder(
        JsonlAuditStore(
            tmp_path / "invalid-clock-audit.jsonl",
            lambda: datetime(2026, 1, 1),
        ),
        DataHandlingPolicy(),
    )
    with pytest.raises(ValueError, match="audit storage clock"):
        recorder.emit("TEST", {})


def test_rpl_001_previous_execution_is_replayed_through_snapshot_tools(
    recorded_replay,
) -> None:
    fixture = recorded_replay
    audit = InMemoryAuditSink()

    replayed = asyncio.run(
        replay_previous_execution(
            fixture["bundle"],
            fixture["skill"],
            replay_execution_id="exec-replayed",
            principal=fixture["principal"],
            policy=fixture["policy"],
            snapshots=fixture["snapshots"],
            runtime=fixture["runtime"],
            audit_sink=audit,
        )
    )

    assert fixture["bundle"].original_execution_id == "exec-original"
    assert replayed.result.execution_id == "exec-replayed"
    assert replayed.result.semantic_view() == fixture["original"].semantic_view()
    assert replayed.tool_call_count == 2
    assert fixture["provider"].call_count == 2
    assert audit.events[0].event_type == "EXECUTION_STARTED"


def test_replay_orchestration_rejects_invalid_id_and_semantic_identity(
    recorded_replay,
) -> None:
    fixture = recorded_replay
    arguments = {
        "bundle": fixture["bundle"],
        "skill": fixture["skill"],
        "principal": fixture["principal"],
        "policy": fixture["policy"],
        "snapshots": fixture["snapshots"],
        "runtime": fixture["runtime"],
        "audit_sink": InMemoryAuditSink(),
    }
    with pytest.raises(ValueError, match="replay_execution_id"):
        asyncio.run(
            replay_previous_execution(
                replay_execution_id="",
                **arguments,
            )
        )
    with pytest.raises(ValueError, match="semantic hash"):
        asyncio.run(
            replay_previous_execution(
                replay_execution_id="exec-replayed",
                **{
                    **arguments,
                    "bundle": replace(
                        fixture["bundle"], semantic_hash="sha256:other"
                    ),
                },
            )
        )


def test_rpl_005_replay_result_is_compared_automatically(recorded_replay) -> None:
    fixture = recorded_replay

    report = asyncio.run(
        replay_and_compare(
            fixture["bundle"],
            fixture["skill"],
            replay_execution_id="exec-compared",
            principal=fixture["principal"],
            policy=fixture["policy"],
            snapshots=fixture["snapshots"],
            runtime=fixture["runtime"],
            audit_sink=InMemoryAuditSink(),
        )
    )

    assert report.matches is True
    assert report.differences == ()
    assert report.execution.result.semantic_view() == fixture[
        "original"
    ].semantic_view()
    assert fixture["bundle"].original_result_ref is not None
    assert (
        fixture["bundle"].original_result_ref.classification
        is DataClassification.CONFIDENTIAL
    )
    assert report.runtime_version_changed is False


def test_replay_comparison_requires_a_valid_original_result_snapshot(
    recorded_replay,
) -> None:
    fixture = recorded_replay
    arguments = {
        "skill": fixture["skill"],
        "replay_execution_id": "exec-compared",
        "principal": fixture["principal"],
        "policy": fixture["policy"],
        "snapshots": fixture["snapshots"],
        "runtime": fixture["runtime"],
        "audit_sink": InMemoryAuditSink(),
    }

    with pytest.raises(ValueError, match="no original result"):
        asyncio.run(
            replay_and_compare(
                replace(fixture["bundle"], original_result_ref=None),
                **arguments,
            )
        )

    invalid_ref = fixture["snapshots"].put(
        fixture["principal"].tenant_id,
        ["not", "a", "result"],
        DataClassification.INTERNAL,
        30,
    )
    with pytest.raises(TypeError, match="invalid original result"):
        asyncio.run(
            replay_and_compare(
                replace(fixture["bundle"], original_result_ref=invalid_ref),
                **arguments,
            )
        )


def test_rpl_006_replay_mismatch_reports_deterministic_differences(
    recorded_replay,
) -> None:
    fixture = recorded_replay
    altered = deepcopy(fixture["original"].semantic_view())
    altered["status"] = "FAILED"
    altered["original_only"] = True
    del altered["resources"]
    altered["outputs"].append({"unexpected": True})
    altered_ref = fixture["snapshots"].put(
        fixture["principal"].tenant_id,
        altered,
        DataClassification.CONFIDENTIAL,
        30,
    )

    report = asyncio.run(
        replay_and_compare(
            replace(fixture["bundle"], original_result_ref=altered_ref),
            fixture["skill"],
            replay_execution_id="exec-different",
            principal=fixture["principal"],
            policy=fixture["policy"],
            snapshots=fixture["snapshots"],
            runtime=fixture["runtime"],
            audit_sink=InMemoryAuditSink(),
        )
    )

    assert report.matches is False
    assert [difference.path for difference in report.differences] == [
        "/original_only",
        "/outputs/1",
        "/resources",
        "/status",
    ]
    assert report.differences[0].replayed == {"$missing": True}
    assert report.differences[1].replayed == {"$missing": True}
    assert report.differences[2].original == {"$missing": True}
    assert report.differences[3].original == "FAILED"
    assert report.differences[3].replayed == "COMPLETED"


def test_replay_difference_handles_root_lists_missing_items_and_escaped_paths() -> None:
    differences = compare_replay_values(
        {"a/b~": [1], "left": [1, 2]},
        {"a/b~": [2, 3], "left": [1]},
    )

    assert [difference.path for difference in differences] == [
        "/a~1b~0/0",
        "/a~1b~0/1",
        "/left/1",
    ]
    assert differences[1].original == {"$missing": True}
    assert differences[2].replayed == {"$missing": True}
    assert compare_replay_values("original", "replayed") == (
        type(differences[0])("/", "original", "replayed"),
    )


def test_rpl_007_runtime_version_difference_is_recorded(recorded_replay) -> None:
    fixture = recorded_replay
    legacy_bundle = replace(fixture["bundle"], runtime_version="0.0.9")

    report = asyncio.run(
        replay_and_compare(
            legacy_bundle,
            fixture["skill"],
            replay_execution_id="exec-version-change",
            principal=fixture["principal"],
            policy=fixture["policy"],
            snapshots=fixture["snapshots"],
            runtime=fixture["runtime"],
            audit_sink=InMemoryAuditSink(),
            runtime_version="0.1.0",
        )
    )

    assert report.matches is True
    assert report.original_runtime_version == "0.0.9"
    assert report.replay_runtime_version == "0.1.0"
    assert report.runtime_version_changed is True

    with pytest.raises(ValueError, match="runtime_version"):
        asyncio.run(
            replay_and_compare(
                fixture["bundle"],
                fixture["skill"],
                replay_execution_id="exec-invalid-version",
                principal=fixture["principal"],
                policy=fixture["policy"],
                snapshots=fixture["snapshots"],
                runtime=fixture["runtime"],
                audit_sink=InMemoryAuditSink(),
                runtime_version="",
            )
        )


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("semantic_hash", "", "semantic_hash"),
        ("tenant_id", "", "tenant_id"),
        ("original_execution_id", "", "original_execution_id"),
        ("runtime_version", "", "runtime_version"),
    ],
)
def test_replay_bundle_rejects_empty_identity_fields(
    recorded_replay, field_name, value, message
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(recorded_replay["bundle"], **{field_name: value})


def test_replay_bundle_rejects_cross_tenant_references(recorded_replay) -> None:
    fixture = recorded_replay
    with pytest.raises(ValueError, match="cross-tenant"):
        replace(
            fixture["bundle"],
            inputs_ref=replace(
                fixture["bundle"].inputs_ref,
                tenant_id="tenant-other",
            ),
        )


def test_replay_bundle_creation_validates_and_derives_original_identity(
    recorded_replay,
) -> None:
    fixture = recorded_replay
    recorder = RecordingToolExecutor(
        fixture["provider"],
        fixture["snapshots"],
        fixture["policy"],
    )
    derived = create_replay_bundle(
        fixture["skill"],
        fixture["request"],
        fixture["original"],
        recorder,
        fixture["snapshots"],
    )
    assert derived.original_execution_id == fixture["original"].execution_id

    with pytest.raises(ValueError, match="original result semantic hash"):
        create_replay_bundle(
            fixture["skill"],
            fixture["request"],
            replace(fixture["original"], semantic_hash="sha256:other"),
            recorder,
            fixture["snapshots"],
        )

    with pytest.raises(ValueError, match="execution_id"):
        create_replay_bundle(
            fixture["skill"],
            fixture["request"],
            replace(fixture["original"], execution_id="exec-other"),
            recorder,
            fixture["snapshots"],
        )
    with pytest.raises(ValueError, match="share a store"):
        create_replay_bundle(
            fixture["skill"],
            fixture["request"],
            fixture["original"],
            recorder,
            InMemorySnapshotStore(),
        )
    mismatched_policy_recorder = RecordingToolExecutor(
        fixture["provider"],
        fixture["snapshots"],
        DataHandlingPolicy(snapshot_retention_days=1),
    )
    with pytest.raises(ValueError, match="data policies"):
        create_replay_bundle(
            fixture["skill"],
            fixture["request"],
            fixture["original"],
            mismatched_policy_recorder,
            fixture["snapshots"],
        )


def test_replay_bundle_uses_declared_classification_and_minimizes_data() -> None:
    catalog = build_tool_catalog()
    source = SOURCE.replace(
        "year: Year classification INTERNAL;",
        "year: Year classification CONFIDENTIAL;",
    )
    skill = NslCompiler(catalog).compile(source).skill
    runtime = RuntimeEngine(catalog)
    principal = build_principal()
    policy = DataHandlingPolicy()
    snapshots = ProtectedSnapshotStore(VaultProtector())
    recorder = RecordingToolExecutor(
        build_mock_executor(catalog), snapshots, policy
    )
    request = ExecutionRequest(
        execution_id="exec-minimized",
        inputs={"year": 2026, "unused_input": "DROP-ME"},
        runtime_context={
            "user": {"team_id": "TEAM-FINANCE", "unused": "DROP-ME"},
            "unused_context": "DROP-ME",
        },
        principal=principal,
        data_policy=policy,
    )
    original = asyncio.run(
        runtime.execute(
            skill, request, recorder, InMemoryAuditSink(), snapshots
        )
    )

    bundle = create_replay_bundle(
        skill,
        request,
        original,
        recorder,
        snapshots,
    )

    assert bundle.inputs_ref.classification is DataClassification.CONFIDENTIAL
    assert bundle.context_ref.classification is DataClassification.INTERNAL
    assert snapshots.get(bundle.inputs_ref, principal) == {"year": 2026}
    assert snapshots.get(bundle.context_ref, principal) == {
        "user": {"team_id": "TEAM-FINANCE"}
    }
    assert snapshots._items[bundle.inputs_ref.snapshot_id].plaintext is None
    assert "DROP-ME" not in repr(snapshots._items)


def test_protected_snapshot_replay_is_encrypted_isolated_and_deterministic() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    runtime = RuntimeEngine(catalog)
    principal = build_principal()
    policy = DataHandlingPolicy(snapshot_retention_days=1)
    clock = MutableClock()
    protector = VaultProtector()
    snapshots = ProtectedSnapshotStore(protector, clock)
    provider = build_mock_executor(catalog)
    recorder = RecordingToolExecutor(provider, snapshots, policy)
    request = ExecutionRequest(
        execution_id="exec-protected-live",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=principal,
        data_policy=policy,
    )
    original = asyncio.run(
        runtime.execute(
            skill,
            request,
            recorder,
            InMemoryAuditSink(),
            snapshots,
        )
    )
    bundle = create_replay_bundle(
        skill,
        request,
        original,
        recorder,
        snapshots,
    )

    report = asyncio.run(
        replay_and_compare(
            bundle,
            skill,
            replay_execution_id="exec-protected-replay",
            principal=principal,
            policy=policy,
            snapshots=snapshots,
            runtime=runtime,
            audit_sink=InMemoryAuditSink(),
        )
    )

    assert report.matches is True
    assert provider.call_count == 2
    assert report.execution.tool_call_count == 2
    assert all(call.result_ref.snapshot_id for call in bundle.tool_calls)
    assert bundle.original_result_ref is not None
    assert all(
        item.plaintext is None
        for item in snapshots._items.values()
        if item.classification
        in {DataClassification.CONFIDENTIAL, DataClassification.RESTRICTED}
    )
    assert "PRJ-PARENT-001" not in repr(snapshots._items)

    clock.advance(days=1)
    with pytest.raises(KeyError, match="snapshot expired"):
        asyncio.run(
            replay_and_compare(
                bundle,
                skill,
                replay_execution_id="exec-expired-replay",
                principal=principal,
                policy=policy,
                snapshots=snapshots,
                runtime=runtime,
                audit_sink=InMemoryAuditSink(),
            )
        )
