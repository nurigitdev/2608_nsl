from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from nsl.approval import (
    ApprovalEvidence,
    ApprovalRequest,
    ApprovalResumeCoordinator,
    ApprovalSuspensionCoordinator,
    RecordedApprovalClock,
    ResumeClaimError,
    ResumeToken,
)
from nsl.audit import AuditRecorder, InMemoryAuditSink
from nsl.checkpoints import CheckpointCoordinator, ExecutionCheckpoint
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
    ApprovalRequestId,
    ApprovalStatus,
    BusinessOperationId,
    CommitState,
    DataClassification,
    ExecutionStatus,
    IdempotencyKey,
    PreWriteEligibility,
    RiskLevel,
    STRING,
    ToolCapability,
    ValueEnvelope,
    VerificationStatus,
    domain,
    money_type,
    record_type,
)
from nsl.idempotency import (
    IdempotencyCoordinator,
    IdempotencyDisposition,
    IdempotencyEvidenceRecord,
    IdempotencyReservation,
    ProviderIdempotencyEvidence,
)
from nsl.ir import ApprovalStatement, LetStatement, NsoCodec, WriteExpression
from nsl.preflight import ExecutionTrustProfile
from nsl.protected_snapshots import ProtectedSnapshotBlob, ProtectedSnapshotStore
from nsl.runtime import (
    ControlledExecutionPlan,
    ExecutionRequest,
    RuntimeContractError,
    RuntimeEngine,
)
from nsl.runtime_models import ValidationSummary, ValidationSummaryStatus, WriteSummary
from nsl.security import DataHandlingPolicy
from nsl.tools import (
    Atomicity,
    CommitConfirmation,
    CommitSemantics,
    IdempotencySemantics,
    MockToolExecutor,
    OperationKind,
    PostWriteVerification,
    ToolContract,
    ToolContractCatalog,
    WriteSemantics,
)
from nsl.trust import PackageTrustEvidence, TrustStatus, VerifiedSkillArtifact
from nsl.validation import (
    BusinessPreconditionEvidence,
    BusinessPreconditionMechanism,
    PolicyDecision,
    PolicyEffect,
    PreWriteEvidence,
    WriteContractEvidence,
    WriteGuard,
    WriteGuardRequest,
    write_policy_request,
)
from nsl.verification import (
    PostWriteVerifier,
    VerificationObservation,
    VerificationRequest,
    VerificationResult,
)
from nsl.write_audit import (
    ProtectedWriteAuditEvidence,
    WriteAuditEventType,
    WriteAuditIdentity,
    WriteAuditRecorder,
)
from nsl.write_execution import (
    TrustedCommitAwareWriteExecutor,
    TrustedWriteExecutionCoordinator,
    WriteAttemptRecord,
    WriteCallRequestBuilder,
    WriteCommitDecision,
    WriteCommitUnknown,
    WriteCommitted,
    WriteExecutionCoordinator,
    WriteNotCommitted,
    WriteOutcomeCoordinator,
    WriteOutcomeIdentity,
    WriteOutcomeReceipt,
    WriteOutcomeRecord,
    WriteResultEnvelope,
    WriteRequestedReceipt,
)
from nsl.write_replay import (
    WriteReplayEvidenceComposer,
    WriteReplayEvidenceLoader,
    WriteReplayIdentity,
)
from nsl.write_replay_execution import ReplayWriteSession


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "voucher_draft_validation"
SOURCE = (EXAMPLE / "voucher_draft_write.ns").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 21, 2, 0, tzinfo=UTC)
VOUCHER_DRAFT = record_type("VoucherDraft", voucher_no=STRING)


