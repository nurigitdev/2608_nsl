use std::cmp::Ordering;
use std::collections::BTreeMap;

use nsl_core::{
    CurrencyCode, DecimalError, Money, MoneyError, NslDecimal, Value, ValueData, ValueError,
};

const ORACLE_FIXTURE: &str = include_str!("fixtures/decimal-money.tsv");
const TOKEN_BUDGET: usize = 1_048_576;

fn decimal(text: &str) -> NslDecimal {
    text.parse().unwrap()
}

fn money(amount: &str, currency: &str) -> Money {
    Money::new(decimal(amount), currency.parse().unwrap())
}

fn fixed(decimal: &NslDecimal) -> String {
    decimal.to_fixed_point(TOKEN_BUDGET).unwrap()
}

fn order_text(order: Ordering) -> &'static str {
    match order {
        Ordering::Less => "-1",
        Ordering::Equal => "0",
        Ordering::Greater => "1",
    }
}

fn assert_decimal(actual: Result<NslDecimal, DecimalError>, expected: &str, row: &str) {
    match expected {
        "INVALID_TEXT" => assert_eq!(actual, Err(DecimalError::InvalidText), "{row}"),
        "DIVISION_BY_ZERO" => assert_eq!(actual, Err(DecimalError::DivisionByZero), "{row}"),
        "OVERFLOW" => assert_eq!(actual, Err(DecimalError::Overflow), "{row}"),
        text => {
            let actual = actual.unwrap();
            assert_eq!(fixed(&actual), text, "{row}");
            let roundtrip = decimal(text);
            assert_eq!(roundtrip, actual, "{row}");
            assert_eq!(fixed(&roundtrip), text, "{row}");
        }
    }
}

#[test]
fn frozen_decimal_parse_encoding_arithmetic_and_order() {
    for row in ORACLE_FIXTURE.lines() {
        let fields: Vec<_> = row.split('\t').collect();
        match fields.as_slice() {
            ["parse", input, expected] => assert_decimal(input.parse(), expected, row),
            [
                "decimal",
                left,
                right,
                sum,
                difference,
                product,
                quotient,
                order,
            ] => {
                let (left, right) = (decimal(left), decimal(right));
                assert_decimal(left.checked_add(&right), sum, row);
                assert_decimal(left.checked_sub(&right), difference, row);
                assert_decimal(left.checked_mul(&right), product, row);
                assert_decimal(left.checked_div(&right), quotient, row);
                assert_eq!(order_text(left.cmp(&right)), *order, "{row}");
                assert_eq!(left == right, *order == "0", "{row}");
            }
            ["guard", left, right, sum, difference] => {
                let (left, right) = (decimal(left), decimal(right));
                assert_decimal(left.checked_add(&right), sum, row);
                assert_decimal(left.checked_sub(&right), difference, row);
            }
            _ if row.starts_with('#')
                || matches!(fields.first(), Some(&("money" | "currency"))) => {}
            _ => panic!("invalid fixture row: {row}"),
        }
    }
}

#[test]
fn frozen_money_currency_arithmetic_and_order() {
    for row in ORACLE_FIXTURE.lines() {
        let fields: Vec<_> = row.split('\t').collect();
        match fields.as_slice() {
            [
                "money",
                left,
                currency,
                right,
                other_currency,
                encoded,
                sum,
                difference,
                order,
            ] => {
                let (left, right) = (money(left, currency), money(right, other_currency));
                assert_eq!(
                    Value::Money(left.clone()).to_data(TOKEN_BUDGET).unwrap(),
                    ValueData::Object(BTreeMap::from([
                        ("$type".into(), "Money".into()),
                        ("amount".into(), (*encoded).into()),
                        ("currency".into(), (*currency).into()),
                    ])),
                    "{row}"
                );
                let added = left.checked_add(&right);
                let subtracted = left.checked_sub(&right);
                let compared = left.checked_cmp(&right);
                if *currency == *other_currency {
                    assert_eq!(order_text(compared.unwrap()), *order, "{row}");
                    for (actual, expected) in [(added, sum), (subtracted, difference)] {
                        let actual = actual.unwrap();
                        assert_eq!(actual.currency().as_str(), *currency, "{row}");
                        assert_decimal(Ok(actual.amount().clone()), expected, row);
                    }
                } else {
                    assert_eq!([*sum, *difference, *order], ["CURRENCY_MISMATCH"; 3]);
                    assert_eq!(added, Err(MoneyError::CurrencyMismatch), "{row}");
                    assert_eq!(subtracted, Err(MoneyError::CurrencyMismatch), "{row}");
                    assert_eq!(compared, Err(MoneyError::CurrencyMismatch), "{row}");
                }
            }
            ["currency", currency, "INVALID_CURRENCY"] => {
                assert_eq!(
                    currency.parse::<CurrencyCode>(),
                    Err(ValueError::InvalidCurrency),
                    "{row}"
                );
            }
            _ if row.starts_with('#')
                || matches!(fields.first(), Some(&("parse" | "decimal" | "guard"))) => {}
            _ => panic!("invalid fixture row: {row}"),
        }
    }
}

