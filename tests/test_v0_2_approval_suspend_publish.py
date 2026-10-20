from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nsl.approval import (
    ApprovalPort,
    ApprovalRequest,
    ApprovalSuspension,
    ApprovalSuspensionCoordinator,
    CheckpointPort,
)
from nsl.checkpoints import CheckpointCoordinator, ExecutionCheckpoint
from nsl.compiler import NslCompiler
from nsl.core import (
    ApprovalRequestId,
    ApprovalStatus,
    BusinessOperationId,
    DataClassification,
    ExecutionStatus,
    PayloadHash,
    RiskLevel,
)
from nsl.runtime_models import ExecutionContext, ExecutionRequest
from nsl.security import DataHandlingPolicy
from nsl.vertical_slice import build_principal, build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)
NOW = datetime(2026, 10, 20, tzinfo=UTC)


def _context() -> ExecutionContext:
    skill = NslCompiler(build_tool_catalog()).compile(SOURCE).skill
    return ExecutionContext(
        skill,
        ExecutionRequest(
            execution_id="exec-0048",
            inputs={"year": 2026},
            runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
            principal=build_principal(),
            data_policy=DataHandlingPolicy(),
            business_operation_id=BusinessOperationId("business-0048"),
        ),
    )


def _request(ctx: ExecutionContext, **changes: object) -> ApprovalRequest:
    values: dict[str, object] = {
        "tenant_id": ctx.tenant_id,
        "execution_id": ctx.identity.execution_id,
        "business_operation_id": ctx.identity.business_operation_id,
        "approval_id": "APPROVE_BUDGET",
        "approval_request_id": ApprovalRequestId("approval-request-0048"),
        "skill_id": ctx.skill.skill_id,
        "skill_version": ctx.skill.skill_version,
        "skill_semantic_hash": ctx.skill.semantic_hash,
        "write_node_id": "write0001",
        "tool_id": "finance.create_draft",
        "payload_hash": PayloadHash("sha256:" + "b" * 64),
        "write_intent_hash": "write-intent-0048",
        "risk_level": RiskLevel.R3,
        "message": "Create draft",
        "expires_at": NOW + timedelta(hours=1),
        "display_safe_summary": {"line_count": 1},
        "protected_payload_ref": "snapshot://tenant-a/payload-0048",
        "data_classification": DataClassification.CONFIDENTIAL,
    }
    values.update(changes)
    return ApprovalRequest(**values)  # type: ignore[arg-type]


class RecordingCheckpointPort:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.saved: list[ExecutionCheckpoint] = []

    async def save(self, checkpoint: ExecutionCheckpoint) -> None:
        self.events.append("save")
        if self.error is not None:
            raise self.error
        checkpoint.verify()
        self.saved.append(checkpoint)


class IdempotentApprovalPort:
    def __init__(self, events: list[str], fail_once: bool = False) -> None:
        self.events = events
        self.fail_once = fail_once
        self.calls: list[str] = []
        self.requests: dict[str, ApprovalRequest] = {}

    async def publish(self, request: ApprovalRequest) -> None:
        self.events.append("publish")
        self.calls.append(request.approval_request_id)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("publication acknowledgement lost")
        previous = self.requests.setdefault(request.approval_request_id, request)
        if previous != request:
            raise RuntimeError("ApprovalRequestId collision")


def _suspend(
    ctx: ExecutionContext,
    request: ApprovalRequest,
    checkpoints: RecordingCheckpointPort,
    approvals: IdempotentApprovalPort,
) -> ApprovalSuspension:
    return asyncio.run(
        ApprovalSuspensionCoordinator().suspend(
            ctx,
            request,
            checkpoints,
            approvals,
        )
    )


def test_apr_004_transitions_to_waiting_for_approval() -> None:
    ctx = _context()
    suspension = _suspend(
        ctx,
        _request(ctx),
        RecordingCheckpointPort([]),
        IdempotentApprovalPort([]),
    )

    assert suspension.result.status is ExecutionStatus.WAITING_FOR_APPROVAL
    assert suspension.result.approval is not None
    assert suspension.result.approval.status is ApprovalStatus.PENDING
    assert suspension.result.suspension is not None
    assert suspension.result.suspension.reason == "APPROVAL_REQUIRED"


def test_apr_005_checkpoint_is_saved_before_approval_publication() -> None:
    events: list[str] = []
    ctx = _context()
    checkpoints = RecordingCheckpointPort(events)

    suspension = _suspend(
        ctx,
        _request(ctx),
        checkpoints,
        IdempotentApprovalPort(events),
    )

    assert events == ["save", "publish"]
    assert checkpoints.saved == [suspension.checkpoint]
    assert suspension.checkpoint.checkpoint_version == 1


def test_apr_005_save_failure_prevents_publication() -> None:
    events: list[str] = []
    ctx = _context()

    with pytest.raises(OSError, match="durable storage unavailable"):
        _suspend(
            ctx,
            _request(ctx),
            RecordingCheckpointPort(
                events,
                OSError("durable storage unavailable"),
            ),
            IdempotentApprovalPort(events),
        )

    assert events == ["save"]
    assert ctx.checkpoint.approval_request_published is False


def test_apr_006_publish_retry_reuses_request_identity_idempotently() -> None:
    events: list[str] = []
    ctx = _context()
    request = _request(ctx)
    checkpoints = RecordingCheckpointPort(events)
    approvals = IdempotentApprovalPort(events, fail_once=True)

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        _suspend(ctx, request, checkpoints, approvals)
    suspension = _suspend(ctx, request, checkpoints, approvals)

    assert events == ["save", "publish", "save", "publish"]
    assert approvals.calls == [
        "approval-request-0048",
        "approval-request-0048",
    ]
    assert list(approvals.requests) == ["approval-request-0048"]
    assert suspension.checkpoint.checkpoint_version == 2
    assert ctx.checkpoint.approval_request_published is True


