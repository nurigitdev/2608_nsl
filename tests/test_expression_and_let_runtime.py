from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from nsl.audit import AuditRecorder, InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.core import INT, STRING, ExecutionStatus, ValueEnvelope, list_type, record_type
from nsl.diagnostics import DiagnosticCode
from nsl.ir import (
    BinaryExpr,
    CallExpr,
    FieldExpr,
    LetStatement,
    LiteralExpr,
    ProjectionExpr,
    ReadExpr,
    SymbolRefExpr,
)
from nsl.runtime import ExpressionEvaluationError, RuntimeContractError, RuntimeEngine
from nsl.runtime_models import (
    ExecutionContext,
    ExecutionRequest,
    ImmutableBindingError,
)
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")


def runtime_parts() -> tuple[RuntimeEngine, ExecutionContext]:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    request = ExecutionRequest(
        execution_id="exec-let-runtime",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )
    context = ExecutionContext(
        skill,
        request,
        AuditRecorder(InMemoryAuditSink(), request.data_policy),
    )
    return RuntimeEngine(catalog), context


class ValueErrorToolExecutor:
    async def execute(self, request):
        raise ValueError("sensitive provider implementation detail")


@pytest.mark.parametrize("value", (-1, 0, 1))
def test_let_001_let_creates_an_immutable_binding(value: int) -> None:
    engine, context = runtime_parts()
    statement = LetStatement(
        "stmt-let",
        "s-let-value",
        LiteralExpr("expr-let-value", value, INT),
    )

    asyncio.run(
        engine._execute_block(
            context,
            (statement,),
            build_mock_executor(engine.tool_catalog),
        )
    )

    binding = context.resolve("s-let-value")
    assert binding.value == value
    assert binding.type_info == INT
    assert context.frames == [{"s-let-value": binding}]


