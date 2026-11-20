use crate::{ExecutionContext, ResultEncodingError, RuntimeClock};
use nsl_core::{DataClassification, REDACTED, ValueData};
pub(super) fn sensitive_values<C: RuntimeClock>(
    ctx: &ExecutionContext<C>,
    budget: usize,
) -> Result<Vec<String>, ResultEncodingError> {
    let mut collected = std::collections::BTreeSet::new();
    for value in ctx
        .envelopes()
        .filter(|v| v.classification().rank() >= DataClassification::Confidential.rank())
    {
        let encoded = value.value().to_data(budget)?;
        let mut pending = vec![&encoded];
        while let Some(value) = pending.pop() {
            match value {
                ValueData::Object(fields) => pending.extend(fields.values()),
                ValueData::Array(items) => pending.extend(items),
                ValueData::String(s) if !s.is_empty() => {
                    collected.insert(s.clone());
                }
                ValueData::Number(n) if n.as_str().len() >= 4 => {
                    collected.insert(n.as_str().into());
                }
                _ => {}
            }
        }
    }
    let mut values = collected.into_iter().collect::<Vec<_>>();
    values.sort_by_key(|s| std::cmp::Reverse(s.chars().count()));
    Ok(values)
}
pub(super) fn redact(message: &str, sensitive: &[String]) -> String {
    let mut message = message.to_string();
    for value in sensitive {
        message = message.replace(value, REDACTED);
    }
    nsl_core::redact_text(&message)
}
