from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from nsl import CompileError, DiagnosticCode, NslCompiler, SourceFile
from nsl.audit import InMemoryAuditSink
from nsl.core import (
    CurrencyMismatchError,
    ExecutionStatus,
    Money,
    MoneyValidationError,
    TypeRef,
    decode_value,
    encode_value,
    money_type,
    sum_money,
)
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.security import DataHandlingPolicy
from nsl.tools import ToolContractCatalog
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]


def test_mny_001_money_never_accepts_or_produces_binary_float() -> None:
    first = Money(Decimal("0.1"), "KRW")
    second = Money(Decimal("0.2"), "KRW")
    result = first + second

    assert result.amount == Decimal("0.3")
    assert type(result.amount) is Decimal

    for invalid in (0.1, 1, "0.1"):
        with pytest.raises(ValueError, match="finite Decimal"):
            Money(invalid, "KRW")


def test_mny_002_money_keeps_amount_and_currency_as_immutable_fields() -> None:
    value = Money(Decimal("-0.00"), "KRW")

    assert value.amount == Decimal("-0.00")
    assert value.currency == "KRW"
    assert encode_value(value) == {
        "$type": "Money",
        "amount": "-0.00",
        "currency": "KRW",
    }
    with pytest.raises(FrozenInstanceError):
        value.amount = Decimal("1")


@pytest.mark.parametrize(
    ("amounts", "expected"),
    (
        ((), Decimal("0")),
        (("0",), Decimal("0")),
        (("0.1", "0.2"), Decimal("0.3")),
        (("-1", "0", "2"), Decimal("1")),
    ),
)
def test_mny_006_money_sum_requires_one_declared_currency(
    amounts: tuple[str, ...], expected: Decimal
) -> None:
    values = tuple(Money(Decimal(amount), "KRW") for amount in amounts)
    assert sum_money(values, "KRW") == Money(expected, "KRW")

    with pytest.raises(ValueError, match="currency mismatch"):
        sum_money((Money(Decimal("1"), "USD"),), "KRW")
    with pytest.raises(ValueError, match="requires Money values"):
        sum_money((Decimal("1"),), "KRW")


def test_mny_008_currency_uses_iso_4217_code_form_at_every_boundary() -> None:
    for currency in ("KRW", "USD", "JPY"):
        assert Money(Decimal("0"), currency).currency == currency
        assert money_type(currency).currency == currency

    for invalid in ("KR", "KRWW", "krw", "K1W", "K RW", "원화"):
        with pytest.raises(ValueError, match="ISO 4217"):
            Money(Decimal("0"), invalid)
        with pytest.raises(ValueError, match="ISO 4217"):
            money_type(invalid)
        with pytest.raises(ValueError, match="ISO 4217"):
            TypeRef(kind="money", currency=invalid)

    with pytest.raises(ValueError, match="ISO 4217"):
        TypeRef.from_data({"kind": "money", "currency": "usd"})

    source = SourceFile.from_text(
        "skills/invalid_currency.ns",
        '''language NSL "0.1";
skill TEST.INVALID_CURRENCY {
    version "1.0.0";
    risk READ_ONLY;
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }
    input {
        amount: Money<krw>;
    }
}
''',
    )
    with pytest.raises(CompileError) as captured:
        NslCompiler(ToolContractCatalog(())).compile(source)
    assert captured.value.code == DiagnosticCode.PAR_UNKNOWN_TYPE
    assert captured.value.logical_path == "skills/invalid_currency.ns"
    assert captured.value.snippet == "        amount: Money<krw>;"


def test_py_005_money_calculation_paths_keep_python_decimal() -> None:
    first = Money(Decimal("123456789.123456789"), "KRW")
    second = Money(Decimal("0.000000001"), "KRW")
    results = (
        first + second,
        first - second,
        sum_money((first, second), "KRW"),
        sum_money((), "KRW"),
    )

    assert all(type(result.amount) is Decimal for result in results)


def test_tst_005_money_boundary_robustness_and_worst_cases() -> None:
    finite_boundaries = (
        Decimal("0"),
        Decimal("-0"),
        Decimal("1E-999999"),
        Decimal("-1E+999999"),
    )
    for amount in finite_boundaries:
        value = Money(amount, "KRW")
        assert decode_value(encode_value(value)) == value

    for amount in (
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ):
        with pytest.raises(MoneyValidationError, match="finite Decimal"):
            Money(amount, "KRW")

    krw = Money(Decimal("1"), "KRW")
    usd = Money(Decimal("1"), "USD")
    for operation in (
        lambda: krw + usd,
        lambda: krw - usd,
        lambda: krw < usd,
        lambda: krw <= usd,
        lambda: krw > usd,
        lambda: krw >= usd,
        lambda: sum_money((krw, usd), "KRW"),
    ):
        with pytest.raises(CurrencyMismatchError, match="currency mismatch"):
            operation()

    for invalid_amount in (0.1, 1, None, "not-a-decimal"):
        with pytest.raises(ValueError, match="decimal string"):
            decode_value(
                {
                    "$type": "Money",
                    "amount": invalid_amount,
                    "currency": "KRW",
                }
            )


def test_tst_005_runtime_reports_mixed_currency_as_explicit_failure() -> None:
    source = (ROOT / "examples" / "project_budget_check.ns").read_text(
        encoding="utf-8"
    )
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(source).skill
    mock = build_mock_executor(catalog)
    original = mock.handlers["PROJECT.LIST_CHILD_PROJECTS"]

    def mixed_currency(arguments):
        values = original(arguments)
        values[1]["expense_amount"] = Money(Decimal("1"), "USD")
        return values

    mock.handlers["PROJECT.LIST_CHILD_PROJECTS"] = mixed_currency
    request = ExecutionRequest(
        execution_id="exec-mixed-currency",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
        data_policy=DataHandlingPolicy(),
    )
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill, request, mock, InMemoryAuditSink()
        )
    )

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.error is not None
    assert result.error.code == DiagnosticCode.TOOL_EXECUTION_FAILURE
    assert result.error.detail_code == "OUTPUT_CONTRACT_VIOLATION"
    assert result.error.message == (
        "result does not match contract: PROJECT.LIST_CHILD_PROJECTS"
    )
    assert result.checks == ()
    assert result.outputs == ()
