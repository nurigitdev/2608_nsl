from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from nsl.approval import (
    ApprovalClock,
    ApprovalEvidence,
    ApprovalEvidenceValidation,
    ApprovalEvidenceValidator,
    ApprovalEvidenceVerifier,
    ApprovalRequest,
    ApprovalResumeBindings,
    ApprovalResumeCoordinator,
    ApprovalResumeOutcome,
    ApprovalSuspensionCoordinator,
    ApprovalValidationResult,
    RecordedApprovalClock,
    ResumeCheckpointPort,
    ResumeClaimError,
    ResumeToken,
)
from nsl.checkpoints import CheckpointCoordinator, ExecutionCheckpoint
from nsl.compiler import NslCompiler
from nsl.core import (
    ApprovalDecision,
    ApprovalRequestId,
    ApprovalStatus,
    BusinessOperationId,
    DataClassification,
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
NOW = datetime(2026, 10, 20, 1, 2, 3, tzinfo=UTC)
PAYLOAD_HASH = PayloadHash("sha256:" + "b" * 64)
INTENT_HASH = "write-intent-0049"


def _context() -> ExecutionContext:
    skill = NslCompiler(build_tool_catalog()).compile(SOURCE).skill
    return ExecutionContext(
        skill,
        ExecutionRequest(
            execution_id="exec-0049",
            inputs={"year": 2026},
            runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
            principal=build_principal(),
            data_policy=DataHandlingPolicy(),
            business_operation_id=BusinessOperationId("business-0049"),
        ),
    )


def _approval_request(ctx: ExecutionContext) -> ApprovalRequest:
    return ApprovalRequest(
        tenant_id=ctx.tenant_id,
        execution_id=ctx.identity.execution_id,
        business_operation_id=ctx.identity.business_operation_id,
        approval_id="APPROVE_BUDGET",
        approval_request_id=ApprovalRequestId("approval-request-0049"),
        skill_id=ctx.skill.skill_id,
        skill_version=ctx.skill.skill_version,
        skill_semantic_hash=ctx.skill.semantic_hash,
        write_node_id="write0001",
        tool_id="finance.create_draft",
        payload_hash=PAYLOAD_HASH,
        write_intent_hash=INTENT_HASH,
        risk_level=RiskLevel.R3,
        message="Create draft",
        expires_at=NOW + timedelta(hours=1),
        display_safe_summary={"line_count": 1},
        protected_payload_ref="snapshot://tenant-a/payload-0049",
        data_classification=DataClassification.CONFIDENTIAL,
    )


def _evidence(
    ctx: ExecutionContext,
    **changes: object,
) -> ApprovalEvidence:
    values: dict[str, object] = {
        "tenant_id": ctx.tenant_id,
        "execution_id": ctx.identity.execution_id,
        "business_operation_id": ctx.identity.business_operation_id,
        "approval_id": "APPROVE_BUDGET",
        "approval_request_id": ApprovalRequestId("approval-request-0049"),
        "skill_semantic_hash": ctx.skill.semantic_hash,
        "decision": ApprovalDecision.APPROVED,
        "approver_id": "approver-0049",
        "decided_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
        "payload_hash": PAYLOAD_HASH,
        "write_intent_hash": INTENT_HASH,
        "evidence_integrity": "opaque-signed-evidence-0049",
    }
    values.update(changes)
    return ApprovalEvidence(**values)  # type: ignore[arg-type]


class AtomicCheckpointPort:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.checkpoints: dict[tuple[str, int], ExecutionCheckpoint] = {}
        self.latest: dict[str, int] = {}
        self.states: dict[tuple[str, int], str] = {}
        self.claims: dict[tuple[str, int], str] = {}

    async def save(self, checkpoint: ExecutionCheckpoint) -> None:
        self.events.append("save")
        key = (checkpoint.execution_id, checkpoint.checkpoint_version)
        self.checkpoints[key] = checkpoint
        self.latest[checkpoint.execution_id] = checkpoint.checkpoint_version
        self.states[key] = "AVAILABLE"

    async def claim(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> ExecutionCheckpoint:
        self.events.append("claim")
        key = (execution_id, checkpoint_version)
        if key not in self.checkpoints or self.latest.get(execution_id) != checkpoint_version:
            raise ResumeClaimError("stale checkpoint version")
        state = self.states[key]
        if state == "LIVE":
            raise ResumeClaimError("checkpoint already has a live claim")
        if state == "CONSUMED":
            raise ResumeClaimError("checkpoint already consumed")
        self.states[key] = "LIVE"
        self.claims[key] = claim_id
        return self.checkpoints[key]

    async def release(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> None:
        self.events.append("release")
        key = (execution_id, checkpoint_version)
        self._require_owner(key, claim_id)
        self.states[key] = "AVAILABLE"
        del self.claims[key]

    async def consume(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> None:
        self.events.append("consume")
        key = (execution_id, checkpoint_version)
        self._require_owner(key, claim_id)
        self.states[key] = "CONSUMED"
        del self.claims[key]

    def _require_owner(self, key: tuple[str, int], claim_id: str) -> None:
        if self.states.get(key) != "LIVE" or self.claims.get(key) != claim_id:
            raise ResumeClaimError("claim ownership mismatch")


class ApprovalPublisher:
    async def publish(self, request: ApprovalRequest) -> None:
        return None


class RecordingVerifier:
    def __init__(self, result: object = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.seen: list[ApprovalEvidence] = []

    def verify(self, evidence: ApprovalEvidence) -> bool:
        self.seen.append(evidence)
        if self.error is not None:
            raise self.error
        return self.result  # type: ignore[return-value]


class RecordingClock:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self.value  # type: ignore[return-value]


class RecordingCheckpointCoordinator(CheckpointCoordinator):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def restore(self, *args, **kwargs) -> ExecutionContext:
        self.events.append("restore")
        return super().restore(*args, **kwargs)


def _prepare() -> tuple[ExecutionContext, AtomicCheckpointPort, ExecutionCheckpoint]:
    ctx = _context()
    port = AtomicCheckpointPort([])
    suspension = asyncio.run(
        ApprovalSuspensionCoordinator().suspend(
            ctx,
            _approval_request(ctx),
            port,
            ApprovalPublisher(),
        )
    )
    port.events.clear()
    return ctx, port, suspension.checkpoint


def _token(
    ctx: ExecutionContext,
    checkpoint: ExecutionCheckpoint,
    evidence: ApprovalEvidence | None = None,
) -> ResumeToken:
    return ResumeToken(
        execution_id=ctx.identity.execution_id,
        business_operation_id=ctx.identity.business_operation_id,
        checkpoint_version=checkpoint.checkpoint_version,
        approval_evidence=evidence or _evidence(ctx),
    )


def _resume(
    ctx: ExecutionContext,
    port: AtomicCheckpointPort,
    checkpoint: ExecutionCheckpoint,
    *,
    evidence: ApprovalEvidence | None = None,
    verifier: object | None = None,
    clock: object | None = None,
    coordinator: ApprovalResumeCoordinator | None = None,
    token: ResumeToken | None = None,
    current_intent: str = INTENT_HASH,
    current_payload: PayloadHash = PAYLOAD_HASH,
    claim_id: str = "claim-0049",
) -> ApprovalResumeOutcome:
    return asyncio.run(
        (coordinator or ApprovalResumeCoordinator()).resume(
            token=token or _token(ctx, checkpoint, evidence),
            skill=ctx.skill,
            request=ctx.request,
            approval_id="APPROVE_BUDGET",
            write_node_id="write0001",
            current_write_intent_hash=current_intent,
            current_payload_hash=current_payload,
            claim_id=claim_id,
            checkpoint_port=port,
            verifier=verifier or RecordingVerifier(),  # type: ignore[arg-type]
            clock=clock or RecordedApprovalClock(NOW),  # type: ignore[arg-type]
        )
    )


def test_rd_004_claim_occurs_before_mutable_state_restore() -> None:
    ctx, port, checkpoint = _prepare()
    coordinator = ApprovalResumeCoordinator(
        checkpoints=RecordingCheckpointCoordinator(port.events)
    )

    outcome = _resume(ctx, port, checkpoint, coordinator=coordinator)

    assert port.events == ["claim", "restore", "consume"]
    assert outcome.claim_consumed is True


def test_rd_004_consumed_checkpoint_rejects_duplicate_resume() -> None:
    ctx, port, checkpoint = _prepare()
    _resume(ctx, port, checkpoint)

    with pytest.raises(ResumeClaimError, match="consumed"):
        _resume(ctx, port, checkpoint, claim_id="claim-duplicate")

    assert port.events == ["claim", "consume", "claim"]


def test_rd_004_live_and_stale_claims_are_rejected_before_restore() -> None:
    ctx, port, checkpoint = _prepare()
    asyncio.run(port.claim(checkpoint.execution_id, checkpoint.checkpoint_version, "live"))

    with pytest.raises(ResumeClaimError, match="live claim"):
        _resume(ctx, port, checkpoint, claim_id="second")
    asyncio.run(port.release(checkpoint.execution_id, checkpoint.checkpoint_version, "live"))
    port.latest[checkpoint.execution_id] = checkpoint.checkpoint_version + 1
    with pytest.raises(ResumeClaimError, match="stale"):
        _resume(ctx, port, checkpoint, claim_id="stale")

    assert "restore" not in port.events


def test_apr_009_integrity_verifier_must_authenticate_opaque_evidence() -> None:
    ctx, port, checkpoint = _prepare()
    verifier = RecordingVerifier(False)

    outcome = _resume(ctx, port, checkpoint, verifier=verifier)

    assert verifier.seen == [outcome.context and _evidence(ctx)]
    assert outcome.validation.validation is ApprovalEvidenceValidation.INVALID
    assert outcome.validation.reason_code == "APPROVAL_EVIDENCE_INVALID"
    assert outcome.validation.approval_gate_passed is False
    assert outcome.claim_consumed is False
    assert port.events == ["claim", "release"]


@pytest.mark.parametrize(
    ("field", "value", "expected", "reason"),
    [
        (
            "business_operation_id",
            BusinessOperationId("other-business"),
            ApprovalEvidenceValidation.BUSINESS_OPERATION_MISMATCH,
            "APPROVAL_BUSINESS_OPERATION_MISMATCH",
        ),
        (
            "approval_request_id",
            ApprovalRequestId("other-request"),
            ApprovalEvidenceValidation.APPROVAL_REQUEST_MISMATCH,
            "APPROVAL_REQUEST_MISMATCH",
        ),
        (
            "write_intent_hash",
            "other-intent",
            ApprovalEvidenceValidation.INTENT_MISMATCH,
            "APPROVAL_INTENT_MISMATCH",
        ),
        (
            "payload_hash",
            PayloadHash("sha256:" + "c" * 64),
            ApprovalEvidenceValidation.PAYLOAD_MISMATCH,
            "APPROVAL_PAYLOAD_MISMATCH",
        ),
        (
            "tenant_id",
            "other-tenant",
            ApprovalEvidenceValidation.INVALID,
            "APPROVAL_EVIDENCE_BINDING_MISMATCH",
        ),
        (
            "execution_id",
            "other-execution",
            ApprovalEvidenceValidation.INVALID,
            "APPROVAL_EVIDENCE_BINDING_MISMATCH",
        ),
        (
            "approval_id",
            "OTHER_APPROVAL",
            ApprovalEvidenceValidation.INVALID,
            "APPROVAL_EVIDENCE_BINDING_MISMATCH",
        ),
        (
            "skill_semantic_hash",
            "sha256:" + "f" * 64,
            ApprovalEvidenceValidation.INVALID,
            "APPROVAL_EVIDENCE_BINDING_MISMATCH",
        ),
    ],
)
def test_apr_009_010_011_all_current_bindings_fail_closed(
    field: str,
    value: object,
    expected: ApprovalEvidenceValidation,
    reason: str,
) -> None:
    ctx, port, checkpoint = _prepare()
    evidence = _evidence(ctx, **{field: value})

    outcome = _resume(ctx, port, checkpoint, evidence=evidence)

    assert outcome.validation.validation is expected
    assert outcome.validation.reason_code == reason
    assert outcome.validation.approval_gate_passed is False
    assert outcome.claim_consumed is False
    assert port.events == ["claim", "release"]


def test_apr_010_current_intent_recalculation_mismatch_blocks() -> None:
    ctx, port, checkpoint = _prepare()

    outcome = _resume(
        ctx,
        port,
        checkpoint,
        current_intent="recalculated-intent",
    )

    assert outcome.validation.validation is ApprovalEvidenceValidation.INTENT_MISMATCH
    assert outcome.claim_consumed is False


def test_apr_011_current_canonical_payload_mismatch_blocks() -> None:
    ctx, port, checkpoint = _prepare()

    outcome = _resume(
        ctx,
        port,
        checkpoint,
        current_payload=PayloadHash("sha256:" + "d" * 64),
    )

    assert outcome.validation.validation is ApprovalEvidenceValidation.PAYLOAD_MISMATCH
    assert outcome.claim_consumed is False


def test_valid_approval_consumes_claim_and_opens_only_approval_gate() -> None:
    ctx, port, checkpoint = _prepare()

    outcome = _resume(ctx, port, checkpoint)

    assert outcome.validation == ApprovalValidationResult(
        ApprovalEvidenceValidation.VALID,
        ApprovalStatus.APPROVED,
        None,
    )
    assert outcome.validation.approval_gate_passed is True
    assert outcome.validation.requires_reapproval is False
    assert outcome.context.approvals.records["APPROVE_BUDGET"] is ApprovalStatus.APPROVED
    assert outcome.claim_consumed is True


def test_apr_012_rejected_decision_is_consumed_without_write_permission() -> None:
    ctx, port, checkpoint = _prepare()
    evidence = _evidence(ctx, decision=ApprovalDecision.REJECTED)

    outcome = _resume(ctx, port, checkpoint, evidence=evidence)

    assert outcome.validation.validation is ApprovalEvidenceValidation.VALID
    assert outcome.validation.approval_status is ApprovalStatus.REJECTED
    assert outcome.validation.reason_code == "APPROVAL_REJECTED"
    assert outcome.validation.approval_gate_passed is False
    assert outcome.context.approvals.records["APPROVE_BUDGET"] is ApprovalStatus.REJECTED
    assert outcome.claim_consumed is True


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(microseconds=1)])
def test_apr_013_expiry_boundary_requires_reapproval(offset: timedelta) -> None:
    ctx, port, checkpoint = _prepare()
    expires_at = NOW + timedelta(hours=1)
    evidence = _evidence(ctx, expires_at=expires_at)

    outcome = _resume(
        ctx,
        port,
        checkpoint,
        evidence=evidence,
        clock=RecordedApprovalClock(expires_at + offset),
    )

    assert outcome.validation.validation is ApprovalEvidenceValidation.EXPIRED
    assert outcome.validation.approval_status is ApprovalStatus.EXPIRED
    assert outcome.validation.requires_reapproval is True
    assert outcome.validation.approval_gate_passed is False
    assert outcome.claim_consumed is True


def test_rd_005_before_expiry_and_no_expiry_are_valid() -> None:
    ctx, port, checkpoint = _prepare()
    expires_at = NOW + timedelta(hours=1)
    before = _resume(
        ctx,
        port,
        checkpoint,
        evidence=_evidence(ctx, expires_at=expires_at),
        clock=RecordedApprovalClock(expires_at - timedelta(microseconds=1)),
    )
    assert before.validation.approval_gate_passed is True

    ctx, port, checkpoint = _prepare()
    without_expiry = _resume(
        ctx,
        port,
        checkpoint,
        evidence=_evidence(ctx, expires_at=None),
        clock=RecordedApprovalClock(NOW + timedelta(days=3650)),
    )
    assert without_expiry.validation.approval_gate_passed is True


@pytest.mark.parametrize(
    "clock_value",
    [
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=9))),
        "2026-01-01T00:00:00Z",
    ],
)
def test_rd_005_invalid_clock_time_releases_claim(clock_value: object) -> None:
    ctx, port, checkpoint = _prepare()

    with pytest.raises((TypeError, ValueError), match="ApprovalClock.now"):
        _resume(ctx, port, checkpoint, clock=RecordingClock(clock_value))

    assert port.events == ["claim", "release"]