def test_let_002_an_existing_binding_cannot_be_reassigned() -> None:
    engine, context = runtime_parts()
    first = LetStatement(
        "stmt-first",
        "s-let-value",
        LiteralExpr("expr-first", 1, INT),
    )
    reassignment = LetStatement(
        "stmt-reassignment",
        "s-let-value",
        LiteralExpr("expr-reassignment", 2, INT),
    )
    tools = build_mock_executor(engine.tool_catalog)

    with pytest.raises(ImmutableBindingError, match="already bound"):
        asyncio.run(engine._execute_block(context, (first, reassignment), tools))
    assert context.resolve("s-let-value").value == 1

    invalid_skill = replace(
        context.skill,
        body=(first, reassignment),
    ).with_computed_hash()
    result = asyncio.run(
        engine.execute(
            invalid_skill,
            context.request,
            tools,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code == DiagnosticCode.RUNTIME_EVALUATION
    assert result.error.detail_code == "IMMUTABLE_BINDING_ERROR"


def test_let_003_pure_expressions_have_no_side_effects() -> None:
    engine, context = runtime_parts()
    item_type = record_type("Item", amount=INT)
    items_type = list_type(item_type)
    items = [{"amount": -1}, {"amount": 0}, {"amount": 2}]
    record = {"amount": 7}
    context.bind("s-items", ValueEnvelope.complete(items, items_type))
    context.bind("s-record", ValueEnvelope.complete(record, item_type))
    projected = ProjectionExpr(
        "expr-project",
        SymbolRefExpr("expr-items", "s-items", items_type),
        "amount",
        list_type(INT),
    )
    expression = BinaryExpr(
        "expr-add",
        "ADD",
        CallExpr("expr-sum", "sum", (projected,), INT),
        LiteralExpr("expr-zero", 0, INT),
        INT,
    )
    field = FieldExpr(
        "expr-field",
        SymbolRefExpr("expr-record", "s-record", item_type),
        "amount",
        INT,
    )
    before = deepcopy(
        (
            context.input_values,
            context.context_values,
            context.frames,
            context.check_frames,
            context.checks,
            context.outputs,
            context.resources,
            context.invocation_counter,
        )
    )
    tools = build_mock_executor(engine.tool_catalog)

    calculated = asyncio.run(engine._evaluate(context, expression, tools))
    selected = asyncio.run(engine._evaluate(context, field, tools))

    after = (
        context.input_values,
        context.context_values,
        context.frames,
        context.check_frames,
        context.checks,
        context.outputs,
        context.resources,
        context.invocation_counter,
    )
    assert calculated.value == 1
    assert selected.value == 7
    assert after == before
    assert items == [{"amount": -1}, {"amount": 0}, {"amount": 2}]
    assert record == {"amount": 7}


def test_let_004_expression_results_preserve_their_declared_types() -> None:
    engine, context = runtime_parts()
    item_type = record_type("Item", amount=INT)
    items_type = list_type(item_type)
    context.bind(
        "s-items",
        ValueEnvelope.complete([{"amount": -1}, {"amount": 2}], items_type),
    )
    context.bind("s-record", ValueEnvelope.complete({"amount": 7}, item_type))
    expressions = (
        LiteralExpr("expr-literal", 1, INT),
        SymbolRefExpr("expr-symbol", "s-items", items_type),
        FieldExpr(
            "expr-field",
            SymbolRefExpr("expr-record", "s-record", item_type),
            "amount",
            INT,
        ),
        ProjectionExpr(
            "expr-project",
            SymbolRefExpr("expr-project-source", "s-items", items_type),
            "amount",
            list_type(INT),
        ),
        CallExpr(
            "expr-call",
            "sum",
            (
                ProjectionExpr(
                    "expr-call-source",
                    SymbolRefExpr("expr-call-items", "s-items", items_type),
                    "amount",
                    list_type(INT),
                ),
            ),
            INT,
        ),
        BinaryExpr(
            "expr-binary",
            "ADD",
            LiteralExpr("expr-left", -1, INT),
            LiteralExpr("expr-right", 1, INT),
            INT,
        ),
    )
    tools = build_mock_executor(engine.tool_catalog)

    for expression in expressions:
        result = asyncio.run(engine._evaluate(context, expression, tools))
        assert result.type_info == expression.type_info

    engine._bind_inputs_and_contexts(context)
    first_statement = context.skill.body[0]
    assert isinstance(first_statement, LetStatement)
    assert isinstance(first_statement.value, ReadExpr)
    read_result = asyncio.run(
        engine._evaluate(context, first_statement.value, tools)
    )
    assert read_result.type_info == first_statement.value.type_info

    mismatched = SymbolRefExpr("expr-mismatch", "s-record", STRING)
    with pytest.raises(RuntimeContractError, match="expression type mismatch"):
        asyncio.run(engine._evaluate(context, mismatched, tools))


@pytest.mark.parametrize(
    "expression",
    (
        BinaryExpr(
            "expr-divide-zero",
            "DIV",
            LiteralExpr("expr-dividend", 1, INT),
            LiteralExpr("expr-divisor", 0, INT),
            INT,
        ),
        FieldExpr(
            "expr-missing-field",
            LiteralExpr("expr-empty-record", {}, record_type("Item", amount=INT)),
            "amount",
            INT,
        ),
        ProjectionExpr(
            "expr-missing-projection",
            LiteralExpr(
                "expr-empty-items",
                [{}],
                list_type(record_type("Item", amount=INT)),
            ),
            "amount",
            list_type(INT),
        ),
    ),
)
def test_let_005_expression_errors_follow_the_strict_failure_policy(
    expression,
) -> None:
    engine, context = runtime_parts()
    tools = build_mock_executor(engine.tool_catalog)

    with pytest.raises(ExpressionEvaluationError) as captured:
        asyncio.run(engine._evaluate(context, expression, tools))
    assert captured.value.node_id == expression.node_id
    assert captured.value.detail_code == "EXPRESSION_EVALUATION_ERROR"

    statement = LetStatement("stmt-failing-let", "s-failing-let", expression)
    invalid_skill = replace(context.skill, body=(statement,)).with_computed_hash()
    result = asyncio.run(
        engine.execute(
            invalid_skill,
            context.request,
            tools,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code == DiagnosticCode.RUNTIME_EVALUATION
    assert result.error.node_id == expression.node_id
    assert result.error.detail_code == "EXPRESSION_EVALUATION_ERROR"


def test_let_005_read_provider_errors_keep_the_unexpected_error_boundary() -> None:
    engine, context = runtime_parts()

    result = asyncio.run(
        engine.execute(
            context.skill,
            context.request,
            ValueErrorToolExecutor(),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code == DiagnosticCode.RUNTIME_UNEXPECTED
    assert result.error.message == "An unexpected runtime error occurred."
    assert "sensitive" not in repr(result)
