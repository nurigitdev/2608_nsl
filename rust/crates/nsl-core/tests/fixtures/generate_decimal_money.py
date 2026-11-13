"""Generate the Rust Decimal and Money differential fixture from the frozen Oracle."""

from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext
import operator
from pathlib import Path
import sys


def main(oracle_root: Path) -> None:
    sys.path.insert(0, str(oracle_root.resolve()))
    from nsl.core import CurrencyMismatchError, Money, MoneyValidationError, decode_value, encode_value
    from nsl.integration import IntegrationContractError, _normalize_value

    def emit(*fields: object) -> None:
        print(*fields, sep="\t")

    def decimal(token: str) -> Decimal:
        return decode_value({"$type": "Decimal", "value": token})

    def encoded_decimal(value: Decimal) -> str:
        return encode_value(value)["value"]

    def result(operation, left, right, encode):
        try:
            return encode(operation(left, right))
        except (DivisionByZero, InvalidOperation):
            return "DIVISION_BY_ZERO"
        except Overflow:
            return "OVERFLOW"
        except CurrencyMismatchError:
            return "CURRENCY_MISMATCH"

    print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    with localcontext() as context:
        assert (context.prec, context.rounding, context.Emin, context.Emax) == (
            28, "ROUND_HALF_EVEN", -999_999, 999_999
        )
        for token in (
            "0", "-0.00", "-0E+9", "1E-3", "1.20E+3", "0.", ".1", "01.0", " +1_2.30_0E-1 ",
            " －", "١٢.٣٠e−٢", "١٢.٣٠e-٢", "𝟘.𝟙𝟘", "𑽑𑽒.𑽐", "².0",
            "", "1.2.3", "1e", "1e2e3", "1 e2", "1_ e2", "NaN", "-sNaN42", "Infinity", "+inf",
            "-Infinity", "1e-1999999999999999998", "10e999999999999999999",
            "123456789012345678901234567890123456789.00000",
        ):
            try:
                value = decimal(token)
                _normalize_value(value, "fixture", 0, [0])
            except (ValueError, IntegrationContractError):
                emit("parse", token, "INVALID_TEXT")
            else:
                emit("parse", token, encoded_decimal(value))

        pairs = (
            ("0.10", "0.20"), ("1E-3", "-0.00"), ("-0.0", "-0.00"), ("-0.0", "0.00"),
            ("0.0", "-0.00"), ("-0.00", "2.0"), ("-0E+9", "-2.0"), ("0.0100", "-0.01000"),
            ("1.0", "1.00"), ("1.01", "1.01001"), ("-1.01", "-1.01001"),
            ("1.00", "2"), ("2", "1.00"), ("1", "3"), ("2", "3"), ("-1", "6"),
            ("0", "0"), ("7", "0.0"), ("1E+3", "2E-3"), ("0E+9", "1E-100"),
            ("1234567890123456789012345678", "0.499999999"),
            ("1234567890123456789012345678", "0.5"),
            ("1234567890123456789012345678", "0.500000001"),
            ("1234567890123456789012345679", "0.5"),
            ("-1234567890123456789012345678", "-0.5"),
            ("-1234567890123456789012345679", "-0.5"),
            ("1234567890123456789012345678.5", "1E-1000000"),
            ("1234567890123456789012345678.5", "-1E-1000000"),
            ("9999999999999999999999999999", "0.5"),
            ("0.9999999999999999999999999999", "0.00000000000000000000000000005"),
            ("2.469135780246913578024691357", "0.5"),
            ("2.469135780246913578024691359", "0.5"),
            ("1.2345678901234567890123456785", "1"),
            ("1.2345678901234567890123456795", "1"),
            ("1.2345678901234567890123456785000000000001", "1"),
            ("1.2345678901234567890123456784999999999999", "1"),
        )
        for left_text, right_text in pairs:
            left, right = decimal(left_text), decimal(right_text)
            if abs(right.adjusted()) == 1_000_000:
                # Tiny operands exercise the guard digit. Limit this row to add/subtract.
                emit("guard", left_text, right_text, encoded_decimal(left + right), encoded_decimal(left - right))
                continue
            emit("decimal", left_text, right_text,
                 *(result(op, left, right, encoded_decimal) for op in (
                     operator.add, operator.sub, operator.mul, operator.truediv
                 )), (left > right) - (left < right))

        for left_text, currency, right_text, other_currency in (
            ("0.10", "KRW", "0.20", "KRW"), ("-0.00", "USD", "-0", "USD"),
            ("1.0", "USD", "1.00", "USD"), ("-1.0", "USD", "0", "USD"),
            ("1234567890123456789012345679", "KRW", "0.5", "KRW"),
            ("9999999999999999999999999999", "ZZZ", "0.5", "ZZZ"),
            ("0.10", "USD", "0.10", "KRW"),
        ):
            left, right = Money(decimal(left_text), currency), Money(decimal(right_text), other_currency)
            emit("money", left_text, currency, right_text, other_currency, encode_value(left)["amount"],
                 *(result(op, left, right, lambda value: encode_value(value)["amount"]) for op in (
                     operator.add, operator.sub
                 )), result(lambda a, b: (a > b) - (a < b), left, right, int))

        for token in ("", "KR", "KRWW", "krw", "K0W", "ＫＲＷ", " KRW", "KRW "):
            try:
                Money(Decimal("0"), token)
            except MoneyValidationError:
                emit("currency", token, "INVALID_CURRENCY")
            else:
                raise AssertionError(f"Oracle accepted invalid currency {token!r}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
