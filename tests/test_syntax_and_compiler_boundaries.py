from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from nsl.compiler import CompileError, NslCompiler
from nsl.core import BOOL, INT, STRING, YEAR
from nsl.syntax import (
    AstBinary,
    AstCall,
    AstLiteral,
    AstPath,
    AstRead,
    Lexer,
    Parser,
    TokenKind,
)
from nsl.tools import ToolContractCatalog
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")


def parse_expression(text: str):
    return Parser(Lexer().tokenize(text))._expression()


def test_lexer_comment_escape_positions_and_two_character_operator() -> None:
    tokens = Lexer().tokenize('// comment\n"a\\n\\t\\"\\\\x" <= 1')
    assert tokens[0].value == 'a\n\t"\\x'
    assert tokens[0].line == 2
    assert tokens[1].value == "<="
    assert tokens[-1].kind == "EOF"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("@", "unexpected character"),
        ('"unterminated', "unterminated string"),
        ('"escape\\', "unterminated string escape"),
    ],
)
def test_lexer_robustness_errors(source, message) -> None:
    with pytest.raises(CompileError, match=message):
        Lexer().tokenize(source)


@pytest.mark.parametrize(
    ("source", "expected_type", "expected_value"),
    [
        ("0", INT, 0),
        ("1.25", None, Decimal("1.25")),
        ('"value"', STRING, "value"),
        ("true", BOOL, True),
        ("false", BOOL, False),
    ],
)
def test_parser_literal_boundaries(source, expected_type, expected_value) -> None:
    expression = parse_expression(source)
    assert isinstance(expression, AstLiteral)
    assert expression.value == expected_value
    if expected_type is not None:
        assert expression.type_info == expected_type


def test_parser_call_path_read_and_parenthesized_shapes() -> None:
    call = parse_expression("sum(a, b)")
    assert isinstance(call, AstCall)
    assert len(call.arguments) == 2
    assert isinstance(parse_expression("empty()"), AstCall)

    path = parse_expression("parent.budget")
    assert isinstance(path, AstPath)
    assert path.parts == ("parent", "budget")

    read = parse_expression("read PROJECT.TOOL()")
    assert isinstance(read, AstRead)
    assert read.arguments == ()

    grouped = parse_expression("(a + b) * c")
    assert isinstance(grouped, AstBinary)
    assert grouped.operator == "*"
    assert isinstance(grouped.left, AstBinary)


def test_parser_expectation_and_expression_errors_have_locations() -> None:
    parser = Parser(Lexer().tokenize("wrong"))
    with pytest.raises(CompileError, match="expected 'language'.*1:1"):
        parser.parse()

    parser = Parser(Lexer().tokenize("1"))
    with pytest.raises(CompileError, match="expected IDENT"):
        parser._expect_kind(TokenKind.IDENTIFIER)

    with pytest.raises(CompileError, match="expected expression"):
        parse_expression("}")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (SOURCE.replace("version \"1.0.0\";", "", 1), "version, risk, and limits"),
        (SOURCE.replace("Money<KRW>", "UnknownType", 1), "unknown type"),
        (SOURCE.replace("classification INTERNAL", "classification SECRET", 1), "unknown data classification"),
        (SOURCE.replace("collection_size 1000;", ""), "limits must define exactly"),
        (SOURCE.replace("tool_calls 11;", "tool_calls 0;"), "all limits must be positive"),
        (SOURCE.replace("foreach parent in parents max 10", "foreach parent in parents max 0"), "foreach max must be positive"),
        (SOURCE.replace("let parents =", "unknown parents =", 1), "unexpected statement"),
    ],
)
def test_parser_boundary_failures(source, message) -> None:
    with pytest.raises(CompileError, match=message):
        NslCompiler(build_tool_catalog()).compile(source)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (SOURCE.replace('language NSL "0.1"', 'language NSL "0.2"'), "unsupported NSL version"),
        (SOURCE.replace("risk READ_VALIDATE", "risk WRITE"), "unsupported risk profile"),
        (
            SOURCE.replace(
                'tool PROJECT.LIST_CHILD_PROJECTS version "1.0.0";',
                'tool PROJECT.LIST_PARENT_PROJECTS version "1.0.0";',
            ),
            "duplicate required tool",
        ),
        (SOURCE.replace('version "1.0.0";\n    }', 'version "9.0.0";\n    }', 1), "incompatible tool version"),
        (SOURCE.replace("team_id: TeamId", "year: TeamId"), "duplicate symbol"),
        (SOURCE.replace("assert spent <= parent.budget", "assert spent"), "CHECK assert must have Bool"),
        (SOURCE.replace("foreach parent in parents", "foreach parent in year"), "foreach collection must be List"),
        (SOURCE.replace("sum(children.expense_amount)", "sum(unknown)"), "unknown identifier"),
        (SOURCE.replace("sum(children.expense_amount)", "average(children.expense_amount)"), "unsupported built-in"),
        (SOURCE.replace("sum(children.expense_amount)", "sum(year)"), "sum requires"),
        (SOURCE.replace("spent <= parent.budget", "spent <= year"), "binary type mismatch"),
        (SOURCE.replace("team_id: team_id", "team_id: year"), "tool argument type mismatch"),
        (SOURCE.replace("team_id: team_id", "other: team_id"), "tool arguments"),
        (SOURCE.replace("remaining: remaining;", ""), "EMIT fields must exactly match"),
        (SOURCE.replace("remaining: remaining;", "remaining: parent.project_code;"), "output type mismatch"),
        (SOURCE.replace("spent: Money<KRW> classification CONFIDENTIAL", "spent: Money<KRW> classification INTERNAL"), "classification.*too weak"),
        (SOURCE.replace("tool_calls 11;", "tool_calls 10;"), "tool call bound"),
        (SOURCE.replace("loop_iterations 10;", "loop_iterations 9;"), "loop bound"),
        (SOURCE.replace("emitted_rows 10;", "emitted_rows 9;"), "emit bound"),
    ],
)
def test_compiler_semantic_robustness(source, message) -> None:
    with pytest.raises(CompileError, match=message):
        NslCompiler(build_tool_catalog()).compile(source)


def test_compiler_rejects_write_contract() -> None:
    catalog = build_tool_catalog()
    contracts = []
    for contract in catalog._contracts.values():
        contracts.append(
            replace(contract, capability="WRITE")
            if contract.tool_id == "PROJECT.LIST_CHILD_PROJECTS"
            else contract
        )
    with pytest.raises(CompileError, match="WRITE tool"):
        NslCompiler(ToolContractCatalog(tuple(contracts))).compile(SOURCE)
