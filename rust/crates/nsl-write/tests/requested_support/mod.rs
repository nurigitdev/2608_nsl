#![allow(dead_code)]
use crate::{guard_support as g, support::*};
use nsl_core::*;
use nsl_write::*;
use std::{
    future::Future,
    sync::Mutex,
    task::{Context, Poll, Waker},
};
pub fn baseline() -> ValueData {
    g::rows(include_str!("../fixtures/write-requested-build-oracle.tsv"))
        .next()
        .unwrap()
        .1
}
pub fn intent(d: &ValueData) -> WriteIntent {
    WriteIntent::new(
        &WriteIntentSpec {
            tenant_id: get(d, "tenant_id").into(),
            business_operation_id: get(d, "business_operation_id").into(),
            node_id: get(d, "node_id").into(),
            tool_ref: get(d, "tool_ref").into(),
            tool_id: get(d, "tool_id").into(),
            approval_id: get(d, "approval_id").into(),
            skill_semantic_hash: get(d, "skill_semantic_hash").into(),
            canonical_arguments: arguments(field(d, "canonical_arguments")),
            payload_hash: get(d, "payload_hash").into(),
            write_intent_hash: get(d, "write_intent_hash").into(),
        },
        snapshot(limits()),
    )
    .unwrap()
}
pub fn record_spec(d: &ValueData) -> WriteAttemptRecordSpec {
    WriteAttemptRecordSpec {
        tenant_id: get(d, "tenant_id").into(),
        execution_id: get(d, "execution_id").into(),
        business_operation_id: get(d, "business_operation_id").into(),
        invocation_id: get(d, "invocation_id").into(),
        node_id: get(d, "node_id").into(),
        tool_id: get(d, "tool_id").into(),
        contract_hash: get(d, "contract_hash").into(),
        approval_request_id: get(d, "approval_request_id").into(),
        idempotency_key: get(d, "idempotency_key").into(),
        payload_hash: get(d, "payload_hash").into(),
        write_intent_hash: get(d, "write_intent_hash").into(),
        precondition_evidence_id: get(d, "precondition_evidence_id").into(),
        requested_at: g::instant(d, "requested_at"),
    }
}
pub fn receipt(d: &ValueData) -> Result<WriteRequestedReceipt, WriteExecutionError> {
    WriteRequestedReceipt::new(WriteRequestedReceiptSpec {
        record_hash: get(d, "record_hash").into(),
        durable_ref: get(d, "durable_ref").into(),
        persisted_at: g::instant(d, "persisted_at"),
        durable: g::yes(d, "durable"),
    })
}
pub fn call_spec(d: &ValueData) -> WriteCallRequestSpec {
    WriteCallRequestSpec {
        tenant_id: get(d, "tenant_id").into(),
        execution_id: get(d, "execution_id").into(),
        business_operation_id: get(d, "business_operation_id").into(),
        invocation_id: get(d, "invocation_id").into(),
        node_id: get(d, "node_id").into(),
        tool_ref: get(d, "tool_ref").into(),
        tool_id: get(d, "tool_id").into(),
        contract_hash: get(d, "contract_hash").into(),
        arguments: arguments(field(d, "arguments")),
        idempotency_key: get(d, "idempotency_key").into(),
        payload_hash: get(d, "payload_hash").into(),
        approval_id: get(d, "approval_id").into(),
        approval_request_id: get(d, "approval_request_id").into(),
        write_intent_hash: get(d, "write_intent_hash").into(),
        business_preconditions: g::precondition(field(d, "business_preconditions")).unwrap(),
        trace_refs: arr(field(d, "trace_refs"))
            .iter()
            .map(|v| text(v).into())
            .collect(),
    }
}
pub fn call_data(r: &WriteCallRequest) -> ValueData {
    let s = r.metadata();
    object([
        ("tenant_id", txt(&s.tenant_id)),
        ("execution_id", txt(&s.execution_id)),
        ("business_operation_id", txt(&s.business_operation_id)),
        ("invocation_id", txt(&s.invocation_id)),
        ("node_id", txt(&s.node_id)),
        ("tool_ref", txt(&s.tool_ref)),
        ("tool_id", txt(&s.tool_id)),
        ("contract_hash", txt(&s.contract_hash)),
        ("arguments", encoded(r.arguments())),
        ("idempotency_key", txt(r.idempotency_key().as_str())),
        ("payload_hash", txt(r.payload_hash().as_str())),
        ("approval_id", txt(&s.approval_id)),
        ("approval_request_id", txt(&s.approval_request_id)),
        ("write_intent_hash", txt(&s.write_intent_hash)),
        ("business_preconditions", s.business_preconditions.to_data()),
        (
            "trace_refs",
            ValueData::Array(s.trace_refs.iter().map(txt).collect()),
        ),
    ])
}
pub fn record_data(r: &WriteAttemptRecord) -> ValueData {
    object([
        ("record", r.to_data()),
        ("canonical", txt(hex(r.canonical_bytes()))),
        ("record_hash", txt(r.record_hash().as_str())),
    ])
}
pub struct Setup {
    pub d: ValueData,
    pub i: WriteIntent,
    pub c: WriteContractEvidence,
    pub reservation: IdempotencyReservation,
    pub b: BusinessPreconditionEvidence,
    pub refs: Vec<String>,
}
impl Setup {
    pub fn new(d: ValueData) -> Self {
        let r = field(&d, "reservation");
        let e = field(r, "evidence");
        let evidence = IdempotencyEvidenceRecord::new(IdempotencyEvidenceRecordSpec {
            tenant_id: get(e, "tenant_id").into(),
            business_operation_id: get(e, "business_operation_id").into(),
            write_node_id: get(e, "write_node_id").into(),
            tool_id: get(e, "tool_id").into(),
            contract_hash: get(e, "contract_hash").into(),
            idempotency_key: get(e, "idempotency_key").into(),
            payload_hash: get(e, "payload_hash").into(),
            provider_guarantee_ref: get(e, "provider_guarantee_ref").into(),
            retained_until: g::instant(e, "retained_until"),
        })
        .unwrap();
        let disp = match get(r, "disposition") {
            "CREATED" => IdempotencyDisposition::Created,
            "REUSED" => IdempotencyDisposition::Reused,
            "CONFLICT" => IdempotencyDisposition::Conflict,
            _ => panic!(),
        };
        Self {
            i: intent(field(&d, "intent")),
            c: g::contract_evidence(field(&d, "contract")).unwrap(),
            reservation: IdempotencyReservation::new(
                disp,
                evidence,
                g::optional(r, "business_result_ref"),
            )
            .unwrap(),
            b: g::precondition(field(&d, "precondition")).unwrap(),
            refs: arr(field(&d, "trace_refs"))
                .iter()
                .map(|v| text(v).into())
                .collect(),
            d,
        }
    }
    pub fn build_request(&self) -> WriteCallBuildRequest<'_> {
        let c = field(&self.d, "context");
        WriteCallBuildRequest {
            intent: &self.i,
            context: WriteCallContext {
                tenant_id: get(c, "tenant_id"),
                execution_id: get(c, "execution_id"),
                business_operation_id: if field(c, "business_operation_id") == &ValueData::Null {
                    None
                } else {
                    Some(get(c, "business_operation_id"))
                },
            },
            contract_evidence: &self.c,
            reservation: &self.reservation,
            approval_request_id: get(&self.d, "approval_request_id"),
            invocation_id: get(&self.d, "invocation_id"),
            business_preconditions: &self.b,
            trace_refs: &self.refs,
        }
    }
    pub fn build(&self) -> Result<WriteCallPreparation, WriteExecutionError> {
        WriteCallRequestBuilder.build(self.build_request(), snapshot(limits()))
    }
    pub fn created(&self) -> PreparedWriteCall {
        let WriteCallPreparation::Created(c) = self.build().unwrap() else {
            panic!()
        };
        c
    }
}
pub const NOW: &str = "2026-10-20T03:00:00+00:00";
pub fn now() -> UtcInstant {
    NOW.parse().unwrap()
}
pub fn allowed() -> WriteGuardDecision {
    WriteGuardDecision::new(PreWriteEligibility::Allowed, None).unwrap()
}
pub fn context(flag: &mut bool) -> WriteEvidenceContext<'_> {
    WriteEvidenceContext {
        tenant_id: "tenant-a",
        execution_id: "exec-0053",
        business_operation_id: Some("business-0053"),
        write_request_durable: flag,
    }
}
pub fn ready<F: Future>(f: F) -> F::Output {
    let mut f = std::pin::pin!(f);
    match f.as_mut().poll(&mut Context::from_waker(Waker::noop())) {
        Poll::Ready(v) => v,
        Poll::Pending => panic!("reference adapter must complete immediately"),
    }
}
#[derive(Default)]
pub struct Store {
    pub records: Vec<WriteAttemptRecord>,
    pub events: Vec<String>,
    pub provider_calls: usize,
}
pub struct Evidence {
    pub state: Mutex<Store>,
    pub mode: String,
    pub delta: i32,
}
impl Default for Evidence {
    fn default() -> Self {
        Self {
            state: Mutex::new(Store::default()),
            mode: "valid".into(),
            delta: 0,
        }
    }
}
impl WriteEvidencePort for Evidence {
    fn record_requested<'a>(&'a self, r: &'a WriteAttemptRecord) -> WriteEvidenceFuture<'a> {
        Box::pin(async move {
            let mut s = self.state.lock().unwrap();
            s.events.push("WRITE_REQUESTED".into());
            if self.mode == "fail-before" {
                return Err(WriteEvidencePortError);
            }
            s.records.push(r.clone());
            if matches!(self.mode.as_str(), "raise" | "fail-after") {
                return Err(WriteEvidencePortError);
            }
            let t = match self.delta {
                -1 => "2026-10-20T02:59:59.999999Z".parse().unwrap(),
                1 => "2026-10-20T03:00:00.000001Z".parse().unwrap(),
                _ => r.metadata().requested_at,
            };
            Ok(WriteRequestedReceipt::new(WriteRequestedReceiptSpec {
                record_hash: if self.mode == "mismatch" {
                    format!("sha256:{}", "f".repeat(64))
                } else {
                    r.record_hash().to_string()
                },
                durable_ref: "write-evidence:0053".into(),
                persisted_at: t,
                durable: self.mode != "not_durable",
            })
            .unwrap())
        })
    }
}
/// Test-only spy consumes the private durable handoff. Production provider port
/// and commit/outcome interpretation remain R9; no provider trait is added here.
pub fn invoke(spy: &Evidence, permit: DurableWriteRequest) -> &'static str {
    assert_eq!(
        permit.receipt().metadata().record_hash,
        permit.record().record_hash().as_str()
    );
    assert!(permit.receipt().metadata().durable);
    assert_eq!(
        permit.request().metadata().payload_hash,
        permit.record().metadata().payload_hash
    );
    let mut s = spy.state.lock().unwrap();
    assert!(s.records.iter().any(|r| r == permit.record()));
    s.events.push("PROVIDER_WRITE".into());
    s.provider_calls += 1;
    "raw-provider-response"
}
