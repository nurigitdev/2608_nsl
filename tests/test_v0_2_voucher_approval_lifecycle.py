from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from nsl.approval import (
    ApprovalEvidence,
    ApprovalRequest,
    ApprovalResumeCoordinator,
    ApprovalSuspensionCoordinator,
    ApprovalValidationResult,
    RecordedApprovalClock,
    ResumeClaimError,
    ResumeToken,
)
from nsl.audit import InMemoryAuditSink
from nsl.checkpoints import (
    CheckpointCoordinator,
    CheckpointedExecution,
    ExecutionCheckpoint,
)
from nsl.cli_config import (
    load_json_document,
    load_mock_handlers_document,
    load_principal,
    load_tool_catalog,
    load_value_object,
)
from nsl.compiler import NslCompiler
from nsl.core import (
    ApprovalDecision,
    ApprovalEvidenceValidation,
    ApprovalRequestId,
    ApprovalStatus,
    BusinessOperationId,
    DataClassification,
    ExecutionStatus,
    PayloadHash,
    PreWriteEligibility,
    RiskLevel,
    ToolCapability,
    ValueEnvelope,
    domain,
    money_type,
)
from nsl.runtime import RuntimeContractError, RuntimeEngine
from nsl.runtime_models import (
    ExecutionRequest,
    ValidationSummary,
    ValidationSummaryStatus,
)
from nsl.tools import MockToolExecutor
from nsl.validation import PreWriteEvaluator, PreWriteEvidence
from nsl.write_intent import compute_payload_hash, compute_write_intent_hash


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "voucher_draft_validation"
SOURCE = (EXAMPLE / "voucher_draft_validation.ns").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 21, 1, 2, 3, tzinfo=UTC)
BUSINESS_OPERATION_ID = BusinessOperationId("business-voucher-0062")


class RecordingToolPort:
    def __init__(self, catalog, delegate) -> None:
        self.catalog = catalog
        self.delegate = delegate
        self.read_calls = 0
        self.write_calls = 0

    async def execute(self, request):
        contract = self.catalog.resolve(request.tool_id, request.tool_version)
        if contract.capability is ToolCapability.WRITE:
            self.write_calls += 1
        else:
            self.read_calls += 1
        return await self.delegate.execute(request)


class AtomicCheckpointPort:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.checkpoints: dict[tuple[str, int], ExecutionCheckpoint] = {}
        self.latest: dict[str, int] = {}
        self.states: dict[tuple[str, int], str] = {}
        self.claims: dict[tuple[str, int], str] = {}
        self.save_error: Exception | None = None

    async def save(self, checkpoint: ExecutionCheckpoint) -> None:
        self.events.append("save")
        if self.save_error is not None:
            raise self.save_error
        checkpoint.verify()
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
        if self.states[key] == "LIVE":
            raise ResumeClaimError("checkpoint already has a live claim")
        if self.states[key] == "CONSUMED":
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


class IdempotentApprovalPort:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.calls: list[ApprovalRequestId] = []
        self.requests: dict[ApprovalRequestId, ApprovalRequest] = {}
        self.fail_once = False

    async def publish(self, request: ApprovalRequest) -> None:
        self.events.append("publish")
        self.calls.append(request.approval_request_id)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("publication acknowledgement lost")
        previous = self.requests.setdefault(request.approval_request_id, request)
        if previous != request:
            raise RuntimeError("ApprovalRequestId collision")


class RecordingVerifier:
    def __init__(self, *, valid: bool = True, error: Exception | None = None) -> None:
        self.valid = valid
        self.error = error
        self.calls = 0

    def verify(self, evidence: ApprovalEvidence) -> bool:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.valid


def _request(execution_id: str, *, business_identity: bool = True) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs=load_value_object(EXAMPLE / "input.json", "input"),
        runtime_context=load_value_object(EXAMPLE / "context.json", "context"),
        principal=load_principal(EXAMPLE / "principal.json"),
        business_operation_id=(BUSINESS_OPERATION_ID if business_identity else None),
    )


