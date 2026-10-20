from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from nsl.approval import (
    ApprovalEvidence,
    ApprovalRequest,
    ApprovalResumeCoordinator,
    ApprovalSuspensionCoordinator,
    RecordedApprovalClock,
    ResumeToken,
)
from nsl.audit import AuditRecorder, InMemoryAuditSink
from nsl.checkpoints import CheckpointCoordinator
from nsl.cli_config import (
    load_json_document,
    load_mock_handlers_document,
    load_principal,
    load_value_object,
)
from nsl.compiler import NslCompiler
from nsl.core import (
    ApprovalDecision,
    ApprovalRequestId,
    ApprovalStatus,
    BusinessOperationId,
    CommitState,
    DataClassification,
    ExecutionStatus,
    IdempotencyKey,
    RiskLevel,
    ValueEnvelope,
    VerificationStatus,
)
from nsl.idempotency import (
    IdempotencyCoordinator,
    IdempotencyDisposition,
    ProviderIdempotencyEvidence,
)
from nsl.ir import NsoCodec
from nsl.preflight import ExecutionTrustProfile
from nsl.protected_snapshots import ProtectedSnapshotStore
from nsl.runtime import ControlledExecutionPlan, ExecutionRequest, RuntimeEngine
from nsl.runtime_models import ValidationSummaryStatus, WriteSummary
from nsl.security import DataHandlingPolicy
from nsl.tools import MockToolExecutor
from nsl.trust import PackageTrustEvidence, TrustStatus, VerifiedSkillArtifact
from nsl.validation import (
    BusinessPreconditionEvidence,
    BusinessPreconditionMechanism,
    PolicyDecision,
    PolicyEffect,
    WriteGuard,
    WriteGuardRequest,
    write_policy_request,
)
from nsl.verification import PostWriteVerifier, VerificationResult
from nsl.write_audit import (
    ProtectedWriteAuditEvidence,
    WriteAuditEventType,
    WriteAuditIdentity,
    WriteAuditRecorder,
)
from nsl.write_execution import (
    TrustedCommitAwareWriteExecutor,
    TrustedWriteExecutionCoordinator,
    WriteCallRequestBuilder,
    WriteCommitDecision,
    WriteCommitUnknown,
    WriteCommitted,
    WriteExecutionCoordinator,
    WriteNotCommitted,
    WriteOutcomeCoordinator,
    WriteOutcomeIdentity,
    WriteResultEnvelope,
)
from nsl.write_replay import (
    WriteReplayEvidenceComposer,
    WriteReplayEvidenceLoader,
    WriteReplayIdentity,
)
from nsl.write_replay_execution import ReplayWriteSession
from tests.test_v0_2_controlled_runtime import (
    _ApprovalVerifier,
    _IdempotencyPort,
    _LifecyclePort,
    _OutcomePort,
    _VaultProtector,
    _VerificationPort,
    _WriteEvidencePort,
    _contract_evidence,
    _execution_evidence,
    _run_voucher_e2e,
)
from tests.test_v0_2_project_validation import (
    EXAMPLE,
    WRITE_SOURCE,
    _catalog,
)


NOW = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)


class _ProjectWritePort:
    def __init__(self, output_type, events: list[str]) -> None:
        self.output_type = output_type
        self.events = events
        self.calls = 0

    async def execute(self, request):
        self.events.append("PROVIDER_WRITE")
        self.calls += 1
        return WriteCommitted(
            WriteResultEnvelope(
                identity=WriteOutcomeIdentity.from_request(request),
                value=ValueEnvelope.complete(
                    {"project_code": "PROJECT-NEX-0064", "status": "DRAFT"},
                    self.output_type,
                    DataClassification.INTERNAL,
                ),
                business_reference="PROJECT-NEX-0064",
                provider_transaction_id="provider-tx-project-0065",
                committed_at=NOW + timedelta(seconds=2),
                evidence_refs=("provider-commit:0065",),
            )
        )


def _compiled():
    catalog, write_contract = _catalog()
    skill = NslCompiler(catalog).compile(WRITE_SOURCE).skill
    return catalog, write_contract, skill


