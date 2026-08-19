from __future__ import annotations

import ast as python_ast
import json
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

from nsl import (
    CompileError,
    DiagnosticCode,
    DiagnosticPhase,
    NslCompiler,
    ParseMode,
    SourceFile,
    SourceId,
    SourceLocation,
)
from nsl.core import BOOL
from nsl.ir import ProjectionExpr
from nsl.syntax import (
    AstBinary,
    AstCall,
    AstCheck,
    AstEmit,
    AstForeach,
    AstIncludeDeclaration,
    AstIncludeFragment,
    AstLet,
    AstLiteral,
    AstNode,
    AstPath,
    AstRead,
    Lexer,
    Parser,
)
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE_TEXT = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def _ast_nodes(value: Any):
    if isinstance(value, AstNode):
        yield value
        for item in fields(value):
            if not item.name.endswith("span"):
                yield from _ast_nodes(getattr(value, item.name))
    elif isinstance(value, tuple):
        for item in value:
            yield from _ast_nodes(item)


def _parse_expression(source: str):
    return Parser(Lexer().tokenize(source))._expression()


def _instances(value: Any, expected_type: type):
    if isinstance(value, expected_type):
        yield value
    if is_dataclass(value):
        for item in fields(value):
            yield from _instances(getattr(value, item.name), expected_type)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _instances(item, expected_type)


def _golden_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, AstNode):
        result = {"node": type(value).__name__}
        result.update(
            {
                item.name: _golden_value(getattr(value, item.name))
                for item in fields(value)
                if not item.name.endswith("span")
            }
        )
        return result
    if is_dataclass(value):
        return {
            item.name: _golden_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, tuple):
        return [_golden_value(item) for item in value]
    return value


def test_par_001_token_stream_produces_source_spanned_ast() -> None:
    ast = Parser(Lexer().tokenize(SOURCE_TEXT)).parse()
    nodes = tuple(_ast_nodes(ast))

    assert len(nodes) > 20
    assert all(node.span is not None for node in nodes)
    assert ast.span is not None
    assert ast.span.start.offset == 0
    assert ast.span.end.offset == SOURCE_TEXT.rindex("}") + 1


def test_par_002_parser_is_handwritten_recursive_descent() -> None:
    tree = python_ast.parse((ROOT / "nsl" / "syntax.py").read_text(encoding="utf-8"))
    parser = next(
        node
        for node in tree.body
        if isinstance(node, python_ast.ClassDef) and node.name == "Parser"
    )
    methods = {
        node.name: node
        for node in parser.body
        if isinstance(node, python_ast.FunctionDef)
    }

    assert {"parse", "_statement", "_expression", "_primary"} <= set(methods)
    for recursive_method in ("_statement", "_expression"):
        calls = {
            node.func.attr
            for node in python_ast.walk(methods[recursive_method])
            if isinstance(node, python_ast.Call)
            and isinstance(node.func, python_ast.Attribute)
        }
        assert recursive_method in calls


def test_par_003_expression_precedence_and_associativity_are_exact() -> None:
    expression = _parse_expression("a + b * c < d == e")
    assert isinstance(expression, AstBinary)
    assert expression.operator == "=="
    assert isinstance(expression.left, AstBinary)
    assert expression.left.operator == "<"
    assert isinstance(expression.left.left, AstBinary)
    assert expression.left.left.operator == "+"
    assert isinstance(expression.left.left.right, AstBinary)
    assert expression.left.left.right.operator == "*"

    left_associative = _parse_expression("a - b - c")
    assert isinstance(left_associative, AstBinary)
    assert left_associative.operator == "-"
    assert isinstance(left_associative.left, AstBinary)
    assert left_associative.left.operator == "-"

    grouped = _parse_expression("(a + b) * c")
    assert isinstance(grouped, AstBinary)
    assert grouped.operator == "*"
    assert isinstance(grouped.left, AstBinary)
    assert grouped.left.operator == "+"