def test_rd_005_recorded_clock_rejects_non_utc_time() -> None:
    with pytest.raises(ValueError, match="UTC"):
        RecordedApprovalClock(datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="UTC"):
        RecordedApprovalClock(
            datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=9)))
        )


@pytest.mark.parametrize("result", [None, "true", 1])
def test_rd_006_verifier_must_return_exact_bool(result: object) -> None:
    ctx, port, checkpoint = _prepare()

    with pytest.raises(TypeError, match="return bool"):
        _resume(ctx, port, checkpoint, verifier=RecordingVerifier(result))

    assert port.events == ["claim", "release"]


def test_verifier_dependency_failure_releases_claim_for_retry() -> None:
    ctx, port, checkpoint = _prepare()

    with pytest.raises(OSError, match="verifier unavailable"):
        _resume(
            ctx,
            port,
            checkpoint,
            verifier=RecordingVerifier(error=OSError("verifier unavailable")),
        )

    assert port.events == ["claim", "release"]
    retried = _resume(ctx, port, checkpoint, claim_id="retry-claim")
    assert retried.validation.approval_gate_passed is True


def test_token_identity_mismatch_is_rejected_before_claim() -> None:
    ctx, port, checkpoint = _prepare()
    valid = _token(ctx, checkpoint)

    for token, message in (
        (replace(valid, execution_id="other"), "execution_id"),
        (
            replace(
                valid,
                business_operation_id=BusinessOperationId("other"),
            ),
            "business_operation_id",
        ),
    ):
        with pytest.raises(ValueError, match=message + ".*binding mismatch"):
            _resume(ctx, port, checkpoint, token=token)

    assert port.events == []