def _catalog() -> tuple[ToolContractCatalog, ToolContract]:
    reads = load_tool_catalog(EXAMPLE / "tools.json")
    contracts = tuple(
        reads.get(tool_id, "1.0.0")
        for tool_id in (
            "ACCOUNT.GET_ACCOUNT",
            "PROJECT.GET_PROJECT",
            "FINANCE.GET_ACCOUNTING_PERIOD",
        )
    )
    write = ToolContract(
        tool_id="FINANCE.CREATE_VOUCHER_DRAFT",
        version="1.0.0",
        capability=ToolCapability.WRITE,
        input_types=(
            ("account_code", domain("AccountCode")),
            ("project_code", domain("ProjectCode")),
            ("accounting_period", domain("AccountingPeriod")),
            ("amount", money_type("KRW")),
        ),
        output_type=VOUCHER_DRAFT,
        required_scope="finance:voucher:write",
        output_classification=DataClassification.CONFIDENTIAL,
        operation_kind=OperationKind.CREATE,
        risk_level=RiskLevel.R3,
        write_semantics=WriteSemantics(
            side_effect=True,
            atomicity=Atomicity.ATOMIC,
            idempotency=IdempotencySemantics(
                required=True,
                scope="TOOL_AND_TENANT",
                retention="PROVIDER_DEFINED",
                duplicate_behavior="RETURN_ORIGINAL_RESULT",
                payload_conflict_behavior="IDEMPOTENCY_CONFLICT",
            ),
            commit=CommitSemantics(CommitConfirmation.EXPLICIT),
            approval_required=True,
            preconditions_supported=True,
            verification=PostWriteVerification(
                tool_id="FINANCE.GET_VOUCHER",
                reference_schema=VOUCHER_DRAFT,
                consistency_hint="READ_AFTER_WRITE_STRONG",
            ),
        ),
    )
    verification = ToolContract(
        tool_id="FINANCE.GET_VOUCHER",
        version="1.0.0",
        capability=ToolCapability.READ,
        input_types=(("voucher_no", STRING),),
        output_type=VOUCHER_DRAFT,
        required_scope="finance:voucher:verify",
        output_classification=DataClassification.CONFIDENTIAL,
        risk_level=RiskLevel.R1,
    )
    return ToolContractCatalog(contracts + (write, verification)), write


def _compiled():
    catalog, write = _catalog()
    return catalog, write, NslCompiler(catalog).compile(SOURCE).skill


def _request(
    *,
    execution_id: str = "exec-voucher-controlled",
    with_business_operation: bool = True,
) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs=load_value_object(EXAMPLE / "input.json", "input"),
        runtime_context=load_value_object(EXAMPLE / "context.json", "context"),
        principal=load_principal(EXAMPLE / "principal_write.json"),
        business_operation_id=(
            BusinessOperationId("business-voucher-0063")
            if with_business_operation
            else None
        ),
    )


def _read_tools(catalog: ToolContractCatalog) -> MockToolExecutor:
    fixture = load_json_document(EXAMPLE / "fixtures" / "success.json")
    return MockToolExecutor(
        catalog,
        load_mock_handlers_document(fixture, catalog),
    )


def _prepared_context():
    catalog, write, skill = _compiled()
    request = _request()
    tools = _read_tools(catalog)
    engine = RuntimeEngine(catalog)
    prepared = asyncio.run(
        engine.execute_controlled_prefix(
            skill,
            request,
            tools,
            InMemoryAuditSink(),
        )
    )
    assert prepared.checkpoint is not None
    context = CheckpointCoordinator().restore(
        prepared.checkpoint,
        skill,
        request,
    )
    intent = asyncio.run(engine.prepare_controlled_write_intent(context, tools))
    context.approvals.records[intent.approval_id] = ApprovalStatus.APPROVED
    return engine, tools, write, context, intent


def _identity(intent) -> WriteOutcomeIdentity:
    return WriteOutcomeIdentity(
        tenant_id=intent.tenant_id,
        business_operation_id=intent.business_operation_id,
        invocation_id="write-invocation-0063",
        node_id=intent.node_id,
        tool_id=intent.tool_id,
        idempotency_key=IdempotencyKey("idem:sha256:" + "d" * 64),
        payload_hash=intent.payload_hash,
        approval_request_id=ApprovalRequestId("approval-voucher-0063"),
        write_intent_hash=intent.write_intent_hash,
    )