@pytest.mark.parametrize(
    "operator", ["+", "-", "*", "/", "<", "<=", ">", ">=", "==", "!="]
)
def test_par_004_binary_expression_ast_preserves_operator_and_operands(
    operator: str,
) -> None:
    expression = _parse_expression(f"left {operator} right")
    assert isinstance(expression, AstBinary)
    assert expression.operator == operator
    assert expression.left == AstPath(("left",))
    assert expression.right == AstPath(("right",))
    assert expression.span is not None
    assert expression.span.start.offset == 0
    assert expression.span.end.offset == len(f"left {operator} right")


@pytest.mark.parametrize(
    ("source", "argument_count"),
    [("empty()", 0), ("single(value)", 1), ("many(a, nested(b), c)", 3)],
)
def test_par_005_function_call_ast_preserves_ordered_arguments(
    source: str, argument_count: int
) -> None:
    expression = _parse_expression(source)
    assert isinstance(expression, AstCall)
    assert len(expression.arguments) == argument_count
    assert expression.span is not None
    assert expression.span.start.offset == 0
    assert expression.span.end.offset == len(source)
    if source.startswith("many"):
        assert isinstance(expression.arguments[1], AstCall)
        assert expression.arguments[1].name == "nested"


@pytest.mark.parametrize(
    ("source", "parts"),
    [
        ("identifier", ("identifier",)),
        ("parent.budget", ("parent", "budget")),
        ("children.items.amount", ("children", "items", "amount")),
    ],
)
def test_par_006_field_reference_ast_preserves_every_path_segment(
    source: str, parts: tuple[str, ...]
) -> None:
    expression = _parse_expression(source)
    assert isinstance(expression, AstPath)
    assert expression.parts == parts
    assert expression.span is not None
    assert expression.span.end.offset == len(source)


@pytest.mark.parametrize(
    ("source", "argument_names"),
    [
        ("read PROJECT.EMPTY()", ()),
        ("read PROJECT.TOOL(first: one, second: two)", ("first", "second")),
    ],
)
def test_par_007_read_expression_ast_preserves_tool_and_named_arguments(
    source: str, argument_names: tuple[str, ...]
) -> None:
    expression = _parse_expression(source)
    assert isinstance(expression, AstRead)
    assert expression.tool_id.startswith("PROJECT.")
    assert tuple(name for name, _ in expression.arguments) == argument_names
    assert all(isinstance(value, AstPath) for _, value in expression.arguments)
    assert expression.span is not None
    assert expression.span.end.offset == len(source)


def test_par_008_let_statement_ast_preserves_name_value_and_scope() -> None:
    skill = Parser(Lexer().tokenize(SOURCE_TEXT)).parse()
    top_level = skill.body[0]
    loop = skill.body[1]

    assert isinstance(top_level, AstLet)
    assert top_level.name == "parents"
    assert isinstance(top_level.value, AstRead)
    assert top_level.span is not None
    assert isinstance(loop, AstForeach)
    nested = tuple(item for item in loop.body if isinstance(item, AstLet))
    assert tuple(item.name for item in nested) == ("children", "spent", "remaining")
    assert all(item.span is not None for item in nested)


@pytest.mark.parametrize("maximum", [1, 2_147_483_647])
def test_par_009_foreach_statement_ast_preserves_bounded_body(maximum: int) -> None:
    source = SOURCE_TEXT.replace(
        "foreach parent in parents max 10",
        f"foreach parent in parents max {maximum}",
    )
    skill = Parser(Lexer().tokenize(source)).parse()
    loop = skill.body[1]

    assert isinstance(loop, AstForeach)
    assert loop.iterator == "parent"
    assert loop.collection == AstPath(("parents",))
    assert loop.max_iterations == maximum
    assert len(loop.body) == 5
    assert loop.span is not None


