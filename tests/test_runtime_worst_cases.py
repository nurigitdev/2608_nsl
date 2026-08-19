from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from nsl.audit import AuditRecorder, InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.core import (
    BOOL,
    DECIMAL,
    INT,
    STRING,
    CheckStatus,
    Completeness,
    DataClassification,
    ExecutionStatus,
    Money,
    Presence,
    ValueEnvelope,
    domain,
    list_type,
)
from nsl.ir import (
    BinaryExpr,
    CallExpr,
    CheckStatement,
    FieldExpr,
    ForeachStatement,
    LiteralExpr,
    ProjectionExpr,
    ReadExpr,
    ResultPolicy,
)
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.runtime_models import _ExecutionContext
from nsl.security import DataHandlingPolicy
from nsl.tools import MockToolExecutor
from nsl.vertical_slice import (
    PARENT_PROJECT,
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")


@pytest.fixture
def runtime_fixture():
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    engine = RuntimeEngine(catalog)
    policy = DataHandlingPolicy(snapshot_retention_days=1)
    request = ExecutionRequest(
        execution_id="exec-worst-case",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
        data_policy=policy,
    )
    return catalog, skill, engine, policy, request


def execute(engine, skill, request, tools):
    return asyncio.run(engine.execute(skill, request, tools, InMemoryAuditSink()))


def rehashed(skill, **changes):
    return replace(skill, **changes).with_computed_hash()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda skill: rehashed(skill, ir_version="2.0"),
        lambda skill: rehashed(skill, language_version="0.2"),
        lambda skill: rehashed(skill, semantics_profile="UNSUPPORTED"),
        lambda skill: replace(skill, semantic_hash="sha256:tampered"),
        lambda skill: rehashed(
            skill, analysis=replace(skill.analysis, bounded=False)
        ),
        lambda skill: rehashed(
            skill,
            required_tools=(
                replace(skill.required_tools[0], contract_hash="sha256:changed"),
                *skill.required_tools[1:],
            ),
        ),
        lambda skill: rehashed(
            skill,
            required_tools=(
                replace(skill.required_tools[0], capability="WRITE"),
                *skill.required_tools[1:],
            ),
        ),
    ],
)
def test_runtime_preflight_rejects_malformed_or_unsupported_ir(
    runtime_fixture, mutation
) -> None:
    catalog, skill, engine, _, request = runtime_fixture
    result = execute(engine, mutation(skill), request, build_mock_executor(catalog))
    assert result.status is ExecutionStatus.FAILED
    assert result.error.code == "NSL-E8001"


def test_sec_007_only_registered_tools_can_reach_executor(runtime_fixture) -> None:
    catalog, skill, engine, _, request = runtime_fixture
    first_statement = skill.body[0]
    assert isinstance(first_statement.value, ReadExpr)
    unknown_read = replace(first_statement.value, tool_ref="tool9999")
    malicious = rehashed(
        skill,
        body=(replace(first_statement, value=unknown_read), *skill.body[1:]),
    )
    tools = build_mock_executor(catalog)

    result = execute(engine, malicious, request, tools)

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code == "NSL-E8001"
    assert result.error.message == "read references unregistered tool: tool9999"
    assert tools.call_count == 0


@pytest.mark.parametrize(
    ("inputs", "context", "message"),
    [
        ({}, {"user": {"team_id": "TEAM-FINANCE"}}, "missing required input"),
        ({"year": 0.0}, {"user": {"team_id": "TEAM-FINANCE"}}, "runtime type mismatch"),
        ({"year": True}, {"user": {"team_id": "TEAM-FINANCE"}}, "runtime type mismatch"),
        ({"year": 2026}, {}, "missing runtime context path"),
        ({"year": 2026}, {"user": None}, "missing runtime context path"),
        ({"year": 2026}, {"user": {"team_id": 1}}, "runtime type mismatch"),
    ],
)
def test_runtime_input_context_robustness(
    runtime_fixture, inputs, context, message
) -> None:
    catalog, skill, engine, policy, request = runtime_fixture
    changed = replace(request, inputs=inputs, runtime_context=context, data_policy=policy)
    result = execute(engine, skill, changed, build_mock_executor(catalog))
    assert result.status is ExecutionStatus.FAILED
    assert message in result.error.message