@pytest.mark.parametrize(
    ("changes", "error_type", "message"),
    [
        ({"execution_id": ""}, ValueError, "execution_id"),
        ({"business_operation_id": ""}, ValueError, "business_operation_id"),
        ({"checkpoint_version": 0}, ValueError, "positive"),
        ({"checkpoint_version": True}, ValueError, "positive"),
        ({"approval_evidence": object()}, TypeError, "ApprovalEvidence"),
    ],
)
def test_resume_token_rejects_malformed_values(
    changes: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    ctx, _, checkpoint = _prepare()
    values: dict[str, object] = {
        "execution_id": ctx.identity.execution_id,
        "business_operation_id": ctx.identity.business_operation_id,
        "checkpoint_version": checkpoint.checkpoint_version,
        "approval_evidence": _evidence(ctx),
    }
    values.update(changes)

    with pytest.raises(error_type, match=message):
        ResumeToken(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("position", "message"),
    [
        ("token", "ResumeToken"),
        ("skill", "SkillObject"),
        ("request", "ExecutionRequest"),
        ("checkpoint_port", "ResumeCheckpointPort"),
        ("verifier", "ApprovalEvidenceVerifier"),
        ("clock", "ApprovalClock"),
    ],
)
def test_resume_rejects_invalid_contracts_before_claim(
    position: str,
    message: str,
) -> None:
    ctx, port, checkpoint = _prepare()
    values: dict[str, object] = {
        "token": _token(ctx, checkpoint),
        "skill": ctx.skill,
        "request": ctx.request,
        "checkpoint_port": port,
        "verifier": RecordingVerifier(),
        "clock": RecordedApprovalClock(NOW),
    }
    values[position] = object()

    with pytest.raises(TypeError, match=message):
        asyncio.run(
            ApprovalResumeCoordinator().resume(
                token=values["token"],  # type: ignore[arg-type]
                skill=values["skill"],  # type: ignore[arg-type]
                request=values["request"],  # type: ignore[arg-type]
                approval_id="APPROVE_BUDGET",
                write_node_id="write0001",
                current_write_intent_hash=INTENT_HASH,
                current_payload_hash=PAYLOAD_HASH,
                claim_id="claim-invalid",
                checkpoint_port=values["checkpoint_port"],  # type: ignore[arg-type]
                verifier=values["verifier"],  # type: ignore[arg-type]
                clock=values["clock"],  # type: ignore[arg-type]
            )
        )

    assert port.events == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("approval_id", "", "approval_id"),
        ("write_node_id", "", "write_node_id"),
        ("current_write_intent_hash", "", "current_write_intent_hash"),
        ("current_payload_hash", "invalid", "SHA-256"),
        ("claim_id", "", "claim_id"),
    ],
)
def test_resume_rejects_invalid_runtime_values_before_claim(
    field: str,
    value: object,
    message: str,
) -> None:
    ctx, port, checkpoint = _prepare()
    values: dict[str, object] = {
        "approval_id": "APPROVE_BUDGET",
        "write_node_id": "write0001",
        "current_write_intent_hash": INTENT_HASH,
        "current_payload_hash": PAYLOAD_HASH,
        "claim_id": "claim-invalid",
    }
    values[field] = value

    with pytest.raises(ValueError, match=message):
        asyncio.run(
            ApprovalResumeCoordinator().resume(
                token=_token(ctx, checkpoint),
                skill=ctx.skill,
                request=ctx.request,
                approval_id=values["approval_id"],  # type: ignore[arg-type]
                write_node_id=values["write_node_id"],  # type: ignore[arg-type]
                current_write_intent_hash=values["current_write_intent_hash"],  # type: ignore[arg-type]
                current_payload_hash=values["current_payload_hash"],  # type: ignore[arg-type]
                claim_id=values["claim_id"],  # type: ignore[arg-type]
                checkpoint_port=port,
                verifier=RecordingVerifier(),
                clock=RecordedApprovalClock(NOW),
            )
        )

    assert port.events == []