def _request(execution_id: str = "exec-project-e2e-0065") -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs=load_value_object(EXAMPLE / "input.json", "input"),
        runtime_context=load_value_object(EXAMPLE / "context.json", "context"),
        principal=load_principal(EXAMPLE / "principal_write.json"),
        business_operation_id=BusinessOperationId("business-project-0065"),
    )


def _read_tools(catalog) -> MockToolExecutor:
    fixture = load_json_document(EXAMPLE / "fixtures" / "success.json")
    return MockToolExecutor(
        catalog,
        load_mock_handlers_document(fixture, catalog),
    )


def _policy_decision(intent, context, contract_evidence) -> PolicyDecision:
    request = write_policy_request(intent, context, contract_evidence)
    return PolicyDecision(
        decision_id="policy-decision-0065",
        policy_id="project-write-policy",
        policy_version="1",
        effect=PolicyEffect.ALLOW,
        tenant_id=request.tenant_id,
        subject_id=request.subject_id,
        action=request.action,
        tool_id=request.tool_id,
        risk_level=request.risk_level,
        business_operation_id=request.business_operation_id,
        write_node_id=request.write_node_id,
        write_intent_hash=request.write_intent_hash,
        evidence_ref="policy-evidence:0065",
        evaluated_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=1),
    )


def _business_precondition(intent, contract) -> BusinessPreconditionEvidence:
    return BusinessPreconditionEvidence(
        evidence_id="precondition-0065",
        tenant_id=intent.tenant_id,
        business_operation_id=intent.business_operation_id,
        write_node_id=intent.node_id,
        tool_id=intent.tool_id,
        contract_hash=contract.contract_hash,
        payload_hash=intent.payload_hash,
        write_intent_hash=intent.write_intent_hash,
        mechanism=BusinessPreconditionMechanism.ETAG,
        evidence_ref="etag:opaque:project-0065",
        observed_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=1),
        provider_atomic_enforcement=True,
    )


def _trust(skill):
    package_hash = "sha256:" + "c" * 64
    package_id = "nsp:" + package_hash
    artifact = VerifiedSkillArtifact(
        skill,
        "sha256:" + sha256(NsoCodec.encode(skill)).hexdigest(),
        PackageTrustEvidence(
            package_id=package_id,
            package_hash=package_hash,
            publisher_id="publisher.project",
            signing_key_id="key-project-0065",
            verified_at=NOW - timedelta(seconds=10),
            trust_status=TrustStatus.VERIFIED,
            trust_policy_id="trust-policy-0065",
        ),
    )
    profile = ExecutionTrustProfile.production(
        tenant_id="tenant-project",
        trust_policy_id="trust-policy-0065",
        expected_package_id=package_id,
        expected_artifact_hash=artifact.artifact_hash,
        expected_semantic_hash=artifact.semantic_hash,
        trusted_publisher_ids=frozenset({"publisher.project"}),
        trusted_signing_key_ids=frozenset({"key-project-0065"}),
        max_trust_age_seconds=60,
    )
    return artifact, profile


