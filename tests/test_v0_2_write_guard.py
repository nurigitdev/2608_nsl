from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from nsl.compiler import NslCompiler
from nsl.core import (
    ApprovalStatus,
    BusinessOperationId,
    CheckStatus,
    DataClassification,
    PreWriteEligibility,
    RiskLevel,
    ToolCapability,
    ValueEnvelope,
    domain,
)
from nsl.runtime_models import ExecutionContext, ExecutionRequest, ValidationSummary
from nsl.security import DataHandlingPolicy
from nsl.tools import (
    Atomicity,
    CommitConfirmation,
    CommitSemantics,
    IdempotencySemantics,
    OperationKind,
    ToolContract,
    WriteSemantics,
)
from nsl.validation import (
    BusinessPreconditionEvidence,
    BusinessPreconditionMechanism,
    PolicyDecision,
    PolicyEffect,
    PreWriteEvidence,
    RiskAcceptancePolicy,
    RuntimePolicyPort,
    WriteContractEvidence,
    WriteGuard,
    WriteGuardDecision,
    WriteGuardRequest,
    write_policy_request,
)
from nsl.vertical_slice import build_principal, build_tool_catalog
from nsl.write_intent import (
    WriteIntent,
    compute_payload_hash,
    compute_write_intent_hash,
)


NOW = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)


def _intent(
    *,
    tenant_id: str = "tenant-a",
    business_operation_id: str = "business-0052",
    node_id: str = "write0001",
    tool_id: str = "FINANCE.CREATE_DRAFT",
) -> WriteIntent:
    arguments = {
        "detail": ValueEnvelope.complete(
            {"description": "KTX", "amount": Decimal("84000")},
            domain("DraftPayload"),
            DataClassification.CONFIDENTIAL,
        )
    }
    payload_hash = compute_payload_hash(arguments)
    semantic_hash = "sha256:" + "a" * 64
    return WriteIntent(
        tenant_id=tenant_id,
        business_operation_id=BusinessOperationId(business_operation_id),
        node_id=node_id,
        tool_ref="write_tool",
        tool_id=tool_id,
        approval_id="APPROVE_DRAFT",
        skill_semantic_hash=semantic_hash,
        canonical_arguments=arguments,
        payload_hash=payload_hash,
        write_intent_hash=compute_write_intent_hash(
            skill_semantic_hash=semantic_hash,
            write_node_id=node_id,
            tool_id=tool_id,
            payload_hash=payload_hash,
        ),
    )


