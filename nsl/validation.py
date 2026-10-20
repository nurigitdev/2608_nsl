from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .core import (
    BOOL,
    ApprovalStatus,
    BusinessOperationId,
    CheckStatus,
    Completeness,
    PayloadHash,
    Presence,
    PreWriteEligibility,
    RiskLevel,
    ToolCapability,
    ValueEnvelope,
)
from .data_protection import ensure_no_credential_material
from .ir import CheckStatement
from .runtime_models import (
    CheckResult,
    ExecutionContext,
    ValidationSummary,
    ValidationSummaryStatus,
)
from .write_intent import WriteIntent


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


class PolicyEffect(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class BusinessPreconditionMechanism(StrEnum):
    VERSION = "VERSION"
    REVISION = "REVISION"
    ETAG = "ETAG"
    PRECONDITION_TOKEN = "PRECONDITION_TOKEN"
    TRANSACTIONAL_REVALIDATION = "TRANSACTIONAL_REVALIDATION"
    EQUIVALENT_PROVIDER_MECHANISM = "EQUIVALENT_PROVIDER_MECHANISM"


def _required_evidence_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    ensure_no_credential_material(value, name)
    return value


def _write_guard_utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must use UTC")
    return value


def _sha256_evidence(value: object, name: str) -> str:
    text = _required_evidence_text(value, name)
    digest = text.removeprefix("sha256:")
    if (
        not text.startswith("sha256:")
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 value")
    return text


@dataclass(frozen=True, slots=True)
class WriteContractEvidence:
    tool_id: str
    contract_hash: str
    capability: ToolCapability
    risk_level: RiskLevel
    preconditions_supported: bool

    def __post_init__(self) -> None:
        _required_evidence_text(self.tool_id, "WriteContractEvidence.tool_id")
        _sha256_evidence(
            self.contract_hash,
            "WriteContractEvidence.contract_hash",
        )
        if not isinstance(self.capability, ToolCapability):
            raise TypeError(
                "WriteContractEvidence.capability must be a ToolCapability"
            )
        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError("WriteContractEvidence.risk_level must be a RiskLevel")
        if type(self.preconditions_supported) is not bool:
            raise TypeError("preconditions_supported must be Bool")


@dataclass(frozen=True, slots=True)
class WritePolicyRequest:
    tenant_id: str
    subject_id: str
    action: str
    tool_id: str
    risk_level: RiskLevel
    business_operation_id: BusinessOperationId
    write_node_id: str
    write_intent_hash: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "subject_id",
            "action",
            "tool_id",
            "business_operation_id",
            "write_node_id",
        ):
            _required_evidence_text(
                getattr(self, name),
                f"WritePolicyRequest.{name}",
            )
        if self.action != "WRITE":
            raise ValueError("WritePolicyRequest.action must be WRITE")
        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError("WritePolicyRequest.risk_level must be a RiskLevel")
        _sha256_evidence(
            self.write_intent_hash,
            "WritePolicyRequest.write_intent_hash",
        )


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision_id: str
    policy_id: str
    policy_version: str
    effect: PolicyEffect
    tenant_id: str
    subject_id: str
    action: str
    tool_id: str
    risk_level: RiskLevel
    business_operation_id: BusinessOperationId
    write_node_id: str
    write_intent_hash: str
    evidence_ref: str
    evaluated_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "policy_id",
            "policy_version",
            "tenant_id",
            "subject_id",
            "action",
            "tool_id",
            "business_operation_id",
            "write_node_id",
            "evidence_ref",
        ):
            _required_evidence_text(
                getattr(self, name),
                f"PolicyDecision.{name}",
            )
        if not isinstance(self.effect, PolicyEffect):
            raise TypeError("PolicyDecision.effect must be a PolicyEffect")
        if self.action != "WRITE":
            raise ValueError("PolicyDecision.action must be WRITE")
        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError("PolicyDecision.risk_level must be a RiskLevel")
        _sha256_evidence(
            self.write_intent_hash,
            "PolicyDecision.write_intent_hash",
        )
        evaluated = _write_guard_utc(
            self.evaluated_at,
            "PolicyDecision.evaluated_at",
        )
        expires = _write_guard_utc(
            self.expires_at,
            "PolicyDecision.expires_at",
        )
        if expires <= evaluated:
            raise ValueError("PolicyDecision must expire after evaluation")

    def matches(self, request: WritePolicyRequest) -> bool:
        return (
            self.tenant_id == request.tenant_id
            and self.subject_id == request.subject_id
            and self.action == request.action
            and self.tool_id == request.tool_id
            and self.risk_level is request.risk_level
            and self.business_operation_id == request.business_operation_id
            and self.write_node_id == request.write_node_id
            and self.write_intent_hash == request.write_intent_hash
        )