async def _run_project_e2e():
    catalog, write_contract, skill = _compiled()
    request = _request()
    tools = _read_tools(catalog)
    engine = RuntimeEngine(catalog)
    prefix = await engine.execute_controlled_prefix(
        skill,
        request,
        tools,
        InMemoryAuditSink(),
    )
    assert prefix.checkpoint is not None
    assert prefix.result.validation is not None
    assert prefix.result.validation.status is ValidationSummaryStatus.ALL_PASS
    context = CheckpointCoordinator().restore(prefix.checkpoint, skill, request)
    intent = await engine.prepare_controlled_write_intent(context, tools)
    plan = ControlledExecutionPlan.from_skill(skill)

    approval_request = ApprovalRequest(
        tenant_id=context.tenant_id,
        execution_id=context.identity.execution_id,
        business_operation_id=intent.business_operation_id,
        approval_id=plan.approval.approval_id,
        approval_request_id=ApprovalRequestId("approval-project-0065"),
        skill_id=skill.skill_id,
        skill_version=skill.skill_version,
        skill_semantic_hash=skill.semantic_hash,
        write_node_id=intent.node_id,
        tool_id=intent.tool_id,
        payload_hash=intent.payload_hash,
        write_intent_hash=intent.write_intent_hash,
        risk_level=RiskLevel.R3,
        message=plan.approval.message,
        expires_at=NOW + timedelta(minutes=5),
        display_safe_summary={"document_type": "project-draft"},
        protected_payload_ref="approval-payload:0065",
        data_classification=DataClassification.INTERNAL,
    )
    suspension_evidence = replace(
        _execution_evidence(context),
        approval_status=ApprovalStatus.NOT_REQUESTED,
        approval_evidence_valid=False,
        current_policy_allowed=True,
        tool_preconditions_passed=True,
        business_preconditions_passed=True,
    )
    lifecycle = _LifecyclePort()
    suspension = await ApprovalSuspensionCoordinator().suspend(
        context,
        approval_request,
        lifecycle,
        lifecycle,
        suspension_evidence,
    )
    approval_evidence = ApprovalEvidence(
        tenant_id=context.tenant_id,
        execution_id=context.identity.execution_id,
        business_operation_id=intent.business_operation_id,
        approval_id=plan.approval.approval_id,
        approval_request_id=approval_request.approval_request_id,
        skill_semantic_hash=skill.semantic_hash,
        decision=ApprovalDecision.APPROVED,
        approver_id="project-manager-0065",
        decided_at=NOW,
        expires_at=approval_request.expires_at,
        payload_hash=intent.payload_hash,
        write_intent_hash=intent.write_intent_hash,
        evidence_integrity="approval-signature:0065",
    )
    resumed = await ApprovalResumeCoordinator().resume(
        token=ResumeToken(
            execution_id=request.execution_id,
            business_operation_id=intent.business_operation_id,
            checkpoint_version=suspension.checkpoint.checkpoint_version,
            approval_evidence=approval_evidence,
        ),
        skill=skill,
        request=request,
        approval_id=plan.approval.approval_id,
        write_node_id=intent.node_id,
        current_write_intent_hash=intent.write_intent_hash,
        current_payload_hash=intent.payload_hash,
        claim_id="claim-project-0065",
        checkpoint_port=lifecycle,
        verifier=_ApprovalVerifier("approval-signature:0065"),
        clock=RecordedApprovalClock(NOW + timedelta(seconds=1)),
    )
    context = resumed.context

    idempotency_port = _IdempotencyPort()
    idempotency = IdempotencyCoordinator(idempotency_port)
    provider_evidence = ProviderIdempotencyEvidence(
        tool_id=write_contract.tool_id,
        contract_hash=write_contract.contract_hash,
        retention_policy="PROVIDER_DEFINED",
        provider_guarantee_ref="provider-idempotency:0065",
        verified_at=NOW,
        guaranteed_until=NOW + timedelta(days=30),
        durable_duplicate_guarantee=True,
    )
    reserve_arguments = {
        "intent": intent,
        "context": context,
        "contract": write_contract,
        "provider_evidence": provider_evidence,
        "now": NOW,
        "required_until": NOW + timedelta(days=7),
    }
    reservation = await idempotency.reserve(**reserve_arguments)
    retry_reservation = await idempotency.reserve(**reserve_arguments)

    contract_evidence = _contract_evidence(write_contract)
    precondition = _business_precondition(intent, write_contract)
    decision = WriteGuard().evaluate(
        WriteGuardRequest(
            intent=intent,
            context=context,
            contract_evidence=contract_evidence,
            execution_evidence=_execution_evidence(context),
            policy_decision=_policy_decision(intent, context, contract_evidence),
            business_preconditions=precondition,
        ),
        now=NOW,
    )
    call_request = WriteCallRequestBuilder().build(
        intent=intent,
        context=context,
        contract_evidence=contract_evidence,
        reservation=reservation,
        approval_request_id=approval_request.approval_request_id,
        invocation_id="write-invocation-0065",
        business_preconditions=precondition,
        trace_refs=("trace-project:0065",),
    )

    write_events: list[str] = []
    write_evidence = _WriteEvidencePort(write_events, "0065")
    write_provider = _ProjectWritePort(write_contract.output_type, write_events)
    outcome_port = _OutcomePort("0065")
    artifact, profile = _trust(skill)
    commit = await TrustedCommitAwareWriteExecutor(
        TrustedWriteExecutionCoordinator(
            WriteExecutionCoordinator(write_provider, write_evidence)
        ),
        WriteOutcomeCoordinator(outcome_port),
    ).execute(
        artifact=artifact,
        profile=profile,
        trust_checked_at=NOW,
        request=call_request,
        decision=decision,
        context=context,
        requested_at=NOW,
        recorded_at=NOW + timedelta(seconds=2),
    )
    assert isinstance(commit.outcome, WriteCommitted)

    verification_port = _VerificationPort("0065")
    verification = await PostWriteVerifier().verify(
        commit.outcome.result,
        write_contract,
        context,
        verification_port,
    )
    result = await engine.complete_controlled_execution(
        context=context,
        approval_request_id=approval_request.approval_request_id,
        commit=commit,
        verification=verification,
        tools=tools,
        audit_sink=InMemoryAuditSink(),
    )

    audit_sink = InMemoryAuditSink()
    audit = WriteAuditRecorder(
        AuditRecorder(
            audit_sink,
            DataHandlingPolicy(),
            execution_id=request.execution_id,
            skill_id=skill.skill_id,
            skill_version=skill.skill_version,
            semantic_hash=skill.semantic_hash,
            principal=request.principal,
        )
    )
    audit_identity = WriteAuditIdentity.from_write_outcome(commit.outcome.identity)
    protected = ProtectedWriteAuditEvidence(
        data_classification=DataClassification.INTERNAL,
        protected_payload_ref="write-payload:0065",
        display_safe_summary={"document_type": "project-draft"},
        evidence_refs=("approval-signature:0065",),
        outcome_status="RECORDED",
    )
    for event_type in (
        WriteAuditEventType.WRITE_INTENT_CREATED,
        WriteAuditEventType.APPROVAL_REQUESTED,
        WriteAuditEventType.EXECUTION_SUSPENDED,
        WriteAuditEventType.APPROVAL_APPROVED,
        WriteAuditEventType.EXECUTION_RESUMED,
        WriteAuditEventType.WRITE_REQUESTED,
        WriteAuditEventType.WRITE_COMMIT_CONFIRMED,
    ):
        audit.record(event_type, audit_identity, protected)
    audit.record_verification(
        verification,
        data_classification=DataClassification.INTERNAL,
        display_safe_summary={"document_type": "project-draft"},
        protected_payload_ref="verification-payload:0065",
    )

    snapshots = ProtectedSnapshotStore(_VaultProtector("0065"), lambda: NOW)
    composer = WriteReplayEvidenceComposer(snapshots, retention_days=30)
    loader = WriteReplayEvidenceLoader(snapshots)
    replay_identity = WriteReplayIdentity(
        tenant_id=context.tenant_id,
        execution_id=context.identity.execution_id,
        business_operation_id=intent.business_operation_id,
        skill_semantic_hash=skill.semantic_hash,
        node_id=intent.node_id,
        approval_request_id=approval_request.approval_request_id,
        payload_hash=intent.payload_hash,
        write_intent_hash=intent.write_intent_hash,
        idempotency_key=reservation.evidence.idempotency_key,
    )
    replay_bundle = composer.compose(
        replay_identity,
        approval=approval_evidence,
        write_request=write_evidence.records[0],
        commit_outcome=outcome_port.records[0],
        verification_outcome=verification,
    )
    replay = ReplayWriteSession.open(replay_bundle, loader, request.principal)
    replay_decision = await replay.execute_write(call_request)
    return {
        "result": result,
        "reservation": reservation,
        "retry_reservation": retry_reservation,
        "idempotency_calls": idempotency_port.calls,
        "write_events": write_events,
        "write_calls": write_provider.calls,
        "verification_calls": verification_port.calls,
        "audit_events": tuple(event.event_type for event in audit_sink.events),
        "replay_decision": replay_decision,
        "replay_write_calls": replay.write_call_count,
    }


