mod guard_support;
mod requested_support;
mod support;
use guard_support as g;
use nsl_core::*;
use nsl_write::*;
use requested_support::*;
use std::{
    future::Future,
    task::{Context, Poll, Waker},
};
use support::{arr, field, get, txt};
fn check(name: &str, result: Result<ValueData, WriteExecutionError>, expected: &ValueData) {
    let e = arr(expected);
    match result {
        Ok(value) => {
            assert_eq!(support::text(&e[0]), "ok", "{name}");
            assert_eq!(value, e[1], "{name}");
        }
        Err(error) => {
            assert_eq!(support::text(&e[0]), "error", "{name}: {error:?}");
            assert!(error.path.starts_with("$."));
            assert!(!error.to_string().contains("Bearer"));
            assert!(std::error::Error::source(&error).is_none());
            assert_eq!(
                error.detail_code().map(txt).unwrap_or(ValueData::Null),
                e[3],
                "{name}"
            );
        }
    }
}
#[test]
fn frozen_call_record_receipt_models_and_admission_match_oracle() {
    for (name, d, e) in g::rows(include_str!("fixtures/write-requested-models-oracle.tsv")) {
        let v = field(&d, "value");
        let result = match get(&d, "kind") {
            "call" => WriteCallRequest::new(&call_spec(v), support::snapshot(support::limits()))
                .map(|r| call_data(&r)),
            "record" => WriteAttemptRecord::new(record_spec(v)).map(|r| record_data(&r)),
            "receipt" => receipt(v).map(|r| r.to_data()),
            _ => panic!(),
        };
        check(&name, result, &e);
    }
}
#[test]
fn created_request_builder_binding_and_argument_snapshots_match_oracle() {
    for (name, d, e) in g::rows(include_str!("fixtures/write-requested-build-oracle.tsv")) {
        let s = Setup::new(d);
        let result = s.build().map(|v| match v {
            WriteCallPreparation::Created(p) => call_data(p.request()),
            other => panic!("{other:?}"),
        });
        check(&name, result, &e);
    }
}
#[test]
fn record_canonical_bytes_domain_and_all_thirteen_identity_fields_match_oracle() {
    let mut hashes = std::collections::BTreeSet::new();
    let base = WriteAttemptRecord::from_request(Setup::new(baseline()).created().request(), now());
    for (name, d, e) in g::rows(include_str!("fixtures/write-requested-records-oracle.tsv")) {
        let record = WriteAttemptRecord::new(record_spec(&d)).unwrap();
        assert_eq!(record_data(&record), arr(&e)[1], "{name}");
        if name.starts_with("changed-") {
            assert_ne!(record.record_hash(), base.record_hash(), "{name}");
            assert!(hashes.insert(record.record_hash().to_string()));
        }
        if name.starts_with("omitted-") {
            assert_eq!(record.record_hash(), base.record_hash());
        }
    }
    assert_eq!(hashes.len(), 13);
    assert_eq!(
        base.record_hash(),
        WriteAttemptRecord::from_request(Setup::new(baseline()).created().request(), now())
            .record_hash()
    );
}
#[test]
fn persistence_receipt_and_guard_failures_never_reach_provider_spy() {
    for (name, d, e) in g::rows(include_str!(
        "fixtures/write-requested-durability-oracle.tsv"
    )) {
        let s = Setup::new(baseline());
        let spy = Evidence {
            mode: get(&d, "mode").into(),
            delta: match field(&d, "delta") {
                ValueData::Number(n) => n.as_str().parse().unwrap(),
                _ => panic!(),
            },
            ..Default::default()
        };
        let mut flag = g::yes(&d, "flag");
        let decision =
            WriteGuardDecision::new(get(&d, "eligibility").parse().unwrap(), None).unwrap();
        let mut ctx = context(&mut flag);
        let override_ctx = field(&d, "context");
        if let Some(v) = g::opt(override_ctx, "tenant_id") {
            ctx.tenant_id = support::text(v);
        }
        if let Some(v) = g::opt(override_ctx, "execution_id") {
            ctx.execution_id = support::text(v);
        }
        if let Some(v) = g::opt(override_ctx, "business_operation_id") {
            ctx.business_operation_id = Some(support::text(v));
        }
        let result =
            ready(WriteRequestedCoordinator::new(&spy).persist(s.created(), &decision, ctx, now()));
        let expected = &arr(&e)[1];
        let observed = result.map(|permit| {
            assert!(flag);
            txt(invoke(&spy, permit))
        });
        check(&name, observed, field(expected, "result"));
        let state = spy.state.lock().unwrap();
        assert_eq!(flag, g::yes(expected, "flag"), "{name}");
        assert_eq!(
            ValueData::Array(state.events.iter().map(txt).collect()),
            *field(expected, "events"),
            "{name}"
        );
        assert_eq!(
            ValueData::Array(state.records.iter().map(record_data).collect()),
            *field(expected, "records"),
            "{name}"
        );
        assert_eq!(
            state.provider_calls.to_string(),
            match field(expected, "provider_calls") {
                ValueData::Number(n) => n.as_str(),
                _ => panic!(),
            },
            "{name}"
        );
    }
}
#[test]
fn approved_conflict_and_reused_dispositions_preserve_original_oracle_observations() {
    for (name, d, oracle) in g::rows(include_str!(
        "fixtures/write-requested-dispositions-oracle.tsv"
    )) {
        let original = &arr(&oracle)[1];
        assert_eq!(
            field(original, "provider_calls"),
            &ValueData::Number("1".parse().unwrap()),
            "{name}"
        );
        let native = get(&d, "native").to_owned();
        let s = Setup::new(d);
        let spy = Evidence::default();
        let mut flag = false;
        match s.build() {
            Ok(WriteCallPreparation::Created(p)) => {
                assert_eq!(native, "CREATED");
                assert_eq!(call_data(p.request()), *field(original, "request"));
                let permit = ready(WriteRequestedCoordinator::new(&spy).persist(
                    p,
                    &allowed(),
                    context(&mut flag),
                    now(),
                ))
                .unwrap();
                assert!(flag);
                invoke(&spy, permit);
                assert_eq!(spy.state.lock().unwrap().provider_calls, 1);
            }
            Ok(WriteCallPreparation::Reused(r)) => {
                assert_eq!(native, "REUSED");
                assert_eq!(r, s.reservation);
                assert_eq!(r.business_result_ref(), Some("result:original"));
            }
            Ok(WriteCallPreparation::Pending(r)) => {
                assert_eq!(native, "PENDING");
                assert_eq!(r, s.reservation);
                assert_eq!(r.business_result_ref(), None);
            }
            Err(err) => {
                assert_eq!(native, "CONFLICT");
                assert_eq!(err.kind, WriteExecutionErrorKind::Conflict);
                assert_eq!(err.detail_code(), Some("IDEMPOTENCY_CONFLICT"));
                assert_eq!(err.path, "$.reservation.disposition");
            }
        }
        if native != "CREATED" {
            let state = spy.state.lock().unwrap();
            assert!(state.records.is_empty());
            assert!(state.events.is_empty());
            assert_eq!(state.provider_calls, 0);
            assert!(!flag);
        }
    }
}
struct Waiting {
    inner: Evidence,
    accepted: bool,
}
impl WriteEvidencePort for Waiting {
    fn record_requested<'a>(&'a self, r: &'a WriteAttemptRecord) -> WriteEvidenceFuture<'a> {
        Box::pin(async move {
            if self.accepted {
                self.inner.record_requested(r).await?;
            }
            std::future::pending().await
        })
    }
}
#[test]
fn dropped_futures_and_storage_uncertainty_cannot_mint_durable_handoffs() {
    for flag_before in [false, true] {
        for accepted in [false, true] {
            let s = Setup::new(baseline());
            let waiting = Waiting {
                inner: Evidence::default(),
                accepted,
            };
            let mut flag = flag_before;
            let decision = allowed();
            {
                let f = WriteRequestedCoordinator::new(&waiting);
                let future = f.persist(s.created(), &decision, context(&mut flag), now());
                let mut future = std::pin::pin!(future);
                assert!(matches!(
                    future
                        .as_mut()
                        .poll(&mut Context::from_waker(Waker::noop())),
                    Poll::Pending
                ));
            }
            assert_eq!(flag, flag_before);
            let store = waiting.inner.state.lock().unwrap();
            assert_eq!(store.records.len(), usize::from(accepted));
            assert_eq!(store.provider_calls, 0);
        }
    }
    for mode in ["fail-before", "fail-after"] {
        let spy = Evidence {
            mode: mode.into(),
            ..Default::default()
        };
        let mut flag = false;
        let s = Setup::new(baseline());
        let error = ready(WriteRequestedCoordinator::new(&spy).persist(
            s.created(),
            &allowed(),
            context(&mut flag),
            now(),
        ))
        .unwrap_err();
        assert_eq!(error.kind, WriteExecutionErrorKind::Port);
        assert_eq!(error.detail_code(), None);
        assert!(!flag);
        let store = spy.state.lock().unwrap();
        assert_eq!(store.records.len(), usize::from(mode == "fail-after"));
        assert_eq!(store.provider_calls, 0);
    }
    let s = Setup::new(baseline());
    let spy = Evidence::default();
    let mut flag = false;
    drop(WriteRequestedCoordinator::new(&spy).persist(
        s.created(),
        &allowed(),
        context(&mut flag),
        now(),
    ));
    assert!(spy.state.lock().unwrap().events.is_empty());
    assert!(!flag);
}
#[test]
fn snapshots_and_inherited_value_metadata_budgets_are_checked_before_cloning() {
    let s = Setup::new(baseline());
    let p = s.created();
    let mut spec = p.request().metadata().clone();
    let original = call_data(p.request());
    spec.trace_refs.push("new-trace".into());
    spec.arguments.clear();
    assert_eq!(call_data(p.request()), original);
    let spec = p.request().metadata();
    let bytes = canonical_write_arguments(&spec.arguments, support::limits())
        .unwrap()
        .len();
    for max in [0, 1, bytes - 1, bytes, bytes + 1] {
        let mut profile = support::snapshot(support::limits());
        profile.canonical.canonical.max_bytes = max;
        let result = WriteCallRequest::new(spec, profile);
        assert_eq!(result.is_ok(), max >= bytes);
        if let Err(e) = result {
            assert!(matches!(e.kind, WriteExecutionErrorKind::Snapshot(_)));
            assert_eq!(e.detail_code(), None);
        }
    }
    for (axis, boundary) in [("text", 18), ("nodes", 7), ("depth", 2)] {
        for max in [0, 1, boundary - 1, boundary, boundary + 1] {
            let mut profile = support::snapshot(support::limits());
            match axis {
                "text" => profile.metadata.max_text_bytes = max,
                "nodes" => profile.metadata.max_nodes = max,
                "depth" => profile.metadata.max_depth = max,
                _ => unreachable!(),
            }
            let observed = WriteCallRequest::new(spec, profile);
            assert_eq!(observed.is_ok(), max >= boundary, "{axis}={max}");
            let built = WriteCallRequestBuilder.build(s.build_request(), profile);
            assert_eq!(built.is_ok(), observed.is_ok());
        }
    }
    for (axis, boundary) in [("nodes", 9), ("depth", 4)] {
        for max in [0, 1, boundary - 1, boundary, boundary + 1] {
            let mut profile = support::snapshot(support::limits());
            if axis == "nodes" {
                profile.canonical.canonical.max_nodes = max;
            } else {
                profile.canonical.canonical.max_depth = max;
            }
            assert_eq!(
                WriteCallRequest::new(spec, profile).is_ok(),
                max >= boundary,
                "canonical {axis}={max}"
            );
            assert_eq!(
                WriteCallRequestBuilder
                    .build(s.build_request(), profile)
                    .is_ok(),
                max >= boundary,
                "builder canonical {axis}={max}"
            );
        }
    }
    let mut missing_flag = false;
    let mut missing = context(&mut missing_flag);
    missing.business_operation_id = None;
    let spy = Evidence::default();
    assert_eq!(
        ready(WriteRequestedCoordinator::new(&spy).persist(
            s.created(),
            &allowed(),
            missing,
            now()
        ))
        .unwrap_err()
        .kind,
        WriteExecutionErrorKind::Binding
    );
    assert!(spy.state.lock().unwrap().records.is_empty());
}
#[test]
fn repeated_explicit_preparation_keeps_record_identity_and_safe_error_debug() {
    let s = Setup::new(baseline());
    let spy = Evidence::default();
    let mut flag = false;
    for n in 1..=2 {
        let permit = ready(WriteRequestedCoordinator::new(&spy).persist(
            s.created(),
            &allowed(),
            context(&mut flag),
            now(),
        ))
        .unwrap();
        for debug in [
            format!("{permit:?}"),
            format!("{:?}", permit.request()),
            format!("{:?}", permit.request().metadata()),
            format!("{:?}", permit.record()),
            format!("{:?}", permit.record().metadata()),
            format!("{:?}", permit.receipt()),
            format!("{:?}", permit.receipt().metadata()),
            format!("{:?}", s.build().unwrap()),
        ] {
            assert!(!debug.contains("tenant-a"));
            assert!(!debug.contains("sha256:"));
        }
        invoke(&spy, permit);
        assert_eq!(spy.state.lock().unwrap().provider_calls, n);
    }
    let state = spy.state.lock().unwrap();
    assert_eq!(state.records[0], state.records[1]);
    assert_eq!(
        state.records[0].record_hash(),
        state.records[1].record_hash()
    );
    assert!(flag);
    for k in [
        WriteExecutionErrorKind::Model,
        WriteExecutionErrorKind::Binding,
        WriteExecutionErrorKind::Port,
        WriteExecutionErrorKind::NotAllowed,
        WriteExecutionErrorKind::Snapshot(WriteIntentErrorKind::Type),
    ] {
        let e = WriteExecutionError {
            kind: k,
            path: "$.safe".into(),
            message: "safe",
        };
        assert_eq!(e.detail_code(), None);
        assert_eq!(e.to_string(), "$.safe: safe");
        assert!(std::error::Error::source(&e).is_none());
    }
    assert_eq!(
        format!("{WriteEvidencePortError:?}"),
        "WriteEvidencePortError"
    );
    for invalid in [
        "2026-10-20T03:00:00",
        "2026-10-20T03:00:00+09:00",
        "2026-10-20T03:00:00.1234567Z",
    ] {
        assert!(invalid.parse::<UtcInstant>().is_err());
    }
}
#[test]
fn deep_type_metadata_is_rejected_on_small_stack_before_request_or_builder_clone() {
    std::thread::Builder::new()
        .stack_size(64 * 1024 * 1024)
        .spawn(|| {
            let s = Setup::new(baseline());
            let p = s.created();
            let mut spec = p.request().metadata().clone();
            let mut ty = TypeRef::new(TypeKind::Domain("DraftPayload".into())).unwrap();
            for _ in 0..8192 {
                ty = TypeRef::new(TypeKind::List(Box::new(ty))).unwrap();
            }
            let old = &spec.arguments["detail"];
            let envelope = ValueEnvelope::new(
                old.value().clone(),
                ty,
                old.presence(),
                old.completeness(),
                old.classification(),
                old.provenance_refs().to_vec(),
            );
            spec.arguments.insert("detail".into(), envelope);
            let mut intent_spec = s.i.metadata().clone();
            intent_spec.canonical_arguments = spec.arguments.clone();
            let mut large = support::snapshot(support::limits());
            large.metadata.max_depth = 20_000;
            large.metadata.max_nodes = 100_000;
            let deep = WriteIntent::new(&intent_spec, large).unwrap();
            std::thread::scope(|scope| {
                std::thread::Builder::new()
                    .stack_size(512 * 1024)
                    .spawn_scoped(scope, || {
                        let e = WriteCallRequest::new(&spec, support::snapshot(support::limits()))
                            .unwrap_err();
                        assert_eq!(
                            e.kind,
                            WriteExecutionErrorKind::Snapshot(WriteIntentErrorKind::Type)
                        );
                        assert!(e.path.contains("type_ref.item"));
                        let request = WriteCallBuildRequest {
                            intent: &deep,
                            ..s.build_request()
                        };
                        let e = WriteCallRequestBuilder
                            .build(request, support::snapshot(support::limits()))
                            .unwrap_err();
                        assert_eq!(
                            e.kind,
                            WriteExecutionErrorKind::Snapshot(WriteIntentErrorKind::Type)
                        );
                    })
                    .unwrap()
                    .join()
                    .unwrap();
            });
        })
        .unwrap()
        .join()
        .unwrap();
}
