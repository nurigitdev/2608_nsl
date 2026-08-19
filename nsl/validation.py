from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .core import BOOL, CheckStatus, Completeness, Presence, ValueEnvelope
from .ir import CheckStatement
from .runtime_models import CheckResult


class CheckEvaluationError(RuntimeError):
    pass


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
