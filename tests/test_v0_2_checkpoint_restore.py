from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from nsl.checkpoints import (
    CHECKPOINT_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
    CheckpointCoordinator,
    CheckpointError,
    ExecutionCheckpoint,
)
from nsl.compiler import NslCompiler
from nsl.core import (
    ApprovalStatus,
    BusinessOperationId,
    CheckStatus,
    Completeness,
    DataClassification,
    INT,
    PayloadHash,
    Presence,
    STRING,
    ValueEnvelope,
)
from nsl.ir import canonical_json
from nsl.runtime_models import (
    CheckResult,
    EmitRecord,
    ExecutionContext,
    ExecutionRequest,
)
from nsl.security import DataHandlingPolicy
from nsl.vertical_slice import build_principal, build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)
SKILL = NslCompiler(build_tool_catalog()).compile(SOURCE).skill
PAYLOAD_HASH = PayloadHash("sha256:" + "a" * 64)


class FakeRuntimeClock:
    def __init__(self) -> None:
        self.calls = 0

    def monotonic_ns(self) -> int:
        self.calls += 1
        return 1_000_000_000


def _request(
    *,
    execution_id: str = "exec-checkpoint",
    business_operation_id: str = "business-checkpoint",
    tenant_id: str = "tenant-checkpoint",
    policy: DataHandlingPolicy | None = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(tenant_id=tenant_id),
        data_policy=policy or DataHandlingPolicy(),
        business_operation_id=BusinessOperationId(business_operation_id),
    )


def _populated_context() -> ExecutionContext:
    context = ExecutionContext(SKILL, _request())
    input_value = ValueEnvelope.complete(
        2026,
        INT,
        DataClassification.CONFIDENTIAL,
        ("input:year",),
    )
    text_value = ValueEnvelope.complete(
        "TEAM-FINANCE",
        STRING,
        provenance_refs=("context:user.team_id",),
    )
    context.bind_input("sym-input", input_value)
    context.bind_context("sym-context", text_value)
    context.bind("sym-root", input_value)
    context.bind_check("check-root", text_value)
    context.push_frame()
    context.bind("sym-inner", text_value)
    context.bind_check("check-inner", input_value)
    context.checks.append(
        CheckResult(
            "check-budget",
            CheckStatus.PASS,
            "ERROR",
            "budget is valid",
            "check0001",
            Presence.PRESENT,
            Completeness.COMPLETE,
            ("tool:budget",),
            None,
        )
    )
    context.outputs.append(EmitRecord({"team": text_value, "year": input_value}))
    context.resources.tool_calls = 1
    context.resources.loop_iterations = 2
    context.resources.emitted_rows = 1
    context.resources.max_collection_size_seen = 2
    context.data_completeness = Completeness.PARTIAL
    context.invocation_counter = 3
    context.foreach_depth = 1
    context.current_node_id = "approval0001"
    context.approvals.records["approval0001"] = ApprovalStatus.APPROVED
    context.writes.entries.append(
        {"event_type": "WRITE_REQUESTED", "node_id": "write0001"}
    )
    context.idempotency.keys_by_write_node["write0001"] = "idem-write0001"
    context.idempotency.payload_hashes_by_write_node["write0001"] = PAYLOAD_HASH
    context.checkpoint.last_completed_node_id = "approval0001"
    context.checkpoint.suspended_at_node_id = "write0001"
    context.checkpoint.trace_sequence = 4
    context.checkpoint.approval_request_published = True
    context.checkpoint.write_request_durable = True
    context.checkpoint.approval_request_ids_by_write_node["write0001"] = (
        "approval-request-0001"
    )
    context.checkpoint.write_intent_hashes_by_write_node["write0001"] = (
        "sha256:" + "b" * 64
    )
    context.checkpoint.payload_hashes_by_write_node["write0001"] = PAYLOAD_HASH
    return context


def _resign(
    checkpoint: ExecutionCheckpoint,
    *,
    state: dict | None = None,
    **changes,
) -> ExecutionCheckpoint:
    values = {
        "execution_id": checkpoint.execution_id,
        "business_operation_id": checkpoint.business_operation_id,
        "checkpoint_version": checkpoint.checkpoint_version,
        "skill_id": checkpoint.skill_id,
        "skill_version": checkpoint.skill_version,
        "semantic_hash": checkpoint.semantic_hash,
        "ir_version": checkpoint.ir_version,
        "runtime_version": checkpoint.runtime_version,
        "state_payload": (
            canonical_json(state) if state is not None else checkpoint.state_payload
        ),
    }
    values.update(changes)
    return ExecutionCheckpoint.create(**values)


