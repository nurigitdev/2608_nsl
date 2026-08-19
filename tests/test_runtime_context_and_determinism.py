from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from nsl.audit import AuditRecorder, InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.core import INT, ExecutionStatus, ValueEnvelope
from nsl.ir import ForeachStatement, NsoCodec
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.runtime_models import ExecutionContext
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")


def execution_request(execution_id: str = "exec-runtime") -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )


def test_rt_001_source_execution_uses_compiler_in_memory_skill_object() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill

    with patch(
        "nsl.syntax.Lexer.tokenize",
        side_effect=AssertionError("runtime attempted to read NSL source"),
    ):
        result = asyncio.run(
            RuntimeEngine(catalog).execute(
                skill,
                execution_request("exec-rt-001"),
                build_mock_executor(catalog),
                InMemoryAuditSink(),
            )
        )

    assert result.status is ExecutionStatus.COMPLETED
    assert result.skill_id == skill.skill_id
    assert result.semantic_hash == skill.semantic_hash


def test_rt_002_nso_executes_directly_without_compiler() -> None:
    catalog = build_tool_catalog()
    artifact = NslCompiler(catalog).compile(SOURCE).nso_bytes
    skill = NsoCodec.decode(artifact)

    with patch.object(
        NslCompiler,
        "compile",
        side_effect=AssertionError("runtime attempted to compile source"),
    ):
        result = asyncio.run(
            RuntimeEngine(catalog).execute(
                skill,
                execution_request("exec-rt-002"),
                build_mock_executor(catalog),
                InMemoryAuditSink(),
            )
        )

    assert result.status is ExecutionStatus.COMPLETED
    assert result.semantic_hash == skill.semantic_hash


def test_rt_003_runtime_creates_an_isolated_execution_context_per_run() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    created: list[ExecutionContext] = []

    def create_context(*args) -> ExecutionContext:
        context = ExecutionContext(*args)
        created.append(context)
        return context

    with patch("nsl.runtime.ExecutionContext", side_effect=create_context):
        for index in range(2):
            result = asyncio.run(
                RuntimeEngine(catalog).execute(
                    skill,
                    execution_request(f"exec-rt-003-{index}"),
                    build_mock_executor(catalog),
                    InMemoryAuditSink(),
                )
            )
            assert result.status is ExecutionStatus.COMPLETED

    assert len(created) == 2
    assert created[0] is not created[1]
    assert created[0].input_values is not created[1].input_values
    assert created[0].context_values is not created[1].context_values
    assert [len(item.frames) for item in created] == [1, 1]
    assert [len(item.check_frames) for item in created] == [1, 1]


@pytest.mark.parametrize("execution_id", [None, "", " "])
def test_rt_011_execution_id_rejects_missing_or_blank_values(execution_id) -> None:
    with pytest.raises(ValueError, match="execution_id must be a non-empty string"):
        execution_request(execution_id)