@runtime_checkable
class RuntimePolicyPort(Protocol):
    def authorize_write(self, request: WritePolicyRequest) -> PolicyDecision: ...


@dataclass(frozen=True, slots=True)
class BusinessPreconditionEvidence:
    evidence_id: str
    tenant_id: str
    business_operation_id: BusinessOperationId
    write_node_id: str
    tool_id: str
    contract_hash: str
    payload_hash: PayloadHash
    write_intent_hash: str
    mechanism: BusinessPreconditionMechanism
    evidence_ref: str
    observed_at: datetime
    expires_at: datetime
    provider_atomic_enforcement: bool

    def __post_init__(self) -> None:
        for name in (
            "evidence_id",
            "tenant_id",
            "business_operation_id",
            "write_node_id",
            "tool_id",
            "evidence_ref",
        ):
            _required_evidence_text(
                getattr(self, name),
                f"BusinessPreconditionEvidence.{name}",
            )
        _sha256_evidence(
            self.contract_hash,
            "BusinessPreconditionEvidence.contract_hash",
        )
        _sha256_evidence(
            self.payload_hash,
            "BusinessPreconditionEvidence.payload_hash",
        )
        _sha256_evidence(
            self.write_intent_hash,
            "BusinessPreconditionEvidence.write_intent_hash",
        )
        if not isinstance(self.mechanism, BusinessPreconditionMechanism):
            raise TypeError(
                "BusinessPreconditionEvidence.mechanism must be a "
                "BusinessPreconditionMechanism"
            )
        observed = _write_guard_utc(
            self.observed_at,
            "BusinessPreconditionEvidence.observed_at",
        )
        expires = _write_guard_utc(
            self.expires_at,
            "BusinessPreconditionEvidence.expires_at",
        )
        if expires <= observed:
            raise ValueError(
                "BusinessPreconditionEvidence must expire after observation"
            )
        if type(self.provider_atomic_enforcement) is not bool:
            raise TypeError("provider_atomic_enforcement must be Bool")


@dataclass(frozen=True, slots=True)
class RiskAcceptancePolicy:
    accepted_levels: frozenset[RiskLevel]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.accepted_levels, frozenset)
            or not self.accepted_levels
            or not all(
                isinstance(level, RiskLevel) for level in self.accepted_levels
            )
        ):
            raise TypeError(
                "accepted_levels must be a non-empty frozenset of RiskLevel"
            )

    @classmethod
    def initial_v0_2(cls) -> RiskAcceptancePolicy:
        return cls(frozenset({RiskLevel.R3}))


