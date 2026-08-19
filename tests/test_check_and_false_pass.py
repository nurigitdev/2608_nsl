from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from nsl.audit import InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.core import (
    BOOL,
    CheckStatus,
    Completeness,
    DataClassification,
    ExecutionStatus,
    Presence,
    ValueEnvelope,
)
from nsl.ir import CheckStatement, LiteralExpr
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.validation import (
    CheckEvaluator,
    CheckEvaluationError,
    PredicateEvaluation,
    ReasoningValidationRequest,
    ReasoningValidatorAdapter,
    StrictRuleEvaluator,
)
from nsl.tools import ToolContractCatalog
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def execution_request(execution_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )


def test_val_001_check_evaluates_only_exact_bool_values() -> None:
    for value in (False, True):
        evaluation = PredicateEvaluation.from_value(
            ValueEnvelope.complete(value, BOOL, DataClassification.INTERNAL)
        )
        assert evaluation.truth is value

    for value in (0, 1, "true", None):
        with pytest.raises(CheckEvaluationError, match="not Bool"):
            PredicateEvaluation.from_value(
                ValueEnvelope.complete(value, BOOL, DataClassification.INTERNAL)
            )


def test_val_001_forged_truthy_bool_value_cannot_pass_at_runtime() -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    base_check = base_skill.body[1].body[3]
    assert isinstance(base_check, CheckStatement)
    forged_check = replace(
        base_check,
        condition=LiteralExpr("forged-truthy-bool", 1, BOOL),
    )
    forged_loop = replace(base_skill.body[1], body=(forged_check,))
    skill = replace(
        base_skill,
        body=(base_skill.body[0], forged_loop),
    ).with_computed_hash()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-val-001-forged"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.checks == ()
    assert result.error is not None
    assert result.error.message == "CHECK predicate is not Bool"


def test_val_002_complete_true_predicate_becomes_pass() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-val-002"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert len(result.checks) == 1
    assert result.checks[0].status is CheckStatus.PASS
    assert result.checks[0].completeness is Completeness.COMPLETE


def test_val_003_complete_false_predicate_becomes_fail() -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    base_check = base_skill.body[1].body[3]
    assert isinstance(base_check, CheckStatement)
    false_check = replace(
        base_check,
        condition=LiteralExpr("complete-false", False, BOOL),
    )
    skill = replace(
        base_skill,
        body=(base_skill.body[0], replace(base_skill.body[1], body=(false_check,))),
    ).with_computed_hash()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-val-003"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert len(result.checks) == 1
    assert result.checks[0].status is CheckStatus.FAIL
    assert result.checks[0].completeness is Completeness.COMPLETE


@pytest.mark.parametrize(
    ("truth", "completeness"),
    (
        (True, Completeness.PARTIAL),
        (False, Completeness.PARTIAL),
        (True, Completeness.UNKNOWN),
        (False, Completeness.UNKNOWN),
    ),
)
def test_val_004_indeterminate_predicate_becomes_unknown(
    truth: bool, completeness: Completeness
) -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    statement = skill.body[1].body[3]
    assert isinstance(statement, CheckStatement)
    predicate = ValueEnvelope(
        truth,
        BOOL,
        Presence.PRESENT,
        completeness,
        DataClassification.INTERNAL,
    )

    result = StrictRuleEvaluator().evaluate(statement, predicate)

    assert result.status is CheckStatus.UNKNOWN
    assert result.completeness is completeness


def test_val_007_check_result_and_used_facts_are_audited() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    statement = skill.body[1].body[3]
    assert isinstance(statement, CheckStatement)
    audit = InMemoryAuditSink()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-val-007"),
            build_mock_executor(catalog),
            audit,
        )
    )

    check = result.checks[0]
    event = next(item for item in audit.events if item.event_type == "CHECK_COMPLETED")
    assert event.payload == {
        "node_id": statement.node_id,
        "check_id": check.check_id,
        "status": check.status.value,
        "condition_node_id": statement.condition.node_id,
        "presence": check.presence.value,
        "completeness": check.completeness.value,
        "reason": None,
        "provenance_refs": list(check.provenance_refs),
    }
    assert check.provenance_refs


