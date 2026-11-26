mod guard_support;
mod requested_support;
mod support;
use guard_support as g;
use nsl_core::*;
use nsl_write::*;
use requested_support as r;
use std::{
    collections::BTreeMap,
    future::Future,
    sync::{
        Mutex,
        atomic::{AtomicUsize, Ordering},
    },
    task::{Context, Poll, Waker},
};
use support::*;

fn identity_spec(d: &ValueData) -> WriteOutcomeIdentitySpec {
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
fn identity_data(v: &WriteOutcomeIdentity) -> ValueData {
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
fn refs(d: &ValueData) -> Vec<String> {
    arr(field(d, "evidence_refs"))
        .iter()
        .map(|v| text(v).into())
        .collect()
}
fn failure(d: &ValueData) -> Result<WriteFailureSpec, WriteExecutionError> {
    Ok(WriteFailureSpec {
        identity: WriteOutcomeIdentity::new(identity_spec(field(d, "identity")))?,
        reason_code: get(d, "reason_code").into(),
        message: get(d, "message").into(),
        provider_transaction_id: g::optional(d, "provider_transaction_id"),
        evidence_refs: refs(d),
    })
}
fn result(d: &ValueData) -> Result<WriteResultEnvelope, WriteExecutionError> {
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
fn result_data(v: &WriteResultEnvelope) -> ValueData {
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
fn failure_data(s: &WriteFailureSpec) -> ValueData {
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
fn parse_outcome(d: &ValueData) -> WriteExecutionOutcome {
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
fn outcome_data(v: &WriteExecutionOutcome) -> ValueData {
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
#[test]
fn outcome_identity_envelope_and_states_match_frozen_oracle_boundaries() {
    let mut count = 0;
    for (name, d, e) in g::rows(include_str!("fixtures/write-invocation-models-oracle.tsv")) {
        let v = field(&d, "value");
        let observed = match get(&d, "kind") {
            "identity" => WriteOutcomeIdentity::new(identity_spec(v)).map(|v| identity_data(&v)),
            "result" => result(v).map(|v| result_data(&v)),
            "not-committed" => failure(v)
                .and_then(WriteNotCommitted::new)
                .map(|v| failure_data(v.metadata())),
            "unknown" => failure(v)
                .and_then(WriteCommitUnknown::new)
                .map(|v| failure_data(v.metadata())),
            _ => panic!(),
        };
        let expected = arr(&e);
        match observed {
            Ok(v) => {
                assert_eq!(text(&expected[0]), "ok", "{name}");
                assert_eq!(v, expected[1], "{name}");
            }
            Err(error) => {
                assert_eq!(text(&expected[0]), "error", "{name}");
                assert_eq!(
                    error.detail_code().map(txt).unwrap_or(ValueData::Null),
                    expected[3],
                    "{name}"
                );
                assert!(error.path.starts_with("$."));
                assert!(!format!("{error:?} {error}").contains("synthetic-only"));
            }
        }
        count += 1;
    }
    assert_eq!(count, 155);
}
struct Provider {
    case: String,
    expected: ValueData,
    events: Mutex<Vec<String>>,
    calls: AtomicUsize,
}
impl WriteExecutionPort for Provider {
    fn execute<'a>(&'a self, request: &'a WriteCallRequest) -> WriteExecutionFuture<'a> {
        Box::pin(async move {
            self.calls.fetch_add(1, Ordering::SeqCst);
            self.events.lock().unwrap().push("PROVIDER_WRITE".into());
            assert_eq!(r::call_data(request), *field(&self.expected, "request"));
            match self.case.as_str() {
                "committed" | "not-committed" | "unknown" | "foreign-identity" => {
                    Ok(parse_outcome(field(&self.expected, "outcome")))
                }
                "timeout" => Err(WriteProviderError::Timeout),
                "cancelled" => Err(WriteProviderError::Cancelled),
                "connection" => Err(WriteProviderError::ConnectionLost),
                "exception" => Err(WriteProviderError::Exception),
                "malformed" => Err(WriteProviderError::MalformedAcknowledgement),
                _ => panic!(),
            }
        })
    }
}
#[test]
fn one_awaited_provider_call_preserves_outcomes_and_maps_uncertainty_like_oracle() {
    let mut count = 0;
    for (name, _, e) in g::rows(include_str!(
        "fixtures/write-invocation-invocation-oracle.tsv"
    )) {
        let expected = arr(&e)[1].clone();
        let s = r::Setup::new(r::baseline());
        let evidence = r::Evidence::default();
        let mut flag = false;
        let permit = r::ready(WriteRequestedCoordinator::new(&evidence).persist(
            s.created(),
            &r::allowed(),
            r::context(&mut flag),
            r::now(),
        ))
        .unwrap();
        assert!(flag);
        let provider = Provider {
            case: name.clone(),
            expected: expected.clone(),
            events: Mutex::new(evidence.state.lock().unwrap().events.clone()),
            calls: AtomicUsize::new(0),
        };
        let invoked = r::ready(WriteInvocationCoordinator::new(&provider).invoke(permit));
        let observed = object([
            (
                "events",
                ValueData::Array(provider.events.lock().unwrap().iter().map(txt).collect()),
            ),
            (
                "calls",
                ValueData::Number((provider.calls.load(Ordering::SeqCst) as i64).into()),
            ),
            ("flag", ValueData::Bool(flag)),
            ("request", r::call_data(invoked.request())),
            (
                "requested_record_hash",
                txt(invoked.requested_record().record_hash().as_str()),
            ),
            ("outcome", outcome_data(invoked.outcome())),
        ]);
        assert_eq!(observed, expected, "{name}");
        assert_eq!(
            invoked.requested_receipt().metadata().record_hash,
            invoked.requested_record().record_hash().as_str()
        );
        assert_eq!(provider.calls.load(Ordering::SeqCst), 1);
        assert_eq!(evidence.state.lock().unwrap().records.len(), 1);
        if name == "foreign-identity" {
            // Raw invocation observation only. Final identity admission and
            // durable outcome recording are R9-S03, as in the frozen Oracle.
            assert_ne!(
                invoked.outcome().identity(),
                &WriteOutcomeIdentity::from_request(invoked.request())
            );
        } else {
            assert_eq!(
                invoked.outcome().identity(),
                &WriteOutcomeIdentity::from_request(invoked.request())
            );
        }
        assert!(!format!("{invoked:?}").contains("DRAFT-0054"));
        count += 1;
    }
    assert_eq!(count, 9);
}
struct PendingProvider(AtomicUsize);
impl WriteExecutionPort for PendingProvider {
    fn execute<'a>(&'a self, _: &'a WriteCallRequest) -> WriteExecutionFuture<'a> {
        self.0.fetch_add(1, Ordering::SeqCst);
        Box::pin(std::future::pending())
    }
}
#[test]
fn lazy_invocation_and_dropped_pending_futures_do_not_retry_or_infer_rollback() {
    for poll in [false, true] {
        let s = r::Setup::new(r::baseline());
        let evidence = r::Evidence::default();
        let mut flag = false;
        let permit = r::ready(WriteRequestedCoordinator::new(&evidence).persist(
            s.created(),
            &r::allowed(),
            r::context(&mut flag),
            r::now(),
        ))
        .unwrap();
        let provider = PendingProvider(AtomicUsize::new(0));
        let coordinator = WriteInvocationCoordinator::new(&provider);
        let mut invocation = Box::pin(coordinator.invoke(permit));
        assert_eq!(provider.0.load(Ordering::SeqCst), 0);
        if poll {
            let mut cx = Context::from_waker(Waker::noop());
            assert!(matches!(invocation.as_mut().poll(&mut cx), Poll::Pending));
            assert!(matches!(invocation.as_mut().poll(&mut cx), Poll::Pending));
        }
        drop(invocation);
        assert_eq!(provider.0.load(Ordering::SeqCst), usize::from(poll));
        assert!(flag);
        assert_eq!(evidence.state.lock().unwrap().records.len(), 1);
    }
}
#[test]
fn production_invocation_is_unreachable_after_guard_binding_or_evidence_failure() {
    struct FailureProvider(AtomicUsize);
    impl WriteExecutionPort for FailureProvider {
        fn execute<'a>(&'a self, _: &'a WriteCallRequest) -> WriteExecutionFuture<'a> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Box::pin(async { Err(WriteProviderError::Exception) })
        }
    }
    async fn pipeline(
        provider: &dyn WriteExecutionPort,
        evidence: &r::Evidence,
        setup: &r::Setup,
        decision: &WriteGuardDecision,
        context: WriteEvidenceContext<'_>,
    ) -> Result<WriteInvocation, WriteExecutionError> {
        let durable = WriteRequestedCoordinator::new(evidence)
            .persist(setup.created(), decision, context, r::now())
            .await?;
        Ok(WriteInvocationCoordinator::new(provider)
            .invoke(durable)
            .await)
    }
    let provider = FailureProvider(AtomicUsize::new(0));
    for mode in ["raise", "mismatch", "not_durable"] {
        for old_flag in [false, true] {
            let s = r::Setup::new(r::baseline());
            let evidence = r::Evidence {
                mode: mode.into(),
                ..Default::default()
            };
            let mut flag = old_flag;
            let persisted = r::ready(pipeline(
                &provider,
                &evidence,
                &s,
                &r::allowed(),
                r::context(&mut flag),
            ));
            assert!(persisted.is_err());
            assert_eq!(flag, old_flag);
        }
    }
    let s = r::Setup::new(r::baseline());
    let evidence = r::Evidence::default();
    let mut flag = false;
    let blocked = WriteGuardDecision::new(PreWriteEligibility::Blocked, None).unwrap();
    assert!(
        r::ready(pipeline(
            &provider,
            &evidence,
            &s,
            &blocked,
            r::context(&mut flag),
        ))
        .is_err()
    );
    assert!(evidence.state.lock().unwrap().records.is_empty());
    let mut context = r::context(&mut flag);
    context.tenant_id = "other-tenant";
    assert!(r::ready(pipeline(&provider, &evidence, &s, &r::allowed(), context,)).is_err());
    assert!(!flag);
    assert_eq!(provider.0.load(Ordering::SeqCst), 0);
    let result = r::ready(pipeline(
        &provider,
        &evidence,
        &s,
        &r::allowed(),
        r::context(&mut flag),
    ))
    .unwrap();
    assert_eq!(
        result.outcome().commit_state(),
        CommitState::UnknownCommitState
    );
    assert!(flag);
    assert_eq!(provider.0.load(Ordering::SeqCst), 1);
}
#[test]
fn model_debugs_hide_owned_business_values_and_identity_changes_remain_explicit() {
    let request = r::Setup::new(r::baseline()).created();
    let identity = WriteOutcomeIdentity::from_request(request.request());
    let spec = identity.metadata().clone();
    assert_eq!(WriteOutcomeIdentity::new(spec.clone()).unwrap(), identity);
    assert!(!format!("{spec:?} {identity:?}").contains("tenant-a"));
    for field in [
        "tenant",
        "business",
        "invocation",
        "node",
        "tool",
        "key",
        "payload",
        "approval",
        "intent",
    ] {
        let mut s = spec.clone();
        match field {
            "tenant" => s.tenant_id.push_str("-other"),
            "business" => s.business_operation_id.push_str("-other"),
            "invocation" => s.invocation_id.push_str("-other"),
            "node" => s.node_id.push_str("-other"),
            "tool" => s.tool_id.push_str("-other"),
            "key" => s.idempotency_key = format!("idem:sha256:{}", "x".repeat(64)),
            "payload" => s.payload_hash = format!("sha256:{}", "f".repeat(64)),
            "approval" => s.approval_request_id.push_str("-other"),
            "intent" => s.write_intent_hash = format!("sha256:{}", "f".repeat(64)),
            _ => unreachable!(),
        }
        assert_ne!(WriteOutcomeIdentity::new(s).unwrap(), identity);
    }
    let spec = WriteResultEnvelopeSpec {
        identity,
        value: ValueEnvelope::complete(
            Value::String("private-business-value".into()),
            TypeRef::new(TypeKind::Primitive("String".into())).unwrap(),
            DataClassification::Confidential,
        ),
        business_reference: "private-reference".into(),
        provider_transaction_id: None,
        committed_at: r::now(),
        evidence_refs: vec![],
    };
    assert!(!format!("{spec:?}").contains("private"));
    let result = WriteResultEnvelope::new(spec).unwrap();
    assert!(!format!("{result:?}").contains("private"));
    let outcome = WriteExecutionOutcome::Committed(result);
    assert!(!format!("{outcome:?}").contains("private"));
    for unknown in [false, true] {
        let spec = WriteFailureSpec {
            identity: WriteOutcomeIdentity::from_request(request.request()),
            reason_code: "BUSINESS_REASON".into(),
            message: "private-message".into(),
            provider_transaction_id: None,
            evidence_refs: vec![],
        };
        assert!(!format!("{spec:?}").contains("private"));
        let debug = if unknown {
            format!("{:?}", WriteCommitUnknown::new(spec).unwrap())
        } else {
            format!("{:?}", WriteNotCommitted::new(spec).unwrap())
        };
        assert!(!debug.contains("private"));
    }
}