def _execute_checkpointed(
    fixture: str,
    *,
    request: ExecutionRequest | None = None,
) -> tuple[CheckpointedExecution, object, ExecutionRequest, RecordingToolPort]:
    catalog = load_tool_catalog(EXAMPLE / "tools.json")
    skill = NslCompiler(catalog).compile(SOURCE).skill
    fixture_document = load_json_document(EXAMPLE / "fixtures" / fixture)
    mock = MockToolExecutor(
        catalog,
        load_mock_handlers_document(fixture_document, catalog),
    )
    port = RecordingToolPort(catalog, mock)
    actual_request = request or _request(f"exec-0062-{fixture}")
    outcome = asyncio.run(
        RuntimeEngine(catalog).execute_checkpointed(
            skill,
            actual_request,
            port,
            InMemoryAuditSink(),
        )
    )
    return outcome, skill, actual_request, port


def _pre_write_evidence(outcome: CheckpointedExecution) -> PreWriteEvidence:
    assert outcome.result.validation is not None
    return PreWriteEvidence(
        validation=outcome.result.validation,
        runtime_preflight_passed=True,
        current_policy_allowed=True,
        tool_preconditions_passed=True,
        business_preconditions_passed=True,
        intent_binding_valid=True,
        idempotency_ready=True,
        durability_ready=True,
        trust_valid=True,
        replay_live_write_allowed=True,
    )


def _approval_request(skill, request: ExecutionRequest) -> ApprovalRequest:
    config = json.loads((EXAMPLE / "approval.json").read_text(encoding="utf-8"))
    inputs = request.inputs
    arguments = {
        "account_code": ValueEnvelope.complete(
            inputs["account_code"],
            domain("AccountCode"),
            DataClassification.CONFIDENTIAL,
        ),
        "project_code": ValueEnvelope.complete(
            inputs["project_code"],
            domain("ProjectCode"),
            DataClassification.CONFIDENTIAL,
        ),
        "accounting_period": ValueEnvelope.complete(
            inputs["accounting_period"],
            domain("AccountingPeriod"),
            DataClassification.CONFIDENTIAL,
        ),
        "amount": ValueEnvelope.complete(
            inputs["amount"],
            money_type("KRW"),
            DataClassification.CONFIDENTIAL,
        ),
    }
    payload_hash = compute_payload_hash(arguments)
    intent_hash = compute_write_intent_hash(
        skill_semantic_hash=skill.semantic_hash,
        write_node_id=config["write_node_id"],
        tool_id=config["tool_id"],
        payload_hash=payload_hash,
    )
    return ApprovalRequest(
        tenant_id=request.principal.tenant_id,
        execution_id=request.execution_id,
        business_operation_id=BUSINESS_OPERATION_ID,
        approval_id=config["approval_id"],
        approval_request_id=ApprovalRequestId(config["approval_request_id"]),
        skill_id=skill.skill_id,
        skill_version=skill.skill_version,
        skill_semantic_hash=skill.semantic_hash,
        write_node_id=config["write_node_id"],
        tool_id=config["tool_id"],
        payload_hash=payload_hash,
        write_intent_hash=intent_hash,
        risk_level=RiskLevel(config["risk_level"]),
        message=config["message"],
        expires_at=NOW + timedelta(seconds=config["ttl_seconds"]),
        display_safe_summary=config["display_safe_summary"],
        protected_payload_ref=config["protected_payload_ref"],
        data_classification=DataClassification(config["data_classification"]),
    )


def _suspended_flow():
    validation, skill, request, tools = _execute_checkpointed("success.json")
    pre_write_evidence = _pre_write_evidence(validation)
    decision = PreWriteEvaluator().evaluate(pre_write_evidence)
    assert decision.eligibility is PreWriteEligibility.REQUIRES_APPROVAL
    assert validation.checkpoint is not None
    context = CheckpointCoordinator().restore(validation.checkpoint, skill, request)
    approval_request = _approval_request(skill, request)
    events: list[str] = []
    checkpoints = AtomicCheckpointPort(events)
    approvals = IdempotentApprovalPort(events)
    suspension = asyncio.run(
        ApprovalSuspensionCoordinator().suspend(
            context,
            approval_request,
            checkpoints,
            approvals,
            pre_write_evidence,
        )
    )
    return (
        validation,
        skill,
        request,
        tools,
        context,
        approval_request,
        checkpoints,
        approvals,
        suspension,
    )


