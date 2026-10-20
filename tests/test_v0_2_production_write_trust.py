from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from nsl.compiler import NslCompiler
from nsl.core import (
    ApprovalRequestId,
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
    domain,
    list_type,
)
from nsl.idempotency import (
    IdempotencyDisposition,
    IdempotencyEvidenceRecord,
    IdempotencyReservation,
)
from nsl.ir import (
    ForeachStatement,
    LetStatement,
    LiteralExpr,
    NsoCodec,
    WriteExpression,
)
from nsl.preflight import (
    MAX_TRUST_AGE_SECONDS,
    ExecutionTrustProfile,
    RuntimePreflightValidator,
    TrustPreflightError,
    TrustPreflightStatus,
    _contains_write,
)
from nsl.runtime_models import ExecutionContext, ExecutionRequest
from nsl.security import DataHandlingPolicy, RuntimeEnvironment
from nsl.tools import (
    Atomicity,
    CommitConfirmation,
    CommitSemantics,
    IdempotencySemantics,
    OperationKind,
    ToolContract,
    ToolContractCatalog,
    WriteSemantics,
)
from nsl.trust import (
    PackageTrustEvidence,
    TrustStatus,
    VerifiedSkillArtifact,
)
from nsl.validation import (
    BusinessPreconditionEvidence,
    BusinessPreconditionMechanism,
    WriteContractEvidence,
    WriteGuardDecision,
)
from nsl.vertical_slice import build_principal
from nsl.write_execution import (
    TrustedCommitAwareWriteExecutor,
    TrustedWriteExecutionCoordinator,
    WriteAttemptRecord,
    WriteCallRequest,
    WriteCallRequestBuilder,
    WriteExecutionCoordinator,
    WriteOutcomeCoordinator,
    WriteOutcomeReceipt,
    WriteOutcomeRecord,
    WriteRequestedReceipt,
)
from nsl.write_intent import WriteIntentBuilder


NOW = datetime(2026, 10, 20, 4, 0, tzinfo=UTC)
TRUSTED_AT = NOW - timedelta(seconds=30)
PACKAGE_HASH = "sha256:" + "b" * 64
PACKAGE_ID = "nsp:" + PACKAGE_HASH
WRITE_RESULT = domain("DraftRecord")


def _write_contract() -> ToolContract:
    return ToolContract(
        tool_id="FINANCE.CREATE_DRAFT",
        version="1.0.0",
        capability=ToolCapability.WRITE,
        input_types=(("detail", STRING),),
        output_type=WRITE_RESULT,
        required_scope="finance:draft:write",
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
        ),
    )


def _write_skill():
    source = '''language NSL "0.2";
skill FINANCE.PRODUCTION_TRUST {
    version "1.0.0";
    risk WRITE;
    requires { tool FINANCE.CREATE_DRAFT version "1.0.0"; }
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }
    input { detail: String classification CONFIDENTIAL; }
    output { result: String classification CONFIDENTIAL; }
    approve CREATE_DRAFT { message "Create draft?"; }
    let draft = write FINANCE.CREATE_DRAFT(
        detail = detail
    ) approved_by CREATE_DRAFT;
}
'''
    return NslCompiler(ToolContractCatalog((_write_contract(),))).compile(source).skill


def _read_skill():
    source = '''language NSL "0.2";
skill FINANCE.PRODUCTION_READ {
    version "1.0.0";
    risk READ_ONLY;
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }
    output { result: String; }
}
'''
    return NslCompiler(ToolContractCatalog(())).compile(source).skill


def _artifact(*, trust_status: TrustStatus = TrustStatus.VERIFIED):
    skill = _write_skill()
    artifact_hash = "sha256:" + sha256(NsoCodec.encode(skill)).hexdigest()
    trust = PackageTrustEvidence(
        package_id=PACKAGE_ID,
        package_hash=PACKAGE_HASH,
        publisher_id="publisher.finance",
        signing_key_id="key-finance-1",
        verified_at=TRUSTED_AT,
        trust_status=trust_status,
        trust_policy_id="trust-policy-0056",
    )
    return VerifiedSkillArtifact(skill, artifact_hash, trust)


