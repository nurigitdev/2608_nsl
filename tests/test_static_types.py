from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from nsl import CompileError, DiagnosticCode, NslCompiler, SourceFile
from nsl.core import (
    BOOL,
    CHECK_STATUS,
    DATE,
    DATETIME,
    DECIMAL,
    INT,
    STRING,
    TypeRef,
    YEAR,
    domain,
    list_type,
    money_type,
)
from nsl.ir import (
    BinaryExpr,
    CallExpr,
    ForeachStatement,
    LetStatement,
    LiteralExpr,
    ProjectionExpr,
    ReadExpr,
)
from nsl.tools import ToolContractCatalog
from nsl.type_system import StaticTypeChecker
from nsl.vertical_slice import (
    CHILD_PROJECT,
    PARENT_PROJECT,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT_SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def _skill(body: str = "", *, inputs: str = "", outputs: str = "") -> str:
    return f'''language NSL "0.1";

skill TEST.STATIC_TYPES {{
    version "1.0.0";
    risk READ_VALIDATE;
    limits {{
        tool_calls 100;
        loop_iterations 100;
        emitted_rows 100;
        collection_size 100;
    }}
    input {{
{inputs}
    }}
    output {{
{outputs}
    }}
{body}
}}
'''


def _compile(source_text: str):
    source = SourceFile.from_text("skills/static_types.ns", source_text)
    return NslCompiler(ToolContractCatalog(())).compile(source)


def test_typ_001_static_type_system_supports_v0_1_type_set() -> None:
    inputs = """        text: String;
        count: Int;
        ratio: Decimal;
        enabled: Bool;
        start_date: Date;
        created_at: DateTime;
        year: Year;
        amount: Money<KRW>;
        team: TeamId;
        employee: EmployeeId;
        project: ProjectCode;
        organization: OrganizationId;
        counts: List<Int>;
        status: CheckStatus;"""
    result = _compile(_skill(inputs=inputs))
    actual = {item.name: item.type_info for item in result.skill.inputs}

    assert actual == {
        "text": STRING,
        "count": INT,
        "ratio": DECIMAL,
        "enabled": BOOL,
        "start_date": DATE,
        "created_at": DATETIME,
        "year": YEAR,
        "amount": money_type("KRW"),
        "team": domain("TeamId"),
        "employee": domain("EmployeeId"),
        "project": domain("ProjectCode"),
        "organization": domain("OrganizationId"),
        "counts": list_type(INT),
        "status": CHECK_STATUS,
    }


def test_typ_002_assignment_and_expression_types_are_checked() -> None:
    body = """    let calculated = count + 1;
    emit {
        value: calculated;
    }"""
    compiled = _compile(
        _skill(
            inputs="        count: Int;",
            outputs="        value: Int;",
            body=body,
        )
    )
    statement = compiled.skill.body[0]
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, BinaryExpr)
    assert statement.value.type_info == INT

    division = _compile(
        _skill(
            inputs="        count: Int;",
            outputs="        value: Decimal;",
            body=body.replace("count + 1", "count / 2"),
        )
    )
    division_statement = division.skill.body[0]
    assert isinstance(division_statement, LetStatement)
    assert isinstance(division_statement.value, BinaryExpr)
    assert division_statement.value.type_info == DECIMAL

    mismatched = _skill(
        inputs="        count: Int;",
        outputs="        value: String;",
        body=body,
    )
    with pytest.raises(CompileError) as captured:
        _compile(mismatched)
    assert captured.value.code == DiagnosticCode.SEM_OUTPUT_TYPE
    assert captured.value.logical_path == "skills/static_types.ns"
    assert captured.value.snippet == "        value: calculated;"


def test_typ_003_check_assert_rejects_non_bool_type() -> None:
    check = """    check STRICT_BOOL {
        assert true;
        severity ERROR;
        on_fail REPORT;
        message "strict";
    }"""
    compiled = _compile(
        _skill(inputs="        count: Int;", body=check)
    )
    assert compiled.skill.body[0].check_id == "STRICT_BOOL"

    with pytest.raises(CompileError) as captured:
        _compile(
            _skill(
                inputs="        count: Int;",
                body=check.replace("assert true;", "assert count;"),
            )
        )
    assert captured.value.code == DiagnosticCode.SEM_CHECK_CONDITION_TYPE
    assert captured.value.snippet == "        assert count;"


def test_typ_004_tool_input_must_match_contract_type() -> None:
    mismatched = PROJECT_SOURCE.replace(
        "        team_id: team_id", "        team_id: year", 1
    )
    source = SourceFile.from_text("skills/tool_input.ns", mismatched)

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    error = captured.value
    assert error.code == DiagnosticCode.SEM_TOOL_ARGUMENT_TYPE
    assert error.logical_path == "skills/tool_input.ns"
    assert error.snippet == "        team_id: year"


