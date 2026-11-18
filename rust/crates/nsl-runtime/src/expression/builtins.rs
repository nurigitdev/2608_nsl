use super::{ExpressionRuntimeError, operations, primitive, values};
use nsl_core::{MoneySumError, TypeKind, TypeRef, Value, ValueEnvelope, sum_money};

pub(super) fn evaluate(
    name: &str,
    arguments: &[ValueEnvelope],
    expected: &TypeRef,
    node: &str,
) -> Result<Value, ExpressionRuntimeError> {
    let contract = |message| ExpressionRuntimeError::Contract(message);
    if !matches!(name, "count" | "min" | "max" | "sum") {
        return Err(contract(format!("unsupported built-in call: {name}")));
    }
    if arguments.len() != 1 {
        return Err(contract(format!("{name} requires exactly one argument")));
    }
    let TypeKind::List(item) = arguments[0].type_ref().kind() else {
        return Err(contract(format!("{name} requires a List argument")));
    };
    if name == "sum"
        && !primitive(item, "Int")
        && !primitive(item, "Decimal")
        && !matches!(item.kind(), TypeKind::Money(_))
    {
        return Err(contract("sum requires List<Int|Decimal|Money>".into()));
    }
    if (name == "count" && !primitive(expected, "Int"))
        || (name != "count" && expected != item.as_ref())
    {
        return Err(contract(format!(
            "invalid result type for built-in: {name}"
        )));
    }
    let source = arguments[0].value();
    let bad_collection = || contract(format!("{name} cannot evaluate the supplied collection"));
    if matches!(name, "min" | "max") {
        let empty = match source {
            Value::Null => true,
            Value::Bool(v) => !*v,
            Value::Int(v) => v.as_str() == "0",
            Value::Decimal(v) => v == &"0".parse().expect("zero"),
            Value::String(v) => v.is_empty(),
            Value::List(v) => v.is_empty(),
            Value::Record(v) => v.is_empty(),
            _ => false,
        };
        if empty {
            return Err(contract(format!("{name} requires a non-empty collection")));
        }
    }
    if name == "count" {
        let length = match source {
            Value::List(v) => v.len(),
            Value::Record(v) => v.len(),
            Value::String(v) => v.chars().count(),
            _ => return Err(bad_collection()),
        };
        return Ok(Value::Int(crate::data::integer(length)));
    }
    // Python iterates mappings by key and strings by Unicode code point. These
    // states may be supplied to the component with inconsistent envelope data.
    let owned;
    let items = match source {
        Value::List(v) => v.as_slice(),
        Value::Record(v) => {
            owned = v
                .keys()
                .map(|s| Value::String(s.clone()))
                .collect::<Vec<_>>();
            &owned
        }
        Value::String(v) => {
            owned = v
                .chars()
                .map(|c| Value::String(c.to_string()))
                .collect::<Vec<_>>();
            &owned
        }
        _ => return Err(bad_collection()),
    };
    if matches!(name, "min" | "max") {
        let mut iter = items.iter();
        let mut best = iter
            .next()
            .ok_or_else(|| contract(format!("{name} requires a non-empty collection")))?;
        for value in iter {
            let order = if name == "max" {
                values::compare(best, value)
            } else {
                values::compare(value, best)
            }
            .map_err(|e| match e {
                values::ComparisonError::Unordered => bad_collection(),
                e => e.at(node),
            })?;
            if order.is_lt() {
                best = value;
            }
        }
        return Ok(best.clone());
    }
    if let TypeKind::Money(currency) = expected.kind() {
        return sum_money(items, currency)
            .map(Value::Money)
            .map_err(|e| match e {
                MoneySumError::InvalidValue => ExpressionRuntimeError::Money {
                    kind: "MoneyValidationError",
                    message: e.to_string(),
                },
                MoneySumError::CurrencyMismatch { expected, actual } => operations::money_error(
                    nsl_core::MoneyError::CurrencyMismatch,
                    expected.as_str(),
                    actual.as_str(),
                    node,
                ),
                MoneySumError::Decimal(_) => super::failed(node),
            });
    }
    let mut total = if primitive(expected, "Decimal") {
        Value::Decimal("0".parse().expect("zero"))
    } else {
        Value::Int(0.into())
    };
    for value in items {
        if values::decimal(value).is_none() {
            return Err(bad_collection());
        }
        total = operations::binary("ADD", &total, value, expected, node)?;
    }
    Ok(total)
}
