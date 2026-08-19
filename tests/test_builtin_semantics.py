from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from nsl import (
    CompileError,
    DiagnosticCode,
    NslCompiler,
    SourceFile,
    SourceLocation,
)
from nsl.audit import InMemoryAuditSink
from nsl.builtins import (
    EMPTY_MONEY_SUM_POLICY,
    BuiltinEvaluationError,
    BuiltinRegistry,
    BuiltinSignatureError,
    UnknownBuiltinError,
    V0_1_BUILTINS,
)
from nsl.core import (
    DECIMAL,
    INT,
    STRING,
    ExecutionStatus,
    Money,
    list_type,
    money_type,
)
from nsl.ir import CallExpr, LetStatement
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.security import DataHandlingPolicy
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


_PARENT_AGGREGATION_SKILL = '''language NSL "0.1";
skill FINANCE.COUNT_PARENT_PROJECTS {
    version "1.0.0";
    risk READ_ONLY;
    requires {
        tool PROJECT.LIST_PARENT_PROJECTS version "1.0.0";
    }
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 100;
    }
    input {
        year: Year classification INTERNAL;
    }
    context {
        team_id: TeamId from "user.team_id" classification INTERNAL;
    }
    output {
        parent_count: Int classification CONFIDENTIAL;
    }
    let parents = read PROJECT.LIST_PARENT_PROJECTS(
        year: year,
        team_id: team_id
    );
    let first_parent = min(parents);
    let parent_count = count(parents);
    emit {
        parent_count: parent_count;
    }
}
'''


def _aggregation_request(execution_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
        data_policy=DataHandlingPolicy(),
    )


def test_blt_001_builtin_evaluation_is_pure_and_preserves_inputs() -> None:
    values = [3, -1, 0, 8]
    before = values.copy()

    first = V0_1_BUILTINS.evaluate(
        "sum", (values,), (list_type(INT),), INT
    )
    second = V0_1_BUILTINS.evaluate(
        "sum", (values,), (list_type(INT),), INT
    )

    assert first == second == 10
    assert values == before


@pytest.mark.parametrize(
    ("name", "expected"),
    (("sum", 4), ("count", 3), ("min", -2), ("max", 5)),
)
def test_blt_002_runtime_registry_owns_v0_1_aggregation_semantics(
    name: str, expected: int
) -> None:
    registry = BuiltinRegistry()
    values = [1, -2, 5]
    signature = registry.resolve(name, (list_type(INT),))

    assert registry.names == frozenset({"sum", "count", "min", "max"})
    assert signature.name == name
    assert registry.evaluate(
        name,
        (values,),
        signature.argument_types,
        signature.result_type,
    ) == expected


@pytest.mark.parametrize(
    ("name", "expected_amount"),
    (("min", Decimal("-1")), ("max", Decimal("3"))),
)
def test_blt_002_money_min_and_max_dispatch_by_function_name(
    name: str, expected_amount: Decimal
) -> None:
    item_type = money_type("KRW")
    values = [
        Money(Decimal("2"), "KRW"),
        Money(Decimal("-1"), "KRW"),
        Money(Decimal("3"), "KRW"),
    ]

    result = V0_1_BUILTINS.evaluate(
        name, (values,), (list_type(item_type),), item_type
    )

    assert result == Money(expected_amount, "KRW")


def test_blt_003_registry_rejects_arbitrary_builtin_registration() -> None:
    registry = BuiltinRegistry()

    assert not hasattr(registry, "register")
    with pytest.raises(TypeError):
        BuiltinRegistry({"custom": object()})
    with pytest.raises(AttributeError):
        registry.custom = object()
    with pytest.raises(UnknownBuiltinError, match="unsupported built-in"):
        registry.resolve("custom", (list_type(INT),))


@pytest.mark.parametrize(
    ("item_type", "values", "expected"),
    (
        (INT, [-4, 0, 9], 5),
        (
            DECIMAL,
            [Decimal("0.1"), Decimal("-0.2"), Decimal("0.3")],
            Decimal("0.2"),
        ),
        (
            money_type("KRW"),
            [Money(Decimal("-1"), "KRW"), Money(Decimal("2.5"), "KRW")],
            Money(Decimal("1.5"), "KRW"),
        ),
    ),
)
def test_blt_004_sum_supports_int_decimal_and_money_collections(
    item_type, values, expected
) -> None:
    collection_type = list_type(item_type)
    signature = V0_1_BUILTINS.resolve("sum", (collection_type,))

    assert signature.result_type == item_type
    assert V0_1_BUILTINS.evaluate(
        "sum", (values,), (collection_type,), item_type
    ) == expected


