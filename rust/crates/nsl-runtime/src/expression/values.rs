use super::{ExpressionRuntimeError, failed, operations};
use nsl_core::{NslDecimal, Value};
use std::cmp::Ordering;

pub(super) fn decimal(value: &Value) -> Option<NslDecimal> {
    match value {
        Value::Decimal(v) => Some(v.clone()),
        Value::Int(v) => v.as_str().parse().ok(),
        Value::Bool(v) => Some(if *v { "1" } else { "0" }.parse().expect("boolean integer")),
        _ => None,
    }
}
pub(super) enum ComparisonError {
    Unordered,
    Currency { left: String, right: String },
    Unexpected { kind: &'static str, detail: String },
}
impl ComparisonError {
    pub(super) fn at(self, node: &str) -> ExpressionRuntimeError {
        match self {
            Self::Unordered => failed(node),
            Self::Currency { left, right } => {
                operations::money_error(nsl_core::MoneyError::CurrencyMismatch, &left, &right, node)
            }
            Self::Unexpected { kind, detail } => {
                ExpressionRuntimeError::Unexpected { kind, detail }
            }
        }
    }
}
pub(super) fn equal(left: &Value, right: &Value) -> Result<bool, ComparisonError> {
    let mut pairs = vec![(left, right)];
    while let Some((left, right)) = pairs.pop() {
        if let (Some(a), Some(b)) = (decimal(left), decimal(right)) {
            if a != b {
                return Ok(false);
            }
            continue;
        }
        let same = match (left, right) {
            (Value::List(a), Value::List(b)) => {
                if a.len() != b.len() {
                    return Ok(false);
                }
                pairs.extend(a.iter().zip(b));
                true
            }
            (Value::Record(a), Value::Record(b)) => {
                if a.len() != b.len() {
                    return Ok(false);
                }
                for (key, value) in a {
                    let Some(other) = b.get(key) else {
                        return Ok(false);
                    };
                    pairs.push((value, other));
                }
                true
            }
            (Value::DateTime(a), Value::DateTime(b)) => {
                let (a, az) = instant(a)?;
                let (b, bz) = instant(b)?;
                az == bz && a == b
            }
            // Python Money dataclass equality includes currency, unlike its
            // checked ordering operations; Core checked_cmp remains unchanged.
            _ => left == right,
        };
        if !same {
            return Ok(false);
        }
    }
    Ok(true)
}
pub(super) fn compare<'a>(
    mut left: &'a Value,
    mut right: &'a Value,
) -> Result<Ordering, ComparisonError> {
    'outer: loop {
        if let (Some(a), Some(b)) = (decimal(left), decimal(right)) {
            return Ok(a.cmp(&b));
        }
        return match (left, right) {
            (Value::String(a), Value::String(b)) | (Value::Date(a), Value::Date(b)) => Ok(a.cmp(b)),
            (Value::DateTime(a), Value::DateTime(b)) => {
                let (a, az) = instant(a)?;
                let (b, bz) = instant(b)?;
                if az != bz {
                    Err(ComparisonError::Unordered)
                } else {
                    Ok(a.cmp(&b))
                }
            }
            (Value::Money(a), Value::Money(b)) => {
                a.checked_cmp(b).map_err(|_| ComparisonError::Currency {
                    left: a.currency().as_str().into(),
                    right: b.currency().as_str().into(),
                })
            }
            (Value::Money(_), other) => Err(money_attribute(other)),
            (Value::List(a), Value::List(b)) => {
                for (x, y) in a.iter().zip(b) {
                    if !equal(x, y)? {
                        left = x;
                        right = y;
                        continue 'outer;
                    }
                }
                Ok(a.len().cmp(&b.len()))
            }
            _ => Err(ComparisonError::Unordered),
        };
    }
}

pub(super) fn money_attribute(value: &Value) -> ComparisonError {
    let name = match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Int(_) => "int",
        Value::String(_) => "str",
        Value::Decimal(_) => "decimal.Decimal",
        Value::Money(_) => "Money",
        Value::Date(_) => "datetime.date",
        Value::DateTime(_) => "datetime.datetime",
        Value::List(_) => "list",
        Value::Record(_) => "dict",
    };
    ComparisonError::Unexpected {
        kind: "AttributeError",
        detail: format!("'{name}' object has no attribute 'currency'"),
    }
}

// Core DateTime values hold ISO text, not a timezone framework object. Compare
// Python isoformat() representations at microsecond precision, preserving the
// distinction between naive and fixed-offset aware values. Security/evidence
// timestamp admission is a different boundary.
fn instant(text: &str) -> Result<(i128, bool), ComparisonError> {
    let invalid = || ComparisonError::Unordered;
    let (date, time) = text.split_once('T').ok_or_else(invalid)?;
    let mut date = date.split('-');
    let year = number(date.next().ok_or_else(invalid)?)?;
    let month = number(date.next().ok_or_else(invalid)?)?;
    let day = number(date.next().ok_or_else(invalid)?)?;
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    if date.next().is_some()
        || !(1..=9999).contains(&year)
        || !(1..=12).contains(&month)
        || !(1..=days[(month.clamp(1, 12) - 1) as usize]).contains(&day)
    {
        return Err(invalid());
    }
    let ordinal = 365 * (year - 1) + (year - 1) / 4 - (year - 1) / 100
        + (year - 1) / 400
        + days[..(month - 1) as usize].iter().sum::<i128>()
        + day;
    let (time, offset) = if let Some(index) = time.find(['+', '-']) {
        (&time[..index], Some(&time[index..]))
    } else if let Some(time) = time.strip_suffix('Z') {
        (time, Some("+00:00"))
    } else {
        (time, None)
    };
    let micros = time_micros(time)?;
    let offset = offset
        .map(|text| {
            let sign = if text.starts_with('-') { -1 } else { 1 };
            time_micros(&text[1..]).map(|n| sign * n)
        })
        .transpose()?;
    Ok((
        ordinal * 86_400_000_000 + micros - offset.unwrap_or(0),
        offset.is_some(),
    ))
}
fn number(text: &str) -> Result<i128, ComparisonError> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(ComparisonError::Unordered);
    }
    text.parse().map_err(|_| ComparisonError::Unordered)
}
fn time_micros(text: &str) -> Result<i128, ComparisonError> {
    let mut fields = text.split(':');
    let hours = number(fields.next().ok_or(ComparisonError::Unordered)?)?;
    let minutes = number(fields.next().ok_or(ComparisonError::Unordered)?)?;
    let seconds = fields.next().unwrap_or("0");
    let (seconds, fraction) = seconds.split_once('.').unwrap_or((seconds, ""));
    let seconds = number(seconds)?;
    if fields.next().is_some() || hours > 23 || minutes > 59 || seconds > 59 || fraction.len() > 6 {
        return Err(ComparisonError::Unordered);
    }
    let fraction = if fraction.is_empty() {
        0
    } else {
        number(fraction)? * 10_i128.pow(6 - fraction.len() as u32)
    };
    Ok(((hours * 60 + minutes) * 60 + seconds) * 1_000_000 + fraction)
}