def _prepared_context():
    catalog, write_contract, skill = _compiled()
    request = _request("exec-project-outcomes-0065")
    tools = _read_tools(catalog)
    engine = RuntimeEngine(catalog)
    prefix = asyncio.run(
        engine.execute_controlled_prefix(
            skill,
            request,
            tools,
            InMemoryAuditSink(),
        )
    )
    assert prefix.checkpoint is not None
    context = CheckpointCoordinator().restore(prefix.checkpoint, skill, request)
    intent = asyncio.run(engine.prepare_controlled_write_intent(context, tools))
    context.approvals.records[intent.approval_id] = ApprovalStatus.APPROVED
    return engine, tools, write_contract, context, intent


def _identity(intent) -> WriteOutcomeIdentity:
    return WriteOutcomeIdentity(
        tenant_id=intent.tenant_id,
        business_operation_id=intent.business_operation_id,
        invocation_id="write-invocation-project-outcome-0065",
        node_id=intent.node_id,
        tool_id=intent.tool_id,
        idempotency_key=IdempotencyKey("idem:sha256:" + "e" * 64),
        payload_hash=intent.payload_hash,
        approval_request_id=ApprovalRequestId("approval-project-0065"),
        write_intent_hash=intent.write_intent_hash,
    )


def _committed(intent, output_type) -> tuple[WriteCommitDecision, VerificationResult]:
    identity = _identity(intent)
    result = WriteResultEnvelope(
        identity=identity,
        value=ValueEnvelope.complete(
            {"project_code": "PROJECT-NEX-0064", "status": "DRAFT"},
            output_type,
            DataClassification.INTERNAL,
        ),
        business_reference="PROJECT-NEX-0064",
        provider_transaction_id="provider-project-outcome-0065",
        committed_at=NOW,
        evidence_refs=("provider-evidence:0065",),
    )
    outcome = WriteCommitted(result)
    commit = WriteCommitDecision(
        execution_status=ExecutionStatus.COMPLETED,
        outcome=outcome,
        write_summary=WriteSummary(
            commit_state=CommitState.COMMITTED,
            tool_id=identity.tool_id,
            node_id=identity.node_id,
            idempotency_key=identity.idempotency_key,
            payload_hash=identity.payload_hash,
            provider_transaction_id=result.provider_transaction_id,
            business_reference=result.business_reference,
        ),
        automatic_retry_allowed=False,
        reconciliation_required=False,
    )
    verification = VerificationResult(
        identity=identity,
        commit_state=CommitState.COMMITTED,
        status=VerificationStatus.VERIFIED,
        business_reference=result.business_reference,
        verification_tool_id="PROJECT.GET_PROJECT",
        reason_code=None,
        evidence_ref="verification-evidence:0065",
        observed_at=NOW,
    )
    return commit, verification


