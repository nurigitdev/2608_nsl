mod guard_support;
mod invocation_support;
mod requested_support;
mod support;
use guard_support as g;
use invocation_support::*;
use nsl_core::*;
use nsl_write::*;
use requested_support as r;
use std::{
    collections::BTreeMap,
    future::Future,
    sync::Mutex,
    task::{Context, Poll, Waker},
};
use support::*;
const AT: &str = "2026-10-20T03:00:02+00:00";
fn spec(d: &ValueData) -> WriteOutcomeRecordSpec {
    WriteOutcomeRecordSpec {
        requested_record_hash: get(d, "requested_record_hash").into(),
        identity: WriteOutcomeIdentity::new(identity_spec(field(d, "identity"))).unwrap(),
        commit_state: get(d, "commit_state").parse().unwrap(),
        reason_code: g::optional(d, "reason_code"),
        provider_transaction_id: g::optional(d, "provider_transaction_id"),
        business_reference: g::optional(d, "business_reference"),
        evidence_refs: refs(d),
        recorded_at: g::instant(d, "recorded_at"),
    }
}
fn record_data(r: &WriteOutcomeRecord) -> ValueData {
    object([
        ("record", r.to_data()),
        ("canonical", txt(hex(r.canonical_bytes()))),
        ("record_hash", txt(r.record_hash().as_str())),
    ])
}
fn receipt(d: &ValueData) -> Result<WriteOutcomeReceipt, WriteExecutionError> {
    WriteOutcomeReceipt::new(WriteOutcomeReceiptSpec {
        record_hash: get(d, "record_hash").into(),
        durable_ref: get(d, "durable_ref").into(),
        persisted_at: g::instant(d, "persisted_at"),
    })
}
fn base() -> WriteOutcomeRecord {
    let (_, d, _) = g::rows(include_str!("fixtures/write-outcomes-records-oracle.tsv"))
        .find(|(n, _, _)| n == "unknown")
        .unwrap();
    WriteOutcomeRecord::new(spec(&d)).unwrap()
}
fn checked(name: &str, result: Result<ValueData, WriteExecutionError>, expected: &ValueData) {
    let e = arr(expected);
    match result {
        Ok(v) => {
            assert_eq!(text(&e[0]), "ok", "{name}");
            assert_eq!(v, e[1], "{name}");
        }
        Err(error) => {
            assert_eq!(text(&e[0]), "error", "{name}");
            assert_eq!(
                error.detail_code().map(txt).unwrap_or(ValueData::Null),
                e[3],
                "{name}"
            );
            assert!(error.path.starts_with("$."));
            assert!(!format!("{error:?} {error}").contains("synthetic-only"));
        }
    }
}
#[test]
fn outcome_record_receipt_and_journal_models_match_frozen_oracle() {
    let mut count = 0;
    for (name, d, e) in g::rows(include_str!("fixtures/write-outcomes-models-oracle.tsv")) {
        let result = match get(&d, "kind") {
            "record" => {
                let value = field(&d, "value");
                // Core's enum parser is the dynamic fixture adapter. Wrong
                // Python runtime classes cannot enter the typed constructor.
                get(value, "commit_state")
                    .parse::<CommitState>()
                    .map_err(|_| WriteExecutionError {
                        kind: WriteExecutionErrorKind::Model,
                        path: "$.record.commit_state".into(),
                        message: "expected typed CommitState",
                    })
                    .and_then(|_| WriteOutcomeRecord::new(spec(value)))
                    .map(|r| record_data(&r))
            }
            "receipt" => receipt(field(&d, "value")).map(|r| r.to_data()),
            "journal" => WriteOutcomeRecord::new(spec(field(&d, "record")))
                .and_then(|r| WriteJournalEntry::new(r, get(&d, "durable_ref").into()))
                .map(|r| r.to_data()),
            _ => panic!(),
        };
        checked(&name, result, &e);
        count += 1;
    }
    assert_eq!(count, 67);
}
#[test]
fn all_sixteen_hash_fields_unicode_and_utc_boundaries_match_oracle_bytes() {
    let baseline = base();
    let mut changed = std::collections::BTreeSet::new();
    let mut count = 0;
    for (name, d, e) in g::rows(include_str!("fixtures/write-outcomes-records-oracle.tsv")) {
        let r = WriteOutcomeRecord::new(spec(&d)).unwrap();
        assert_eq!(record_data(&r), arr(&e)[1], "{name}");
        if name.starts_with("identity-") || name.starts_with("record-") {
            assert_ne!(r.record_hash(), baseline.record_hash());
            assert!(changed.insert(r.record_hash().to_string()));
        }
        assert_ne!(r.record_hash().as_str(), r.metadata().requested_record_hash);
        count += 1;
    }
    assert_eq!(count, 26);
    assert_eq!(changed.len(), 16);
}
struct Provider {
    data: ValueData,
    calls: Mutex<usize>,
}
impl WriteExecutionPort for Provider {
    fn execute<'a>(
        &'a self,
        _: &'a WriteCallRequest,
        _: WriteCancellationSignal,
    ) -> WriteExecutionFuture<'a> {
        Box::pin(async move {
            *self.calls.lock().unwrap() += 1;
            Ok(parse_outcome(&self.data))
        })
    }
}
fn invocation(data: ValueData, at: UtcInstant) -> WriteInvocation {
    let setup = r::Setup::new(r::baseline());
    let requested = r::Evidence::default();
    let mut flag = false;
    let durable = r::ready(WriteRequestedCoordinator::new(&requested).persist(
        setup.created(),
        &r::allowed(),
        r::context(&mut flag),
        at,
    ))
    .unwrap();
    assert!(flag);
    let provider = Provider {
        data,
        calls: Mutex::new(0),
    };
    let result = r::ready(
        WriteInvocationCoordinator::new(&provider)
            .invoke(durable, WriteCancellationSignal::default()),
    );
    assert_eq!(*provider.calls.lock().unwrap(), 1);
    result
}
struct Evidence {
    case: String,
    delta: i64,
    records: Mutex<Vec<WriteOutcomeRecord>>,
}
impl WriteOutcomeEvidencePort for Evidence {
    fn record_outcome<'a>(
        &'a self,
        record: &'a WriteOutcomeRecord,
    ) -> WriteOutcomeEvidenceFuture<'a> {
        Box::pin(async move {
            self.records.lock().unwrap().push(record.clone());
            if self.case == "storage-error" {
                return Err(WriteOutcomeEvidencePortError::Storage);
            }
            if self.case == "malformed-receipt" {
                return Err(WriteOutcomeEvidencePortError::MalformedReceipt);
            }
            let at = match self.delta {
                -1 => "2026-10-20T03:00:01.999999Z",
                1 => "2026-10-20T03:00:02.000001Z",
                _ => AT,
            };
            Ok(WriteOutcomeReceipt::new(WriteOutcomeReceiptSpec {
                record_hash: if self.case == "receipt-hash" {
                    format!("sha256:{}", "f".repeat(64))
                } else {
                    record.record_hash().to_string()
                },
                durable_ref: "outcome:0054".into(),
                persisted_at: at.parse().unwrap(),
            })
            .unwrap())
        })
    }
}
fn maps(invocation: &WriteInvocation) -> (BTreeMap<String, String>, BTreeMap<String, String>) {
    let s = invocation.request().metadata();
    (
        BTreeMap::from([(s.node_id.clone(), s.idempotency_key.clone())]),
        BTreeMap::from([(s.node_id.clone(), s.payload_hash.clone())]),
    )
}
fn context<'a>(
    keys: &'a BTreeMap<String, String>,
    payloads: &'a BTreeMap<String, String>,
    journal: &'a mut Vec<ValueData>,
) -> WriteOutcomeContext<'a> {
    WriteOutcomeContext {
        tenant_id: "tenant-a",
        execution_id: "exec-0053",
        business_operation_id: Some("business-0053"),
        keys_by_write_node: keys,
        payload_hashes_by_write_node: payloads,
        journal,
    }
}
fn map_data(keys: &BTreeMap<String, String>, payloads: &BTreeMap<String, String>) -> ValueData {
    let data = |m: &BTreeMap<String, String>| {
        ValueData::Object(m.iter().map(|(k, v)| (k.clone(), txt(v))).collect())
    };
    object([
        ("keys_by_write_node", data(keys)),
        ("payload_hashes_by_write_node", data(payloads)),
    ])
}
#[test]
fn invocation_bindings_receipt_validation_and_atomic_journal_append_match_oracle() {
    let mut count = 0;
    for (name, c, e) in g::rows(include_str!(
        "fixtures/write-outcomes-persistence-oracle.tsv"
    )) {
        let input = field(&e, "input_outcome").clone();
        let inv = invocation(input.clone(), r::now());
        let (mut keys, mut payloads) = maps(&inv);
        let case = get(&c, "case");
        match case {
            "missing-key" => keys.clear(),
            "missing-payload" => payloads.clear(),
            "foreign-key" => *keys.values_mut().next().unwrap() = "other".into(),
            "foreign-payload" => *payloads.values_mut().next().unwrap() = "other".into(),
            _ => {}
        }
        let before = map_data(&keys, &payloads);
        let mut journal = Vec::new();
        if g::yes(&c, "prefix") {
            journal.push(
                WriteJournalEntry::new(base(), "prior-outcome:0054".into())
                    .unwrap()
                    .to_data(),
            );
        }
        let old_journal = journal.clone();
        let mut ctx = context(&keys, &payloads, &mut journal);
        match case {
            "context-execution" => ctx.execution_id = "other-execution",
            "context-tenant" => ctx.tenant_id = "other-tenant",
            "context-business" => ctx.business_operation_id = Some("other-business"),
            "context-business-missing" => ctx.business_operation_id = None,
            _ => {}
        }
        let delta = match g::opt(&c, "delta") {
            Some(ValueData::Number(n)) => n.as_str().parse().unwrap(),
            _ => 0,
        };
        let port = Evidence {
            case: case.into(),
            delta,
            records: Mutex::new(vec![]),
        };
        let persisted =
            r::ready(WriteOutcomeCoordinator::new(&port).persist(inv, ctx, AT.parse().unwrap()));
        let result = match persisted {
            Ok(v) => {
                assert_eq!(
                    v.entry().record().metadata().requested_record_hash,
                    v.invocation().requested_record().record_hash().as_str()
                );
                assert_eq!(v.entry().durable_ref(), v.receipt().metadata().durable_ref);
                ValueData::Array(vec![txt("ok"), outcome_data(v.invocation().outcome())])
            }
            Err(error) => {
                assert_eq!(journal, old_journal);
                assert_eq!(outcome_data(error.invocation().outcome()), input);
                assert!(!error.invocation().automatic_retry_allowed());
                assert!(!format!("{error:?} {error}").contains("protected"));
                ValueData::Array(vec![
                    txt("error"),
                    txt(if error.error.kind == WriteExecutionErrorKind::Port {
                        "OSError"
                    } else {
                        "WriteOutcomeError"
                    }),
                    error
                        .error
                        .detail_code()
                        .map(txt)
                        .unwrap_or(ValueData::Null),
                ])
            }
        };
        let stored = port.records.lock().unwrap();
        let mut events = vec![txt("WRITE_REQUESTED"), txt("PROVIDER_WRITE")];
        if !stored.is_empty() {
            events.push(txt("WRITE_OUTCOME"));
        }
        let observed = object([
            ("result", result),
            ("events", ValueData::Array(events)),
            ("calls", ValueData::Number((stored.len() as i64).into())),
            (
                "stored",
                ValueData::Array(stored.iter().map(record_data).collect()),
            ),
            ("journal", ValueData::Array(journal)),
            ("keys", map_data(&keys, &payloads)),
            ("keys_before", before),
            ("flag", ValueData::Bool(true)),
            ("input_outcome", input),
        ]);
        assert_eq!(observed, e, "{name}");
        count += 1;
    }
    assert_eq!(count, 50);
}
fn input() -> ValueData {
    let (_, _, e) = g::rows(include_str!(
        "fixtures/write-outcomes-persistence-oracle.tsv"
    ))
    .next()
    .unwrap();
    field(&e, "input_outcome").clone()
}
#[test]
fn matching_real_requested_record_is_mandatory_and_foreign_valid_receipts_fail() {
    struct ForeignReceipt(String);
    impl WriteOutcomeEvidencePort for ForeignReceipt {
        fn record_outcome<'a>(
            &'a self,
            _: &'a WriteOutcomeRecord,
        ) -> WriteOutcomeEvidenceFuture<'a> {
            Box::pin(async move {
                Ok(WriteOutcomeReceipt::new(WriteOutcomeReceiptSpec {
                    record_hash: self.0.clone(),
                    durable_ref: "foreign-valid-receipt".into(),
                    persisted_at: AT.parse().unwrap(),
                })
                .unwrap())
            })
        }
    }
    let port = Evidence {
        case: "valid".into(),
        delta: 0,
        records: Mutex::new(vec![]),
    };
    let first = invocation(input(), r::now());
    let (keys, payloads) = maps(&first);
    let mut journal = Vec::new();
    let first = r::ready(WriteOutcomeCoordinator::new(&port).persist(
        first,
        context(&keys, &payloads, &mut journal),
        AT.parse().unwrap(),
    ))
    .unwrap();
    let prior_hash = first.entry().record().record_hash().to_string();
    let next = invocation(input(), "2026-10-20T03:00:00.000001Z".parse().unwrap());
    assert_ne!(
        first.invocation().requested_record().record_hash(),
        next.requested_record().record_hash()
    );
    let before = journal.clone();
    let error = r::ready(
        WriteOutcomeCoordinator::new(&ForeignReceipt(prior_hash)).persist(
            next,
            context(&keys, &payloads, &mut journal),
            AT.parse().unwrap(),
        ),
    )
    .unwrap_err();
    assert_eq!(
        error.error.detail_code(),
        Some("WRITE_OUTCOME_EVIDENCE_INVALID")
    );
    assert_eq!(journal, before);
    assert_eq!(
        error.invocation().outcome().commit_state(),
        CommitState::Committed
    );
}
#[test]
fn dropped_outcome_storage_future_cannot_acknowledge_or_append_to_the_journal() {
    struct Pending(Mutex<Vec<WriteOutcomeRecord>>);
    impl WriteOutcomeEvidencePort for Pending {
        fn record_outcome<'a>(
            &'a self,
            r: &'a WriteOutcomeRecord,
        ) -> WriteOutcomeEvidenceFuture<'a> {
            Box::pin(async move {
                self.0.lock().unwrap().push(r.clone());
                std::future::pending().await
            })
        }
    }
    let inv = invocation(input(), r::now());
    let (keys, payloads) = maps(&inv);
    let mut journal = vec![txt("prior")];
    let before = journal.clone();
    let port = Pending(Mutex::new(vec![]));
    let coordinator = WriteOutcomeCoordinator::new(&port);
    let mut future = Box::pin(coordinator.persist(
        inv,
        context(&keys, &payloads, &mut journal),
        AT.parse().unwrap(),
    ));
    assert!(matches!(
        future
            .as_mut()
            .poll(&mut Context::from_waker(Waker::noop())),
        Poll::Pending
    ));
    drop(future);
    assert_eq!(port.0.lock().unwrap().len(), 1);
    assert_eq!(journal, before);
}
#[test]
fn outcome_record_receipt_and_journal_debugs_hide_business_metadata() {
    let r = base();
    let spec = r.metadata().clone();
    let receipt_spec = WriteOutcomeReceiptSpec {
        record_hash: r.record_hash().to_string(),
        durable_ref: "private-evidence-reference".into(),
        persisted_at: AT.parse().unwrap(),
    };
    assert!(!format!("{spec:?} {r:?} {receipt_spec:?}").contains("private"));
    let receipt = WriteOutcomeReceipt::new(receipt_spec).unwrap();
    let entry = WriteJournalEntry::new(r, "private-evidence-reference".into()).unwrap();
    assert!(!format!("{receipt:?} {entry:?}").contains("private"));
}