def _state(checkpoint: ExecutionCheckpoint) -> dict:
    return json.loads(checkpoint.state_payload)


def test_ctx_207_restores_all_execution_local_state_and_injected_clock() -> None:
    coordinator = CheckpointCoordinator("runtime-test")
    original = _populated_context()
    checkpoint = coordinator.capture(original)
    clock = FakeRuntimeClock()

    restored = coordinator.restore(checkpoint, SKILL, _request(), clock)

    assert restored.input_values == original.input_values
    assert restored.context_values == original.context_values
    assert restored.frames == original.frames
    assert restored.check_frames == original.check_frames
    assert restored.checks == original.checks
    assert restored.outputs == original.outputs
    assert restored.usage() == original.usage()
    assert restored.data_completeness is Completeness.PARTIAL
    assert restored.invocation_counter == 3
    assert restored.foreach_depth == 1
    assert restored.current_node_id == "approval0001"
    assert restored.approvals == original.approvals
    assert restored.writes == original.writes
    assert restored.idempotency == original.idempotency
    assert restored.checkpoint == original.checkpoint
    assert restored.resource_guard.meter is restored.resources
    assert clock.calls == 1


def test_ctx_207_default_clock_restore_and_empty_state_are_supported() -> None:
    coordinator = CheckpointCoordinator()
    context = ExecutionContext(SKILL, _request())

    restored = coordinator.restore(
        coordinator.capture(context), SKILL, _request()
    )

    assert restored.frames == [{}]
    assert restored.check_frames == [{}]
    assert restored.checkpoint.checkpoint_version == 1


def test_ctx_207_checkpoint_bytes_are_immutable_canonical_and_round_trip() -> None:
    first = CheckpointCoordinator("runtime-test").capture(_populated_context())
    second = CheckpointCoordinator("runtime-test").capture(_populated_context())

    assert first == second
    assert first.to_bytes() == second.to_bytes()
    assert ExecutionCheckpoint.from_bytes(first.to_bytes()) == first
    assert first.to_data()["schema_version"] == CHECKPOINT_SCHEMA_VERSION
    assert _state(first)["schema_version"] == STATE_SCHEMA_VERSION


def test_ctx_207_checkpoint_version_is_monotonic_from_zero() -> None:
    coordinator = CheckpointCoordinator()
    context = ExecutionContext(SKILL, _request())

    first = coordinator.capture(context)
    second = coordinator.capture(context)

    assert first.checkpoint_version == 1
    assert second.checkpoint_version == 2
    assert context.checkpoint.checkpoint_version == 2


def test_ctx_208_checkpoint_is_bound_to_skill_semantic_hash() -> None:
    coordinator = CheckpointCoordinator()
    checkpoint = coordinator.capture(ExecutionContext(SKILL, _request()))
    rebound = _resign(checkpoint, semantic_hash="sha256:" + "f" * 64)

    with pytest.raises(CheckpointError, match="semantic_hash.*binding mismatch"):
        coordinator.restore(rebound, SKILL, _request())


def test_ctx_209_preserves_business_approval_write_and_payload_identities() -> None:
    coordinator = CheckpointCoordinator()
    original = _populated_context()
    checkpoint = coordinator.capture(original)

    restored = coordinator.restore(checkpoint, SKILL, _request())

    assert checkpoint.business_operation_id == "business-checkpoint"
    assert restored.identity.business_operation_id == "business-checkpoint"
    assert restored.checkpoint.approval_request_ids_by_write_node == {
        "write0001": "approval-request-0001"
    }
    assert restored.checkpoint.write_intent_hashes_by_write_node == {
        "write0001": "sha256:" + "b" * 64
    }
    assert restored.checkpoint.payload_hashes_by_write_node == {
        "write0001": PAYLOAD_HASH
    }


def test_checkpoint_capture_requires_business_operation_identity() -> None:
    request = replace(_request(), business_operation_id=None)

    with pytest.raises(CheckpointError, match="business operation identity"):
        CheckpointCoordinator().capture(ExecutionContext(SKILL, request))


