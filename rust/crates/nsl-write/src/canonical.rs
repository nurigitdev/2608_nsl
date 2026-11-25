use crate::{WriteIntentError, WriteIntentErrorKind, WriteLimits, error, hash, required};
use nsl_core::{JsonLimit, PayloadHash, Value, ValueData, ValueEnvelope, canonical_json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
const PAYLOAD_DOMAIN: &[u8] = b"NSL-WRITE-PAYLOAD-V1\0";
const INTENT_DOMAIN: &[u8] = b"NSL-WRITE-INTENT-V1\0";
fn limited(k: JsonLimit, path: &str) -> WriteIntentError {
    error(
        WriteIntentErrorKind::Limit(k),
        path,
        "WRITE canonical limit exceeded",
    )
}
fn temporal(value: &str, datetime: bool, path: &str) -> Result<String, WriteIntentError> {
    let normalized = if datetime {
        nsl_core::canonical_datetime(value)
    } else {
        nsl_core::normalize_date(value)
    }
    .ok_or_else(|| {
        error(
            WriteIntentErrorKind::Type,
            path,
            "invalid WRITE temporal value",
        )
    })?;
    if datetime && !normalized[10..].contains(['+', '-']) {
        return Err(error(
            WriteIntentErrorKind::Type,
            path,
            "WRITE DateTime must be timezone-aware",
        ));
    }
    Ok(normalized)
}
fn preflight(
    arguments: &BTreeMap<String, ValueEnvelope>,
    limits: WriteLimits,
) -> Result<(), WriteIntentError> {
    let mut nodes = 1usize.saturating_add(arguments.len());
    let mut bytes = 2usize;
    let mut work = Vec::new();
    if nodes > limits.canonical.max_nodes {
        return Err(limited(JsonLimit::Nodes, "$"));
    }
    for (name, envelope) in arguments {
        bytes = bytes.saturating_add(name.len());
        if bytes > limits.canonical.max_bytes {
            return Err(limited(JsonLimit::Bytes, "$"));
        }
        work.push((envelope.value(), 1, format!("$[{name:?}]")));
    }
    while let Some((value, depth, path)) = work.pop() {
        if depth > limits.canonical.max_depth {
            return Err(limited(JsonLimit::Depth, &path));
        }
        let extra = match value {
            Value::Decimal(_) | Value::Date(_) | Value::DateTime(_) => 2,
            Value::Money(_) => 3,
            _ => 0,
        };
        nodes = nodes.saturating_add(extra);
        if nodes > limits.canonical.max_nodes {
            return Err(limited(JsonLimit::Nodes, &path));
        }
        if extra > 0 && depth.saturating_add(1) > limits.canonical.max_depth {
            return Err(limited(JsonLimit::Depth, &path));
        }
        match value {
            Value::String(s) | Value::Date(s) | Value::DateTime(s) => {
                bytes = bytes.saturating_add(s.len())
            }
            Value::Int(n) => bytes = bytes.saturating_add(n.as_str().len()),
            Value::Decimal(d) => {
                bytes = bytes.saturating_add(
                    d.to_fixed_point(limits.max_decimal_bytes.min(limits.canonical.max_bytes))
                        .map_err(|_| limited(JsonLimit::Bytes, &path))?
                        .len(),
                )
            }
            Value::Money(m) => {
                bytes = bytes.saturating_add(
                    m.amount()
                        .to_fixed_point(limits.max_decimal_bytes.min(limits.canonical.max_bytes))
                        .map_err(|_| limited(JsonLimit::Bytes, &path))?
                        .len(),
                )
            }
            Value::List(items) => {
                nodes = nodes.saturating_add(items.len());
                if nodes > limits.canonical.max_nodes {
                    return Err(limited(JsonLimit::Nodes, &path));
                }
                for (i, v) in items.iter().enumerate().rev() {
                    work.push((v, depth.saturating_add(1), format!("{path}[{i}]")));
                }
            }
            Value::Record(fields) => {
                nodes = nodes.saturating_add(fields.len());
                if nodes > limits.canonical.max_nodes {
                    return Err(limited(JsonLimit::Nodes, &path));
                }
                for (k, v) in fields.iter().rev() {
                    bytes = bytes.saturating_add(k.len());
                    if bytes > limits.canonical.max_bytes {
                        return Err(limited(JsonLimit::Bytes, &path));
                    }
                    work.push((v, depth.saturating_add(1), format!("{path}[{k:?}]")));
                }
            }
            Value::Null | Value::Bool(_) => {}
        }
        if bytes > limits.canonical.max_bytes {
            return Err(limited(JsonLimit::Bytes, &path));
        }
    }
    if bytes > limits.canonical.max_bytes {
        return Err(limited(JsonLimit::Bytes, "$"));
    }
    Ok(())
}
fn encode(value: &Value, path: &str, max_decimal: usize) -> Result<ValueData, WriteIntentError> {
    match value {
        Value::Date(s) | Value::DateTime(s) => {
            let datetime = matches!(value, Value::DateTime(_));
            Ok(ValueData::Object(BTreeMap::from([
                (
                    "$type".into(),
                    ValueData::String(if datetime { "DateTime" } else { "Date" }.into()),
                ),
                (
                    "value".into(),
                    ValueData::String(temporal(s, datetime, path)?),
                ),
            ])))
        }
        Value::List(items) => Ok(ValueData::Array(
            items
                .iter()
                .enumerate()
                .map(|(i, v)| encode(v, &format!("{path}[{i}]"), max_decimal))
                .collect::<Result<_, _>>()?,
        )),
        Value::Record(fields) => Ok(ValueData::Object(
            fields
                .iter()
                .map(|(k, v)| {
                    Ok((
                        k.clone(),
                        encode(v, &format!("{path}[{k:?}]"), max_decimal)?,
                    ))
                })
                .collect::<Result<_, _>>()?,
        )),
        _ => value
            .to_data(max_decimal)
            .map_err(|_| limited(JsonLimit::Bytes, path)),
    }
}
pub(crate) fn canonical(
    data: &ValueData,
    limits: WriteLimits,
) -> Result<Vec<u8>, WriteIntentError> {
    let bytes = canonical_json(data).map_err(|_| {
        error(
            WriteIntentErrorKind::IntegerTooWide,
            "$",
            "canonical JSON integer exceeds the Oracle limit",
        )
    })?;
    if bytes.len() > limits.canonical.max_bytes {
        return Err(limited(JsonLimit::Bytes, "$"));
    }
    Ok(bytes)
}
/// Oracle identity includes only envelope values, never TypeRef/status/provenance.
pub fn canonical_write_arguments(
    arguments: &BTreeMap<String, ValueEnvelope>,
    limits: WriteLimits,
) -> Result<Vec<u8>, WriteIntentError> {
    preflight(arguments, limits)?;
    let data = ValueData::Object(
        arguments
            .iter()
            .map(|(k, v)| {
                Ok((
                    k.clone(),
                    encode(v.value(), &format!("$[{k:?}]"), limits.max_decimal_bytes)?,
                ))
            })
            .collect::<Result<_, _>>()?,
    );
    canonical(&data, limits)
}
pub(crate) fn payload_hash(canonical: &[u8]) -> PayloadHash {
    let mut h = Sha256::new();
    h.update(PAYLOAD_DOMAIN);
    h.update(canonical);
    PayloadHash::from_digest(h.finalize().into())
}
pub fn compute_payload_hash(
    arguments: &BTreeMap<String, ValueEnvelope>,
    limits: WriteLimits,
) -> Result<PayloadHash, WriteIntentError> {
    Ok(payload_hash(&canonical_write_arguments(arguments, limits)?))
}
pub fn compute_write_intent_hash(
    skill_semantic_hash: &str,
    write_node_id: &str,
    tool_id: &str,
    payload_hash: &str,
) -> Result<PayloadHash, WriteIntentError> {
    hash(skill_semantic_hash, "$.skill_semantic_hash")?;
    required(write_node_id, "$.write_node_id")?;
    required(tool_id, "$.tool_id")?;
    hash(payload_hash, "$.payload_hash")?;
    let data = ValueData::Object(
        [
            ("skill_semantic_hash", skill_semantic_hash),
            ("write_node_id", write_node_id),
            ("tool_id", tool_id),
            ("payload_hash", payload_hash),
        ]
        .into_iter()
        .map(|(k, v)| (k.into(), ValueData::String(v.into())))
        .collect(),
    );
    let canonical = canonical_json(&data).expect("string-only canonical intent identity");
    let mut h = Sha256::new();
    h.update(INTENT_DOMAIN);
    h.update(canonical);
    Ok(PayloadHash::from_digest(h.finalize().into()))
}