@pytest.mark.parametrize(
    "limit_mutation",
    [
        lambda skill: rehashed(
            skill, limits=replace(skill.limits, tool_calls=0)
        ),
        lambda skill: rehashed(
            skill, limits=replace(skill.limits, loop_iterations=0)
        ),
        lambda skill: rehashed(
            skill, limits=replace(skill.limits, emitted_rows=0)
        ),
        lambda skill: rehashed(
            skill, limits=replace(skill.limits, collection_size=0)
        ),
        lambda skill: rehashed(
            skill,
            body=(
                skill.body[0],
                replace(skill.body[1], max_iterations=0),
            ),
        ),
    ],
)
def test_runtime_resource_zero_boundary_is_enforced(
    runtime_fixture, limit_mutation
) -> None:
    catalog, skill, engine, _, request = runtime_fixture
    result = execute(
        engine, limit_mutation(skill), request, build_mock_executor(catalog)
    )
    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    assert result.error.code == "NSL-E6001"


@pytest.mark.parametrize(
    "limit_name",
    ["tool_calls", "loop_iterations", "emitted_rows", "collection_size"],
)
def test_sec_008_resource_limits_cannot_be_bypassed_by_static_analysis(
    runtime_fixture, limit_name
) -> None:
    catalog, skill, engine, _, request = runtime_fixture
    forged = rehashed(
        skill,
        limits=replace(skill.limits, **{limit_name: 0}),
        analysis=replace(
            skill.analysis,
            max_tool_calls=0,
            max_loop_iterations=0,
            max_emit_records=0,
            bounded=True,
        ),
    )

    result = execute(engine, forged, request, build_mock_executor(catalog))

    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    assert result.error is not None
    assert result.error.code == "NSL-E6001"


def test_empty_collection_is_complete_and_emits_nothing(runtime_fixture) -> None:
    catalog, skill, engine, _, request = runtime_fixture
    mock = build_mock_executor(catalog)
    mock.handlers["PROJECT.LIST_PARENT_PROJECTS"] = lambda arguments: []

    result = execute(engine, skill, request, mock)
    assert result.status is ExecutionStatus.COMPLETED
    assert result.outputs == ()
    assert result.checks == ()
    assert result.resources.max_collection_size_seen == 0


def test_budget_exceeded_produces_fail_not_execution_error(runtime_fixture) -> None:
    catalog, skill, engine, _, request = runtime_fixture
    mock = build_mock_executor(catalog)
    mock.handlers["PROJECT.LIST_CHILD_PROJECTS"] = lambda arguments: [
        {
            "project_code": "CHILD-WORST",
            "parent_project": "PARENT-001",
            "expense_amount": Money(Decimal("100000001"), "KRW"),
        }
    ]

    result = execute(engine, skill, request, mock)
    assert result.status is ExecutionStatus.COMPLETED
    assert result.checks[0].status is CheckStatus.FAIL
    assert result.outputs[0].values["remaining"] == Money(Decimal("-1"), "KRW")


def context_for_direct_evaluation(runtime_fixture) -> tuple[RuntimeEngine, _ExecutionContext, MockToolExecutor]:
    catalog, skill, engine, policy, request = runtime_fixture
    audit = AuditRecorder(InMemoryAuditSink(), policy)
    context = _ExecutionContext(skill, request, audit, frames=[{}])
    return engine, context, build_mock_executor(catalog)


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        ("ADD", 5),
        ("SUB", 1),
        ("MUL", 6),
        ("DIV", Decimal("1.5")),
        ("LT", False),
        ("LE", False),
        ("GT", True),
        ("GE", True),
        ("EQ", False),
        ("NE", True),
    ],
)
def test_runtime_binary_operator_matrix(runtime_fixture, operator, expected) -> None:
    engine, context, tools = context_for_direct_evaluation(runtime_fixture)
    result_type = (
        BOOL
        if operator in {"LT", "LE", "GT", "GE", "EQ", "NE"}
        else DECIMAL
        if operator == "DIV"
        else INT
    )
    expression = BinaryExpr(
        "expr-op",
        operator,
        LiteralExpr("left", 3, INT),
        LiteralExpr("right", 2, INT),
        result_type,
    )
    result = asyncio.run(engine._evaluate(context, expression, tools))
    assert result.value == expected


