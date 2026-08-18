from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from nsl import (
    CompileError,
    DiagnosticCode,
    NslCompiler,
    SourceFile,
    SourceId,
    SourceLocation,
    SourcePosition,
    SourceSpan,
)
from nsl.core import DataClassification, INT
from nsl.ir import (
    CheckStatement,
    EmitStatement,
    FieldExpr,
    ForeachStatement,
    LetStatement,
    SymbolRefExpr,
)
from nsl.symbols import ScopeKind, SymbolNamespace, SymbolTable
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE_TEXT = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def _compile(source_text: str = SOURCE_TEXT):
    source = SourceFile.from_text("skills/symbols.ns", source_text)
    return NslCompiler(build_tool_catalog()).compile(source)


def _with_duplicate_sibling_foreach() -> str:
    loop_start = SOURCE_TEXT.index("    foreach ")
    root_end = SOURCE_TEXT.rfind("\n}")
    loop = SOURCE_TEXT[loop_start:root_end]
    return (
        SOURCE_TEXT[:root_end]
        + "\n\n"
        + loop
        + SOURCE_TEXT[root_end:]
    ).replace("tool_calls 11;", "tool_calls 21;").replace(
        "loop_iterations 10;", "loop_iterations 20;"
    ).replace("emitted_rows 10;", "emitted_rows 20;")


def test_sem_001_all_value_and_check_identifiers_are_symbolized() -> None:
    symbols = _compile().skill.symbols
    by_name = {symbol.name: symbol for symbol in symbols}

    assert by_name["year"].category == "INPUT"
    assert by_name["team_id"].category == "CONTEXT"
    assert by_name["parents"].category == "VARIABLE"
    assert by_name["parent"].category == "ITERATOR"
    assert by_name["children"].category == "VARIABLE"
    assert by_name["spent"].category == "VARIABLE"
    assert by_name["remaining"].category == "VARIABLE"
    assert by_name["BUDGET_LIMIT"].category == "CHECK"
    assert tuple(symbol.symbol_id for symbol in symbols) == tuple(
        f"s{index:04d}" for index in range(1, len(symbols) + 1)
    )


@pytest.mark.parametrize(
    ("source_text", "unknown_name"),
    [
        (
            SOURCE_TEXT.replace(
                "sum(children.expense_amount)", "sum(not_declared)"
            ),
            "not_declared",
        ),
        (
            SOURCE_TEXT.replace(
                "assert spent <= parent.budget;",
                "assert BUDGET_LIMIT.status == BUDGET_LIMIT.status;",
            ),
            "BUDGET_LIMIT",
        ),
    ],
)
def test_sem_002_undeclared_or_not_yet_declared_identifier_is_rejected(
    source_text: str, unknown_name: str
) -> None:
    lines = source_text.splitlines()
    line_number, line = next(
        (index, value)
        for index, value in enumerate(lines, start=1)
        if unknown_name in value
        and not value.lstrip().startswith("check ")
    )

    with pytest.raises(CompileError) as captured:
        _compile(source_text)

    error = captured.value
    assert error.code == DiagnosticCode.SEM_UNKNOWN_IDENTIFIER
    assert error.location == SourceLocation(
        line_number, line.index(unknown_name) + 1
    )
    assert error.snippet == line
    assert error.logical_path == "skills/symbols.ns"


def test_sem_003_duplicate_variable_in_same_scope_is_rejected() -> None:
    declaration = "        let spent = sum(children.expense_amount);"
    source_text = SOURCE_TEXT.replace(
        declaration, declaration + "\n" + declaration, 1
    )
    duplicate_line = source_text.splitlines().index(declaration, 45) + 1

    with pytest.raises(CompileError) as captured:
        _compile(source_text)

    error = captured.value
    assert error.code == DiagnosticCode.SEM_DUPLICATE_SYMBOL
    assert error.location == SourceLocation(duplicate_line, 9)
    assert error.snippet == declaration


def test_sem_004_let_binding_has_no_reassignment_path() -> None:
    first_statement = _compile().skill.body[0]
    assert isinstance(first_statement, LetStatement)
    with pytest.raises(FrozenInstanceError):
        first_statement.target_symbol_id = "s9999"  # type: ignore[misc]

    reassignment = SOURCE_TEXT.replace(
        "let spent = sum(children.expense_amount);",
        "spent = sum(children.expense_amount);",
    )
    with pytest.raises(CompileError) as captured:
        _compile(reassignment)
    assert captured.value.code == DiagnosticCode.PAR_UNEXPECTED_STATEMENT