def test_typ_005_tool_result_type_is_attached_to_ir_and_binding() -> None:
    result = NslCompiler(build_tool_catalog()).compile(PROJECT_SOURCE)
    parent_read = result.skill.body[0]
    loop = result.skill.body[1]
    assert isinstance(parent_read, LetStatement)
    assert isinstance(parent_read.value, ReadExpr)
    assert parent_read.value.type_info == list_type(PARENT_PROJECT)
    assert isinstance(loop, ForeachStatement)
    assert loop.collection.type_info == list_type(PARENT_PROJECT)

    child_read = loop.body[0]
    assert isinstance(child_read, LetStatement)
    assert isinstance(child_read.value, ReadExpr)
    assert child_read.value.type_info == list_type(CHILD_PROJECT)


def test_typ_006_list_element_type_flows_through_loop_projection_and_sum() -> None:
    result = NslCompiler(build_tool_catalog()).compile(PROJECT_SOURCE)
    loop = result.skill.body[1]
    assert isinstance(loop, ForeachStatement)

    symbols = {symbol.name: symbol for symbol in result.skill.symbols}
    assert symbols["parent"].type_info == PARENT_PROJECT
    assert symbols["children"].type_info == list_type(CHILD_PROJECT)
    assert symbols["spent"].type_info == money_type("KRW")

    spent = loop.body[1]
    assert isinstance(spent, LetStatement)
    assert isinstance(spent.value, CallExpr)
    projection = spent.value.arguments[0]
    assert isinstance(projection, ProjectionExpr)
    assert projection.type_info == list_type(money_type("KRW"))
    assert spent.value.type_info == money_type("KRW")