def _profile(
    artifact: VerifiedSkillArtifact,
    **changes: object,
) -> ExecutionTrustProfile:
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "trust_policy_id": "trust-policy-0056",
        "expected_package_id": PACKAGE_ID,
        "expected_artifact_hash": artifact.artifact_hash,
        "expected_semantic_hash": artifact.semantic_hash,
        "trusted_publisher_ids": frozenset({"publisher.finance"}),
        "trusted_signing_key_ids": frozenset({"key-finance-1"}),
        "max_trust_age_seconds": 60,
    }
    values.update(changes)
    return ExecutionTrustProfile.production(**values)  # type: ignore[arg-type]


def _context(skill=None, *, tenant_id: str = "tenant-a") -> ExecutionContext:
    selected = skill or _write_skill()
    return ExecutionContext(
        selected,
        ExecutionRequest(
            execution_id="exec-0056",
            inputs={"detail": "draft"},
            runtime_context={},
            principal=build_principal(tenant_id=tenant_id),
            data_policy=DataHandlingPolicy(),
            business_operation_id=BusinessOperationId("business-0056"),
        ),
    )


def _request(context: ExecutionContext) -> WriteCallRequest:
    statement = context.skill.body[1]
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, WriteExpression)
    arguments = {
        "detail": ValueEnvelope.complete(
            "draft",
            STRING,
            DataClassification.CONFIDENTIAL,
        )
    }
    intent = WriteIntentBuilder().build(statement.value, arguments, context)
    contract = _write_contract()
    contract_evidence = WriteContractEvidence(
        tool_id=contract.tool_id,
        contract_hash=contract.contract_hash,
        capability=ToolCapability.WRITE,
        risk_level=RiskLevel.R3,
        preconditions_supported=True,
    )
    reservation = IdempotencyReservation(
        IdempotencyDisposition.CREATED,
        IdempotencyEvidenceRecord(
            tenant_id=context.tenant_id,
            business_operation_id=intent.business_operation_id,
            write_node_id=intent.node_id,
            tool_id=intent.tool_id,
            contract_hash=contract.contract_hash,
            idempotency_key=IdempotencyKey("idem:sha256:" + "d" * 64),
            payload_hash=intent.payload_hash,
            provider_guarantee_ref="provider-policy-0056",
            retained_until=NOW + timedelta(days=30),
        ),
    )
    precondition = BusinessPreconditionEvidence(
        evidence_id="precondition-0056",
        tenant_id=context.tenant_id,
        business_operation_id=intent.business_operation_id,
        write_node_id=intent.node_id,
        tool_id=intent.tool_id,
        contract_hash=contract.contract_hash,
        payload_hash=intent.payload_hash,
        write_intent_hash=intent.write_intent_hash,
        mechanism=BusinessPreconditionMechanism.ETAG,
        evidence_ref="etag:opaque:0056",
        observed_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=1),
        provider_atomic_enforcement=True,
    )
    return WriteCallRequestBuilder().build(
        intent=intent,
        context=context,
        contract_evidence=contract_evidence,
        reservation=reservation,
        approval_request_id=ApprovalRequestId("approval-request-0056"),
        invocation_id="invoke-0056",
        business_preconditions=precondition,
    )


class _EvidencePort:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def record_requested(
        self,
        record: WriteAttemptRecord,
    ) -> WriteRequestedReceipt:
        self.events.append("WRITE_REQUESTED")
        return WriteRequestedReceipt(
            record_hash=record.record_hash,
            durable_ref="write-evidence:0056",
            persisted_at=record.requested_at,
            durable=True,
        )


class _WritePort:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def execute(self, request: WriteCallRequest) -> str:
        self.events.append("PROVIDER_WRITE")
        return request.invocation_id


def _execute(
    artifact: VerifiedSkillArtifact,
    profile: ExecutionTrustProfile,
    *,
    now: datetime = NOW,
    context: ExecutionContext | None = None,
) -> tuple[str, list[str], ExecutionContext]:
    selected_context = context or _context(artifact.skill)
    events: list[str] = []
    trusted = TrustedWriteExecutionCoordinator(
        WriteExecutionCoordinator(_WritePort(events), _EvidencePort(events))
    )
    result = asyncio.run(
        trusted.execute(
            artifact=artifact,
            profile=profile,
            trust_checked_at=now,
            request=_request(selected_context),
            decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
            context=selected_context,
            requested_at=NOW,
        )
    )
    return result, events, selected_context