def test_par_010_check_statement_ast_preserves_policy_and_message() -> None:
    skill = Parser(Lexer().tokenize(SOURCE_TEXT)).parse()
    loop = skill.body[1]
    assert isinstance(loop, AstForeach)
    check = loop.body[3]

    assert isinstance(check, AstCheck)
    assert check.check_id == "BUDGET_LIMIT"
    assert isinstance(check.condition, AstBinary)
    assert check.condition.operator == "<="
    assert (check.severity, check.on_fail) == ("ERROR", "REPORT")
    assert "초과" in check.message
    assert check.span is not None


def test_par_011_emit_statement_ast_preserves_ordered_output_expressions() -> None:
    skill = Parser(Lexer().tokenize(SOURCE_TEXT)).parse()
    loop = skill.body[1]
    assert isinstance(loop, AstForeach)
    emit = loop.body[4]

    assert isinstance(emit, AstEmit)
    assert tuple(name for name, _ in emit.fields) == (
        "parent_project",
        "budget",
        "spent",
        "remaining",
        "status",
    )
    assert all(isinstance(expression, AstPath) for _, expression in emit.fields)
    assert emit.span is not None


def test_par_012_syntax_error_reports_expected_token_location_and_snippet() -> None:
    source = SourceFile.from_text(
        "missing_semicolon.ns",
        'language NSL "0.1";\nskill TEST.SKILL {\nversion "1.0.0"\n}',
        SourceId("source:missing-semicolon"),
    )
    parser = Parser(Lexer().tokenize(source), source)

    with pytest.raises(CompileError) as captured:
        parser.parse()

    error = captured.value
    assert "expected ';'" in error.public_message
    assert error.location == SourceLocation(4, 1)
    assert error.snippet == "}"

    other_source = SourceFile.from_text(
        "other.ns", source.text, SourceId("source:other")
    )
    with pytest.raises(ValueError, match="share a source_id"):
        Parser(Lexer().tokenize(source), other_source)


def test_par_013_parser_does_not_call_eval_or_exec() -> None:
    tree = python_ast.parse((ROOT / "nsl" / "syntax.py").read_text(encoding="utf-8"))
    parser = next(
        node
        for node in tree.body
        if isinstance(node, python_ast.ClassDef) and node.name == "Parser"
    )
    forbidden = {
        node.func.id
        for node in python_ast.walk(parser)
        if isinstance(node, python_ast.Call)
        and isinstance(node.func, python_ast.Name)
        and node.func.id in {"eval", "exec"}
    }
    assert forbidden == set()


@pytest.mark.parametrize(("source", "value"), [("true", True), ("false", False)])
def test_par_014_boolean_literal_expression_ast_is_typed(
    source: str, value: bool
) -> None:
    expression = _parse_expression(source)
    assert isinstance(expression, AstLiteral)
    assert expression.value is value
    assert expression.type_info == BOOL
    assert expression.span is not None

    upper = _parse_expression(source.title())
    assert isinstance(upper, AstPath)


def test_par_015_include_declaration_ast_preserves_path_order_and_span() -> None:
    source = SOURCE_TEXT.replace(
        "    requires {",
        '    include "common/base.ns";\n'
        '    include "common/finance.ns";\n\n'
        "    requires {",
    )
    skill = Parser(Lexer().tokenize(source)).parse()

    assert all(isinstance(item, AstIncludeDeclaration) for item in skill.includes)
    assert tuple(item.path for item in skill.includes) == (
        "common/base.ns",
        "common/finance.ns",
    )
    assert all(item.span is not None for item in skill.includes)
    assert skill.includes[0].span is not None
    assert skill.includes[0].span.start.line < skill.includes[1].span.start.line

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)
    assert captured.value.code == DiagnosticCode.SEM_INCLUDE_REQUIRES_COMPOSITION