def _not_committed(intent, *, unknown: bool) -> WriteCommitDecision:
    identity = _identity(intent)
    if unknown:
        outcome = WriteCommitUnknown(
            identity=identity,
            reason_code="PROVIDER_TIMEOUT",
            message="provider outcome is uncertain",
        )
        state = CommitState.UNKNOWN_COMMIT_STATE
        status = ExecutionStatus.RECONCILIATION_REQUIRED
    else:
        outcome = WriteNotCommitted(
            identity=identity,
            reason_code="BUSINESS_REJECTED",
            message="project draft was rejected",
        )
        state = CommitState.NOT_COMMITTED
        status = ExecutionStatus.FAILED
    return WriteCommitDecision(
        execution_status=status,
        outcome=outcome,
        write_summary=WriteSummary(
            commit_state=state,
            tool_id=identity.tool_id,
            node_id=identity.node_id,
            idempotency_key=identity.idempotency_key,
            payload_hash=identity.payload_hash,
        ),
        automatic_retry_allowed=False,
        reconciliation_required=unknown,
    )


def test_ac_w02_e2e_project_is_committed_verified_audited_and_replayable() -> None:
    evidence = asyncio.run(_run_project_e2e())
    result = evidence["result"]

    assert result.status is ExecutionStatus.COMPLETED
    assert result.outputs[0].values == {"project_code": "PROJECT-NEX-0064"}
    assert result.write is not None
    assert result.write.commit_state is CommitState.COMMITTED
    assert result.verification is not None
    assert result.verification.status is VerificationStatus.VERIFIED
    assert evidence["reservation"].disposition is IdempotencyDisposition.CREATED
    assert evidence["retry_reservation"].disposition is IdempotencyDisposition.REUSED
    assert evidence["reservation"].evidence.idempotency_key == evidence[
        "retry_reservation"
    ].evidence.idempotency_key
    assert evidence["idempotency_calls"] == 2
    assert evidence["write_events"] == ["WRITE_REQUESTED", "PROVIDER_WRITE"]
    assert evidence["write_calls"] == 1
    assert evidence["verification_calls"] == 1
    assert evidence["audit_events"] == (
        "WRITE_INTENT_CREATED",
        "APPROVAL_REQUESTED",
        "EXECUTION_SUSPENDED",
        "APPROVAL_APPROVED",
        "EXECUTION_RESUMED",
        "WRITE_REQUESTED",
        "WRITE_COMMIT_CONFIRMED",
        "WRITE_VERIFIED",
    )
    assert evidence["replay_decision"].side_effect_performed is False
    assert evidence["replay_write_calls"] == 1