def test_sig_001_arc_210_verified_production_write_precedes_durable_request() -> None:
    artifact = _artifact()

    result, events, context = _execute(artifact, _profile(artifact))

    assert result == "invoke-0056"
    assert events == ["WRITE_REQUESTED", "PROVIDER_WRITE"]
    assert context.checkpoint.write_request_durable is True


@pytest.mark.parametrize(
    ("artifact_factory", "profile_changes", "now", "reason_code"),
    [
        (
            lambda: replace(_artifact(), trust=None),
            {},
            NOW,
            "TRUST_EVIDENCE_MISSING",
        ),
        (
            lambda: _artifact(trust_status=TrustStatus.UNVERIFIED),
            {},
            NOW,
            "TRUST_NOT_VERIFIED",
        ),
        (
            lambda: _artifact(trust_status=TrustStatus.REVOKED),
            {},
            NOW,
            "TRUST_NOT_VERIFIED",
        ),
        (
            _artifact,
            {"expected_package_id": "nsp:sha256:" + "f" * 64},
            NOW,
            "PACKAGE_BINDING_MISMATCH",
        ),
        (
            _artifact,
            {"expected_artifact_hash": "sha256:" + "f" * 64},
            NOW,
            "ARTIFACT_BINDING_MISMATCH",
        ),
        (
            _artifact,
            {"expected_semantic_hash": "sha256:" + "f" * 64},
            NOW,
            "SEMANTIC_BINDING_MISMATCH",
        ),
        (
            _artifact,
            {"trust_policy_id": "other-policy"},
            NOW,
            "TRUST_POLICY_MISMATCH",
        ),
        (
            _artifact,
            {"trusted_publisher_ids": frozenset({"other-publisher"})},
            NOW,
            "PUBLISHER_NOT_TRUSTED",
        ),
        (
            _artifact,
            {"trusted_signing_key_ids": frozenset({"other-key"})},
            NOW,
            "SIGNING_KEY_NOT_TRUSTED",
        ),
        (
            _artifact,
            {},
            TRUSTED_AT - timedelta(microseconds=1),
            "TRUST_EVIDENCE_FROM_FUTURE",
        ),
        (
            _artifact,
            {},
            TRUSTED_AT + timedelta(seconds=60),
            "TRUST_EVIDENCE_STALE",
        ),
    ],
)
def test_sig_001_invalid_production_trust_fails_before_all_side_effects(
    artifact_factory,
    profile_changes,
    now,
    reason_code,
) -> None:
    artifact = artifact_factory()
    profile = _profile(artifact, **profile_changes)
    context = _context(artifact.skill)
    events: list[str] = []
    trusted = TrustedWriteExecutionCoordinator(
        WriteExecutionCoordinator(_WritePort(events), _EvidencePort(events))
    )

    with pytest.raises(TrustPreflightError) as caught:
        asyncio.run(
            trusted.execute(
                artifact=artifact,
                profile=profile,
                trust_checked_at=now,
                request=_request(context),
                decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
                context=context,
                requested_at=NOW,
            )
        )

    assert caught.value.reason_code == reason_code
    assert events == []
    assert context.checkpoint.write_request_durable is False


def test_sig_005_trust_freshness_uses_half_open_expiry_boundary() -> None:
    artifact = _artifact()
    profile = _profile(artifact)

    result, events, _ = _execute(
        artifact,
        profile,
        now=TRUSTED_AT + timedelta(seconds=59, microseconds=999999),
    )

    assert result == "invoke-0056"
    assert events == ["WRITE_REQUESTED", "PROVIDER_WRITE"]


def test_development_unsigned_write_requires_explicit_profile_permission() -> None:
    artifact = replace(_artifact(), trust=None)
    validator = RuntimePreflightValidator()

    with pytest.raises(TrustPreflightError) as caught:
        validator.validate(
            artifact,
            ExecutionTrustProfile.development(
                tenant_id="tenant-a",
                allow_unsigned=False,
            ),
            now=NOW,
        )
    assert caught.value.reason_code == "UNSIGNED_DEVELOPMENT_NOT_ALLOWED"

    evidence = validator.validate(
        artifact,
        ExecutionTrustProfile.development(
            tenant_id="tenant-a",
            allow_unsigned=True,
        ),
        now=NOW,
    )
    assert evidence.status is TrustPreflightStatus.NOT_REQUIRED
    assert evidence.package_id is None