def _evidence(
    request: ApprovalRequest,
    *,
    decision: ApprovalDecision = ApprovalDecision.APPROVED,
    expires_at: datetime | None = None,
    **changes: object,
) -> ApprovalEvidence:
    values: dict[str, object] = {
        "tenant_id": request.tenant_id,
        "execution_id": request.execution_id,
        "business_operation_id": request.business_operation_id,
        "approval_id": request.approval_id,
        "approval_request_id": request.approval_request_id,
        "skill_semantic_hash": request.skill_semantic_hash,
        "decision": decision,
        "approver_id": "approver-voucher-0062",
        "decided_at": NOW,
        "expires_at": request.expires_at if expires_at is None else expires_at,
        "payload_hash": request.payload_hash,
        "write_intent_hash": request.write_intent_hash,
        "evidence_integrity": "signed-evidence-voucher-0062",
    }
    values.update(changes)
    return ApprovalEvidence(**values)  # type: ignore[arg-type]


def _resume(
    skill,
    request: ExecutionRequest,
    approval_request: ApprovalRequest,
    checkpoint: ExecutionCheckpoint,
    checkpoint_port: AtomicCheckpointPort,
    *,
    evidence: ApprovalEvidence | None = None,
    current_payload_hash: PayloadHash | None = None,
    current_write_intent_hash: str | None = None,
    verifier: RecordingVerifier | None = None,
    clock: RecordedApprovalClock | None = None,
    claim_id: str = "claim-voucher-0062",
):
    return asyncio.run(
        ApprovalResumeCoordinator().resume(
            token=ResumeToken(
                execution_id=request.execution_id,
                business_operation_id=BUSINESS_OPERATION_ID,
                checkpoint_version=checkpoint.checkpoint_version,
                approval_evidence=evidence or _evidence(approval_request),
            ),
            skill=skill,
            request=request,
            approval_id=approval_request.approval_id,
            write_node_id=approval_request.write_node_id,
            current_write_intent_hash=(
                current_write_intent_hash or approval_request.write_intent_hash
            ),
            current_payload_hash=(
                current_payload_hash or approval_request.payload_hash
            ),
            claim_id=claim_id,
            checkpoint_port=checkpoint_port,
            verifier=verifier or RecordingVerifier(),
            clock=clock or RecordedApprovalClock(NOW),
        )
    )


def test_checkpointed_execution_captures_only_completed_business_execution() -> None:
    completed, skill, request, port = _execute_checkpointed("success.json")

    assert completed.result.status is ExecutionStatus.COMPLETED
    assert completed.checkpoint is not None
    restored = CheckpointCoordinator().restore(completed.checkpoint, skill, request)
    assert tuple(restored.checks) == completed.result.checks
    assert tuple(restored.outputs) == completed.result.outputs
    assert restored.usage() == completed.result.resources
    assert (port.read_calls, port.write_calls) == (3, 0)

    failed, _, _, failed_port = _execute_checkpointed("missing_account.json")
    assert failed.result.status is ExecutionStatus.TOOL_ERROR
    assert failed.checkpoint is None
    assert (failed_port.read_calls, failed_port.write_calls) == (1, 0)


def test_checkpointed_execution_rejects_missing_identity_and_invalid_coordinator() -> None:
    catalog = load_tool_catalog(EXAMPLE / "tools.json")
    skill = NslCompiler(catalog).compile(SOURCE).skill
    fixture = load_json_document(EXAMPLE / "fixtures" / "success.json")
    mock = MockToolExecutor(
        catalog,
        load_mock_handlers_document(fixture, catalog),
    )
    port = RecordingToolPort(catalog, mock)
    engine = RuntimeEngine(catalog)

    with pytest.raises(RuntimeContractError, match="business operation"):
        asyncio.run(
            engine.execute_checkpointed(
                skill,
                _request("exec-no-business-id", business_identity=False),
                port,
                InMemoryAuditSink(),
            )
        )
    with pytest.raises(TypeError, match="CheckpointCoordinator"):
        asyncio.run(
            engine.execute_checkpointed(
                skill,
                _request("exec-invalid-coordinator"),
                port,
                InMemoryAuditSink(),
                checkpoint_coordinator=object(),  # type: ignore[arg-type]
            )
        )

    assert (port.read_calls, port.write_calls) == (0, 0)


