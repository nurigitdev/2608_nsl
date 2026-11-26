#![allow(dead_code)]
use crate::{guard_support as g, support::*};
use nsl_core::*;
use nsl_write::*;
use std::collections::BTreeMap;
pub fn identity_spec(d: &ValueData) -> WriteOutcomeIdentitySpec {
    WriteOutcomeIdentitySpec {
        tenant_id: get(d, "tenant_id").into(),
        business_operation_id: get(d, "business_operation_id").into(),
        invocation_id: get(d, "invocation_id").into(),
        node_id: get(d, "node_id").into(),
        tool_id: get(d, "tool_id").into(),
        idempotency_key: get(d, "idempotency_key").into(),
        payload_hash: get(d, "payload_hash").into(),
        approval_request_id: get(d, "approval_request_id").into(),
        write_intent_hash: get(d, "write_intent_hash").into(),
    }
}
pub fn identity_data(v: &WriteOutcomeIdentity) -> ValueData {
    let s = v.metadata();
    object([
        ("tenant_id", txt(&s.tenant_id)),
        ("business_operation_id", txt(&s.business_operation_id)),
        ("invocation_id", txt(&s.invocation_id)),
        ("node_id", txt(&s.node_id)),
        ("tool_id", txt(&s.tool_id)),
        ("idempotency_key", txt(&s.idempotency_key)),
        ("payload_hash", txt(&s.payload_hash)),
        ("approval_request_id", txt(&s.approval_request_id)),
        ("write_intent_hash", txt(&s.write_intent_hash)),
    ])
}
pub fn refs(d: &ValueData) -> Vec<String> {
    arr(field(d, "evidence_refs"))
        .iter()
        .map(|v| text(v).into())
        .collect()
}
pub fn failure(d: &ValueData) -> Result<WriteFailureSpec, WriteExecutionError> {
    Ok(WriteFailureSpec {
        identity: WriteOutcomeIdentity::new(identity_spec(field(d, "identity")))?,
        reason_code: get(d, "reason_code").into(),
        message: get(d, "message").into(),
        provider_transaction_id: g::optional(d, "provider_transaction_id"),
        evidence_refs: refs(d),
    })
}
pub fn result(d: &ValueData) -> Result<WriteResultEnvelope, WriteExecutionError> {
    let mut values = arguments(&object([("value", field(d, "value").clone())]));
    WriteResultEnvelope::new(WriteResultEnvelopeSpec {
        identity: WriteOutcomeIdentity::new(identity_spec(field(d, "identity")))?,
        value: values.remove("value").unwrap(),
        business_reference: get(d, "business_reference").into(),
        provider_transaction_id: g::optional(d, "provider_transaction_id"),
        committed_at: g::instant(d, "committed_at"),
        evidence_refs: refs(d),
    })
}
pub fn result_data(v: &WriteResultEnvelope) -> ValueData {
    let s = v.metadata();
    let value = encoded(&BTreeMap::from([("value".into(), s.value.clone())]));
    object([
        ("identity", identity_data(&s.identity)),
        ("value", field(&value, "value").clone()),
        ("business_reference", txt(&s.business_reference)),
        (
            "provider_transaction_id",
            s.provider_transaction_id
                .as_ref()
                .map(txt)
                .unwrap_or(ValueData::Null),
        ),
        ("committed_at", txt(s.committed_at.to_string())),
        (
            "evidence_refs",
            ValueData::Array(s.evidence_refs.iter().map(txt).collect()),
        ),
    ])
}
pub fn failure_data(s: &WriteFailureSpec) -> ValueData {
    object([
        ("identity", identity_data(&s.identity)),
        ("reason_code", txt(&s.reason_code)),
        ("message", txt(&s.message)),
        (
            "provider_transaction_id",
            s.provider_transaction_id
                .as_ref()
                .map(txt)
                .unwrap_or(ValueData::Null),
        ),
        (
            "evidence_refs",
            ValueData::Array(s.evidence_refs.iter().map(txt).collect()),
        ),
    ])
}
pub fn parse_outcome(d: &ValueData) -> WriteExecutionOutcome {
    let data = field(d, "data");
    match get(d, "kind") {
        "committed" => WriteExecutionOutcome::Committed(result(data).unwrap()),
        "not-committed" => WriteExecutionOutcome::NotCommitted(
            WriteNotCommitted::new(failure(data).unwrap()).unwrap(),
        ),
        "unknown" => {
            WriteExecutionOutcome::Unknown(WriteCommitUnknown::new(failure(data).unwrap()).unwrap())
        }
        _ => panic!(),
    }
}
pub fn outcome_data(v: &WriteExecutionOutcome) -> ValueData {
    let (kind, data) = match v {
        WriteExecutionOutcome::Committed(v) => ("committed", result_data(v)),
        WriteExecutionOutcome::NotCommitted(v) => ("not-committed", failure_data(v.metadata())),
        WriteExecutionOutcome::Unknown(v) => ("unknown", failure_data(v.metadata())),
    };
    object([
        ("kind", txt(kind)),
        ("commit_state", txt(v.commit_state().as_str())),
        ("data", data),
    ])
}