@pytest.mark.parametrize("version", [-1, 0, False])
def test_checkpoint_rejects_non_positive_versions(version) -> None:
    with pytest.raises(CheckpointError, match="checkpoint_version"):
        ExecutionCheckpoint.create(
            execution_id="execution",
            business_operation_id=BusinessOperationId("business"),
            checkpoint_version=version,
            skill_id="skill",
            skill_version="1.0.0",
            semantic_hash="sha256:" + "a" * 64,
            ir_version="1.1",
            runtime_version="runtime",
            state_payload=b"{}",
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"execution_id": ""}, "execution_id"),
        ({"business_operation_id": " "}, "business_operation_id"),
        ({"state_payload": bytearray(b"{}")}, "state_payload"),
    ],
)
def test_checkpoint_rejects_invalid_constructor_values(changes, message) -> None:
    values = {
        "execution_id": "execution",
        "business_operation_id": BusinessOperationId("business"),
        "checkpoint_version": 1,
        "skill_id": "skill",
        "skill_version": "1.0.0",
        "semantic_hash": "sha256:" + "a" * 64,
        "ir_version": "1.1",
        "runtime_version": "runtime",
        "state_payload": b"{}",
    }
    values.update(changes)

    with pytest.raises(CheckpointError, match=message):
        ExecutionCheckpoint.create(**values)


def test_checkpoint_rejects_invalid_integrity_value_and_tampering() -> None:
    checkpoint = CheckpointCoordinator().capture(
        ExecutionContext(SKILL, _request())
    )

    with pytest.raises(CheckpointError, match="SHA-256"):
        replace(checkpoint, integrity_hash="bad")
    with pytest.raises(CheckpointError, match="state_payload"):
        replace(checkpoint, state_payload=bytearray(checkpoint.state_payload))
    with pytest.raises(CheckpointError, match="integrity mismatch"):
        replace(checkpoint, execution_id="other").verify()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"{", "invalid JSON"),
        (b"[]", "invalid object schema"),
        (b'{"x":1,"x":2}', "duplicate object field"),
    ],
)
def test_checkpoint_loader_rejects_malformed_json(payload, message) -> None:
    with pytest.raises(CheckpointError, match=message):
        ExecutionCheckpoint.from_bytes(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 2, "unsupported checkpoint schema"),
        ("state_payload", "!", "invalid base64 payload"),
        ("checkpoint_version", 0, "positive integer"),
        ("execution_id", None, "non-empty string"),
    ],
)
def test_checkpoint_loader_rejects_invalid_envelope_fields(
    field, value, message
) -> None:
    checkpoint = CheckpointCoordinator().capture(
        ExecutionContext(SKILL, _request())
    )
    data = checkpoint.to_data()
    data[field] = value

    with pytest.raises(CheckpointError, match=message):
        ExecutionCheckpoint.from_bytes(canonical_json(data))


def test_checkpoint_loader_rejects_unknown_or_missing_fields() -> None:
    checkpoint = CheckpointCoordinator().capture(
        ExecutionContext(SKILL, _request())
    )
    with_extra = checkpoint.to_data()
    with_extra["extra"] = True
    missing = checkpoint.to_data()
    del missing["skill_id"]

    for data in (with_extra, missing):
        with pytest.raises(CheckpointError, match="invalid object schema"):
            ExecutionCheckpoint.from_bytes(canonical_json(data))


@pytest.mark.parametrize(
    ("changes", "path"),
    [
        ({"execution_id": "other"}, "execution_id"),
        ({"business_operation_id": "other"}, "business_operation_id"),
        ({"skill_id": "other"}, "skill_id"),
        ({"skill_version": "other"}, "skill_version"),
        ({"ir_version": "other"}, "ir_version"),
        ({"runtime_version": "other"}, "runtime_version"),
    ],
)
def test_restore_rejects_resigned_identity_binding_mismatch(changes, path) -> None:
    coordinator = CheckpointCoordinator()
    checkpoint = coordinator.capture(ExecutionContext(SKILL, _request()))
    rebound = _resign(checkpoint, **changes)

    with pytest.raises(CheckpointError, match=path + ".*binding mismatch"):
        coordinator.restore(rebound, SKILL, _request())