@pytest.mark.parametrize("invalid_type", (INT, list_type(STRING)))
def test_blt_004_sum_rejects_unsupported_argument_types(invalid_type) -> None:
    with pytest.raises(BuiltinSignatureError, match="sum requires"):
        V0_1_BUILTINS.resolve("sum", (invalid_type,))


@pytest.mark.parametrize(
    ("item_type", "expected", "expected_class"),
    ((INT, 0, int), (DECIMAL, Decimal("0"), Decimal)),
)
def test_blt_005_empty_int_and_decimal_sum_return_typed_zero(
    item_type, expected, expected_class
) -> None:
    collection_type = list_type(item_type)

    result = V0_1_BUILTINS.evaluate(
        "sum", ([],), (collection_type,), item_type
    )

    assert result == expected
    assert type(result) is expected_class


@pytest.mark.parametrize("currency", ("KRW", "USD"))
def test_blt_006_empty_money_sum_returns_zero_in_declared_currency(
    currency: str,
) -> None:
    item_type = money_type(currency)
    result = V0_1_BUILTINS.evaluate(
        "sum", ([],), (list_type(item_type),), item_type
    )

    assert EMPTY_MONEY_SUM_POLICY == "typed-zero"
    assert result == Money(Decimal("0"), currency)
    assert type(result.amount) is Decimal


@pytest.mark.parametrize(
    ("values", "expected"),
    (([], 0), (["only"], 1), (list(range(257)), 257)),
)
def test_blt_007_count_returns_collection_size(values, expected: int) -> None:
    collection_type = list_type(STRING if values and isinstance(values[0], str) else INT)

    result = V0_1_BUILTINS.evaluate(
        "count", (values,), (collection_type,), INT
    )

    assert result == expected
    assert type(result) is int


def test_blt_007_count_compiles_and_runs_through_the_shared_registry() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(_PARENT_AGGREGATION_SKILL).skill

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            _aggregation_request("exec-builtin-count"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert result.outputs[0].values["parent_count"] == 1
    count_statement = skill.body[2]
    assert isinstance(count_statement, LetStatement)
    assert isinstance(count_statement.value, CallExpr)
    assert count_statement.value.function == "count"
    assert count_statement.value.type_info == INT


@pytest.mark.parametrize("name", ("min", "max"))
def test_blt_008_empty_ordering_aggregation_is_an_explicit_error(
    name: str,
) -> None:
    with pytest.raises(BuiltinEvaluationError, match="non-empty collection"):
        V0_1_BUILTINS.evaluate(
            name, ([],), (list_type(INT),), INT
        )


def test_blt_008_malformed_builtin_contracts_fail_closed() -> None:
    with pytest.raises(BuiltinSignatureError, match="exactly one argument"):
        V0_1_BUILTINS.resolve("sum", ())
    with pytest.raises(BuiltinSignatureError, match="argument count mismatch"):
        V0_1_BUILTINS.evaluate("sum", (), (list_type(INT),), INT)
    with pytest.raises(BuiltinSignatureError, match="invalid result type"):
        V0_1_BUILTINS.evaluate(
            "sum", ([1],), (list_type(INT),), DECIMAL
        )
    with pytest.raises(BuiltinEvaluationError, match="cannot evaluate"):
        V0_1_BUILTINS.evaluate(
            "min", ([{}, {}],), (list_type(STRING),), STRING
        )


def test_blt_008_builtin_error_cannot_become_skill_pass() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(_PARENT_AGGREGATION_SKILL).skill
    mock = build_mock_executor(catalog)
    mock.handlers["PROJECT.LIST_PARENT_PROJECTS"] = lambda arguments: []

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            _aggregation_request("exec-builtin-error"),
            mock,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code == DiagnosticCode.RUNTIME_EVALUATION
    assert result.error.message == "min requires a non-empty collection"
    assert result.checks == ()
    assert result.outputs == ()


def test_blt_009_coalesce_is_disabled_in_the_v0_1_baseline() -> None:
    argument_type = list_type(INT)
    with pytest.raises(UnknownBuiltinError, match="coalesce"):
        V0_1_BUILTINS.resolve("coalesce", (argument_type, argument_type))

    source = SourceFile.from_text(
        "skills/coalesce_disabled.ns",
        _PARENT_AGGREGATION_SKILL.replace(
            "min(parents)", "coalesce(parents, parents)"
        ),
    )
    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == DiagnosticCode.SEM_UNSUPPORTED_BUILTIN
    assert captured.value.logical_path == "skills/coalesce_disabled.ns"
    assert captured.value.location == SourceLocation(27, 24)
    assert captured.value.snippet == (
        "    let first_parent = coalesce(parents, parents);"
    )
