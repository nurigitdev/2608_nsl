use super::{ExpressionRuntimeError, failed, mismatch, primitive, values};
use crate::data::{bigint, integer};
use nsl_core::{MoneyError, TypeRef, Value};

pub(super) fn money_error(
    error: MoneyError,
    left: &str,
    right: &str,
    node: &str,
) -> ExpressionRuntimeError {
    match error {
        MoneyError::CurrencyMismatch => ExpressionRuntimeError::Money {
            kind: "CurrencyMismatchError",
            message: format!("currency mismatch: {left} != {right}"),
        },
        MoneyError::Decimal(_) => failed(node),
    }
}

pub(super) fn binary(
    operator: &str,
    left: &Value,
    right: &Value,
    result: &TypeRef,
    node: &str,
) -> Result<Value, ExpressionRuntimeError> {
    // Approved R6-S03 exception: never construct the Oracle's float result for
    // an Int/Int DIV whose external IR falsely declares a non-Decimal result.
    if operator == "DIV"
        && matches!((left, right), (Value::Int(_), Value::Int(_)))
        && !primitive(result, "Decimal")
    {
        return Err(mismatch(node));
    }
    if matches!(operator, "EQ" | "NE") {
        let equal = values::equal(left, right).map_err(|_| failed(node))?;
        return Ok(Value::Bool(if operator == "EQ" { equal } else { !equal }));
    }
    if matches!(operator, "LT" | "LE" | "GT" | "GE") {
        // Python Money implements < and <=; > and >= reflect to the right
        // operand. Preserve even the currency-mismatch operand order.
        let order = if matches!(operator, "GT" | "GE") {
            values::compare(right, left).map(std::cmp::Ordering::reverse)
        } else {
            values::compare(left, right)
        }
        .map_err(|e| e.at(node))?;
        return Ok(Value::Bool(match operator {
            "LT" => order.is_lt(),
            "LE" => order.is_le(),
            "GT" => order.is_gt(),
            _ => order.is_ge(),
        }));
    }
    if let (Value::Money(a), Value::Money(b)) = (left, right) {
        let value = match operator {
            "ADD" => a.checked_add(b),
            "SUB" => a.checked_sub(b),
            _ => return Err(failed(node)),
        };
        return value
            .map(Value::Money)
            .map_err(|e| money_error(e, a.currency().as_str(), b.currency().as_str(), node));
    }
    if operator == "ADD" {
        match (left, right) {
            (Value::String(a), Value::String(b)) => return Ok(Value::String(format!("{a}{b}"))),
            (Value::List(a), Value::List(b)) => {
                return Ok(Value::List(a.iter().chain(b).cloned().collect()));
            }
            _ => {}
        }
    }
    if matches!(operator, "ADD" | "SUB") && matches!(left, Value::Money(_)) {
        return Err(values::money_attribute(right).at(node));
    }
    let integral = |value: &Value| match value {
        Value::Int(v) => Some(bigint(v)),
        Value::Bool(b) => Some(num_bigint::BigInt::from(u8::from(*b))),
        _ => None,
    };
    if operator != "DIV"
        && let (Some(a), Some(b)) = (integral(left), integral(right))
    {
        return Ok(Value::Int(integer(match operator {
            "ADD" => a + b,
            "SUB" => a - b,
            "MUL" => a * b,
            _ => return Err(failed(node)),
        })));
    }
    let a = values::decimal(left).ok_or_else(|| failed(node))?;
    let b = values::decimal(right).ok_or_else(|| failed(node))?;
    let value = match operator {
        "ADD" => a.checked_add(&b),
        "SUB" => a.checked_sub(&b),
        "MUL" => a.checked_mul(&b),
        "DIV" => a.checked_div(&b),
        _ => return Err(failed(node)),
    };
    value.map(Value::Decimal).map_err(|_| failed(node))
}