@pytest.mark.parametrize(
    ("verification_status", "commit_unknown", "expected_status"),
    (
        (VerificationStatus.FAILED, None, ExecutionStatus.COMPLETED),
        (VerificationStatus.UNKNOWN, None, ExecutionStatus.COMPLETED),
        (None, False, ExecutionStatus.FAILED),
        (None, True, ExecutionStatus.RECONCILIATION_REQUIRED),
    ),
)
def test_ac_w02_unconfirmed_project_outcomes_never_emit_project_code(
    verification_status: VerificationStatus | None,
    commit_unknown: bool | None,
    expected_status: ExecutionStatus,
) -> None:
    engine, tools, write_contract, context, intent = _prepared_context()
    if verification_status is None:
        commit = _not_committed(intent, unknown=bool(commit_unknown))
        verification = None
    else:
        commit, verified = _committed(intent, write_contract.output_type)
        verification = replace(
            verified,
            status=verification_status,
            reason_code="PROJECT_NOT_CONFIRMED",
            evidence_ref="verification-evidence:unconfirmed-0065",
        )

    result = asyncio.run(
        engine.complete_controlled_execution(
            context=context,
            approval_request_id="approval-project-0065",
            commit=commit,
            verification=verification,
            tools=tools,
            audit_sink=InMemoryAuditSink(),
        )
    )

    assert result.status is expected_status
    assert result.outputs == ()
    assert result.write is not None
    assert result.write.commit_state is commit.outcome.commit_state


def test_cross_domain_generalization_uses_one_controlled_runtime_pipeline() -> None:
    voucher = asyncio.run(_run_voucher_e2e())
    project = asyncio.run(_run_project_e2e())
    runtime_source = (EXAMPLE.parents[1] / "nsl" / "runtime.py").read_text(
        encoding="utf-8"
    )

    assert voucher.keys() == project.keys()
    assert voucher["result"].status is project["result"].status
    assert voucher["result"].write.commit_state is CommitState.COMMITTED
    assert project["result"].write.commit_state is CommitState.COMMITTED
    assert voucher["result"].verification.status is VerificationStatus.VERIFIED
    assert project["result"].verification.status is VerificationStatus.VERIFIED
    assert voucher["write_calls"] == project["write_calls"] == 1
    assert voucher["replay_write_calls"] == project["replay_write_calls"] == 1
    assert "FINANCE.CREATE_VOUCHER_DRAFT" not in runtime_source
    assert "PROJECT.CREATE_PROJECT_DRAFT" not in runtime_source