def test_runtime_integer_sum_unsupported_builtin_and_empty_projection(
    runtime_fixture,
) -> None:
    engine, context, tools = context_for_direct_evaluation(runtime_fixture)
    values = LiteralExpr("values", [0, 1], list_type(INT))
    summed = asyncio.run(
        engine._evaluate(context, CallExpr("sum", "sum", (values,), INT), tools)
    )
    assert summed.value == 1

    with pytest.raises(RuntimeError, match="unsupported built-in"):
        asyncio.run(
            engine._evaluate(
                context, CallExpr("bad", "average", (values,), INT), tools
            )
        )

    projects = LiteralExpr("projects", [], list_type(PARENT_PROJECT))
    projection = ProjectionExpr(
        "projection", projects, "project_code", list_type(domain("ProjectCode"))
    )
    projected = asyncio.run(engine._evaluate(context, projection, tools))
    assert projected.presence is Presence.EMPTY


def test_runtime_completeness_algebra_and_defensive_guards(runtime_fixture) -> None:
    engine, context, tools = context_for_direct_evaluation(runtime_fixture)
    assert (
        engine._combine_completeness(Completeness.UNKNOWN, Completeness.COMPLETE)
        is Completeness.UNKNOWN
    )
    assert (
        engine._combine_completeness(Completeness.PARTIAL, Completeness.COMPLETE)
        is Completeness.PARTIAL
    )
    assert (
        engine._combine_completeness(Completeness.COMPLETE, Completeness.COMPLETE)
        is Completeness.COMPLETE
    )

    class UnknownExpression:
        pass

    with pytest.raises(TypeError):
        asyncio.run(engine._evaluate(context, UnknownExpression(), tools))

    class UnknownStatement:
        node_id = "unknown-statement"

    with pytest.raises(TypeError):
        asyncio.run(engine._execute_block(context, (UnknownStatement(),), tools))


def test_runtime_context_immutable_and_unknown_symbol_guards(runtime_fixture) -> None:
    _, context, _ = context_for_direct_evaluation(runtime_fixture)
    value = ValueEnvelope.complete(1, INT)
    context.bind("s-test", value)
    assert context.resolve("s-test") == value
    context.frames.append({})
    assert context.resolve("s-test") == value
    context.bind("s-inner", value)
    with pytest.raises(RuntimeError, match="already bound"):
        context.bind("s-inner", value)
    with pytest.raises(RuntimeError, match="unknown symbol"):
        context.resolve("s-missing")


def test_check_rejects_non_boolean_runtime_ir(runtime_fixture) -> None:
    engine, context, tools = context_for_direct_evaluation(runtime_fixture)
    statement = CheckStatement(
        "check-invalid",
        "INVALID",
        LiteralExpr("literal", 1, INT),
        "ERROR",
        "REPORT",
        "invalid",
        "s-check",
    )
    with pytest.raises(RuntimeError, match="not Bool"):
        asyncio.run(engine._execute_block(context, (statement,), tools))


def test_validate_runtime_type_bool_string_domain_and_unchecked_record(
    runtime_fixture,
) -> None:
    _, _, engine, _, _ = runtime_fixture
    engine._validate_runtime_type(True, BOOL, "bool")
    engine._validate_runtime_type(Decimal("0.1"), DECIMAL, "decimal")
    engine._validate_runtime_type("text", STRING, "string")
    engine._validate_runtime_type("TEAM", domain("TeamId"), "team")
    engine._validate_runtime_type({}, PARENT_PROJECT, "record")
    with pytest.raises(RuntimeError, match="runtime type mismatch"):
        engine._validate_runtime_type(1, BOOL, "bool")
    with pytest.raises(RuntimeError, match="runtime type mismatch"):
        engine._validate_runtime_type(0.1, DECIMAL, "decimal")
