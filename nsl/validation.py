from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .core import (
    BOOL,
    ApprovalStatus,
    CheckStatus,
    Completeness,
    Presence,
    PreWriteEligibility,
    ValueEnvelope,
)
from .ir import CheckStatement
from .runtime_models import (
    CheckResult,
    ValidationSummary,
    ValidationSummaryStatus,
)


class CheckEvaluationError(RuntimeError):
    pass


class WriteEligibilityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PreWriteEvidence:
    validation: ValidationSummary
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUESTED
    approval_evidence_valid: bool = False
    runtime_preflight_passed: bool = False
    current_policy_allowed: bool = False
    tool_preconditions_passed: bool = False
    business_preconditions_passed: bool = False
    intent_binding_valid: bool = False
    idempotency_ready: bool = False
    durability_ready: bool = False
    trust_valid: bool = False
    replay_live_write_allowed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.validation, ValidationSummary):
            raise TypeError("validation must be a ValidationSummary")
        if not isinstance(self.approval_status, ApprovalStatus):
            raise TypeError("approval_status must be an ApprovalStatus")
        controls = (
            ("approval_evidence_valid", self.approval_evidence_valid),
            ("runtime_preflight_passed", self.runtime_preflight_passed),
            ("current_policy_allowed", self.current_policy_allowed),
            ("tool_preconditions_passed", self.tool_preconditions_passed),
            ("business_preconditions_passed", self.business_preconditions_passed),
            ("intent_binding_valid", self.intent_binding_valid),
            ("idempotency_ready", self.idempotency_ready),
            ("durability_ready", self.durability_ready),
            ("trust_valid", self.trust_valid),
            ("replay_live_write_allowed", self.replay_live_write_allowed),
        )
        for name, value in controls:
            if type(value) is not bool:
                raise TypeError(f"{name} must be Bool")


@dataclass(frozen=True, slots=True)
class WriteGuardDecision:
    eligibility: PreWriteEligibility
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.eligibility, PreWriteEligibility):
            raise TypeError("eligibility must be a PreWriteEligibility")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str) or not self.reason_code.strip()
        ):
            raise ValueError("reason_code must be a non-empty string")


class PreWriteEvaluator:
    _BLOCKING_REASONS = (
        "RUNTIME_PREFLIGHT_FAILED",
        "PACKAGE_TRUST_INVALID",
        "POLICY_DENIED",
        "TOOL_PRECONDITION_FAILED",
        "BUSINESS_PRECONDITION_FAILED",
        "WRITE_BINDING_INVALID",
        "IDEMPOTENCY_NOT_READY",
        "DURABILITY_NOT_READY",
        "REPLAY_LIVE_WRITE_FORBIDDEN",
    )

    def evaluate(self, evidence: PreWriteEvidence) -> WriteGuardDecision:
        if not isinstance(evidence, PreWriteEvidence):
            raise TypeError("evidence must be PreWriteEvidence")
        status = evidence.validation.status
        if status is ValidationSummaryStatus.HAS_FAIL_AND_UNKNOWN:
            return self._blocked("REQUIRED_CHECK_FAILED_AND_UNKNOWN")
        if status is ValidationSummaryStatus.HAS_FAIL:
            return self._blocked("REQUIRED_CHECK_FAILED")
        if status is ValidationSummaryStatus.HAS_UNKNOWN:
            return self._blocked("REQUIRED_CHECK_UNKNOWN")

        if evidence.approval_status is ApprovalStatus.REJECTED:
            return self._blocked("APPROVAL_REJECTED")
        if (
            evidence.approval_status is ApprovalStatus.APPROVED
            and not evidence.approval_evidence_valid
        ):
            return self._blocked("APPROVAL_EVIDENCE_INVALID")

        control_values = (
            evidence.runtime_preflight_passed,
            evidence.trust_valid,
            evidence.current_policy_allowed,
            evidence.tool_preconditions_passed,
            evidence.business_preconditions_passed,
            evidence.intent_binding_valid,
            evidence.idempotency_ready,
            evidence.durability_ready,
            evidence.replay_live_write_allowed,
        )
        for reason_code, allowed in zip(
            self._BLOCKING_REASONS, control_values, strict=True
        ):
            if not allowed:
                return self._blocked(reason_code)

        if evidence.approval_status in {
            ApprovalStatus.NOT_REQUESTED,
            ApprovalStatus.PENDING,
            ApprovalStatus.EXPIRED,
        }:
            return WriteGuardDecision(
                PreWriteEligibility.REQUIRES_APPROVAL,
                "APPROVAL_REQUIRED",
            )
        return WriteGuardDecision(PreWriteEligibility.ALLOWED)

    @staticmethod
    def _blocked(reason_code: str) -> WriteGuardDecision:
        return WriteGuardDecision(PreWriteEligibility.BLOCKED, reason_code)


class WriteEligibilityGate:
    @staticmethod
    def require_allowed(decision: WriteGuardDecision) -> None:
        if not isinstance(decision, WriteGuardDecision):
            raise TypeError("decision must be a WriteGuardDecision")
        if decision.eligibility is not PreWriteEligibility.ALLOWED:
            reason = decision.reason_code or "PRE_WRITE_NOT_ALLOWED"
            raise WriteEligibilityError(f"WRITE blocked: {reason}")


@dataclass(frozen=True, slots=True)
class PredicateEvaluation:
    truth: bool
    presence: Presence
    completeness: Completeness
    provenance_refs: tuple[str, ...]

    @classmethod
    def from_value(cls, predicate: ValueEnvelope) -> PredicateEvaluation:
        if predicate.type_info != BOOL or type(predicate.value) is not bool:
            raise CheckEvaluationError("CHECK predicate is not Bool")
        return cls(
            truth=predicate.value,
            presence=predicate.presence,
            completeness=predicate.completeness,
            provenance_refs=predicate.provenance_refs,
        )


@runtime_checkable
class CheckEvaluator(Protocol):
    semantics_profile: str

    def evaluate(
        self, statement: CheckStatement, predicate: ValueEnvelope
    ) -> CheckResult: ...


@dataclass(frozen=True, slots=True)
class ReasoningValidationRequest:
    check_id: str
    condition_node_id: str
    predicate: PredicateEvaluation


@runtime_checkable
class ReasoningValidatorAdapter(Protocol):
    async def validate(
        self, request: ReasoningValidationRequest
    ) -> PredicateEvaluation: ...


class StrictRuleEvaluator:
    semantics_profile = "NSL-0.1-STRICT"

    def evaluate(
        self, statement: CheckStatement, predicate: ValueEnvelope
    ) -> CheckResult:
        evaluation = PredicateEvaluation.from_value(predicate)
        if evaluation.completeness is Completeness.COMPLETE:
            status = (
                CheckStatus.PASS if evaluation.truth else CheckStatus.FAIL
            )
        else:
            status = CheckStatus.UNKNOWN
        reason_code = None
        if evaluation.completeness is Completeness.PARTIAL:
            reason_code = "PARTIAL_INPUT"
        elif evaluation.completeness is Completeness.UNKNOWN:
            reason_code = "UNKNOWN_COMPLETENESS"
        return CheckResult(
            check_id=statement.check_id,
            status=status,
            severity=statement.severity,
            message=statement.message,
            condition_node_id=statement.condition.node_id,
            presence=evaluation.presence,
            completeness=evaluation.completeness,
            provenance_refs=evaluation.provenance_refs,
            reason_code=reason_code,
        )