def test_production_read_only_does_not_invent_write_trust_requirement() -> None:
    skill = _read_skill()
    artifact = VerifiedSkillArtifact(
        skill,
        "sha256:" + sha256(NsoCodec.encode(skill)).hexdigest(),
        None,
    )
    evidence = RuntimePreflightValidator().validate(
        artifact,
        _profile(artifact),
        now=NOW,
    )

    assert evidence.status is TrustPreflightStatus.NOT_REQUIRED
    assert evidence.environment is RuntimeEnvironment.PRODUCTION


@pytest.mark.parametrize(
    "max_age",
    [1, MAX_TRUST_AGE_SECONDS],
)
def test_trust_profile_accepts_exact_resource_boundaries(max_age: int) -> None:
    artifact = _artifact()
    assert _profile(artifact, max_trust_age_seconds=max_age).max_trust_age_seconds == (
        max_age
    )


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"tenant_id": ""}, ValueError, "tenant_id"),
        ({"tenant_id": "api_key=secret"}, ValueError, "credential"),
        ({"trust_policy_id": ""}, ValueError, "trust_policy_id"),
        ({"expected_package_id": ""}, ValueError, "expected_package_id"),
        ({"expected_artifact_hash": "bad"}, ValueError, "SHA-256"),
        ({"expected_semantic_hash": "bad"}, ValueError, "SHA-256"),
        ({"trusted_publisher_ids": set()}, TypeError, "frozenset"),
        ({"trusted_publisher_ids": frozenset()}, ValueError, "cannot be empty"),
        ({"trusted_signing_key_ids": frozenset()}, ValueError, "cannot be empty"),
        ({"max_trust_age_seconds": 0}, ValueError, "between 1"),
        (
            {"max_trust_age_seconds": MAX_TRUST_AGE_SECONDS + 1},
            ValueError,
            "between 1",
        ),
        ({"max_trust_age_seconds": True}, ValueError, "between 1"),
    ],
)
def test_execution_trust_profile_rejects_malformed_security_policy(
    changes,
    error,
    message,
) -> None:
    with pytest.raises(error, match=message):
        _profile(_artifact(), **changes)


def test_execution_trust_profile_rejects_implicit_or_weakened_production() -> None:
    artifact = _artifact()
    profile = _profile(artifact)

    with pytest.raises(TypeError, match="RuntimeEnvironment"):
        replace(profile, environment="PRODUCTION")
    with pytest.raises(ValueError, match="cannot allow"):
        replace(profile, allow_unsigned_development=True)
    with pytest.raises(ValueError, match="must be explicit"):
        replace(profile, max_trust_age_seconds=None)
    with pytest.raises(TypeError, match="Bool"):
        replace(profile, allow_unsigned_development=1)


def test_development_profile_validates_optional_trust_bindings() -> None:
    artifact = _artifact()
    profile = ExecutionTrustProfile(
        tenant_id="tenant-a",
        environment=RuntimeEnvironment.DEVELOPMENT,
        trust_policy_id="trust-policy-0056",
        expected_package_id=PACKAGE_ID,
        expected_artifact_hash=artifact.artifact_hash,
        expected_semantic_hash=artifact.semantic_hash,
        trusted_publisher_ids=frozenset({"publisher.finance"}),
        trusted_signing_key_ids=frozenset({"key-finance-1"}),
        max_trust_age_seconds=60,
    )

    evidence = RuntimePreflightValidator().validate(
        artifact,
        profile,
        now=NOW,
    )
    assert evidence.status is TrustPreflightStatus.VERIFIED


def test_runtime_preflight_rejects_raw_skill_and_invalid_clock() -> None:
    artifact = _artifact()
    validator = RuntimePreflightValidator()

    with pytest.raises(TypeError, match="VerifiedSkillArtifact"):
        validator.validate(  # type: ignore[arg-type]
            artifact.skill,
            _profile(artifact),
            now=NOW,
        )
    with pytest.raises(TypeError, match="ExecutionTrustProfile"):
        validator.validate(artifact, object(), now=NOW)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="datetime"):
        validator.validate(  # type: ignore[arg-type]
            artifact,
            _profile(artifact),
            now="now",
        )
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        validator.validate(
            artifact,
            _profile(artifact),
            now=NOW.replace(tzinfo=None),
        )
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        validator.validate(
            artifact,
            _profile(artifact),
            now=NOW.astimezone(timezone(timedelta(hours=9))),
        )


