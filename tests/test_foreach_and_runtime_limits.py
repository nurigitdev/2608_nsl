from __future__ import annotations

import asyncio
import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

import nsl.runtime as runtime_module
from nsl import CompileError, DiagnosticCode, NslCompiler, SourceFile
from nsl.audit import AuditRecorder, InMemoryAuditSink
from nsl.core import CheckStatus, INT, ExecutionStatus, ValueEnvelope, list_type
from nsl.ir import ForeachStatement, SymbolRefExpr
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.runtime_models import (
    ExecutionContext,
    LimitExceeded,
    MAX_FOREACH_NESTING_DEPTH,
)
from nsl.syntax import Lexer, Parser
from nsl.tools import ToolExecutionError
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)
FOREACH_HEADER = "foreach parent in parents max 10 {"


class FakeRuntimeClock:
    def __init__(self) -> None:
        self.now_ns = 0

    def monotonic_ns(self) -> int:
        return self.now_ns

    def advance_ns(self, nanoseconds: int) -> None:
        self.now_ns += nanoseconds


def execution_request(execution_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )


def test_for_001_foreach_requires_explicit_maximum() -> None:
    source = SourceFile.from_text(
        "skills/foreach_without_max.ns",
        SOURCE.replace(FOREACH_HEADER, "foreach parent in parents {"),
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == DiagnosticCode.PAR_EXPECTED_TOKEN
    assert captured.value.location is not None
    assert captured.value.logical_path == "skills/foreach_without_max.ns"


def test_for_002_foreach_accepts_minimum_positive_integer() -> None:
    compilation = NslCompiler(build_tool_catalog()).compile(
        SOURCE.replace(FOREACH_HEADER, "foreach parent in parents max 1 {")
    )

    assert compilation.skill.body[1].max_iterations == 1


@pytest.mark.parametrize(
    ("maximum", "expected_code"),
    (
        ("0", DiagnosticCode.PAR_NON_POSITIVE_FOREACH_LIMIT),
        ("-1", DiagnosticCode.PAR_EXPECTED_TOKEN_KIND),
        ("1.0", DiagnosticCode.PAR_EXPECTED_TOKEN_KIND),
    ),
)
def test_for_002_foreach_rejects_non_positive_or_non_integer_maximum(
    maximum: str, expected_code: DiagnosticCode
) -> None:
    source = SOURCE.replace(
        FOREACH_HEADER, f"foreach parent in parents max {maximum} {{"
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == expected_code


@pytest.mark.parametrize("item_count", (0, 1, 10))
def test_for_003_runtime_tracks_actual_foreach_iterations(
    item_count: int,
) -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    executor = build_mock_executor(catalog)
    parent_fixture = executor.handlers["PROJECT.LIST_PARENT_PROJECTS"]
    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = (
        lambda arguments: parent_fixture(arguments) * item_count
    )

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(f"exec-for-003-{item_count}"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert result.resources.loop_iterations == item_count


def test_for_004_foreach_stops_before_body_when_collection_exceeds_max() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(
        SOURCE.replace(FOREACH_HEADER, "foreach parent in parents max 1 {")
    ).skill
    executor = build_mock_executor(catalog)
    parent_fixture = executor.handlers["PROJECT.LIST_PARENT_PROJECTS"]
    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = (
        lambda arguments: parent_fixture(arguments) * 2
    )

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-for-004"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    assert result.error is not None
    assert result.error.code == DiagnosticCode.RESOURCE_LIMIT_EXCEEDED
    assert "exceeds max 1" in result.error.message
    assert result.resources.loop_iterations == 0
    assert result.checks == ()
    assert result.outputs == ()


def test_for_005_language_rejects_unbounded_loop_constructs() -> None:
    source = SourceFile.from_text(
        "skills/unbounded_loop.ns",
        SOURCE.replace(FOREACH_HEADER, "while true {"),
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == DiagnosticCode.PAR_UNEXPECTED_STATEMENT
    assert captured.value.logical_path == "skills/unbounded_loop.ns"
    assert captured.value.location is not None


def test_for_006_runtime_limits_nested_foreach_execution_depth() -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    base_loop = base_skill.body[1]
    assert isinstance(base_loop, ForeachStatement)
    body = ()
    for depth in reversed(range(MAX_FOREACH_NESTING_DEPTH + 1)):
        body = (
            ForeachStatement(
                node_id=f"foreach-depth-{depth:02d}",
                iterator_symbol_id=f"sym-depth-{depth:02d}",
                collection=base_loop.collection,
                max_iterations=1,
                body=body,
            ),
        )
    skill = replace(
        base_skill,
        limits=replace(
            base_skill.limits,
            loop_iterations=MAX_FOREACH_NESTING_DEPTH + 1,
        ),
        body=(base_skill.body[0], *body),
    ).with_computed_hash()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-for-006"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    assert result.error is not None
    assert result.error.message == (
        f"foreach nesting depth exceeds {MAX_FOREACH_NESTING_DEPTH}"
    )
    assert result.resources.loop_iterations == MAX_FOREACH_NESTING_DEPTH


def test_for_007_foreach_preserves_deterministic_collection_order() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    observed_orders: list[list[str]] = []

    for index in range(2):
        executor = build_mock_executor(catalog)
        parent_fixture = executor.handlers["PROJECT.LIST_PARENT_PROJECTS"]

        def ordered_parents(arguments):
            template = parent_fixture(arguments)[0]
            return [
                {**template, "project_code": code}
                for code in ("PARENT-B", "PARENT-A", "PARENT-C")
            ]

        executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = ordered_parents
        executor.handlers["PROJECT.LIST_CHILD_PROJECTS"] = lambda arguments: []
        result = asyncio.run(
            RuntimeEngine(catalog).execute(
                skill,
                execution_request(f"exec-for-007-{index}"),
                executor,
                InMemoryAuditSink(),
            )
        )

        assert result.status is ExecutionStatus.COMPLETED
        observed_orders.append(
            [record.values["parent_project"] for record in result.outputs]
        )

    assert observed_orders == [
        ["PARENT-B", "PARENT-A", "PARENT-C"],
        ["PARENT-B", "PARENT-A", "PARENT-C"],
    ]


def test_for_008_parallel_foreach_is_not_supported() -> None:
    parallel_source = SOURCE.replace(
        FOREACH_HEADER, "foreach parent in parents max 10 parallel {"
    )
    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(parallel_source)
    assert captured.value.code == DiagnosticCode.PAR_EXPECTED_TOKEN

    runtime_tree = ast.parse(
        (ROOT / "nsl" / "runtime.py").read_text(encoding="utf-8")
    )
    parallel_calls = {
        node.func.attr
        for node in ast.walk(runtime_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"as_completed", "create_task", "gather"}
    }
    assert parallel_calls == set()


def test_lim_001_skill_defines_explicit_resource_limits() -> None:
    source = SOURCE.replace(
        "        collection_size 1000;",
        "        collection_size 1000;\n        duration 2s;",
    )

    parsed = Parser(Lexer().tokenize(source)).parse()
    compilation = NslCompiler(build_tool_catalog()).compile(source)
    payload = json.loads(compilation.nso_bytes)

    assert parsed.limits is not None
    assert parsed.limits.duration_ms == 2_000
    assert compilation.skill.limits.duration_ms == 2_000
    assert payload["limits"] == {
        "collection_size": 1000,
        "duration_ms": 2_000,
        "emitted_rows": 10,
        "loop_iterations": 10,
        "tool_calls": 11,
    }


def test_lim_001_omitted_duration_uses_explicit_canonical_default() -> None:
    parsed = Parser(Lexer().tokenize(SOURCE)).parse()
    compilation = NslCompiler(build_tool_catalog()).compile(SOURCE)

    assert parsed.limits is not None
    assert parsed.limits.duration_ms == 60_000
    assert compilation.skill.limits.duration_ms == 60_000


@pytest.mark.parametrize(
    ("literal", "milliseconds"),
    (("1ms", 1), ("5m", 300_000)),
)
def test_lim_001_duration_accepts_unit_boundaries(
    literal: str, milliseconds: int
) -> None:
    source = SOURCE.replace(
        "        collection_size 1000;",
        f"        collection_size 1000;\n        duration {literal};",
    )

    skill = NslCompiler(build_tool_catalog()).compile(source).skill

    assert skill.limits.duration_ms == milliseconds


@pytest.mark.parametrize(
    ("replacement", "expected_code"),
    (
        (
            "        collection_size 1000;\n        duration 0ms;",
            DiagnosticCode.PAR_NON_POSITIVE_LIMIT,
        ),
        (
            "        collection_size 1000;\n        duration 1;",
            DiagnosticCode.PAR_EXPECTED_TOKEN_KIND,
        ),
        (
            "        collection_size 1000;\n        duration 1s;\n"
            "        duration 2s;",
            DiagnosticCode.PAR_INVALID_LIMIT_FIELDS,
        ),
        (
            "        collection_size 1000;\n        tool_calls 1;",
            DiagnosticCode.PAR_INVALID_LIMIT_FIELDS,
        ),
        (
            "        collection_size 1000;\n        memory 1;",
            DiagnosticCode.PAR_INVALID_LIMIT_FIELDS,
        ),
    ),
)
def test_lim_001_rejects_invalid_resource_limit_definitions(
    replacement: str, expected_code: DiagnosticCode
) -> None:
    source = SOURCE.replace("        collection_size 1000;", replacement)

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == expected_code


def test_lim_002_runtime_tracks_each_executed_tool_call() -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    skill = replace(
        base_skill,
        limits=replace(base_skill.limits, tool_calls=1),
    ).with_computed_hash()
    executor = build_mock_executor(catalog)
    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = lambda arguments: []

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-lim-002-boundary"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert result.resources.tool_calls == 1
    assert executor.call_count == 1


def test_lim_002_tool_call_over_limit_is_blocked_before_execution() -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    skill = replace(
        base_skill,
        limits=replace(base_skill.limits, tool_calls=1),
    ).with_computed_hash()
    executor = build_mock_executor(catalog)

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-lim-002-exceeded"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    assert result.error is not None
    assert result.error.message == "tool call limit exceeded"
    assert result.resources.tool_calls == 1
    assert executor.call_count == 1


@pytest.mark.parametrize(
    ("parent_count", "expected_status", "expected_outputs"),
    (
        (1, ExecutionStatus.COMPLETED, 1),
        (2, ExecutionStatus.LIMIT_EXCEEDED, 1),
    ),
)
def test_lim_003_runtime_tracks_and_limits_loop_iterations(
    parent_count: int,
    expected_status: ExecutionStatus,
    expected_outputs: int,
) -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    skill = replace(
        base_skill,
        limits=replace(base_skill.limits, loop_iterations=1),
    ).with_computed_hash()
    executor = build_mock_executor(catalog)
    parent_fixture = executor.handlers["PROJECT.LIST_PARENT_PROJECTS"]
    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = (
        lambda arguments: parent_fixture(arguments) * parent_count
    )

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(f"exec-lim-003-{parent_count}"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is expected_status
    assert result.resources.loop_iterations == 1
    assert len(result.outputs) == expected_outputs
    if expected_status is ExecutionStatus.LIMIT_EXCEEDED:
        assert result.error is not None
        assert result.error.message == "loop iteration limit exceeded"


@pytest.mark.parametrize(
    ("parent_count", "expected_status"),
    (
        (1, ExecutionStatus.COMPLETED),
        (2, ExecutionStatus.LIMIT_EXCEEDED),
    ),
)
def test_lim_004_runtime_tracks_and_limits_emitted_rows(
    parent_count: int, expected_status: ExecutionStatus
) -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    skill = replace(
        base_skill,
        limits=replace(base_skill.limits, emitted_rows=1),
    ).with_computed_hash()
    executor = build_mock_executor(catalog)
    parent_fixture = executor.handlers["PROJECT.LIST_PARENT_PROJECTS"]
    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = (
        lambda arguments: parent_fixture(arguments) * parent_count
    )

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(f"exec-lim-004-{parent_count}"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is expected_status
    assert result.resources.emitted_rows == 1
    assert len(result.outputs) == 1
    if expected_status is ExecutionStatus.LIMIT_EXCEEDED:
        assert result.error is not None
        assert result.error.message == "emitted row limit exceeded"


@pytest.mark.parametrize(
    ("elapsed_ns", "expected_status"),
    (
        (999_999, ExecutionStatus.COMPLETED),
        (1_000_000, ExecutionStatus.LIMIT_EXCEEDED),
    ),
)
def test_lim_005_runtime_enforces_total_duration_boundary(
    elapsed_ns: int, expected_status: ExecutionStatus
) -> None:
    catalog = build_tool_catalog()
    source = SOURCE.replace(
        "        collection_size 1000;",
        "        collection_size 1000;\n        duration 1ms;",
    )
    skill = NslCompiler(catalog).compile(source).skill
    executor = build_mock_executor(catalog)
    parent_fixture = executor.handlers["PROJECT.LIST_PARENT_PROJECTS"]
    clock = FakeRuntimeClock()

    def timed_parents(arguments):
        clock.advance_ns(elapsed_ns)
        return parent_fixture(arguments)

    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = timed_parents
    result = asyncio.run(
        RuntimeEngine(catalog, runtime_clock=clock).execute(
            skill,
            execution_request(f"exec-lim-005-{elapsed_ns}"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is expected_status
    if expected_status is ExecutionStatus.LIMIT_EXCEEDED:
        assert result.error is not None
        assert result.error.message == "execution duration limit exceeded"


def test_lim_005_remaining_duration_caps_tool_timeout(monkeypatch) -> None:
    catalog = build_tool_catalog()
    source = SOURCE.replace(
        "        collection_size 1000;",
        "        collection_size 1000;\n        duration 2s;",
    )
    skill = NslCompiler(catalog).compile(source).skill
    observed_timeouts: list[int] = []

    async def duration_timeout(executor, request, timeout_ms):
        observed_timeouts.append(timeout_ms)
        raise ToolExecutionError("TOOL_TIMEOUT", "tool execution timed out")

    monkeypatch.setattr(runtime_module, "execute_with_timeout", duration_timeout)
    audit = InMemoryAuditSink()
    result = asyncio.run(
        RuntimeEngine(catalog, runtime_clock=FakeRuntimeClock()).execute(
            skill,
            execution_request("exec-lim-005-timeout"),
            build_mock_executor(catalog),
            audit,
        )
    )

    assert observed_timeouts == [2_000]
    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    assert result.error is not None
    assert result.error.message == "execution duration limit exceeded"
    failed = next(event for event in audit.events if event.event_type == "TOOL_FAILED")
    assert failed.payload["output"]["status"] == "LIMIT_EXCEEDED"


def test_lim_005_forged_zero_duration_is_rejected_at_runtime() -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    skill = replace(
        base_skill,
        limits=replace(base_skill.limits, duration_ms=0),
    ).with_computed_hash()

    result = asyncio.run(
        RuntimeEngine(catalog, runtime_clock=FakeRuntimeClock()).execute(
            skill,
            execution_request("exec-lim-005-zero"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    assert result.resources.tool_calls == 0
    assert result.resources.loop_iterations == 0
    assert result.resources.emitted_rows == 0


def test_lim_005_expired_deadline_has_no_remaining_tool_timeout() -> None:
    catalog = build_tool_catalog()
    source = SOURCE.replace(
        "        collection_size 1000;",
        "        collection_size 1000;\n        duration 1ms;",
    )
    skill = NslCompiler(catalog).compile(source).skill
    request = execution_request("exec-lim-005-no-remaining")
    clock = FakeRuntimeClock()
    context = ExecutionContext(
        skill,
        request,
        AuditRecorder(InMemoryAuditSink(), request.data_policy),
        clock,
    )
    clock.advance_ns(1_000_000)

    with pytest.raises(LimitExceeded, match="duration limit"):
        context.resource_guard.remaining_duration_ms()


@pytest.mark.parametrize(
    ("collection_size", "exceeds_limit"),
    ((1, False), (2, True)),
)
def test_lim_006_collection_limit_applies_to_all_expression_results(
    collection_size: int, exceeds_limit: bool
) -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    skill = replace(
        base_skill,
        limits=replace(base_skill.limits, collection_size=1),
    ).with_computed_hash()
    request = execution_request(f"exec-lim-006-{collection_size}")
    context = ExecutionContext(
        skill,
        request,
        AuditRecorder(InMemoryAuditSink(), request.data_policy),
    )
    values = list(range(collection_size))
    values_type = list_type(INT)
    context.bind(
        "sym-lim-006-values",
        ValueEnvelope.complete(values, values_type),
    )
    expression = SymbolRefExpr(
        "expr-lim-006-values",
        "sym-lim-006-values",
        values_type,
    )

    if exceeds_limit:
        with pytest.raises(LimitExceeded, match="collection size limit"):
            asyncio.run(
                RuntimeEngine(catalog)._evaluate(
                    context,
                    expression,
                    build_mock_executor(catalog),
                )
            )
    else:
        result = asyncio.run(
            RuntimeEngine(catalog)._evaluate(
                context,
                expression,
                build_mock_executor(catalog),
            )
        )
        assert result.value == values

    assert context.resources.max_collection_size_seen == 0


@pytest.mark.parametrize("boundary", ("input", "context"))
@pytest.mark.parametrize("collection_size", (1, 2))
def test_lim_006_collection_limit_applies_at_external_value_boundaries(
    boundary: str, collection_size: int
) -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    values_type = list_type(INT)
    request = execution_request(
        f"exec-lim-006-{boundary}-{collection_size}"
    )
    if boundary == "input":
        skill = replace(
            base_skill,
            inputs=(replace(base_skill.inputs[0], type_info=values_type),),
            limits=replace(base_skill.limits, collection_size=1),
        )
        request = replace(request, inputs={"year": list(range(collection_size))})
    else:
        skill = replace(
            base_skill,
            contexts=(replace(base_skill.contexts[0], type_info=values_type),),
            limits=replace(base_skill.limits, collection_size=1),
        )
        request = replace(
            request,
            runtime_context={
                "user": {"team_id": list(range(collection_size))}
            },
        )
    context = ExecutionContext(
        skill,
        request,
        AuditRecorder(InMemoryAuditSink(), request.data_policy),
    )

    if collection_size == 2:
        with pytest.raises(LimitExceeded, match="collection size limit"):
            RuntimeEngine(catalog)._bind_inputs_and_contexts(context)
        assert context.resources.max_collection_size_seen == 2
    else:
        RuntimeEngine(catalog)._bind_inputs_and_contexts(context)
        assert context.resources.max_collection_size_seen == 1


@pytest.mark.parametrize(
    "limit_name",
    (
        "tool_calls",
        "loop_iterations",
        "emitted_rows",
        "collection_size",
        "duration_ms",
    ),
)
def test_lim_007_every_resource_limit_returns_limit_exceeded(
    limit_name: str,
) -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    skill = replace(
        base_skill,
        limits=replace(base_skill.limits, **{limit_name: 0}),
    ).with_computed_hash()

    result = asyncio.run(
        RuntimeEngine(catalog, runtime_clock=FakeRuntimeClock()).execute(
            skill,
            execution_request(f"exec-lim-007-{limit_name}"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    assert result.error is not None
    assert result.error.code == DiagnosticCode.RESOURCE_LIMIT_EXCEEDED
    assert result.error.category == "RESOURCE"


def test_lim_008_resource_limit_is_never_reported_as_check_fail() -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    skill = replace(
        base_skill,
        limits=replace(base_skill.limits, emitted_rows=0),
    ).with_computed_hash()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-lim-008"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    assert len(result.checks) == 1
    assert result.checks[0].status is CheckStatus.PASS
    assert all(check.status is not CheckStatus.FAIL for check in result.checks)
    assert result.outputs == ()
    assert result.resources.emitted_rows == 0


@pytest.mark.parametrize(
    ("item_count", "expected_status", "expected_iterations"),
    (
        (10, ExecutionStatus.COMPLETED, 10),
        (11, ExecutionStatus.LIMIT_EXCEEDED, 0),
    ),
)
def test_tst_006_foreach_declared_bound_and_worst_case_excess(
    item_count: int,
    expected_status: ExecutionStatus,
    expected_iterations: int,
) -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    executor = build_mock_executor(catalog)
    parent_fixture = executor.handlers["PROJECT.LIST_PARENT_PROJECTS"]
    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = (
        lambda arguments: parent_fixture(arguments) * item_count
    )

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(f"exec-tst-006-{item_count}"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is expected_status
    assert result.resources.loop_iterations == expected_iterations
    if expected_status is ExecutionStatus.COMPLETED:
        assert result.resources.tool_calls == 11
        assert result.resources.emitted_rows == 10
    else:
        assert result.error is not None
        assert "exceeds max 10" in result.error.message


def test_tst_009_runtime_reports_integrated_resource_usage() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-tst-009"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert result.resources.tool_calls == 2
    assert result.resources.loop_iterations == 1
    assert result.resources.emitted_rows == 1
    assert result.resources.max_collection_size_seen == 3
    assert result.resources.tool_calls <= skill.limits.tool_calls
    assert result.resources.loop_iterations <= skill.limits.loop_iterations
    assert result.resources.emitted_rows <= skill.limits.emitted_rows
    assert result.resources.max_collection_size_seen <= skill.limits.collection_size