def test_apr_007_suspension_is_complete_worker_release_contract() -> None:
    ctx = _context()
    suspension = _suspend(
        ctx,
        _request(ctx),
        RecordingCheckpointPort([]),
        IdempotentApprovalPort([]),
    )

    assert isinstance(suspension, ApprovalSuspension)
    assert suspension.result.suspension is not None
    assert suspension.result.suspension.checkpoint_version == 1
    assert not hasattr(suspension, "wait")
    assert not hasattr(ApprovalPort, "get_decision")


def test_arc_206_runtime_does_not_wait_for_human_approval() -> None:
    assert set(ApprovalPort.__dict__) & {"route", "notify", "await_decision"} == set()
    assert set(ApprovalSuspensionCoordinator.__dict__) & {
        "route",
        "notify",
        "schedule_resume",
    } == set()


def test_arc_208_apr_008_platform_workflow_ownership_stays_outside_ports() -> None:
    assert set(ApprovalPort.__dict__) >= {"publish"}
    assert set(ApprovalPort.__dict__) & {
        "route",
        "render_ui",
        "notify",
        "persist_decision",
        "schedule_resume",
    } == set()
    assert set(CheckpointPort.__dict__) >= {"save"}


def test_arc_209_runtime_exposes_suspend_contract_not_workflow_orchestration() -> None:
    public_methods = {
        name
        for name in ApprovalSuspensionCoordinator.__dict__
        if not name.startswith("_")
    }

    assert public_methods == {"suspend"}


def test_saved_checkpoint_contains_resume_bindings_and_pending_state() -> None:
    ctx = _context()
    request = _request(ctx)
    checkpoints = RecordingCheckpointPort([])
    suspension = _suspend(
        ctx,
        request,
        checkpoints,
        IdempotentApprovalPort([]),
    )
    restored = CheckpointCoordinator().restore(
        suspension.checkpoint,
        ctx.skill,
        ctx.request,
    )

    assert restored.approvals.records == {
        "APPROVE_BUDGET": ApprovalStatus.PENDING
    }
    assert restored.checkpoint.suspended_at_node_id == "write0001"
    assert restored.checkpoint.approval_request_published is False
    assert restored.checkpoint.approval_request_ids_by_write_node == {
        "write0001": "approval-request-0048"
    }
    assert restored.checkpoint.write_intent_hashes_by_write_node == {
        "write0001": "write-intent-0048"
    }
    assert restored.checkpoint.payload_hashes_by_write_node == {
        "write0001": "sha256:" + "b" * 64
    }


@pytest.mark.parametrize(
    ("field", "mismatch"),
    [
        ("tenant_id", "mismatch"),
        ("execution_id", "mismatch"),
        ("business_operation_id", "mismatch"),
        ("skill_id", "mismatch"),
        ("skill_version", "mismatch"),
        ("skill_semantic_hash", "sha256:" + "f" * 64),
    ],
)
def test_suspend_rejects_request_binding_mismatch_before_io(
    field: str,
    mismatch: str,
) -> None:
    events: list[str] = []
    ctx = _context()

    with pytest.raises(ValueError, match=field + ".*binding mismatch"):
        _suspend(
            ctx,
            _request(ctx, **{field: mismatch}),
            RecordingCheckpointPort(events),
            IdempotentApprovalPort(events),
        )

    assert events == []


def test_suspend_rejects_rebinding_write_node_to_another_request() -> None:
    events: list[str] = []
    ctx = _context()
    ctx.checkpoint.approval_request_ids_by_write_node["write0001"] = "prior"

    with pytest.raises(ValueError, match="another ApprovalRequestId"):
        _suspend(
            ctx,
            _request(ctx),
            RecordingCheckpointPort(events),
            IdempotentApprovalPort(events),
        )

    assert events == []


@pytest.mark.parametrize(
    ("invalid", "position", "message"),
    [
        (object(), "ctx", "ExecutionContext"),
        (object(), "request", "ApprovalRequest"),
        (object(), "checkpoint", "CheckpointPort"),
        (object(), "approval", "ApprovalPort"),
    ],
)
def test_suspend_rejects_invalid_contract_objects(
    invalid: object,
    position: str,
    message: str,
) -> None:
    ctx = _context()
    values: dict[str, object] = {
        "ctx": ctx,
        "request": _request(ctx),
        "checkpoint": RecordingCheckpointPort([]),
        "approval": IdempotentApprovalPort([]),
    }
    values[position] = invalid

    with pytest.raises(TypeError, match=message):
        asyncio.run(
            ApprovalSuspensionCoordinator().suspend(
                values["ctx"],  # type: ignore[arg-type]
                values["request"],  # type: ignore[arg-type]
                values["checkpoint"],  # type: ignore[arg-type]
                values["approval"],  # type: ignore[arg-type]
            )
        )


def test_approval_suspension_rejects_invalid_values() -> None:
    ctx = _context()
    valid = _suspend(
        ctx,
        _request(ctx),
        RecordingCheckpointPort([]),
        IdempotentApprovalPort([]),
    )

    with pytest.raises(TypeError, match="checkpoint"):
        ApprovalSuspension(object(), valid.result)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ExecutionResult"):
        ApprovalSuspension(valid.checkpoint, object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="WAITING_FOR_APPROVAL"):
        ApprovalSuspension(
            valid.checkpoint,
            replace(valid.result, status=ExecutionStatus.COMPLETED),
        )