def test_par_016_root_and_include_fragment_parse_modes_are_distinct() -> None:
    fragment_source = SourceFile.from_text(
        "common/finance.ns",
        'include "base.ns";\n'
        "requires { tool PROJECT.TOOL version \"1.0.0\"; }\n"
        'context { team_id: TeamId from "user.team_id"; }\n'
        "limits { tool_calls 1; loop_iterations 1; emitted_rows 1; "
        "collection_size 1; }",
    )
    fragment = Parser(
        Lexer().tokenize(fragment_source), fragment_source
    ).parse(ParseMode.INCLUDE_FRAGMENT)

    assert isinstance(fragment, AstIncludeFragment)
    assert tuple(item.path for item in fragment.includes) == ("base.ns",)
    assert tuple(
        (item.tool_id, item.version) for item in fragment.requires
    ) == (("PROJECT.TOOL", "1.0.0"),)
    assert fragment.requires[0].span is not None
    assert tuple(item.name for item in fragment.contexts) == ("team_id",)
    assert len(fragment.limits) == 1
    assert fragment.span is not None

    empty = Parser(Lexer().tokenize("")).parse(ParseMode.INCLUDE_FRAGMENT)
    assert isinstance(empty, AstIncludeFragment)
    assert empty.includes == empty.requires == empty.contexts == empty.limits == ()

    with pytest.raises(CompileError, match="expected 'language'"):
        Parser(Lexer().tokenize(fragment_source), fragment_source).parse()
    root_source = SourceFile.from_text("root.ns", SOURCE_TEXT)
    with pytest.raises(CompileError, match="not allowed in include fragment"):
        Parser(Lexer().tokenize(root_source), root_source).parse(
            ParseMode.INCLUDE_FRAGMENT
        )


def test_par_017_collection_field_access_is_lowered_after_ast() -> None:
    skill_ast = Parser(Lexer().tokenize(SOURCE_TEXT)).parse()
    loop = skill_ast.body[1]
    assert isinstance(loop, AstForeach)
    spent = loop.body[1]
    assert isinstance(spent, AstLet)
    assert isinstance(spent.value, AstCall)
    source_path = spent.value.arguments[0]
    assert isinstance(source_path, AstPath)
    assert source_path.parts == ("children", "expense_amount")

    compiled = NslCompiler(build_tool_catalog()).compile(SOURCE_TEXT)
    projections = tuple(_instances(compiled.skill, ProjectionExpr))
    assert any(item.field == "expense_amount" for item in projections)


def test_tst_002_project_budget_check_parser_golden_snapshot() -> None:
    actual = _golden_value(Parser(Lexer().tokenize(SOURCE_TEXT)).parse())
    expected = json.loads(
        (ROOT / "tests" / "golden" / "project_budget_check.ast.json").read_text(
            encoding="utf-8"
        )
    )
    assert actual == expected


@pytest.mark.parametrize(
    ("source_text", "mode", "expected_code"),
    [
        ("", ParseMode.ROOT_SKILL, DiagnosticCode.PAR_EXPECTED_TOKEN),
        (
            "language NSL 0.1;",
            ParseMode.ROOT_SKILL,
            DiagnosticCode.PAR_EXPECTED_TOKEN_KIND,
        ),
        (
            SOURCE_TEXT.replace("Money<KRW>", "UnknownType", 1),
            ParseMode.ROOT_SKILL,
            DiagnosticCode.PAR_UNKNOWN_TYPE,
        ),
        (
            "input { year: Year; }",
            ParseMode.INCLUDE_FRAGMENT,
            DiagnosticCode.PAR_UNEXPECTED_FRAGMENT_DECLARATION,
        ),
    ],
)
def test_tst_003_parser_negative_cases_are_structured(
    source_text: str, mode: ParseMode, expected_code: DiagnosticCode
) -> None:
    source = SourceFile.from_text("negative.ns", source_text)
    with pytest.raises(CompileError) as captured:
        Parser(Lexer().tokenize(source), source).parse(mode)

    error = captured.value
    assert error.code == expected_code
    assert error.diagnostic.phase is DiagnosticPhase.PARSER
    assert error.location is not None
    if source_text:
        assert error.snippet is not None