def test_preflight_evidence_is_immutable_and_validated() -> None:
    artifact = _artifact()
    evidence = RuntimePreflightValidator().validate(
        artifact,
        _profile(artifact),
        now=NOW,
    )

    assert evidence.status is TrustPreflightStatus.VERIFIED
    assert evidence.package_id == PACKAGE_ID
    with pytest.raises(FrozenInstanceError):
        evidence.package_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError, match="TrustPreflightStatus"):
        replace(evidence, status="VERIFIED")
    with pytest.raises(TypeError, match="RuntimeEnvironment"):
        replace(evidence, environment="PRODUCTION")
    with pytest.raises(ValueError, match="artifact_hash"):
        replace(evidence, artifact_hash="bad")


def test_tampered_in_memory_identities_are_rechecked_defensively() -> None:
    artifact = _artifact()
    object.__setattr__(artifact, "artifact_hash", "sha256:" + "0" * 64)
    with pytest.raises(TrustPreflightError) as caught:
        RuntimePreflightValidator().validate(
            artifact,
            _profile(_artifact()),
            now=NOW,
        )
    assert caught.value.reason_code == "ARTIFACT_HASH_MISMATCH"

    artifact = _artifact()
    assert artifact.trust is not None
    object.__setattr__(artifact.trust, "package_id", "nsp:sha256:" + "0" * 64)
    with pytest.raises(TrustPreflightError) as caught:
        RuntimePreflightValidator().validate(
            artifact,
            _profile(_artifact()),
            now=NOW,
        )
    assert caught.value.reason_code == "PACKAGE_ID_MISMATCH"

    artifact = _artifact()
    object.__setattr__(
        artifact.skill,
        "semantic_hash",
        "sha256:" + "1" * 64,
    )
    object.__setattr__(
        artifact,
        "artifact_hash",
        "sha256:" + sha256(NsoCodec.encode(artifact.skill)).hexdigest(),
    )
    with pytest.raises(TrustPreflightError) as caught:
        RuntimePreflightValidator().validate(
            artifact,
            _profile(_artifact()),
            now=NOW,
        )
    assert caught.value.reason_code == "SEMANTIC_HASH_MISMATCH"

    artifact = _artifact()
    object.__setattr__(artifact.skill, "semantic_hash", "bad")
    with pytest.raises(TrustPreflightError) as caught:
        RuntimePreflightValidator().validate(
            artifact,
            _profile(_artifact()),
            now=NOW,
        )
    assert caught.value.reason_code == "ARTIFACT_INVALID"


@pytest.mark.parametrize("nested", [False, True])
def test_write_detection_fails_closed_even_without_required_tool_metadata(
    nested: bool,
) -> None:
    skill = _write_skill()
    write_statement = skill.body[1]
    body = (write_statement,)
    if nested:
        body = (
            ForeachStatement(
                node_id="foreach0001",
                iterator_symbol_id="iterator0001",
                collection=LiteralExpr(
                    node_id="literal0001",
                    value=(),
                    type_info=list_type(STRING),
                ),
                max_iterations=1,
                body=(write_statement,),
            ),
        )
    skill = replace(
        skill,
        required_tools=(),
        body=body,
        semantic_hash="",
    ).with_computed_hash()
    assert _contains_write(skill) is True


def test_trusted_write_rejects_cross_tenant_and_context_skill_binding() -> None:
    artifact = _artifact()
    profile = _profile(artifact)

    with pytest.raises(TrustPreflightError) as tenant_error:
        _execute(
            artifact,
            replace(profile, tenant_id="tenant-b"),
            context=_context(artifact.skill),
        )
    assert tenant_error.value.reason_code == "TRUST_TENANT_BINDING_MISMATCH"

    other_skill = replace(
        artifact.skill,
        risk="WRITE_CHANGED",
        semantic_hash="",
    ).with_computed_hash()
    with pytest.raises(TrustPreflightError) as skill_error:
        _execute(
            artifact,
            profile,
            context=_context(other_skill),
        )
    assert skill_error.value.reason_code == "TRUST_SKILL_BINDING_MISMATCH"


