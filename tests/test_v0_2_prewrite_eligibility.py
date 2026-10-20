from __future__ import annotations

from dataclasses import replace

import pytest

from nsl.core import (
    ApprovalStatus,
    CheckStatus,
    Completeness,
    ExecutionStatus,
    PreWriteEligibility,
    Presence,
)
from nsl.runtime_models import (
    CheckResult,
    ExecutionResult,
    ResourceUsage,
    ValidationSummary,
    ValidationSummaryStatus,
)
from nsl.validation import (
    PreWriteEvaluator,
    PreWriteEvidence,
    WriteEligibilityError,
    WriteEligibilityGate,
    WriteGuardDecision,
)


def _check(check_id: str, status: CheckStatus) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        status=status,
        severity="ERROR",
        message=check_id,
        condition_node_id="condition-" + check_id,
        presence=Presence.PRESENT,
        completeness=(
            Completeness.UNKNOWN
            if status is CheckStatus.UNKNOWN
            else Completeness.COMPLETE
        ),
        provenance_refs=("evidence:" + check_id,),
        reason_code=None,
    )


def _summary(*statuses: CheckStatus) -> ValidationSummary:
    return ValidationSummary.from_checks(
        tuple(_check(f"check-{index:04d}", status) for index, status in enumerate(statuses))
    )


def _allowed_evidence(**changes) -> PreWriteEvidence:
    values = {
        "validation": _summary(CheckStatus.PASS),
        "approval_status": ApprovalStatus.APPROVED,
        "approval_evidence_valid": True,
        "runtime_preflight_passed": True,
        "current_policy_allowed": True,
        "tool_preconditions_passed": True,
        "business_preconditions_passed": True,
        "intent_binding_valid": True,
        "idempotency_ready": True,
        "durability_ready": True,
        "trust_valid": True,
        "replay_live_write_allowed": True,
    }
    values.update(changes)
    return PreWriteEvidence(**values)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((), ValidationSummaryStatus.NO_CHECK),
        ((CheckStatus.PASS,), ValidationSummaryStatus.ALL_PASS),
        ((CheckStatus.FAIL,), ValidationSummaryStatus.HAS_FAIL),
        ((CheckStatus.UNKNOWN,), ValidationSummaryStatus.HAS_UNKNOWN),
        (
            (CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.UNKNOWN),
            ValidationSummaryStatus.HAS_FAIL_AND_UNKNOWN,
        ),
    ],
)
def test_validation_summary_has_canonical_statuses(statuses, expected) -> None:
    summary = _summary(*statuses)

    assert summary.status is expected
    assert summary.total_count == len(statuses)
    assert summary.pass_count == statuses.count(CheckStatus.PASS)
    assert summary.fail_count == statuses.count(CheckStatus.FAIL)
    assert summary.unknown_count == statuses.count(CheckStatus.UNKNOWN)


def test_validation_summary_is_order_independent_and_deterministic() -> None:
    checks = (
        _check("z-fail", CheckStatus.FAIL),
        _check("a-unknown", CheckStatus.UNKNOWN),
        _check("a-fail", CheckStatus.FAIL),
    )

    first = ValidationSummary.from_checks(checks)
    second = ValidationSummary.from_checks(tuple(reversed(checks)))

    assert first == second
    assert first.failed_check_ids == ("a-fail", "z-fail")
    assert first.unknown_check_ids == ("a-unknown",)
    assert first.to_data() == {
        "status": "HAS_FAIL_AND_UNKNOWN",
        "total_count": 3,
        "pass_count": 0,
        "fail_count": 2,
        "unknown_count": 1,
        "failed_check_ids": ["a-fail", "z-fail"],
        "unknown_check_ids": ["a-unknown"],
    }