def test_val_008_check_message_is_included_in_runtime_result() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    statement = skill.body[1].body[3]
    assert isinstance(statement, CheckStatement)

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-val-008"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.checks[0].message == statement.message
    assert result.semantic_view()["checks"][0]["message"] == statement.message
    assert result.checks[0].message == (
        "자 프로젝트 지출 합계가 모 프로젝트 예산을 초과했습니다."
    )


def test_val_009_simple_check_uses_strict_rule_evaluator() -> None:
    engine = RuntimeEngine(build_tool_catalog())

    assert isinstance(engine.check_evaluator, CheckEvaluator)
    assert isinstance(engine.check_evaluator, StrictRuleEvaluator)
    assert engine.check_evaluator.semantics_profile == "NSL-0.1-STRICT"


def test_val_010_reasoning_validator_has_typed_future_adapter_boundary() -> None:
    class FakeReasoningAdapter:
        async def validate(
            self, request: ReasoningValidationRequest
        ) -> PredicateEvaluation:
            return request.predicate

    predicate = PredicateEvaluation.from_value(
        ValueEnvelope.complete(True, BOOL, DataClassification.INTERNAL)
    )
    request = ReasoningValidationRequest(
        check_id="BUDGET_LIMIT",
        condition_node_id="expr-reasoning",
        predicate=predicate,
    )
    adapter = FakeReasoningAdapter()

    assert isinstance(adapter, ReasoningValidatorAdapter)
    assert asyncio.run(adapter.validate(request)) is predicate
    assert isinstance(
        RuntimeEngine(build_tool_catalog()).check_evaluator,
        StrictRuleEvaluator,
    )


def test_fp_002_missing_required_tool_result_cannot_reach_check() -> None:
    base_catalog = build_tool_catalog()
    parent = replace(
        base_catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0"),
        empty_is_valid=False,
    )
    child = base_catalog.get("PROJECT.LIST_CHILD_PROJECTS", "1.0.0")
    catalog = ToolContractCatalog((parent, child))
    skill = NslCompiler(catalog).compile(SOURCE).skill
    executor = build_mock_executor(catalog)
    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = lambda arguments: []
    audit = InMemoryAuditSink()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-fp-002"),
            executor,
            audit,
        )
    )

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.checks == ()
    assert result.outputs == ()
    assert result.error is not None
    assert result.error.detail_code == "REQUIRED_TOOL_RESULT_MISSING"
    assert not any(event.event_type == "CHECK_COMPLETED" for event in audit.events)
    failed = next(event for event in audit.events if event.event_type == "TOOL_FAILED")
    assert failed.payload["error_code"] == "REQUIRED_TOOL_RESULT_MISSING"


@pytest.mark.parametrize(
    ("completeness", "reason"),
    (
        (Completeness.PARTIAL, "PARTIAL_INPUT"),
        (Completeness.UNKNOWN, "UNKNOWN_COMPLETENESS"),
    ),
)
def test_fp_005_data_completeness_is_recorded_in_audit(
    completeness: Completeness, reason: str
) -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    delegate = build_mock_executor(catalog)

    class IncompleteChildExecutor:
        async def execute(self, request):
            result = await delegate.execute(request)
            if request.tool_id == "PROJECT.LIST_CHILD_PROJECTS":
                return replace(result, completeness=completeness)
            return result

    audit = InMemoryAuditSink()
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(f"exec-fp-005-{completeness.value.lower()}"),
            IncompleteChildExecutor(),
            audit,
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert result.checks[0].status is CheckStatus.UNKNOWN
    assert result.checks[0].completeness is completeness
    assert result.checks[0].reason_code == reason
    check_event = next(
        event for event in audit.events if event.event_type == "CHECK_COMPLETED"
    )
    assert check_event.payload["completeness"] == completeness.value
    assert check_event.payload["reason"] == reason
    assert check_event.payload["provenance_refs"]
    tool_event = next(
        event
        for event in audit.events
        if event.event_type == "TOOL_COMPLETED"
        and event.payload["tool_id"] == "PROJECT.LIST_CHILD_PROJECTS"
    )
    assert tool_event.payload["completeness"] == completeness.value