def _context(
    *,
    tenant_id: str = "tenant-a",
    business_operation_id: str = "business-0052",
) -> ExecutionContext:
    skill = NslCompiler(build_tool_catalog()).compile(
        '''language NSL "0.1";
skill TEST.WRITE_GUARD_CONTEXT {
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
    ).skill
    return ExecutionContext(
        skill,
        ExecutionRequest(
            execution_id="exec-0052",
            inputs={},
            runtime_context={},
            principal=build_principal(tenant_id=tenant_id),
            data_policy=DataHandlingPolicy(),
            business_operation_id=BusinessOperationId(business_operation_id),
        ),
    )


def _contract(
    *,
    risk_level: RiskLevel = RiskLevel.R3,
    tool_id: str = "FINANCE.CREATE_DRAFT",
) -> ToolContract:
    return ToolContract(
        tool_id=tool_id,
        version="1.0.0",
        capability=ToolCapability.WRITE,
        input_types=(("detail", domain("DraftPayload")),),
        output_type=domain("DraftRecord"),
        required_scope="finance:draft:write",
        output_classification=DataClassification.CONFIDENTIAL,
        operation_kind=OperationKind.CREATE,
        risk_level=risk_level,
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


def _execution_evidence(**changes: object) -> PreWriteEvidence:
    values: dict[str, object] = {
        "validation": ValidationSummary.from_checks(()),
        "approval_status": ApprovalStatus.APPROVED,
        "approval_evidence_valid": True,
        "runtime_preflight_passed": True,
        "current_policy_allowed": False,
        "tool_preconditions_passed": False,
        "business_preconditions_passed": False,
        "intent_binding_valid": True,
        "idempotency_ready": True,
        "durability_ready": True,
        "trust_valid": True,
        "replay_live_write_allowed": True,
    }
    values.update(changes)
    return PreWriteEvidence(**values)  # type: ignore[arg-type]


def _contract_evidence(contract: ToolContract) -> WriteContractEvidence:
    return WriteContractEvidence(
        tool_id=contract.tool_id,
        contract_hash=contract.contract_hash,
        capability=contract.capability,
        risk_level=contract.risk_level or RiskLevel.R3,
        preconditions_supported=(
            contract.write_semantics is not None
            and contract.write_semantics.preconditions_supported
        ),
    )


def _policy(
    intent: WriteIntent,
    context: ExecutionContext,
    contract: ToolContract,
    **changes: object,
) -> PolicyDecision:
    request = write_policy_request(intent, context, _contract_evidence(contract))
    values: dict[str, object] = {
        "decision_id": "policy-decision-0052",
        "policy_id": "write-policy",
        "policy_version": "7",
        "effect": PolicyEffect.ALLOW,
        "tenant_id": request.tenant_id,
        "subject_id": request.subject_id,
        "action": request.action,
        "tool_id": request.tool_id,
        "risk_level": request.risk_level,
        "business_operation_id": request.business_operation_id,
        "write_node_id": request.write_node_id,
        "write_intent_hash": request.write_intent_hash,
        "evidence_ref": "policy:evidence:0052",
        "evaluated_at": NOW - timedelta(seconds=1),
        "expires_at": NOW + timedelta(seconds=1),
    }
    values.update(changes)
    return PolicyDecision(**values)  # type: ignore[arg-type]


def _precondition(
    intent: WriteIntent,
    context: ExecutionContext,
    contract: ToolContract,
    **changes: object,
) -> BusinessPreconditionEvidence:
    values: dict[str, object] = {
        "evidence_id": "precondition-0052",
        "tenant_id": context.tenant_id,
        "business_operation_id": intent.business_operation_id,
        "write_node_id": intent.node_id,
        "tool_id": intent.tool_id,
        "contract_hash": contract.contract_hash,
        "payload_hash": intent.payload_hash,
        "write_intent_hash": intent.write_intent_hash,
        "mechanism": BusinessPreconditionMechanism.ETAG,
        "evidence_ref": "etag:opaque:revision-7",
        "observed_at": NOW - timedelta(seconds=1),
        "expires_at": NOW + timedelta(seconds=1),
        "provider_atomic_enforcement": True,
    }
    values.update(changes)
    return BusinessPreconditionEvidence(**values)  # type: ignore[arg-type]


def _request(
    *,
    intent: WriteIntent | None = None,
    context: ExecutionContext | None = None,
    contract: ToolContract | None = None,
    policy: PolicyDecision | None | object = object(),
    precondition: BusinessPreconditionEvidence | None | object = object(),
    evidence: PreWriteEvidence | None = None,
) -> WriteGuardRequest:
    selected_intent = intent or _intent()
    selected_context = context or _context()
    selected_contract = contract or _contract()
    selected_policy = (
        _policy(selected_intent, selected_context, selected_contract)
        if type(policy) is object
        else policy
    )
    selected_precondition = (
        _precondition(selected_intent, selected_context, selected_contract)
        if type(precondition) is object
        else precondition
    )
    return WriteGuardRequest(
        intent=selected_intent,
        context=selected_context,
        contract_evidence=_contract_evidence(selected_contract),
        execution_evidence=evidence or _execution_evidence(),
        policy_decision=selected_policy,  # type: ignore[arg-type]
        business_preconditions=selected_precondition,  # type: ignore[arg-type]
    )


def test_rsk_003_current_external_policy_and_preconditions_allow_prewrite() -> None:
    request = _request()

    decision = WriteGuard().evaluate(request, now=NOW)

    assert decision == WriteGuardDecision(PreWriteEligibility.ALLOWED)
    assert request.business_preconditions is not None
    assert request.business_preconditions.provider_atomic_enforcement is True


def test_runtime_policy_port_contract_is_external_and_verifiable() -> None:
    class PolicyPort:
        def authorize_write(self, request):
            intent = _intent()
            context = _context()
            contract = _contract()
            assert request == write_policy_request(
                intent,
                context,
                _contract_evidence(contract),
            )
            return _policy(intent, context, contract)

    assert isinstance(PolicyPort(), RuntimePolicyPort)


@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        (None, "POLICY_EVIDENCE_MISSING"),
        ("DENY", "POLICY_DENIED"),
        ("EXPIRED", "POLICY_NOT_CURRENT"),
        ("FUTURE", "POLICY_NOT_CURRENT"),
    ],
)
def test_rsk_003_missing_denied_or_noncurrent_policy_fails_closed(
    policy: str | None,
    reason: str,
) -> None:
    intent = _intent()
    context = _context()
    contract = _contract()
    selected = None
    if policy == "DENY":
        selected = _policy(
            intent,
            context,
            contract,
            effect=PolicyEffect.DENY,
        )
    elif policy == "EXPIRED":
        selected = _policy(
            intent,
            context,
            contract,
            evaluated_at=NOW - timedelta(seconds=2),
            expires_at=NOW,
        )
    elif policy == "FUTURE":
        selected = _policy(
            intent,
            context,
            contract,
            evaluated_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(seconds=2),
        )

    decision = WriteGuard().evaluate(
        _request(
            intent=intent,
            context=context,
            contract=contract,
            policy=selected,
        ),
        now=NOW,
    )

    assert decision.reason_code == reason


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "tenant-other"},
        {"subject_id": "subject-other"},
        {"tool_id": "FINANCE.OTHER"},
        {"risk_level": RiskLevel.R4},
        {"business_operation_id": BusinessOperationId("other-business")},
        {"write_node_id": "write9999"},
        {"write_intent_hash": "sha256:" + "f" * 64},
    ],
)
def test_rsk_003_policy_identity_mismatch_fails_closed(changes) -> None:
    intent = _intent()
    context = _context()
    contract = _contract()
    policy = _policy(intent, context, contract, **changes)

    decision = WriteGuard().evaluate(
        _request(
            intent=intent,
            context=context,
            contract=contract,
            policy=policy,
        ),
        now=NOW,
    )

    assert decision.reason_code == "POLICY_BINDING_MISMATCH"


@pytest.mark.parametrize("risk_level", list(RiskLevel))
def test_rsk_005_initial_acceptance_profile_is_explicitly_r3_only(
    risk_level: RiskLevel,
) -> None:
    intent = _intent()
    context = _context()
    contract = _contract(risk_level=risk_level)
    request = _request(intent=intent, context=context, contract=contract)

    decision = WriteGuard().evaluate(request, now=NOW)

    assert decision.eligibility is (
        PreWriteEligibility.ALLOWED
        if risk_level is RiskLevel.R3
        else PreWriteEligibility.BLOCKED
    )
    assert decision.reason_code == (
        None if risk_level is RiskLevel.R3 else "RISK_NOT_ACCEPTED"
    )


def test_rsk_005_risk_acceptance_is_configurable_not_business_policy() -> None:
    contract = _contract(risk_level=RiskLevel.R4)
    request = _request(contract=contract)
    policy = RiskAcceptancePolicy(frozenset(RiskLevel))

    assert WriteGuard(policy).evaluate(request, now=NOW).eligibility is (
        PreWriteEligibility.ALLOWED
    )


@pytest.mark.parametrize("mechanism", list(BusinessPreconditionMechanism))
def test_wrt_011_all_provider_precondition_mechanisms_are_supported(
    mechanism: BusinessPreconditionMechanism,
) -> None:
    intent = _intent()
    context = _context()
    contract = _contract()
    precondition = _precondition(
        intent,
        context,
        contract,
        mechanism=mechanism,
    )

    decision = WriteGuard().evaluate(
        _request(
            intent=intent,
            context=context,
            contract=contract,
            precondition=precondition,
        ),
        now=NOW,
    )

    assert decision.eligibility is PreWriteEligibility.ALLOWED


@pytest.mark.parametrize(
    ("precondition", "reason"),
    [
        (None, "BUSINESS_PRECONDITION_MISSING"),
        ("EXPIRED", "BUSINESS_PRECONDITION_NOT_CURRENT"),
        ("FUTURE", "BUSINESS_PRECONDITION_NOT_CURRENT"),
        ("NO_PROVIDER_ATOMICITY", "PROVIDER_PRECONDITION_UNAVAILABLE"),
    ],
)
def test_wrt_009_precondition_freshness_and_provider_atomicity_fail_closed(
    precondition: str | None,
    reason: str,
) -> None:
    intent = _intent()
    context = _context()
    contract = _contract()
    selected = None
    if precondition == "EXPIRED":
        selected = _precondition(
            intent,
            context,
            contract,
            observed_at=NOW - timedelta(seconds=1),
            expires_at=NOW,
        )
    elif precondition == "FUTURE":
        selected = _precondition(
            intent,
            context,
            contract,
            observed_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(seconds=2),
        )
    elif precondition == "NO_PROVIDER_ATOMICITY":
        selected = _precondition(
            intent,
            context,
            contract,
            provider_atomic_enforcement=False,
        )

    decision = WriteGuard().evaluate(
        _request(
            intent=intent,
            context=context,
            contract=contract,
            precondition=selected,
        ),
        now=NOW,
    )

    assert decision.reason_code == reason


def test_wrt_009_exact_current_time_boundaries() -> None:
    intent = _intent()
    context = _context()
    contract = _contract()
    policy = _policy(
        intent,
        context,
        contract,
        evaluated_at=NOW,
        expires_at=NOW + timedelta(microseconds=1),
    )
    precondition = _precondition(
        intent,
        context,
        contract,
        observed_at=NOW,
        expires_at=NOW + timedelta(microseconds=1),
    )

    decision = WriteGuard().evaluate(
        _request(
            intent=intent,
            context=context,
            contract=contract,
            policy=policy,
            precondition=precondition,
        ),
        now=NOW,
    )

    assert decision.eligibility is PreWriteEligibility.ALLOWED


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "tenant-other"},
        {"business_operation_id": BusinessOperationId("other-business")},
        {"write_node_id": "write9999"},
        {"tool_id": "FINANCE.OTHER"},
        {"contract_hash": "sha256:" + "b" * 64},
        {"payload_hash": "sha256:" + "c" * 64},
        {"write_intent_hash": "sha256:" + "d" * 64},
    ],
)
def test_wrt_009_precondition_identity_mismatch_fails_closed(changes) -> None:
    intent = _intent()
    context = _context()
    contract = _contract()
    precondition = _precondition(intent, context, contract, **changes)

    decision = WriteGuard().evaluate(
        _request(
            intent=intent,
            context=context,
            contract=contract,
            precondition=precondition,
        ),
        now=NOW,
    )

    assert decision.reason_code == "BUSINESS_PRECONDITION_BINDING_MISMATCH"


def test_rd_007_runtime_decision_does_not_claim_provider_condition_passed() -> None:
    request = _request()
    decision = WriteGuard().evaluate(request, now=NOW)

    assert decision.eligibility is PreWriteEligibility.ALLOWED
    assert not hasattr(decision, "provider_precondition_passed")
    assert request.business_preconditions is not None
    assert request.business_preconditions.evidence_ref == "etag:opaque:revision-7"


@pytest.mark.parametrize(
    ("evidence_change", "reason"),
    [
        ({"runtime_preflight_passed": False}, "RUNTIME_PREFLIGHT_FAILED"),
        ({"approval_status": ApprovalStatus.PENDING}, "APPROVAL_REQUIRED"),
        ({"idempotency_ready": False}, "IDEMPOTENCY_NOT_READY"),
    ],
)
def test_write_guard_preserves_existing_prewrite_axes(evidence_change, reason) -> None:
    decision = WriteGuard().evaluate(
        _request(evidence=_execution_evidence(**evidence_change)),
        now=NOW,
    )

    assert decision.reason_code == reason


def test_write_guard_rejects_context_and_contract_binding_mismatch() -> None:
    tenant_decision = WriteGuard().evaluate(
        _request(context=_context(tenant_id="tenant-other")),
        now=NOW,
    )
    operation_decision = WriteGuard().evaluate(
        _request(context=_context(business_operation_id="other-business")),
        now=NOW,
    )
    intent = _intent()
    context = _context()
    mismatched_contract = _contract(tool_id="FINANCE.OTHER")
    contract_decision = WriteGuard().evaluate(
        _request(
            intent=intent,
            context=context,
            contract=mismatched_contract,
            policy=None,
            precondition=None,
        ),
        now=NOW,
    )

    assert tenant_decision.reason_code == "WRITE_TENANT_BINDING_MISMATCH"
    assert operation_decision.reason_code == "WRITE_BUSINESS_OPERATION_MISMATCH"
    assert contract_decision.reason_code == "WRITE_CONTRACT_INVALID"


def test_write_guard_rejects_read_or_nonprecondition_contract() -> None:
    read_contract = ToolContract(
        tool_id="FINANCE.CREATE_DRAFT",
        version="1.0.0",
        capability=ToolCapability.READ,
        input_types=(("detail", domain("DraftPayload")),),
        output_type=domain("DraftRecord"),
        required_scope="finance:draft:read",
        output_classification=DataClassification.CONFIDENTIAL,
    )
    read_decision = WriteGuard().evaluate(
        _request(contract=read_contract, policy=None, precondition=None),
        now=NOW,
    )
    contract = _contract()
    assert contract.write_semantics is not None
    object.__setattr__(contract.write_semantics, "preconditions_supported", False)
    precondition_decision = WriteGuard().evaluate(
        _request(contract=contract, policy=None, precondition=None),
        now=NOW,
    )

    assert read_decision.reason_code == "WRITE_CONTRACT_INVALID"
    assert precondition_decision.reason_code == "WRITE_CONTRACT_INVALID"


def test_policy_and_precondition_models_are_immutable_and_protected() -> None:
    intent = _intent()
    context = _context()
    contract = _contract()
    policy = _policy(intent, context, contract)
    precondition = _precondition(intent, context, contract)

    with pytest.raises(FrozenInstanceError):
        policy.effect = PolicyEffect.DENY  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        precondition.evidence_ref = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="credential"):
        replace(policy, evidence_ref="api_key=secret")


@pytest.mark.parametrize(
    ("target", "changes", "error", "message"),
    [
        ("policy", {"effect": "ALLOW"}, TypeError, "PolicyEffect"),
        ("policy", {"risk_level": "R3"}, TypeError, "RiskLevel"),
        ("policy", {"action": "READ"}, ValueError, "must be WRITE"),
        ("policy", {"evaluated_at": "now"}, TypeError, "datetime"),
        (
            "policy",
            {"evaluated_at": NOW.replace(tzinfo=None)},
            ValueError,
            "timezone-aware UTC",
        ),
        (
            "policy",
            {"evaluated_at": NOW.astimezone(timezone(timedelta(hours=9)))},
            ValueError,
            "must use UTC",
        ),
        ("policy", {"expires_at": NOW - timedelta(seconds=1)}, ValueError, "expire"),
        ("precondition", {"mechanism": "ETAG"}, TypeError, "mechanism"),
        ("precondition", {"provider_atomic_enforcement": 1}, TypeError, "Bool"),
        ("precondition", {"observed_at": "now"}, TypeError, "datetime"),
        (
            "precondition",
            {"expires_at": NOW - timedelta(seconds=2)},
            ValueError,
            "expire",
        ),
        ("precondition", {"contract_hash": "bad"}, ValueError, "SHA-256"),
    ],
)
def test_write_guard_evidence_models_reject_malformed_values(
    target,
    changes,
    error,
    message,
) -> None:
    intent = _intent()
    context = _context()
    contract = _contract()
    constructor = _policy if target == "policy" else _precondition

    with pytest.raises(error, match=message):
        constructor(intent, context, contract, **changes)


def test_risk_policy_and_guard_constructor_reject_malformed_values() -> None:
    with pytest.raises(TypeError, match="accepted_levels"):
        RiskAcceptancePolicy(frozenset())
    with pytest.raises(TypeError, match="accepted_levels"):
        RiskAcceptancePolicy({RiskLevel.R3})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="risk_policy"):
        WriteGuard(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="evaluator"):
        WriteGuard(evaluator=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="request"):
        WriteGuard().evaluate(object(), now=NOW)  # type: ignore[arg-type]


def test_write_contract_evidence_requires_typed_capability_and_bool() -> None:
    evidence = _contract_evidence(_contract())

    with pytest.raises(TypeError, match="ToolCapability"):
        replace(evidence, capability="WRITE")
    with pytest.raises(TypeError, match="preconditions_supported"):
        replace(evidence, preconditions_supported=1)


def test_write_policy_request_and_guard_request_reject_wrong_types() -> None:
    intent = _intent()
    context = _context()
    contract = _contract()

    contract_evidence = _contract_evidence(contract)
    for field in ("intent", "context", "contract_evidence"):
        values = {
            "intent": intent,
            "context": context,
            "contract_evidence": contract_evidence,
        }
        values[field] = object()
        with pytest.raises(TypeError, match=field):
            write_policy_request(**values)  # type: ignore[arg-type]

    valid = _request()
    for field in (
        "intent",
        "context",
        "contract_evidence",
        "execution_evidence",
        "policy_decision",
        "business_preconditions",
    ):
        with pytest.raises(TypeError, match=field):
            replace(valid, **{field: object()})


def test_write_policy_request_requires_canonical_fields_and_risk() -> None:
    request = write_policy_request(
        _intent(),
        _context(),
        _contract_evidence(_contract()),
    )

    with pytest.raises(ValueError, match="non-empty"):
        replace(request, subject_id="")
    with pytest.raises(ValueError, match="must be WRITE"):
        replace(request, action="READ")
    with pytest.raises(TypeError, match="RiskLevel"):
        replace(request, risk_level="R3")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="SHA-256"):
        replace(request, write_intent_hash="bad")
    with pytest.raises(TypeError, match="RiskLevel"):
        replace(_contract_evidence(_contract()), risk_level=None)


def test_now_must_be_utc_datetime() -> None:
    request = _request()

    with pytest.raises(TypeError, match="datetime"):
        WriteGuard().evaluate(request, now="now")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        WriteGuard().evaluate(request, now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="must use UTC"):
        WriteGuard().evaluate(
            request,
            now=NOW.astimezone(timezone(timedelta(hours=9))),
        )


def test_policy_matches_rejects_different_action_without_invalid_model() -> None:
    intent = _intent()
    context = _context()
    contract = _contract()
    policy = _policy(intent, context, contract)
    request = write_policy_request(intent, context, _contract_evidence(contract))
    object.__setattr__(request, "action", "OTHER")

    assert policy.matches(request) is False