def test_validation_summary_handles_worst_case_collection_deterministically() -> None:
    checks = tuple(
        _check(f"check-{index:04d}", CheckStatus.PASS)
        for index in reversed(range(1_000))
    )

    summary = ValidationSummary.from_checks(checks)

    assert summary.status is ValidationSummaryStatus.ALL_PASS
    assert summary.total_count == 1_000
    assert summary.pass_count == 1_000


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"status": "ALL_PASS"}, TypeError, "ValidationSummaryStatus"),
        ({"total_count": -1}, ValueError, "non-negative integers"),
        ({"pass_count": False}, ValueError, "non-negative integers"),
        ({"pass_count": 1}, ValueError, "do not add up"),
        (
            {"status": ValidationSummaryStatus.ALL_PASS},
            ValueError,
            "status does not match",
        ),
        ({"failed_check_ids": []}, ValueError, "failed_check_ids"),
        ({"failed_check_ids": ("b", "a")}, ValueError, "failed_check_ids"),
        ({"failed_check_ids": ("a", "a")}, ValueError, "failed_check_ids"),
        ({"failed_check_ids": ("", "b")}, ValueError, "failed_check_ids"),
        ({"failed_check_ids": ("a",)}, ValueError, "failed_check_ids"),
    ],
)
def test_validation_summary_rejects_inconsistent_direct_construction(
    changes, error, message
) -> None:
    valid = ValidationSummary(
        ValidationSummaryStatus.HAS_FAIL,
        total_count=2,
        pass_count=0,
        fail_count=2,
        unknown_count=0,
        failed_check_ids=("a", "b"),
    )

    with pytest.raises(error, match=message):
        replace(valid, **changes)


def test_validation_summary_rejects_overlapping_outcomes() -> None:
    with pytest.raises(ValueError, match="outcomes overlap"):
        ValidationSummary(
            ValidationSummaryStatus.HAS_FAIL_AND_UNKNOWN,
            total_count=2,
            pass_count=0,
            fail_count=1,
            unknown_count=1,
            failed_check_ids=("same",),
            unknown_check_ids=("same",),
        )


@pytest.mark.parametrize(
    ("checks", "error", "message"),
    [
        ([], TypeError, "tuple of CheckResult"),
        ((object(),), TypeError, "tuple of CheckResult"),
        ((_check("", CheckStatus.PASS),), ValueError, "unique non-empty"),
        (
            (
                _check("duplicate", CheckStatus.PASS),
                _check("duplicate", CheckStatus.FAIL),
            ),
            ValueError,
            "unique non-empty",
        ),
        (
            (replace(_check("forged", CheckStatus.PASS), status="PASS"),),
            TypeError,
            "CheckStatus",
        ),
    ],
)
def test_validation_summary_rejects_malformed_check_collections(
    checks, error, message
) -> None:
    with pytest.raises(error, match=message):
        ValidationSummary.from_checks(checks)


def test_wel_001_required_check_fail_is_blocked_even_when_approved() -> None:
    decision = PreWriteEvaluator().evaluate(
        _allowed_evidence(validation=_summary(CheckStatus.FAIL))
    )

    assert decision == WriteGuardDecision(
        PreWriteEligibility.BLOCKED,
        "REQUIRED_CHECK_FAILED",
    )


@pytest.mark.parametrize(
    ("statuses", "reason"),
    [
        ((CheckStatus.UNKNOWN,), "REQUIRED_CHECK_UNKNOWN"),
        (
            (CheckStatus.FAIL, CheckStatus.UNKNOWN),
            "REQUIRED_CHECK_FAILED_AND_UNKNOWN",
        ),
    ],
)
def test_wel_002_required_unknown_is_blocked(statuses, reason) -> None:
    decision = PreWriteEvaluator().evaluate(
        _allowed_evidence(validation=_summary(*statuses))
    )

    assert decision.eligibility is PreWriteEligibility.BLOCKED
    assert decision.reason_code == reason