def _committed(intent) -> tuple[WriteCommitDecision, VerificationResult]:
    identity = _identity(intent)
    result = WriteResultEnvelope(
        identity=identity,
        value=ValueEnvelope.complete(
            {"voucher_no": "V-20261021-0001"},
            VOUCHER_DRAFT,
            DataClassification.CONFIDENTIAL,
        ),
        business_reference="V-20261021-0001",
        provider_transaction_id="provider-tx-0063",
        committed_at=NOW,
        evidence_refs=("provider-evidence:0063",),
    )
    outcome = WriteCommitted(result)
    summary = WriteSummary(
        commit_state=CommitState.COMMITTED,
        tool_id=identity.tool_id,
        node_id=identity.node_id,
        idempotency_key=identity.idempotency_key,
        payload_hash=identity.payload_hash,
        provider_transaction_id=result.provider_transaction_id,
        business_reference=result.business_reference,
    )
    commit = WriteCommitDecision(
        execution_status=ExecutionStatus.COMPLETED,
        outcome=outcome,
        write_summary=summary,
        automatic_retry_allowed=False,
        reconciliation_required=False,
    )
    verification = VerificationResult(
        identity=identity,
        commit_state=CommitState.COMMITTED,
        status=VerificationStatus.VERIFIED,
        business_reference=result.business_reference,
        verification_tool_id="FINANCE.GET_VOUCHER",
        reason_code=None,
        evidence_ref="verification-evidence:0063",
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
            message="voucher was rejected",
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


def test_controlled_plan_partitions_full_skill_without_changing_identity() -> None:
    _, _, skill = _compiled()
    plan = ControlledExecutionPlan.from_skill(skill)

    assert len(plan.prefix) == 7
    assert isinstance(plan.approval, ApprovalStatement)
    assert isinstance(plan.write_statement, LetStatement)
    assert isinstance(plan.write_expression, WriteExpression)
    assert len(plan.suffix) == 1
    assert plan.write_expression.approval_ref == plan.approval.approval_id
    assert skill.semantic_hash == skill.with_computed_hash().semantic_hash


def test_direct_runtime_still_rejects_write_but_controlled_prefix_checkpoints() -> None:
    catalog, _, skill = _compiled()
    request = _request()
    tools = _read_tools(catalog)
    engine = RuntimeEngine(catalog)

    direct = asyncio.run(
        engine.execute(skill, request, tools, InMemoryAuditSink())
    )
    prepared = asyncio.run(
        engine.execute_controlled_prefix(
            skill,
            request,
            tools,
            InMemoryAuditSink(),
        )
    )

    assert direct.status is ExecutionStatus.FAILED
    assert direct.error is not None
    assert "controlled v0.2 execution pipeline" in direct.error.message
    assert prepared.result.status is ExecutionStatus.COMPLETED
    assert prepared.result.validation is not None
    assert prepared.result.validation.status is ValidationSummaryStatus.ALL_PASS
    assert prepared.checkpoint is not None
    assert prepared.checkpoint.semantic_hash == skill.semantic_hash
    assert tools.call_count == 3


def test_controlled_write_intent_is_deterministic_after_checkpoint_restore() -> None:
    engine, tools, _, context, first = _prepared_context()
    second = asyncio.run(
        engine.prepare_controlled_write_intent(context, tools)
    )

    assert first == second
    assert first.tool_id == "FINANCE.CREATE_VOUCHER_DRAFT"
    assert first.approval_id == "CREATE_VOUCHER_DRAFT"
    assert tuple(first.canonical_arguments) == (
        "account_code",
        "project_code",
        "accounting_period",
        "amount",
    )
    assert tools.call_count == 3


def test_verified_commit_is_the_only_path_that_emits_voucher_number() -> None:
    engine, tools, _, context, intent = _prepared_context()
    commit, verification = _committed(intent)

    result = asyncio.run(
        engine.complete_controlled_execution(
            context=context,
            approval_request_id="approval-voucher-0063",
            commit=commit,
            verification=verification,
            tools=tools,
            audit_sink=InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert result.pre_write_eligibility is PreWriteEligibility.ALLOWED
    assert result.approval is not None
    assert result.approval.status is ApprovalStatus.APPROVED
    assert result.write is not None
    assert result.write.commit_state is CommitState.COMMITTED
    assert result.verification is not None
    assert result.verification.status is VerificationStatus.VERIFIED
    assert result.outputs[0].values == {"voucher_no": "V-20261021-0001"}


@pytest.mark.parametrize(
    ("verification_status", "commit_unknown", "expected_status"),
    (
        (VerificationStatus.FAILED, None, ExecutionStatus.COMPLETED),
        (VerificationStatus.UNKNOWN, None, ExecutionStatus.COMPLETED),
        (None, False, ExecutionStatus.FAILED),
        (None, True, ExecutionStatus.RECONCILIATION_REQUIRED),
    ),
)
def test_unconfirmed_outcomes_never_emit_voucher_number(
    verification_status: VerificationStatus | None,
    commit_unknown: bool | None,
    expected_status: ExecutionStatus,
) -> None:
    engine, tools, _, context, intent = _prepared_context()
    if verification_status is None:
        commit = _not_committed(intent, unknown=bool(commit_unknown))
        verification = None
    else:
        commit, verified = _committed(intent)
        verification = replace(
            verified,
            status=verification_status,
            reason_code="VOUCHER_NOT_CONFIRMED",
            evidence_ref="verification-evidence:unconfirmed",
        )

    result = asyncio.run(
        engine.complete_controlled_execution(
            context=context,
            approval_request_id="approval-voucher-0063",
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


def test_controlled_runtime_rejects_missing_identity_and_invalid_bindings() -> None:
    catalog, _, skill = _compiled()
    tools = _read_tools(catalog)
    engine = RuntimeEngine(catalog)

    with pytest.raises(RuntimeContractError, match="business operation"):
        asyncio.run(
            engine.execute_controlled_prefix(
                skill,
                _request(with_business_operation=False),
                tools,
                InMemoryAuditSink(),
            )
        )
    with pytest.raises(TypeError, match="checkpoint_coordinator"):
        asyncio.run(
            engine.execute_controlled_prefix(
                skill,
                _request(),
                tools,
                InMemoryAuditSink(),
                checkpoint_coordinator=object(),  # type: ignore[arg-type]
            )
        )

    read_skill = NslCompiler(load_tool_catalog(EXAMPLE / "tools.json")).compile(
        (EXAMPLE / "voucher_draft_validation.ns").read_text(encoding="utf-8")
    ).skill
    with pytest.raises(RuntimeContractError, match="exactly one"):
        ControlledExecutionPlan.from_skill(read_skill)
    with pytest.raises(RuntimeContractError, match="NSL 0.2"):
        ControlledExecutionPlan.from_skill(
            replace(read_skill, language_version="0.1", ir_version="1.0")
        )


class _LifecyclePort:
    def __init__(self) -> None:
        self.checkpoint: ExecutionCheckpoint | None = None
        self.claim_id: str | None = None
        self.consumed = False
        self.published: list[ApprovalRequest] = []

    async def save(self, checkpoint: ExecutionCheckpoint) -> None:
        checkpoint.verify()
        self.checkpoint = checkpoint

    async def publish(self, request: ApprovalRequest) -> None:
        self.published.append(request)

    async def claim(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> ExecutionCheckpoint:
        checkpoint = self.checkpoint
        if (
            checkpoint is None
            or checkpoint.execution_id != execution_id
            or checkpoint.checkpoint_version != checkpoint_version
            or self.claim_id is not None
            or self.consumed
        ):
            raise ResumeClaimError("checkpoint is not claimable")
        self.claim_id = claim_id
        return checkpoint

    async def release(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> None:
        self._require_claim(execution_id, checkpoint_version, claim_id)
        self.claim_id = None

    async def consume(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> None:
        self._require_claim(execution_id, checkpoint_version, claim_id)
        self.claim_id = None
        self.consumed = True

    def _require_claim(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> None:
        checkpoint = self.checkpoint
        if (
            checkpoint is None
            or checkpoint.execution_id != execution_id
            or checkpoint.checkpoint_version != checkpoint_version
            or self.claim_id != claim_id
        ):
            raise ResumeClaimError("claim ownership mismatch")


class _ApprovalVerifier:
    def __init__(self, signature: str = "approval-signature:0063") -> None:
        self.signature = signature

    def verify(self, evidence: ApprovalEvidence) -> bool:
        return evidence.evidence_integrity == self.signature


class _IdempotencyPort:
    def __init__(self) -> None:
        self.records: dict[str, IdempotencyEvidenceRecord] = {}
        self.calls = 0

    async def reserve(
        self,
        evidence: IdempotencyEvidenceRecord,
    ) -> IdempotencyReservation:
        self.calls += 1
        key = str(evidence.idempotency_key)
        existing = self.records.setdefault(key, evidence)
        if existing.payload_hash != evidence.payload_hash:
            return IdempotencyReservation(
                IdempotencyDisposition.CONFLICT,
                evidence,
            )
        disposition = (
            IdempotencyDisposition.CREATED
            if existing is evidence
            else IdempotencyDisposition.REUSED
        )
        return IdempotencyReservation(disposition, existing)


class _WriteEvidencePort:
    def __init__(self, events: list[str], marker: str = "0063") -> None:
        self.events = events
        self.marker = marker
        self.records: list[WriteAttemptRecord] = []

    async def record_requested(
        self,
        record: WriteAttemptRecord,
    ) -> WriteRequestedReceipt:
        self.events.append("WRITE_REQUESTED")
        self.records.append(record)
        return WriteRequestedReceipt(
            record_hash=record.record_hash,
            durable_ref=f"write-request:{self.marker}",
            persisted_at=record.requested_at,
            durable=True,
        )


class _VoucherWritePort:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def execute(self, request):
        self.events.append("PROVIDER_WRITE")
        self.calls += 1
        return WriteCommitted(
            WriteResultEnvelope(
                identity=WriteOutcomeIdentity.from_request(request),
                value=ValueEnvelope.complete(
                    {"voucher_no": "V-20261021-0063"},
                    VOUCHER_DRAFT,
                    DataClassification.CONFIDENTIAL,
                ),
                business_reference="V-20261021-0063",
                provider_transaction_id="provider-tx-voucher-0063",
                committed_at=NOW + timedelta(seconds=2),
                evidence_refs=("provider-commit:0063",),
            )
        )


class _OutcomePort:
    def __init__(self, marker: str = "0063") -> None:
        self.marker = marker
        self.records: list[WriteOutcomeRecord] = []

    async def record_outcome(
        self,
        record: WriteOutcomeRecord,
    ) -> WriteOutcomeReceipt:
        self.records.append(record)
        return WriteOutcomeReceipt(
            record_hash=record.record_hash,
            durable_ref=f"write-outcome:{self.marker}",
            persisted_at=record.recorded_at,
        )


class _VerificationPort:
    def __init__(self, marker: str = "0063") -> None:
        self.marker = marker
        self.calls = 0

    async def read(
        self,
        request: VerificationRequest,
    ) -> VerificationObservation:
        self.calls += 1
        return VerificationObservation(
            request_hash=request.request_hash,
            business_reference=request.business_reference,
            matched=True,
            evidence_ref=f"verification:{self.marker}",
            observed_at=NOW + timedelta(seconds=3),
        )


class _VaultProtector:
    def __init__(self, marker: str = "0063") -> None:
        self.marker = marker
        self.values: dict[bytes, object] = {}

    def seal(self, value, *, tenant_id, classification):
        token = f"ciphertext-{len(self.values) + 1}".encode("ascii")
        self.values[token] = deepcopy(value)
        return ProtectedSnapshotBlob(token, "TEST-AEAD", f"test-key-{self.marker}")

    def unseal(self, blob, *, tenant_id, classification):
        return deepcopy(self.values[blob.ciphertext])


def _contract_evidence(contract: ToolContract) -> WriteContractEvidence:
    return WriteContractEvidence(
        tool_id=contract.tool_id,
        contract_hash=contract.contract_hash,
        capability=contract.capability,
        risk_level=contract.risk_level or RiskLevel.R3,
        preconditions_supported=True,
    )


def _execution_evidence(context) -> PreWriteEvidence:
    return PreWriteEvidence(
        validation=ValidationSummary.from_checks(tuple(context.checks)),
        approval_status=ApprovalStatus.APPROVED,
        approval_evidence_valid=True,
        runtime_preflight_passed=True,
        current_policy_allowed=False,
        tool_preconditions_passed=False,
        business_preconditions_passed=False,
        intent_binding_valid=True,
        idempotency_ready=True,
        durability_ready=True,
        trust_valid=True,
        replay_live_write_allowed=True,
    )


def _policy_decision(intent, context, contract_evidence) -> PolicyDecision:
    policy_request = write_policy_request(intent, context, contract_evidence)
    return PolicyDecision(
        decision_id="policy-decision-0063",
        policy_id="finance-write-policy",
        policy_version="1",
        effect=PolicyEffect.ALLOW,
        tenant_id=policy_request.tenant_id,
        subject_id=policy_request.subject_id,
        action=policy_request.action,
        tool_id=policy_request.tool_id,
        risk_level=policy_request.risk_level,
        business_operation_id=policy_request.business_operation_id,
        write_node_id=policy_request.write_node_id,
        write_intent_hash=policy_request.write_intent_hash,
        evidence_ref="policy-evidence:0063",
        evaluated_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=1),
    )


def _business_precondition(intent, contract) -> BusinessPreconditionEvidence:
    return BusinessPreconditionEvidence(
        evidence_id="precondition-0063",
        tenant_id=intent.tenant_id,
        business_operation_id=intent.business_operation_id,
        write_node_id=intent.node_id,
        tool_id=intent.tool_id,
        contract_hash=contract.contract_hash,
        payload_hash=intent.payload_hash,
        write_intent_hash=intent.write_intent_hash,
        mechanism=BusinessPreconditionMechanism.ETAG,
        evidence_ref="etag:opaque:voucher-0063",
        observed_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=1),
        provider_atomic_enforcement=True,
    )


def _trust(skill):
    package_hash = "sha256:" + "b" * 64
    package_id = "nsp:" + package_hash
    artifact = VerifiedSkillArtifact(
        skill,
        "sha256:" + sha256(NsoCodec.encode(skill)).hexdigest(),
        PackageTrustEvidence(
            package_id=package_id,
            package_hash=package_hash,
            publisher_id="publisher.finance",
            signing_key_id="key-finance-0063",
            verified_at=NOW - timedelta(seconds=10),
            trust_status=TrustStatus.VERIFIED,
            trust_policy_id="trust-policy-0063",
        ),
    )
    profile = ExecutionTrustProfile.production(
        tenant_id="tenant-finance",
        trust_policy_id="trust-policy-0063",
        expected_package_id=package_id,
        expected_artifact_hash=artifact.artifact_hash,
        expected_semantic_hash=artifact.semantic_hash,
        trusted_publisher_ids=frozenset({"publisher.finance"}),
        trusted_signing_key_ids=frozenset({"key-finance-0063"}),
        max_trust_age_seconds=60,
    )
    return artifact, profile


async def _run_voucher_e2e():
    catalog, write_contract, skill = _compiled()
    request = _request(execution_id="exec-voucher-e2e-0063")
    tools = _read_tools(catalog)
    engine = RuntimeEngine(catalog)
    prefix = await engine.execute_controlled_prefix(
        skill,
        request,
        tools,
        InMemoryAuditSink(),
    )
    assert prefix.checkpoint is not None
    context = CheckpointCoordinator().restore(prefix.checkpoint, skill, request)
    intent = await engine.prepare_controlled_write_intent(context, tools)
    plan = ControlledExecutionPlan.from_skill(skill)

    approval_request = ApprovalRequest(
        tenant_id=context.tenant_id,
        execution_id=context.identity.execution_id,
        business_operation_id=intent.business_operation_id,
        approval_id=plan.approval.approval_id,
        approval_request_id=ApprovalRequestId("approval-voucher-0063"),
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
        display_safe_summary={"document_type": "voucher-draft"},
        protected_payload_ref="approval-payload:0063",
        data_classification=DataClassification.CONFIDENTIAL,
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
        approver_id="finance-manager-0063",
        decided_at=NOW,
        expires_at=approval_request.expires_at,
        payload_hash=intent.payload_hash,
        write_intent_hash=intent.write_intent_hash,
        evidence_integrity="approval-signature:0063",
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
        claim_id="claim-voucher-0063",
        checkpoint_port=lifecycle,
        verifier=_ApprovalVerifier(),
        clock=RecordedApprovalClock(NOW + timedelta(seconds=1)),
    )
    assert resumed.validation.approval_gate_passed is True
    context = resumed.context

    idempotency_port = _IdempotencyPort()
    idempotency = IdempotencyCoordinator(idempotency_port)
    provider_evidence = ProviderIdempotencyEvidence(
        tool_id=write_contract.tool_id,
        contract_hash=write_contract.contract_hash,
        retention_policy="PROVIDER_DEFINED",
        provider_guarantee_ref="provider-idempotency:0063",
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
            policy_decision=_policy_decision(
                intent,
                context,
                contract_evidence,
            ),
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
        invocation_id="write-invocation-0063",
        business_preconditions=precondition,
        trace_refs=("trace-voucher:0063",),
    )

    write_events: list[str] = []
    write_evidence = _WriteEvidencePort(write_events)
    write_provider = _VoucherWritePort(write_events)
    outcome_port = _OutcomePort()
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

    verification_port = _VerificationPort()
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
    audit_identity = WriteAuditIdentity.from_write_outcome(
        commit.outcome.identity
    )
    protected = ProtectedWriteAuditEvidence(
        data_classification=DataClassification.CONFIDENTIAL,
        protected_payload_ref="write-payload:0063",
        display_safe_summary={"document_type": "voucher-draft"},
        evidence_refs=("approval-signature:0063",),
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
        data_classification=DataClassification.CONFIDENTIAL,
        display_safe_summary={"document_type": "voucher-draft"},
        protected_payload_ref="verification-payload:0063",
    )

    protector = _VaultProtector()
    snapshots = ProtectedSnapshotStore(protector, lambda: NOW)
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
    replay = ReplayWriteSession.open(
        replay_bundle,
        loader,
        request.principal,
    )
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


def test_ac_w01_e2e_approved_voucher_is_committed_verified_and_replayable() -> None:
    evidence = asyncio.run(_run_voucher_e2e())
    result = evidence["result"]

    assert result.status is ExecutionStatus.COMPLETED
    assert result.outputs[0].values == {"voucher_no": "V-20261021-0063"}
    assert result.write.commit_state is CommitState.COMMITTED
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
