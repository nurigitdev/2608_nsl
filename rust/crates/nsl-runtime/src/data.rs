use nsl_core::{CredentialMaterialError, Integer, Value, ValueData, ensure_no_credential_material};
use num_bigint::BigInt;
use std::collections::BTreeMap;

pub(crate) fn bigint(value: &Integer) -> BigInt {
    value.as_str().parse().expect("validated Core integer")
}
pub(crate) fn integer(value: impl std::fmt::Display) -> Integer {
    value.to_string().parse().expect("integer representation")
}
pub(crate) fn whitespace(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}
pub(crate) fn blank(value: &str) -> bool {
    value.trim_matches(whitespace).is_empty()
}
pub(crate) fn object<const N: usize>(items: [(&str, ValueData); N]) -> ValueData {
    ValueData::Object(items.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
pub(crate) fn strings(items: &[String]) -> ValueData {
    ValueData::Array(items.iter().map(|s| ValueData::String(s.clone())).collect())
}
pub(crate) fn text(value: &str) -> ValueData {
    ValueData::String(value.into())
}
pub(crate) fn optional(value: Option<&str>) -> ValueData {
    value.map_or(ValueData::Null, text)
}
pub(crate) fn number(value: &Integer) -> ValueData {
    ValueData::Number(value.clone())
}

// Screen raw request values without expanding Decimal/Money tokens. This uses
// the same Core key/text detector, including its null/empty-string exception.
pub(crate) fn screen(fields: &BTreeMap<String, Value>) -> Result<(), CredentialMaterialError> {
    let mut pending = Vec::new();
    let add = |fields: &BTreeMap<String, Value>| -> Result<(), CredentialMaterialError> {
        for (key, value) in fields {
            let empty =
                matches!(value, Value::Null) || matches!(value,Value::String(s) if s.is_empty());
            ensure_no_credential_material(&object([(
                key.as_str(),
                if empty {
                    ValueData::Null
                } else {
                    ValueData::Bool(true)
                },
            )]))?;
        }
        Ok(())
    };
    add(fields)?;
    pending.extend(fields.values());
    while let Some(value) = pending.pop() {
        match value {
            Value::String(s) => ensure_no_credential_material(&text(s))?,
            Value::Record(fields) => {
                add(fields)?;
                pending.extend(fields.values());
            }
            Value::List(items) => pending.extend(items),
            _ => {}
        }
    }
    Ok(())
}