@pytest.mark.parametrize(
    "approval_status",
    [
        ApprovalStatus.NOT_REQUESTED,
        ApprovalStatus.PENDING,
        ApprovalStatus.EXPIRED,
    ],
)
def test_wel_003_missing_pending_or_expired_approval_requires_approval(
    approval_status,
) -> None:
    decision = PreWriteEvaluator().evaluate(
        _allowed_evidence(
            approval_status=approval_status,
            approval_evidence_valid=False,
        )
    )

    assert decision == WriteGuardDecision(
        PreWriteEligibility.REQUIRES_APPROVAL,
        "APPROVAL_REQUIRED",
    )


def test_rejected_approval_is_blocked() -> None:
    decision = PreWriteEvaluator().evaluate(
        _allowed_evidence(
            approval_status=ApprovalStatus.REJECTED,
            approval_evidence_valid=False,
        )
    )

    assert decision.reason_code == "APPROVAL_REJECTED"
    assert decision.eligibility is PreWriteEligibility.BLOCKED


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("runtime_preflight_passed", "RUNTIME_PREFLIGHT_FAILED"),
        ("trust_valid", "PACKAGE_TRUST_INVALID"),
        ("current_policy_allowed", "POLICY_DENIED"),
        ("tool_preconditions_passed", "TOOL_PRECONDITION_FAILED"),
        ("business_preconditions_passed", "BUSINESS_PRECONDITION_FAILED"),
        ("intent_binding_valid", "WRITE_BINDING_INVALID"),
        ("idempotency_ready", "IDEMPOTENCY_NOT_READY"),
        ("durability_ready", "DURABILITY_NOT_READY"),
        ("replay_live_write_allowed", "REPLAY_LIVE_WRITE_FORBIDDEN"),
    ],
)
def test_wel_004_each_current_control_failure_prevents_allowed(field, reason) -> None:
    decision = PreWriteEvaluator().evaluate(_allowed_evidence(**{field: False}))

    assert decision == WriteGuardDecision(PreWriteEligibility.BLOCKED, reason)


def test_wel_004_invalid_approval_evidence_prevents_allowed() -> None:
    decision = PreWriteEvaluator().evaluate(
        _allowed_evidence(approval_evidence_valid=False)
    )

    assert decision == WriteGuardDecision(
        PreWriteEligibility.BLOCKED,
        "APPROVAL_EVIDENCE_INVALID",
    )


def test_all_required_evidence_produces_allowed() -> None:
    decision = PreWriteEvaluator().evaluate(_allowed_evidence())

    assert decision == WriteGuardDecision(PreWriteEligibility.ALLOWED)


def test_no_check_is_distinct_but_can_proceed_when_all_controls_allow() -> None:
    decision = PreWriteEvaluator().evaluate(
        _allowed_evidence(validation=_summary())
    )

    assert decision.eligibility is PreWriteEligibility.ALLOWED


def test_default_evidence_is_fail_closed_with_deterministic_priority() -> None:
    evidence = PreWriteEvidence(validation=_summary())

    assert PreWriteEvaluator().evaluate(evidence).reason_code == (
        "RUNTIME_PREFLIGHT_FAILED"
    )


def test_wel_005_reconciliation_is_not_prewrite_eligibility() -> None:
    assert {item.value for item in PreWriteEligibility} == {
        "ALLOWED",
        "BLOCKED",
        "REQUIRES_APPROVAL",
    }
    with pytest.raises(ValueError):
        PreWriteEligibility("UNKNOWN_COMMIT_STATE")


@pytest.mark.parametrize(
    "eligibility",
    [PreWriteEligibility.BLOCKED, PreWriteEligibility.REQUIRES_APPROVAL],
)
def test_wrt_005_write_entry_rejects_every_non_allowed_decision(
    eligibility,
) -> None:
    with pytest.raises(WriteEligibilityError, match="WRITE blocked"):
        WriteEligibilityGate.require_allowed(
            WriteGuardDecision(eligibility, "NOT_ALLOWED")
        )


def test_wrt_005_write_entry_accepts_only_allowed() -> None:
    assert (
        WriteEligibilityGate.require_allowed(
            WriteGuardDecision(PreWriteEligibility.ALLOWED)
        )
        is None
    )