#[test]
fn addition_and_subtraction_are_exact_for_representable_values() {
    for left in [-10_000_i64, -11, -1, 0, 1, 11, 10_000] {
        for right in [-10_000_i64, -11, -1, 0, 1, 11, 10_000] {
            for scale in -3..=6 {
                let a = decimal(&format!("{left}e{scale}"));
                let b = decimal(&format!("{right}e{scale}"));
                let sum = a.checked_add(&b).unwrap();
                let difference = a.checked_sub(&b).unwrap();
                assert_eq!(sum, decimal(&format!("{}e{scale}", left + right)));
                assert_eq!(difference, decimal(&format!("{}e{scale}", left - right)));
                assert_eq!(sum.checked_sub(&b).unwrap(), a);
                assert_eq!(difference.checked_add(&b).unwrap(), a);
                assert_eq!(fixed(&sum), fixed(&b.checked_add(&a).unwrap()));
                for original in [&a, &b, &sum, &difference] {
                    let encoded = fixed(original);
                    assert_eq!(&decimal(&encoded), original);
                    assert_eq!(fixed(&decimal(&encoded)), encoded);
                }
            }
        }
    }
}

#[test]
fn same_currency_money_preserves_exact_amounts_and_currency() {
    for currency in ["KRW", "USD", "ZZZ"] {
        for left in [-10_000_i64, -1, 0, 1, 10_000] {
            for right in [-10_000_i64, -1, 0, 1, 10_000] {
                let first = money(&format!("{left}e-2"), currency);
                let second = money(&format!("{right}e-2"), currency);
                let sum = first.checked_add(&second).unwrap();
                let difference = first.checked_sub(&second).unwrap();
                assert_eq!(sum, money(&format!("{}e-2", left + right), currency));
                assert_eq!(difference, money(&format!("{}e-2", left - right), currency));
                assert_eq!(sum.checked_sub(&second).unwrap(), first);
                assert_eq!(difference.checked_add(&second).unwrap(), first);
                assert_eq!(first.checked_cmp(&second).unwrap(), left.cmp(&right));
                for actual in [&sum, &difference] {
                    let encoded = fixed(actual.amount());
                    assert_eq!(&money(&encoded, currency), actual);
                    assert_eq!(fixed(money(&encoded, currency).amount()), encoded);
                }
            }
        }
    }
}

#[test]
fn exponent_limits_and_fixed_point_allocation_are_checked() {
    let one = decimal("1");
    let zero = decimal("0");
    let maximum_power = decimal("1E+999999");
    assert_eq!(maximum_power.checked_mul(&one).unwrap(), maximum_power);
    assert_eq!(
        maximum_power.checked_mul(&decimal("10")),
        Err(DecimalError::Overflow)
    );
    assert_eq!(
        decimal("1E+1000000").checked_add(&zero),
        Err(DecimalError::Overflow)
    );
    let carry = decimal("9.9999999999999999999999999995E+999999");
    assert_eq!(carry.checked_add(&zero), Err(DecimalError::Overflow));

    let minimum_quantum = decimal("1E-1000026");
    let even_tie = decimal("-5E-1000027").checked_add(&zero).unwrap();
    assert_eq!(even_tie, zero);
    let encoded = fixed(&even_tie);
    assert_eq!(encoded.len(), 1_000_029);
    assert!(encoded.starts_with("-0."));
    assert!(encoded[3..].bytes().all(|byte| byte == b'0'));
    assert_eq!(
        decimal("6E-1000027").checked_add(&zero).unwrap(),
        minimum_quantum
    );
    assert_eq!(
        decimal("3E-1000026").checked_div(&decimal("2")).unwrap(),
        decimal("2E-1000026")
    );
    assert_eq!(
        decimal("1E-1999999999999999997").checked_add(&one).unwrap(),
        one
    );
    assert!(decimal("1E+999999999999999999") > maximum_power);

    let scaled = decimal("-0.001");
    assert_eq!(scaled.to_fixed_point(6).unwrap(), "-0.001");
    assert_eq!(scaled.to_fixed_point(5), Err(DecimalError::EncodingLimit));
    assert_eq!(scaled.to_fixed_point(7).unwrap(), "-0.001");
    assert_eq!(zero.to_fixed_point(0), Err(DecimalError::EncodingLimit));
    assert_eq!(zero.to_fixed_point(1).unwrap(), "0");
    let huge = Value::Decimal(decimal("1E+999999999999999999"));
    let nested = Value::List(vec![Value::Record(BTreeMap::from([(
        "large".into(),
        huge,
    )]))]);
    assert_eq!(
        nested.to_data(TOKEN_BUDGET),
        Err(DecimalError::EncodingLimit)
    );
    let huge_money = Value::Money(money("1E-1999999999999999997", "KRW"));
    assert_eq!(
        huge_money.to_data(TOKEN_BUDGET),
        Err(DecimalError::EncodingLimit)
    );
}