def test_sem_005_sibling_foreach_blocks_have_separate_scopes() -> None:
    symbols = _compile(_with_duplicate_sibling_foreach()).skill.symbols

    for scoped_name in (
        "parent",
        "children",
        "spent",
        "remaining",
        "BUDGET_LIMIT",
    ):
        matching = [
            symbol for symbol in symbols if symbol.name == scoped_name
        ]
        assert len(matching) == 2
        assert matching[0].symbol_id != matching[1].symbol_id


def test_sem_006_foreach_iterator_is_unavailable_after_block() -> None:
    root_end = SOURCE_TEXT.rfind("\n}")
    escaped_line = "    let escaped = parent.project_code;"
    source_text = (
        SOURCE_TEXT[:root_end]
        + "\n\n"
        + escaped_line
        + SOURCE_TEXT[root_end:]
    )
    line_number = source_text.splitlines().index(escaped_line) + 1

    with pytest.raises(CompileError) as captured:
        _compile(source_text)

    error = captured.value
    assert error.code == DiagnosticCode.SEM_UNKNOWN_IDENTIFIER
    assert error.location == SourceLocation(
        line_number, escaped_line.index("parent") + 1
    )
    assert error.snippet == escaped_line


def test_sem_007_check_result_is_resolvable_only_after_check() -> None:
    skill = _compile().skill
    loop = skill.body[1]
    assert isinstance(loop, ForeachStatement)
    check = loop.body[3]
    emit = loop.body[4]
    assert isinstance(check, CheckStatement)
    assert isinstance(emit, EmitStatement)
    status = dict(emit.fields)["status"]
    assert isinstance(status, FieldExpr)
    assert isinstance(status.source, SymbolRefExpr)
    assert status.source.symbol_id == check.result_symbol_id

    use_before_check = SOURCE_TEXT.replace(
        "        check BUDGET_LIMIT {",
        "        let premature = BUDGET_LIMIT.status;\n\n"
        "        check BUDGET_LIMIT {",
    )
    with pytest.raises(CompileError) as captured:
        _compile(use_before_check)
    assert captured.value.code == DiagnosticCode.SEM_UNKNOWN_IDENTIFIER


@pytest.mark.parametrize(
    "source_text",
    [
        SOURCE_TEXT.replace(
            "foreach parent in parents", "foreach year in parents"
        ),
        SOURCE_TEXT.replace("let children =", "let parents =", 1),
        SOURCE_TEXT.replace(
            "        let spent =",
            "        foreach parent in children max 1 {\n"
            "        }\n"
            "        let spent =",
            1,
        ),
    ],
)
def test_sem_008_nested_scope_shadowing_is_rejected(
    source_text: str,
) -> None:
    with pytest.raises(CompileError) as captured:
        _compile(source_text)

    error = captured.value
    assert error.code == DiagnosticCode.SEM_DUPLICATE_SYMBOL
    assert "shadowing is forbidden" in error.public_message
    assert error.location is not None
    assert error.snippet is not None


def test_symbol_table_scope_stack_and_location_fallbacks_are_robust() -> None:
    table = SymbolTable(())
    with pytest.raises(ValueError, match="root"):
        table.leave_scope(table.current_scope_id)

    outer = table.enter_scope(ScopeKind.FOREACH)
    inner = table.enter_scope(ScopeKind.FOREACH)
    with pytest.raises(ValueError, match="stack order"):
        table.leave_scope(outer)
    table.leave_scope(inner)
    table.leave_scope(outer)

    with pytest.raises(CompileError) as captured:
        table.resolve("missing", None)
    assert captured.value.location is None

    position = SourcePosition(0, 1, 1)
    missing_source_span = SourceSpan(
        SourceId("source:not-registered"), position, position
    )
    with pytest.raises(CompileError) as captured:
        table.resolve("missing", missing_source_span)
    assert captured.value.location is None

    binding = table.declare(
        "value",
        SymbolNamespace.VALUE,
        "VARIABLE",
        INT,
        DataClassification.INTERNAL,
        None,
    )
    assert binding.scope_id == "scope0001"