@dataclass(frozen=True, slots=True)
class WriteGuardRequest:
    intent: WriteIntent
    context: ExecutionContext
    contract_evidence: WriteContractEvidence
    execution_evidence: PreWriteEvidence
    policy_decision: PolicyDecision | None
    business_preconditions: BusinessPreconditionEvidence | None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, WriteIntent):
            raise TypeError("intent must be a WriteIntent")
        if not isinstance(self.context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if not isinstance(self.contract_evidence, WriteContractEvidence):
            raise TypeError(
                "contract_evidence must be WriteContractEvidence"
            )
        if not isinstance(self.execution_evidence, PreWriteEvidence):
            raise TypeError("execution_evidence must be PreWriteEvidence")
        if self.policy_decision is not None and not isinstance(
            self.policy_decision, PolicyDecision
        ):
            raise TypeError("policy_decision must be a PolicyDecision or None")
        if self.business_preconditions is not None and not isinstance(
            self.business_preconditions,
            BusinessPreconditionEvidence,
        ):
            raise TypeError(
                "business_preconditions must be BusinessPreconditionEvidence or None"
            )


def write_policy_request(
    intent: WriteIntent,
    context: ExecutionContext,
    contract_evidence: WriteContractEvidence,
) -> WritePolicyRequest:
    if not isinstance(intent, WriteIntent):
        raise TypeError("intent must be a WriteIntent")
    if not isinstance(context, ExecutionContext):
        raise TypeError("context must be an ExecutionContext")
    if not isinstance(contract_evidence, WriteContractEvidence):
        raise TypeError("contract_evidence must be WriteContractEvidence")
    return WritePolicyRequest(
        tenant_id=context.tenant_id,
        subject_id=context.security_context.principal.subject_id,
        action="WRITE",
        tool_id=intent.tool_id,
        risk_level=contract_evidence.risk_level,
        business_operation_id=intent.business_operation_id,
        write_node_id=intent.node_id,
        write_intent_hash=intent.write_intent_hash,
    )


class WriteGuard:
    __slots__ = ("_risk_policy", "_evaluator")

    def __init__(
        self,
        risk_policy: RiskAcceptancePolicy | None = None,
        evaluator: PreWriteEvaluator | None = None,
    ) -> None:
        if risk_policy is not None and not isinstance(
            risk_policy, RiskAcceptancePolicy
        ):
            raise TypeError("risk_policy must be a RiskAcceptancePolicy")
        if evaluator is not None and not isinstance(evaluator, PreWriteEvaluator):
            raise TypeError("evaluator must be a PreWriteEvaluator")
        self._risk_policy = risk_policy or RiskAcceptancePolicy.initial_v0_2()
        self._evaluator = evaluator or PreWriteEvaluator()

    def evaluate(
        self,
        request: WriteGuardRequest,
        *,
        now: datetime,
    ) -> WriteGuardDecision:
        if not isinstance(request, WriteGuardRequest):
            raise TypeError("request must be a WriteGuardRequest")
        current = _write_guard_utc(now, "now")
        intent = request.intent
        context = request.context
        contract = request.contract_evidence
        if context.tenant_id != intent.tenant_id:
            return self._blocked("WRITE_TENANT_BINDING_MISMATCH")
        if context.identity.business_operation_id != intent.business_operation_id:
            return self._blocked("WRITE_BUSINESS_OPERATION_MISMATCH")
        if (
            contract.tool_id != intent.tool_id
            or contract.capability is not ToolCapability.WRITE
            or not contract.preconditions_supported
        ):
            return self._blocked("WRITE_CONTRACT_INVALID")
        if contract.risk_level not in self._risk_policy.accepted_levels:
            return self._blocked("RISK_NOT_ACCEPTED")

        expected_policy = write_policy_request(intent, context, contract)
        policy = request.policy_decision
        if policy is None:
            return self._blocked("POLICY_EVIDENCE_MISSING")
        if not policy.matches(expected_policy):
            return self._blocked("POLICY_BINDING_MISMATCH")
        if policy.evaluated_at > current or current >= policy.expires_at:
            return self._blocked("POLICY_NOT_CURRENT")
        if policy.effect is not PolicyEffect.ALLOW:
            return self._blocked("POLICY_DENIED")

        precondition = request.business_preconditions
        if precondition is None:
            return self._blocked("BUSINESS_PRECONDITION_MISSING")
        if not self._precondition_matches(
            precondition,
            intent,
            context,
            contract,
        ):
            return self._blocked("BUSINESS_PRECONDITION_BINDING_MISMATCH")
        if precondition.observed_at > current or current >= precondition.expires_at:
            return self._blocked("BUSINESS_PRECONDITION_NOT_CURRENT")
        if not precondition.provider_atomic_enforcement:
            return self._blocked("PROVIDER_PRECONDITION_UNAVAILABLE")

        current_evidence = replace(
            request.execution_evidence,
            current_policy_allowed=True,
            tool_preconditions_passed=True,
            business_preconditions_passed=True,
        )
        return self._evaluator.evaluate(current_evidence)

    @staticmethod
    def _precondition_matches(
        evidence: BusinessPreconditionEvidence,
        intent: WriteIntent,
        context: ExecutionContext,
        contract: WriteContractEvidence,
    ) -> bool:
        return (
            evidence.tenant_id == context.tenant_id == intent.tenant_id
            and evidence.business_operation_id == intent.business_operation_id
            and evidence.write_node_id == intent.node_id
            and evidence.tool_id == intent.tool_id == contract.tool_id
            and evidence.contract_hash == contract.contract_hash
            and evidence.payload_hash == intent.payload_hash
            and evidence.write_intent_hash == intent.write_intent_hash
        )

    @staticmethod
    def _blocked(reason_code: str) -> WriteGuardDecision:
        return WriteGuardDecision(PreWriteEligibility.BLOCKED, reason_code)


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