def test_rt_011_execution_id_is_preserved_in_success_and_failure_results() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    engine = RuntimeEngine(catalog)

    success = asyncio.run(
        engine.execute(
            skill,
            execution_request("exec-rt-011-success"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )
    invalid_request = ExecutionRequest(
        execution_id="exec-rt-011-failure",
        inputs={"year": "not-a-year"},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )
    failure = asyncio.run(
        engine.execute(
            skill,
            invalid_request,
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert success.execution_id == "exec-rt-011-success"
    assert failure.execution_id == "exec-rt-011-failure"
    assert failure.status is ExecutionStatus.FAILED


def test_rt_004_execution_context_keeps_namespaces_separate_and_write_once() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    request = execution_request("exec-rt-004")
    context = ExecutionContext(
        skill,
        request,
        AuditRecorder(InMemoryAuditSink(), request.data_policy),
    )
    values = {
        "input": ValueEnvelope.complete(1, INT),
        "context": ValueEnvelope.complete(2, INT),
        "variable": ValueEnvelope.complete(3, INT),
        "check": ValueEnvelope.complete(4, INT),
    }

    context.bind_input("symbol-input", values["input"])
    context.bind_context("symbol-context", values["context"])
    context.bind("symbol-variable", values["variable"])
    context.bind_check("symbol-check", values["check"])

    assert context.input_values == {"symbol-input": values["input"]}
    assert context.context_values == {"symbol-context": values["context"]}
    assert context.frames == [{"symbol-variable": values["variable"]}]
    assert context.check_frames == [{"symbol-check": values["check"]}]
    assert context.checks == []
    assert context.outputs == []
    for namespace, expected in values.items():
        assert context.resolve(f"symbol-{namespace}") == expected

    for symbol_id in (
        "symbol-input",
        "symbol-context",
        "symbol-variable",
        "symbol-check",
    ):
        with pytest.raises(RuntimeError, match="already bound"):
            context.bind(symbol_id, values["variable"])

    context.push_frame()
    context.bind("symbol-inner", values["variable"])
    assert len(context.frames) == len(context.check_frames) == 2
    context.pop_frame()
    with pytest.raises(RuntimeError, match="unknown symbol"):
        context.resolve("symbol-inner")
    with pytest.raises(RuntimeError, match="cannot pop the root"):
        context.pop_frame()

    for _ in range(2):
        context.push_frame()
        context.bind_check("symbol-loop-check", values["check"])
        assert context.resolve("symbol-loop-check") == values["check"]
        context.pop_frame()
        with pytest.raises(RuntimeError, match="unknown symbol"):
            context.resolve("symbol-loop-check")


def test_rt_005_statements_execute_sequentially_in_ir_source_order() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    audit = InMemoryAuditSink()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-rt-005"),
            build_mock_executor(catalog),
            audit,
        )
    )

    foreach = skill.body[1]
    assert isinstance(foreach, ForeachStatement)
    root_ids = [statement.node_id for statement in skill.body]
    loop_ids = [statement.node_id for statement in foreach.body]
    started = [
        event.payload["node_id"]
        for event in audit.events
        if event.event_type == "STATEMENT_STARTED"
    ]
    completed = [
        event.payload["node_id"]
        for event in audit.events
        if event.event_type == "STATEMENT_COMPLETED"
    ]

    assert result.status is ExecutionStatus.COMPLETED
    assert started == [root_ids[0], root_ids[1], *loop_ids]
    assert completed == [root_ids[0], *loop_ids, root_ids[1]]


def test_rt_006_same_inputs_and_tool_results_are_fully_deterministic() -> None:
    catalog = build_tool_catalog()
    skill = NsoCodec.decode(NslCompiler(catalog).compile(SOURCE).nso_bytes)
    engine = RuntimeEngine(catalog)
    results = []
    event_streams = []

    for _ in range(10):
        audit = InMemoryAuditSink()
        results.append(
            asyncio.run(
                engine.execute(
                    skill,
                    execution_request("exec-rt-006"),
                    build_mock_executor(catalog),
                    audit,
                )
            )
        )
        event_streams.append(tuple(audit.events))

    assert all(result == results[0] for result in results)
    assert all(events == event_streams[0] for events in event_streams)
    assert results[0].status is ExecutionStatus.COMPLETED


def test_rel_001_same_nso_and_inputs_preserve_expression_semantics() -> None:
    catalog = build_tool_catalog()
    artifact = NslCompiler(catalog).compile(SOURCE).nso_bytes
    skill = NsoCodec.decode(artifact)
    engine = RuntimeEngine(catalog)
    semantic_views = []

    for index in range(10):
        result = asyncio.run(
            engine.execute(
                skill,
                execution_request(f"exec-rel-001-{index}"),
                build_mock_executor(catalog),
                InMemoryAuditSink(),
            )
        )
        semantic_views.append(result.semantic_view())

    assert all(view == semantic_views[0] for view in semantic_views)
    assert semantic_views[0]["status"] == "COMPLETED"
    assert semantic_views[0]["outputs"]