def test_restore_rejects_tenant_principal_or_policy_mismatch() -> None:
    coordinator = CheckpointCoordinator()
    checkpoint = coordinator.capture(ExecutionContext(SKILL, _request()))
    different_policy = DataHandlingPolicy(
        snapshot_retention_days=1,
        audit_retention_days=1,
    )

    for request in (
        _request(tenant_id="other-tenant"),
        _request(policy=different_policy),
    ):
        with pytest.raises(CheckpointError, match="security context"):
            coordinator.restore(checkpoint, SKILL, request)


def _set_path(state: dict, path: tuple[object, ...], value: object) -> None:
    target = state
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("schema_version",), 2, "unsupported state schema"),
        (("checkpoint_version",), 2, "checkpoint binding mismatch"),
        (("symbols", "frames"), [], "non-empty array"),
        (("symbols", "check_frames"), [], "frame depth mismatch"),
        (("symbols", "inputs"), [], "expected an object"),
        (("checks",), {}, "must be arrays"),
        (("outputs",), {}, "must be arrays"),
        (("resources", "tool_calls"), -1, "non-negative integer"),
        (("resources", "loop_iterations"), False, "non-negative integer"),
        (("execution", "data_completeness"), "BAD", "invalid value"),
        (("execution", "current_node_id"), 1, "non-empty string"),
        (("approvals",), [], "expected an object"),
        (("approvals", "approval0001"), "BAD", "invalid approval status"),
        (("approvals",), {"": "APPROVED"}, "non-empty string"),
        (("writes",), {}, "expected an array"),
        (("idempotency", "keys_by_write_node"), [], "expected an object"),
        (("checkpoint", "checkpoint_version"), 2, "binding mismatch"),
        (("checkpoint", "approval_request_published"), 1, "boolean"),
        (
            ("checkpoint", "approval_request_ids_by_write_node"),
            [],
            "expected an object",
        ),
    ],
)
def test_restore_rejects_malformed_or_inconsistent_state(
    path, value, message
) -> None:
    coordinator = CheckpointCoordinator()
    checkpoint = coordinator.capture(_populated_context())
    state = _state(checkpoint)
    _set_path(state, path, value)
    rebound = _resign(checkpoint, state=state)

    with pytest.raises(CheckpointError, match=message):
        coordinator.restore(rebound, SKILL, _request())


@pytest.mark.parametrize(
    ("container", "field", "value", "message"),
    [
        ("inputs", "provenance_refs", [""], "provenance_refs"),
        ("inputs", "presence", "BAD", "invalid ValueEnvelope"),
        ("checks", "provenance_refs", [""], "invalid CheckResult"),
        ("checks", "status", "BAD", "invalid CheckResult"),
    ],
)
def test_restore_rejects_malformed_value_and_check_records(
    container, field, value, message
) -> None:
    coordinator = CheckpointCoordinator()
    checkpoint = coordinator.capture(_populated_context())
    state = _state(checkpoint)
    if container == "inputs":
        state["symbols"]["inputs"]["sym-input"][field] = value
    else:
        state["checks"][0][field] = value

    with pytest.raises(CheckpointError, match=message):
        coordinator.restore(
            _resign(checkpoint, state=state), SKILL, _request()
        )


def test_restore_rejects_invalid_encoded_write_value() -> None:
    coordinator = CheckpointCoordinator()
    checkpoint = coordinator.capture(_populated_context())
    state = _state(checkpoint)
    state["writes"] = [{"$type": "Decimal", "value": "not-decimal"}]

    with pytest.raises(CheckpointError, match="invalid encoded value"):
        coordinator.restore(
            _resign(checkpoint, state=state), SKILL, _request()
        )


def test_capture_rejects_non_serializable_write_state() -> None:
    context = ExecutionContext(SKILL, _request())
    context.writes.entries.append(object())

    with pytest.raises(CheckpointError, match="not serializable"):
        CheckpointCoordinator().capture(context)


def test_checkpoint_identity_maps_are_isolated_between_contexts() -> None:
    first = ExecutionContext(SKILL, _request(execution_id="first"))
    second = ExecutionContext(SKILL, _request(execution_id="second"))
    first.checkpoint.approval_request_ids_by_write_node["write"] = "approval"
    first.checkpoint.write_intent_hashes_by_write_node["write"] = "intent"
    first.checkpoint.payload_hashes_by_write_node["write"] = PAYLOAD_HASH

    assert second.checkpoint.approval_request_ids_by_write_node == {}
    assert second.checkpoint.write_intent_hashes_by_write_node == {}
    assert second.checkpoint.payload_hashes_by_write_node == {}