def test_missing_checkpoint_approval_state_releases_claim() -> None:
    ctx, port, checkpoint = _prepare()
    restored = CheckpointCoordinator().restore(checkpoint, ctx.skill, ctx.request)
    restored.approvals.records.clear()
    replacement = CheckpointCoordinator().capture(restored)
    asyncio.run(port.save(replacement))
    port.events.clear()

    with pytest.raises(ValueError, match="approval is not pending"):
        _resume(ctx, port, replacement)
    assert port.events == ["claim", "release"]


def test_missing_checkpoint_request_binding_releases_claim() -> None:
    ctx, port, checkpoint = _prepare()
    restored = CheckpointCoordinator().restore(checkpoint, ctx.skill, ctx.request)
    restored.checkpoint.approval_request_ids_by_write_node.clear()
    replacement = CheckpointCoordinator().capture(restored)
    asyncio.run(port.save(replacement))
    port.events.clear()

    with pytest.raises(ValueError, match="request binding is missing"):
        _resume(ctx, port, replacement)
    assert port.events == ["claim", "release"]


def test_validation_and_outcome_reject_invalid_objects() -> None:
    ctx, port, checkpoint = _prepare()
    bindings = ApprovalResumeBindings(
        tenant_id=ctx.tenant_id,
        execution_id=ctx.identity.execution_id,
        business_operation_id=ctx.identity.business_operation_id,
        approval_id="APPROVE_BUDGET",
        approval_request_id=ApprovalRequestId("approval-request-0049"),
        skill_semantic_hash=ctx.skill.semantic_hash,
        payload_hash=PAYLOAD_HASH,
        write_intent_hash=INTENT_HASH,
    )
    validator = ApprovalEvidenceValidator()
    with pytest.raises(TypeError, match="ApprovalEvidence"):
        validator.validate(object(), bindings, RecordingVerifier(), RecordedApprovalClock(NOW))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ApprovalResumeBindings"):
        validator.validate(_evidence(ctx), object(), RecordingVerifier(), RecordedApprovalClock(NOW))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ApprovalEvidenceVerifier"):
        validator.validate(_evidence(ctx), bindings, object(), RecordedApprovalClock(NOW))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ApprovalClock"):
        validator.validate(_evidence(ctx), bindings, RecordingVerifier(), object())  # type: ignore[arg-type]

    valid = _resume(ctx, port, checkpoint)
    with pytest.raises(TypeError, match="ExecutionContext"):
        ApprovalResumeOutcome(object(), checkpoint, "claim", valid.validation, True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ExecutionCheckpoint"):
        ApprovalResumeOutcome(valid.context, object(), "claim", valid.validation, True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="claim_id"):
        ApprovalResumeOutcome(valid.context, checkpoint, "", valid.validation, True)
    with pytest.raises(TypeError, match="ApprovalValidationResult"):
        ApprovalResumeOutcome(valid.context, checkpoint, "claim", object(), True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="claim_consumed"):
        ApprovalResumeOutcome(valid.context, checkpoint, "claim", valid.validation, 1)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="ApprovalEvidenceValidation"):
        ApprovalValidationResult("VALID", ApprovalStatus.APPROVED, None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ApprovalStatus"):
        ApprovalValidationResult(ApprovalEvidenceValidation.VALID, "APPROVED", None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="reason_code"):
        ApprovalValidationResult(
            ApprovalEvidenceValidation.INVALID,
            ApprovalStatus.PENDING,
            "",
        )


def test_resume_protocols_are_runtime_checkable() -> None:
    _, port, _ = _prepare()
    assert isinstance(port, ResumeCheckpointPort)
    assert isinstance(RecordingVerifier(), ApprovalEvidenceVerifier)
    assert isinstance(RecordedApprovalClock(NOW), ApprovalClock)


def test_approval_contracts_are_exported_by_stable_worker_api() -> None:
    from nsl import api

    expected = {
        "ApprovalClock",
        "ApprovalEvidence",
        "ApprovalEvidenceVerifier",
        "ApprovalPort",
        "ApprovalRequest",
        "ApprovalResumeCoordinator",
        "ApprovalResumeOutcome",
        "ApprovalSuspension",
        "ApprovalSuspensionCoordinator",
        "CheckpointPort",
        "RecordedApprovalClock",
        "ResumeCheckpointPort",
        "ResumeToken",
    }

    assert expected <= set(api.__all__)
    assert all(getattr(api, name) is not None for name in expected)