def test_trusted_write_coordinator_rejects_invalid_dependencies_and_context() -> None:
    events: list[str] = []
    delegate = WriteExecutionCoordinator(_WritePort(events), _EvidencePort(events))
    with pytest.raises(TypeError, match="WriteExecutionCoordinator"):
        TrustedWriteExecutionCoordinator(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="RuntimePreflightValidator"):
        TrustedWriteExecutionCoordinator(delegate, object())  # type: ignore[arg-type]

    artifact = _artifact()
    trusted = TrustedWriteExecutionCoordinator(delegate)
    with pytest.raises(TypeError, match="ExecutionContext"):
        asyncio.run(
            trusted.execute(
                artifact=artifact,
                profile=_profile(artifact),
                trust_checked_at=NOW,
                request=object(),  # type: ignore[arg-type]
                decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
                context=object(),  # type: ignore[arg-type]
                requested_at=NOW,
            )
        )
    assert events == []


class _CommitOutcomePort:
    def __init__(self) -> None:
        self.records: list[WriteOutcomeRecord] = []

    async def record_outcome(
        self,
        record: WriteOutcomeRecord,
    ) -> WriteOutcomeReceipt:
        self.records.append(record)
        return WriteOutcomeReceipt(
            record_hash=record.record_hash,
            durable_ref="write-outcome:0056",
            persisted_at=record.recorded_at,
        )


class _TimeoutWritePort(_WritePort):
    async def execute(self, request: WriteCallRequest) -> str:
        self.events.append("PROVIDER_WRITE")
        raise TimeoutError("provider acknowledgement lost")


@pytest.mark.parametrize(
    ("provider_type", "reason_code"),
    (
        (_WritePort, "PROVIDER_ACKNOWLEDGEMENT_MALFORMED"),
        (_TimeoutWritePort, "PROVIDER_TIMEOUT"),
    ),
)
def test_trusted_commit_boundary_maps_unconfirmed_provider_results_to_unknown(
    provider_type,
    reason_code: str,
) -> None:
    artifact = _artifact()
    context = _context(artifact.skill)
    request = _request(context)
    context.idempotency.keys_by_write_node[request.node_id] = request.idempotency_key
    context.idempotency.payload_hashes_by_write_node[
        request.node_id
    ] = request.payload_hash
    events: list[str] = []
    outcomes = _CommitOutcomePort()
    executor = TrustedCommitAwareWriteExecutor(
        TrustedWriteExecutionCoordinator(
            WriteExecutionCoordinator(
                provider_type(events),
                _EvidencePort(events),
            )
        ),
        WriteOutcomeCoordinator(outcomes),
    )

    decision = asyncio.run(
        executor.execute(
            artifact=artifact,
            profile=_profile(artifact),
            trust_checked_at=NOW,
            request=request,
            decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
            context=context,
            requested_at=NOW,
            recorded_at=NOW,
        )
    )

    assert decision.execution_status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert decision.outcome.commit_state is CommitState.UNKNOWN_COMMIT_STATE
    assert decision.outcome.reason_code == reason_code
    assert decision.automatic_retry_allowed is False
    assert decision.reconciliation_required is True
    assert events == ["WRITE_REQUESTED", "PROVIDER_WRITE"]
    assert len(outcomes.records) == 1


def test_trusted_commit_boundary_rejects_invalid_dependencies() -> None:
    events: list[str] = []
    invocation = TrustedWriteExecutionCoordinator(
        WriteExecutionCoordinator(_WritePort(events), _EvidencePort(events))
    )
    outcomes = WriteOutcomeCoordinator(_CommitOutcomePort())

    with pytest.raises(TypeError, match="TrustedWriteExecutionCoordinator"):
        TrustedCommitAwareWriteExecutor(  # type: ignore[arg-type]
            object(),
            outcomes,
        )
    with pytest.raises(TypeError, match="WriteOutcomeCoordinator"):
        TrustedCommitAwareWriteExecutor(  # type: ignore[arg-type]
            invocation,
            object(),
        )