def test_write_entry_uses_safe_fallback_reason() -> None:
    with pytest.raises(WriteEligibilityError, match="PRE_WRITE_NOT_ALLOWED"):
        WriteEligibilityGate.require_allowed(
            WriteGuardDecision(PreWriteEligibility.BLOCKED)
        )


@pytest.mark.parametrize(
    "field",
    [
        "approval_evidence_valid",
        "runtime_preflight_passed",
        "current_policy_allowed",
        "tool_preconditions_passed",
        "business_preconditions_passed",
        "intent_binding_valid",
        "idempotency_ready",
        "durability_ready",
        "trust_valid",
        "replay_live_write_allowed",
    ],
)
def test_prewrite_evidence_requires_exact_bool_controls(field) -> None:
    with pytest.raises(TypeError, match=field + " must be Bool"):
        replace(_allowed_evidence(), **{field: 1})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"validation": object()}, "ValidationSummary"),
        ({"approval_status": "APPROVED"}, "ApprovalStatus"),
    ],
)
def test_prewrite_evidence_rejects_untyped_semantics(changes, message) -> None:
    with pytest.raises(TypeError, match=message):
        replace(_allowed_evidence(), **changes)


@pytest.mark.parametrize(
    ("decision", "error", "message"),
    [
        (WriteGuardDecision(PreWriteEligibility.ALLOWED), None, None),
        (object(), TypeError, "PreWriteEvidence"),
    ],
)
def test_prewrite_evaluator_rejects_non_evidence(decision, error, message) -> None:
    if error is None:
        assert decision.eligibility is PreWriteEligibility.ALLOWED
        return
    with pytest.raises(error, match=message):
        PreWriteEvaluator().evaluate(decision)


@pytest.mark.parametrize(
    ("eligibility", "reason", "error", "message"),
    [
        ("ALLOWED", None, TypeError, "PreWriteEligibility"),
        (PreWriteEligibility.BLOCKED, "", ValueError, "reason_code"),
        (PreWriteEligibility.BLOCKED, 1, ValueError, "reason_code"),
    ],
)
def test_write_guard_decision_rejects_malformed_values(
    eligibility, reason, error, message
) -> None:
    with pytest.raises(error, match=message):
        WriteGuardDecision(eligibility, reason)


def test_write_entry_rejects_untyped_decision() -> None:
    with pytest.raises(TypeError, match="WriteGuardDecision"):
        WriteEligibilityGate.require_allowed(object())


def test_validation_and_eligibility_serialize_as_distinct_result_fields() -> None:
    summary = _summary(CheckStatus.PASS)
    decision = PreWriteEvaluator().evaluate(_allowed_evidence(validation=summary))
    result = ExecutionResult(
        execution_id="execution-prewrite",
        skill_id="PREWRITE.TEST",
        skill_version="1.0.0",
        semantic_hash="sha256:" + "d" * 64,
        status=ExecutionStatus.RUNNING,
        completeness=Completeness.COMPLETE,
        checks=(),
        outputs=(),
        resources=ResourceUsage(0, 0, 0, 0),
        validation=summary,
        pre_write_eligibility=decision.eligibility,
    )

    data = result.to_data()
    assert data["validation"] == summary.to_data()
    assert data["pre_write_eligibility"] == "ALLOWED"
    assert result.semantic_view()["validation"] == summary.to_data()


def test_execution_result_rejects_invalid_validation_summary() -> None:
    with pytest.raises(TypeError, match="ValidationSummary"):
        ExecutionResult(
            execution_id="execution-prewrite",
            skill_id="PREWRITE.TEST",
            skill_version="1.0.0",
            semantic_hash="sha256:" + "d" * 64,
            status=ExecutionStatus.RUNNING,
            completeness=Completeness.COMPLETE,
            checks=(),
            outputs=(),
            resources=ResourceUsage(0, 0, 0, 0),
            validation=object(),
        )