def test_checkpointed_execution_value_rejects_inconsistent_pairs() -> None:
    completed, _, _, _ = _execute_checkpointed("success.json")
    assert completed.checkpoint is not None
    failed_result = replace(completed.result, status=ExecutionStatus.FAILED)

    assert CheckpointedExecution(failed_result, None).checkpoint is None
    with pytest.raises(TypeError, match="ExecutionResult"):
        CheckpointedExecution(object(), None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ExecutionCheckpoint"):
        CheckpointedExecution(completed.result, object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="only completed"):
        CheckpointedExecution(failed_result, completed.checkpoint)
    with pytest.raises(ValueError, match="requires a checkpoint"):
        CheckpointedExecution(completed.result, None)
    with pytest.raises(ValueError, match="skill_id"):
        CheckpointedExecution(
            completed.result,
            replace(completed.checkpoint, skill_id="OTHER.SKILL"),
        )


def test_ac_w01_passing_validation_persists_before_approval_publication() -> None:
    (
        validation,
        _,
        _,
        tools,
        _,
        approval_request,
        checkpoints,
        approvals,
        suspension,
    ) = _suspended_flow()

    assert validation.result.validation is not None
    assert validation.result.validation.status is ValidationSummaryStatus.ALL_PASS
    assert checkpoints.events == ["save", "publish"]
    assert approvals.calls == [ApprovalRequestId("approval-request-voucher-0062")]
    assert suspension.checkpoint.checkpoint_version == 2
    assert suspension.result.status is ExecutionStatus.WAITING_FOR_APPROVAL
    assert suspension.result.validation == validation.result.validation
    assert (
        suspension.result.pre_write_eligibility
        is PreWriteEligibility.REQUIRES_APPROVAL
    )
    assert suspension.result.approval is not None
    assert suspension.result.approval.status is ApprovalStatus.PENDING
    assert approval_request.to_data()["protected_payload_ref"].startswith(
        "snapshot://"
    )
    assert (tools.read_calls, tools.write_calls) == (3, 0)


@pytest.mark.parametrize(
    ("fixture", "expected_status"),
    (
        ("invalid_account.json", ValidationSummaryStatus.HAS_FAIL),
        ("partial_account.json", ValidationSummaryStatus.HAS_UNKNOWN),
        ("unknown_account.json", ValidationSummaryStatus.HAS_UNKNOWN),
    ),
)
def test_ac_w01_nonpassing_validation_cannot_publish_approval(
    fixture: str,
    expected_status: ValidationSummaryStatus,
) -> None:
    outcome, skill, request, tools = _execute_checkpointed(fixture)
    assert outcome.checkpoint is not None
    pre_write_evidence = _pre_write_evidence(outcome)
    decision = PreWriteEvaluator().evaluate(pre_write_evidence)
    context = CheckpointCoordinator().restore(outcome.checkpoint, skill, request)
    events: list[str] = []

    assert outcome.result.validation is not None
    assert outcome.result.validation.status is expected_status
    assert decision.eligibility is PreWriteEligibility.BLOCKED
    with pytest.raises(ValueError, match="complete passing validation"):
        asyncio.run(
            ApprovalSuspensionCoordinator().suspend(
                context,
                _approval_request(skill, request),
                AtomicCheckpointPort(events),
                IdempotentApprovalPort(events),
                pre_write_evidence,
            )
        )
    assert events == []
    assert tools.write_calls == 0


@pytest.mark.parametrize(
    ("evidence_factory", "error_type", "message"),
    (
        (lambda evidence: None, TypeError, "PreWriteEvidence"),
        (
            lambda evidence: replace(
                evidence,
                validation=ValidationSummary.from_checks(()),
            ),
            ValueError,
            "validation binding mismatch",
        ),
        (
            lambda evidence: replace(evidence, trust_valid=False),
            ValueError,
            "REQUIRES_APPROVAL",
        ),
    ),
)
def test_ac_w01_suspend_requires_bound_prewrite_evidence(
    evidence_factory,
    error_type: type[Exception],
    message: str,
) -> None:
    outcome, skill, request, tools = _execute_checkpointed("success.json")
    assert outcome.checkpoint is not None
    context = CheckpointCoordinator().restore(outcome.checkpoint, skill, request)
    evidence = evidence_factory(_pre_write_evidence(outcome))
    events: list[str] = []

    with pytest.raises(error_type, match=message):
        asyncio.run(
            ApprovalSuspensionCoordinator().suspend(
                context,
                _approval_request(skill, request),
                AtomicCheckpointPort(events),
                IdempotentApprovalPort(events),
                evidence,
            )
        )
    assert events == []
    assert tools.write_calls == 0


def test_ac_w01_valid_evidence_resumes_once_without_write() -> None:
    (
        _,
        skill,
        request,
        tools,
        _,
        approval_request,
        checkpoints,
        _,
        suspension,
    ) = _suspended_flow()
    checkpoints.events.clear()

    outcome = _resume(
        skill,
        request,
        approval_request,
        suspension.checkpoint,
        checkpoints,
    )

    assert checkpoints.events == ["claim", "consume"]
    assert outcome.validation == ApprovalValidationResult(
        ApprovalEvidenceValidation.VALID,
        ApprovalStatus.APPROVED,
        None,
    )
    assert outcome.claim_consumed is True
    assert tools.write_calls == 0

    with pytest.raises(ResumeClaimError, match="consumed"):
        _resume(
            skill,
            request,
            approval_request,
            suspension.checkpoint,
            checkpoints,
            claim_id="claim-voucher-duplicate",
        )
    assert tools.write_calls == 0


@pytest.mark.parametrize(
    ("decision", "clock_delta", "status", "validation"),
    (
        (
            ApprovalDecision.REJECTED,
            timedelta(0),
            ApprovalStatus.REJECTED,
            ApprovalEvidenceValidation.VALID,
        ),
        (
            ApprovalDecision.APPROVED,
            timedelta(0),
            ApprovalStatus.EXPIRED,
            ApprovalEvidenceValidation.EXPIRED,
        ),
        (
            ApprovalDecision.APPROVED,
            timedelta(microseconds=1),
            ApprovalStatus.EXPIRED,
            ApprovalEvidenceValidation.EXPIRED,
        ),
    ),
)
def test_ac_w01_rejection_and_expiry_boundaries_never_write(
    decision: ApprovalDecision,
    clock_delta: timedelta,
    status: ApprovalStatus,
    validation: ApprovalEvidenceValidation,
) -> None:
    (
        _,
        skill,
        request,
        tools,
        _,
        approval_request,
        checkpoints,
        _,
        suspension,
    ) = _suspended_flow()
    expires_at = approval_request.expires_at
    assert expires_at is not None
    evidence = _evidence(approval_request, decision=decision)
    clock_value = NOW if decision is ApprovalDecision.REJECTED else expires_at + clock_delta

    outcome = _resume(
        skill,
        request,
        approval_request,
        suspension.checkpoint,
        checkpoints,
        evidence=evidence,
        clock=RecordedApprovalClock(clock_value),
    )

    assert outcome.validation.approval_status is status
    assert outcome.validation.validation is validation
    assert outcome.validation.approval_gate_passed is False
    assert outcome.claim_consumed is True
    assert tools.write_calls == 0


def test_ac_w01_one_microsecond_before_expiry_is_valid_without_write() -> None:
    (
        _,
        skill,
        request,
        tools,
        _,
        approval_request,
        checkpoints,
        _,
        suspension,
    ) = _suspended_flow()
    expires_at = approval_request.expires_at
    assert expires_at is not None

    outcome = _resume(
        skill,
        request,
        approval_request,
        suspension.checkpoint,
        checkpoints,
        clock=RecordedApprovalClock(expires_at - timedelta(microseconds=1)),
    )

    assert outcome.validation.approval_gate_passed is True
    assert tools.write_calls == 0


def test_ac_w01_tampering_and_cross_tenant_evidence_release_claim() -> None:
    for case in ("coordinated-hash-tamper", "cross-tenant"):
        (
            _,
            skill,
            request,
            tools,
            _,
            approval_request,
            checkpoints,
            _,
            suspension,
        ) = _suspended_flow()
        checkpoints.events.clear()
        if case == "coordinated-hash-tamper":
            changed_payload = PayloadHash("sha256:" + "d" * 64)
            changed_intent = compute_write_intent_hash(
                skill_semantic_hash=skill.semantic_hash,
                write_node_id=approval_request.write_node_id,
                tool_id=approval_request.tool_id,
                payload_hash=changed_payload,
            )
            evidence = _evidence(
                approval_request,
                payload_hash=changed_payload,
                write_intent_hash=changed_intent,
            )
            outcome = _resume(
                skill,
                request,
                approval_request,
                suspension.checkpoint,
                checkpoints,
                evidence=evidence,
                current_payload_hash=changed_payload,
                current_write_intent_hash=changed_intent,
            )
            assert (
                outcome.validation.validation
                is ApprovalEvidenceValidation.INTENT_MISMATCH
            )
        else:
            outcome = _resume(
                skill,
                request,
                approval_request,
                suspension.checkpoint,
                checkpoints,
                evidence=_evidence(approval_request, tenant_id="other-tenant"),
            )
            assert (
                outcome.validation.validation
                is ApprovalEvidenceValidation.INVALID
            )
        assert outcome.claim_consumed is False
        assert checkpoints.events == ["claim", "release"]
        assert tools.write_calls == 0


def test_ac_w01_resume_dependency_failure_releases_for_retry() -> None:
    (
        _,
        skill,
        request,
        tools,
        _,
        approval_request,
        checkpoints,
        _,
        suspension,
    ) = _suspended_flow()
    checkpoints.events.clear()

    with pytest.raises(ConnectionError, match="verification unavailable"):
        _resume(
            skill,
            request,
            approval_request,
            suspension.checkpoint,
            checkpoints,
            verifier=RecordingVerifier(
                error=ConnectionError("verification unavailable")
            ),
        )
    assert checkpoints.events == ["claim", "release"]

    retry = _resume(
        skill,
        request,
        approval_request,
        suspension.checkpoint,
        checkpoints,
        claim_id="claim-voucher-retry",
    )
    assert retry.validation.approval_gate_passed is True
    assert tools.write_calls == 0


def test_ac_w01_checkpoint_failure_blocks_publish_and_publish_retry_is_idempotent() -> None:
    validation, skill, request, tools = _execute_checkpointed("success.json")
    assert validation.checkpoint is not None
    context = CheckpointCoordinator().restore(validation.checkpoint, skill, request)
    approval_request = _approval_request(skill, request)
    pre_write_evidence = _pre_write_evidence(validation)
    events: list[str] = []
    checkpoints = AtomicCheckpointPort(events)
    approvals = IdempotentApprovalPort(events)
    checkpoints.save_error = OSError("checkpoint unavailable")

    with pytest.raises(OSError, match="checkpoint unavailable"):
        asyncio.run(
            ApprovalSuspensionCoordinator().suspend(
                context,
                approval_request,
                checkpoints,
                approvals,
                pre_write_evidence,
            )
        )
    assert events == ["save"]
    assert approvals.calls == []

    checkpoints.save_error = None
    approvals.fail_once = True
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        asyncio.run(
            ApprovalSuspensionCoordinator().suspend(
                context,
                approval_request,
                checkpoints,
                approvals,
                pre_write_evidence,
            )
        )
    suspension = asyncio.run(
        ApprovalSuspensionCoordinator().suspend(
            context,
            approval_request,
            checkpoints,
            approvals,
            pre_write_evidence,
        )
    )

    assert approvals.calls == [
        approval_request.approval_request_id,
        approval_request.approval_request_id,
    ]
    assert list(approvals.requests) == [approval_request.approval_request_id]
    assert suspension.checkpoint.checkpoint_version == 4
    assert tools.write_calls == 0