@pytest.mark.parametrize(
    ("original", "replacement", "snippet"),
    (
        (
            "parent_project: parent.project_code",
            "parent_project: parent.unknown_field",
            "            parent_project: parent.unknown_field,",
        ),
        (
            "sum(children.expense_amount)",
            "sum(children.unknown_field)",
            "        let spent = sum(children.unknown_field);",
        ),
    ),
)
def test_typ_007_unknown_record_and_collection_fields_are_rejected(
    original: str, replacement: str, snippet: str
) -> None:
    source = SourceFile.from_text(
        "skills/unknown_field.ns",
        PROJECT_SOURCE.replace(original, replacement, 1),
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    error = captured.value
    assert error.code == DiagnosticCode.SEM_UNKNOWN_FIELD
    assert error.logical_path == "skills/unknown_field.ns"
    assert error.snippet == snippet


@pytest.mark.parametrize("type_name", ("Float", "Double"))
def test_typ_008_float_and_double_types_are_forbidden(type_name: str) -> None:
    source = SourceFile.from_text(
        "skills/no_binary_float.ns",
        _skill(inputs=f"        value: {type_name};"),
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(ToolContractCatalog(())).compile(source)

    error = captured.value
    assert error.code == DiagnosticCode.PAR_UNKNOWN_TYPE
    assert error.logical_path == "skills/no_binary_float.ns"
    assert error.snippet == f"        value: {type_name};"


def test_typ_009_decimal_literals_use_python_decimal_exactly() -> None:
    body = """    let ratio = 0.1 + 0.2;
    emit {
        value: ratio;
    }"""
    result = _compile(
        _skill(outputs="        value: Decimal;", body=body)
    )
    statement = result.skill.body[0]
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, BinaryExpr)
    assert statement.value.type_info == DECIMAL
    assert isinstance(statement.value.left, LiteralExpr)
    assert isinstance(statement.value.right, LiteralExpr)
    assert statement.value.left.value == Decimal("0.1")
    assert statement.value.right.value == Decimal("0.2")
    assert type(statement.value.left.value) is Decimal
    assert type(statement.value.right.value) is Decimal


def test_typ_010_true_and_false_are_statically_typed_bool_literals() -> None:
    body = """    let enabled = true;
    let disabled = false;
    emit {
        yes: enabled;
        no: disabled;
    }"""
    result = _compile(
        _skill(
            outputs="""        yes: Bool;
        no: Bool;""",
            body=body,
        )
    )

    enabled, disabled = result.skill.body[:2]
    assert isinstance(enabled, LetStatement)
    assert isinstance(disabled, LetStatement)
    assert isinstance(enabled.value, LiteralExpr)
    assert isinstance(disabled.value, LiteralExpr)
    assert enabled.value.value is True
    assert disabled.value.value is False
    assert enabled.value.type_info == BOOL
    assert disabled.value.type_info == BOOL


@pytest.mark.parametrize("expression", ("1", '"non-empty"', "flags"))
def test_typ_011_check_requires_exact_bool_without_truthiness(
    expression: str,
) -> None:
    check = f"""    check EXACT_BOOL {{
        assert {expression};
        severity ERROR;
        on_fail REPORT;
        message "strict";
    }}"""
    source = _skill(
        inputs="""        count: Int;
        text: String;
        flags: List<Bool>;""",
        body=check,
    )

    with pytest.raises(CompileError) as captured:
        _compile(source)

    error = captured.value
    assert error.code == DiagnosticCode.SEM_CHECK_CONDITION_TYPE
    assert error.snippet == f"        assert {expression};"


@pytest.mark.parametrize(
    ("operator", "allowed"),
    (
        ("==", True),
        ("!=", True),
        ("<", False),
        ("<=", False),
        (">", False),
        (">=", False),
    ),
)
def test_typ_012_bool_equality_is_allowed_but_ordering_is_forbidden(
    operator: str, allowed: bool
) -> None:
    body = f"""    let result = left {operator} right;
    emit {{
        value: result;
    }}"""
    source = _skill(
        inputs="""        left: Bool;
        right: Bool;""",
        outputs="        value: Bool;",
        body=body,
    )

    if allowed:
        result = _compile(source)
        statement = result.skill.body[0]
        assert isinstance(statement, LetStatement)
        assert isinstance(statement.value, BinaryExpr)
        assert statement.value.type_info == BOOL
        return

    with pytest.raises(CompileError) as captured:
        _compile(source)
    assert captured.value.code == DiagnosticCode.SEM_BINARY_TYPE
    assert captured.value.snippet == f"    let result = left {operator} right;"


@pytest.mark.parametrize(
    ("expression", "inputs", "expected_code"),
    (
        ("bool(1)", "", DiagnosticCode.SEM_UNSUPPORTED_BUILTIN),
        ("int(true)", "", DiagnosticCode.SEM_UNSUPPORTED_BUILTIN),
        ("string(true)", "", DiagnosticCode.SEM_UNSUPPORTED_BUILTIN),
        ("true + false", "", DiagnosticCode.SEM_BINARY_TYPE),
        (
            "sum(flags)",
            "        flags: List<Bool>;",
            DiagnosticCode.SEM_SUM_ARGUMENT_TYPE,
        ),
    ),
)
def test_typ_013_bool_has_no_implicit_conversion_or_numeric_behavior(
    expression: str,
    inputs: str,
    expected_code: DiagnosticCode,
) -> None:
    source = _skill(
        inputs=inputs,
        body=f"    let result = {expression};",
    )

    with pytest.raises(CompileError) as captured:
        _compile(source)

    assert captured.value.code == expected_code


def test_tst_004_type_checker_boundary_and_robustness_matrix() -> None:
    checker = StaticTypeChecker(())
    krw = money_type("KRW")

    assert checker.require_list(
        list_type(INT),
        DiagnosticCode.SEM_FOREACH_COLLECTION_TYPE,
        "list required",
        None,
    ) == INT
    for invalid_list in (INT, TypeRef(kind="list")):
        with pytest.raises(CompileError) as captured:
            checker.require_list(
                invalid_list,
                DiagnosticCode.SEM_FOREACH_COLLECTION_TYPE,
                "list required",
                None,
            )
        assert captured.value.code == DiagnosticCode.SEM_FOREACH_COLLECTION_TYPE

    assert checker.field_result(PARENT_PROJECT, "budget", None) == (krw, False)
    assert checker.field_result(list_type(PARENT_PROJECT), "budget", None) == (
        list_type(krw),
        True,
    )
    for invalid_source in (STRING, TypeRef(kind="list")):
        with pytest.raises(CompileError) as captured:
            checker.field_result(invalid_source, "missing", None)
        assert captured.value.code == DiagnosticCode.SEM_UNKNOWN_FIELD

    for item_type in (INT, DECIMAL, krw):
        assert checker.sum_result(list_type(item_type), None) == item_type
    for invalid_sum in (INT, list_type(BOOL), list_type(STRING)):
        with pytest.raises(CompileError) as captured:
            checker.sum_result(invalid_sum, None)
        assert captured.value.code == DiagnosticCode.SEM_SUM_ARGUMENT_TYPE

    arithmetic_cases = (
        ("+", INT, INT),
        ("-", INT, INT),
        ("*", INT, INT),
        ("/", INT, DECIMAL),
        ("+", DECIMAL, DECIMAL),
        ("-", DECIMAL, DECIMAL),
        ("*", DECIMAL, DECIMAL),
        ("/", DECIMAL, DECIMAL),
        ("+", krw, krw),
        ("-", krw, krw),
    )
    for operator, operand_type, expected in arithmetic_cases:
        assert checker.binary_result(
            operator, operand_type, operand_type, None
        ) == expected

    for operator in ("<", "<=", ">", ">="):
        for ordered_type in (INT, DECIMAL, krw):
            assert checker.binary_result(
                operator, ordered_type, ordered_type, None
            ) == BOOL
    for comparable_type in (BOOL, STRING, domain("TeamId"), list_type(INT)):
        assert checker.binary_result(
            "==", comparable_type, comparable_type, None
        ) == BOOL

    invalid_operations = (
        ("*", krw),
        ("/", krw),
        ("+", BOOL),
        ("<", BOOL),
        ("+", STRING),
        ("<", STRING),
        ("%", INT),
    )
    for operator, operand_type in invalid_operations:
        with pytest.raises(CompileError) as captured:
            checker.binary_result(operator, operand_type, operand_type, None)
        assert captured.value.code == DiagnosticCode.SEM_BINARY_TYPE

    with pytest.raises(CompileError) as captured:
        checker.binary_result("==", INT, DECIMAL, None)
    assert captured.value.code == DiagnosticCode.SEM_BINARY_TYPE
