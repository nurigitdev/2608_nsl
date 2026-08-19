from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from nsl.bounds import StaticBoundAnalyzer, UnboundedStructureError
from nsl.compiler import NslCompiler
from nsl.core import INT
from nsl.diagnostics import CompileError, DiagnosticCode, SourceLocation
from nsl.ir import ForeachStatement, LetStatement, LiteralExpr, NsoCodec
from nsl.source import SourceFile
from nsl.syntax import AstForeach, AstSkill, Lexer, Parser
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")


def test_bnd_001_compiler_calculates_exact_maximum_tool_calls() -> None:
    result = NslCompiler(build_tool_catalog()).compile(SOURCE)

    assert result.skill.analysis.max_tool_calls == 11
    assert result.skill.analysis.bounded is True

    no_call = StaticBoundAnalyzer().analyze(
        (LetStatement("stmt0001", "sym0001", LiteralExpr("expr0001", 0, INT)),)
    )
    assert no_call.max_tool_calls == 0


def test_bnd_002_nested_foreach_max_drives_worst_case_resource_analysis() -> None:
    nested_source = (
        SOURCE.replace("tool_calls 11;", "tool_calls 9;")
        .replace("loop_iterations 10;", "loop_iterations 8;")
        .replace("emitted_rows 10;", "emitted_rows 2;")
        .replace("foreach parent in parents max 10", "foreach parent in parents max 2")
        .replace(
            "        let spent = sum(children.expense_amount);",
            """        foreach child in children max 3 {
            let repeated_children = read PROJECT.LIST_CHILD_PROJECTS(
                parent_project: parent.project_code,
                year: year
            );
        }
        let spent = sum(children.expense_amount);""",
        )
    )

    analysis = NslCompiler(build_tool_catalog()).compile(nested_source).skill.analysis

    assert analysis.max_tool_calls == 1 + 2 * (1 + 3)
    assert analysis.max_loop_iterations == 2 * (1 + 3)
    assert analysis.max_emit_records == 2


def test_bnd_003_tool_call_bound_accepts_equality_and_rejects_one_over() -> None:
    assert (
        NslCompiler(build_tool_catalog()).compile(SOURCE).skill.analysis.max_tool_calls
        == 11
    )
    source = SourceFile.from_text(
        "finance/boundary.ns",
        SOURCE.replace("tool_calls 11;", "tool_calls 10;"),
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    error = captured.value
    assert error.code == DiagnosticCode.SEM_TOOL_CALL_BOUND
    assert error.public_message == "static tool call bound 11 exceeds declared limit 10"
    assert error.location == SourceLocation(12, 5)
    assert error.snippet == "    limits {"
    assert error.logical_path == "finance/boundary.ns"


@pytest.mark.parametrize(
    ("loop_header", "expected_code"),
    [
        ("foreach parent in parents {", DiagnosticCode.PAR_EXPECTED_TOKEN),
        ("foreach parent in parents max 0 {", DiagnosticCode.PAR_NON_POSITIVE_FOREACH_LIMIT),
    ],
)
def test_bnd_004_source_cannot_declare_an_unbounded_loop(
    loop_header, expected_code
) -> None:
    source = SOURCE.replace("foreach parent in parents max 10 {", loop_header)

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == expected_code
    assert captured.value.location is not None


def test_bnd_004_compiler_fails_closed_when_bound_analysis_cannot_prove_safety() -> None:
    source = SourceFile.from_text("finance/unbounded.ns", SOURCE)
    ast = Parser(Lexer().tokenize(source), source).parse()
    assert isinstance(ast, AstSkill)
    malformed_body = tuple(
        replace(statement, max_iterations=0)
        if isinstance(statement, AstForeach)
        else statement
        for statement in ast.body
    )
    malformed_ast = replace(ast, body=malformed_body)

    with patch("nsl.compiler.Parser.parse", return_value=malformed_ast):
        with pytest.raises(CompileError) as captured:
            NslCompiler(build_tool_catalog()).compile(source)

    error = captured.value
    assert error.code == DiagnosticCode.SEM_UNBOUNDED_STRUCTURE
    assert error.location == SourceLocation(1, 1)
    assert error.snippet == 'language NSL "0.1";'
    assert error.logical_path == "finance/unbounded.ns"
    assert isinstance(error.__cause__, UnboundedStructureError)


def test_bnd_005_nso_contains_and_round_trips_static_bound_analysis() -> None:
    result = NslCompiler(build_tool_catalog()).compile(SOURCE)

    payload = json.loads(result.nso_bytes)
    assert payload["analysis"] == {
        "bounded": True,
        "max_emit_records": 10,
        "max_loop_iterations": 10,
        "max_tool_calls": 11,
    }
    assert NsoCodec.decode(result.nso_bytes).analysis == result.skill.analysis


@pytest.mark.parametrize("maximum", [0, -1, True])
def test_static_bound_analyzer_rejects_invalid_loop_maximum(maximum) -> None:
    literal = LiteralExpr("expr0001", 0, INT)
    loop = ForeachStatement("stmt0001", "sym0001", literal, maximum, ())

    with pytest.raises(UnboundedStructureError, match="positive static max"):
        StaticBoundAnalyzer().analyze((loop,))


def test_static_bound_analyzer_rejects_unknown_ir_shapes() -> None:
    analyzer = StaticBoundAnalyzer()

    with pytest.raises(UnboundedStructureError, match="unsupported statement"):
        analyzer.analyze((object(),))  # type: ignore[arg-type]

    malformed = LetStatement("stmt0001", "sym0001", object())  # type: ignore[arg-type]
    with pytest.raises(UnboundedStructureError, match="unsupported expression"):
        analyzer.analyze((malformed,))
