mod support;
use nsl_contract::*;
use nsl_core::*;
use nsl_write::*;
use std::{
    collections::BTreeMap,
    future::Future,
    sync::{Arc, Barrier, Mutex},
    task::{Context, Poll, Waker},
};
use support::*;
const NOW: &str = "2026-10-20T01:00:00+00:00";
const UNTIL: &str = "2026-11-19T01:00:00+00:00";
const HORIZON: &str = "2026-10-27T01:00:00+00:00";
fn empty() -> ValueData {
    object([])
}
fn opt<'a>(d: &'a ValueData, k: &str) -> Option<&'a ValueData> {
    let ValueData::Object(o) = d else { panic!() };
    o.get(k)
}
fn string<'a>(d: &'a ValueData, k: &str, default: &'a str) -> &'a str {
    opt(d, k).map_or(default, text)
}
fn yes(d: &ValueData, k: &str) -> bool {
    opt(d, k) == Some(&ValueData::Bool(true))
}
fn now() -> UtcInstant {
    NOW.parse().unwrap()
}
fn rows(s: &str) -> impl Iterator<Item = (String, ValueData, ValueData)> + '_ {
    s.lines().filter(|s| !s.starts_with('#')).map(|s| {
        let p: Vec<_> = s.split('\t').collect();
        (p[0].into(), parse(&decode(p[1])), parse(&decode(p[2])))
    })
}
fn check_result(name: &str, actual: Result<ValueData, IdempotencyError>, expected: &ValueData) {
    let e = arr(expected);
    match actual {
        Ok(v) => {
            assert_eq!(text(&e[0]), "ok", "{name}: {expected:?}");
            assert_eq!(v, e[1], "{name}");
        }
        Err(err) => {
            assert_eq!(text(&e[0]), "error", "{name}: {err:?}");
            assert!(err.path.starts_with("$."), "{name}");
            assert!(!err.to_string().contains("Bearer"));
            assert!(std::error::Error::source(&err).is_none());
            assert_eq!(
                err.detail_code().map(txt).unwrap_or(ValueData::Null),
                e[3],
                "{name}"
            );
            match text(&e[1]) {
                "IdempotencyRetentionError" => {
                    assert_eq!(err.kind, IdempotencyErrorKind::Retention)
                }
                "IdempotencyStateError" => assert_eq!(err.kind, IdempotencyErrorKind::State),
                "IdempotencyConflictError" => assert_eq!(err.kind, IdempotencyErrorKind::Conflict),
                "OSError" => assert_eq!(err.kind, IdempotencyErrorKind::Port),
                "IdempotencyError" | "CredentialMaterialError" => assert!(matches!(
                    err.kind,
                    IdempotencyErrorKind::Identity | IdempotencyErrorKind::Type
                )),
                other => panic!("unclassified Oracle error {other}"),
            }
        }
    }
}
fn intent(config: &ValueData, tool: &str) -> WriteIntent {
    let args = BTreeMap::from([(
        "detail".into(),
        ValueEnvelope::complete(
            Value::Record(BTreeMap::from([
                ("description".into(), Value::String("KTX".into())),
                (
                    "amount".into(),
                    Value::Decimal(string(config, "amount", "84000").parse().unwrap()),
                ),
            ])),
            TypeRef::new(TypeKind::Domain("DraftPayload".into())).unwrap(),
            DataClassification::Confidential,
        ),
    )]);
    let node = string(config, "node_id", "write0001");
    let semantic = format!("sha256:{}", "a".repeat(64));
    let payload = compute_payload_hash(&args, limits()).unwrap();
    WriteIntent::new(
        &WriteIntentSpec {
            tenant_id: string(config, "tenant_id", "tenant-a").into(),
            business_operation_id: string(config, "business_operation_id", "business-0051").into(),
            node_id: node.into(),
            tool_ref: "write_tool".into(),
            tool_id: tool.into(),
            approval_id: "APPROVE_DRAFT".into(),
            skill_semantic_hash: semantic.clone(),
            write_intent_hash: compute_write_intent_hash(&semantic, node, tool, payload.as_str())
                .unwrap()
                .to_string(),
            payload_hash: payload.to_string(),
            canonical_arguments: args,
        },
        snapshot(limits()),
    )
    .unwrap()
}
fn contract(config: &ValueData) -> ToolContract {
    contract_type(config, None)
}
fn contract_type(config: &ValueData, output: Option<TypeRef>) -> ToolContract {
    let capability = string(config, "capability", "WRITE");
    let domain = |n: &str| TypeRef::new(TypeKind::Domain(n.into())).unwrap();
    ToolContract::new(ToolContractSpec {
        tool_id: string(config, "tool_id", "FINANCE.CREATE_DRAFT").into(),
        version: "1.0.0".into(),
        capability: capability.into(),
        input_types: vec![("detail".into(), domain("DraftPayload"))],
        output_type: output.unwrap_or_else(|| domain("DraftRecord")),
        required_scope: "finance:draft:write".into(),
        output_classification: DataClassification::Confidential,
        options: if capability == "WRITE" {
            ToolContractOptions {
                operation_kind: Some("CREATE".into()),
                risk_level: Some("R3".into()),
                write_semantics: Some(
                    WriteSemantics::new(WriteSemanticsSpec {
                        side_effect: true,
                        atomicity: "ATOMIC".into(),
                        idempotency: Some(
                            IdempotencySemantics::new(
                                true,
                                string(config, "scope", "TOOL_AND_TENANT"),
                                string(config, "retention", "PROVIDER_DEFINED"),
                                string(config, "duplicate_behavior", "RETURN_ORIGINAL_RESULT"),
                                string(config, "payload_conflict_behavior", "IDEMPOTENCY_CONFLICT"),
                            )
                            .unwrap(),
                        ),
                        commit: Some(CommitSemantics::new(CommitConfirmation::Explicit)),
                        approval_required: true,
                        preconditions_supported: true,
                        verification: None,
                    })
                    .unwrap(),
                ),
                ..Default::default()
            }
        } else {
            Default::default()
        },
    })
    .unwrap()
}
fn provider(d: &ValueData) -> Result<ProviderIdempotencyEvidence, IdempotencyError> {
    ProviderIdempotencyEvidence::new(ProviderIdempotencyEvidenceSpec {
        tool_id: get(d, "tool_id").into(),
        contract_hash: get(d, "contract_hash").into(),
        retention_policy: get(d, "retention_policy").into(),
        provider_guarantee_ref: get(d, "provider_guarantee_ref").into(),
        verified_at: get(d, "verified_at").parse().unwrap(),
        guaranteed_until: get(d, "guaranteed_until").parse().unwrap(),
        durable_duplicate_guarantee: yes(d, "durable_duplicate_guarantee"),
    })
}
fn standard_provider(c: &ToolContract, d: &ValueData) -> ProviderIdempotencyEvidence {
    ProviderIdempotencyEvidence::new(ProviderIdempotencyEvidenceSpec {
        tool_id: string(d, "tool_id", c.tool_id()).into(),
        contract_hash: string(d, "contract_hash", c.contract_hash().unwrap().as_str()).into(),
        retention_policy: string(d, "retention_policy", "PROVIDER_DEFINED").into(),
        provider_guarantee_ref: string(
            d,
            "provider_guarantee_ref",
            "provider-policy://finance/idempotency-v1",
        )
        .into(),
        verified_at: string(d, "verified_at", NOW).parse().unwrap(),
        guaranteed_until: string(d, "guaranteed_until", UNTIL).parse().unwrap(),
        durable_duplicate_guarantee: opt(d, "durable_duplicate_guarantee")
            .is_none_or(|v| v == &ValueData::Bool(true)),
    })
    .unwrap()
}
fn record(d: &ValueData) -> Result<IdempotencyEvidenceRecord, IdempotencyError> {
    IdempotencyEvidenceRecord::new(IdempotencyEvidenceRecordSpec {
        tenant_id: get(d, "tenant_id").into(),
        business_operation_id: get(d, "business_operation_id").into(),
        write_node_id: get(d, "write_node_id").into(),
        tool_id: get(d, "tool_id").into(),
        contract_hash: get(d, "contract_hash").into(),
        idempotency_key: get(d, "idempotency_key").into(),
        payload_hash: get(d, "payload_hash").into(),
        provider_guarantee_ref: get(d, "provider_guarantee_ref").into(),
        retained_until: get(d, "retained_until").parse().unwrap(),
    })
}
fn standard_record() -> IdempotencyEvidenceRecord {
    let i = intent(&empty(), "FINANCE.CREATE_DRAFT");
    let c = contract(&empty());
    let e = standard_provider(&c, &empty());
    IdempotencyEvidenceRecord::new(IdempotencyEvidenceRecordSpec {
        tenant_id: i.metadata().tenant_id.clone(),
        business_operation_id: i.metadata().business_operation_id.clone(),
        write_node_id: i.metadata().node_id.clone(),
        tool_id: c.tool_id().into(),
        contract_hash: e.metadata().contract_hash.clone(),
        idempotency_key: idempotency_identity(&i)
            .unwrap()
            .idempotency_key()
            .to_string(),
        payload_hash: i.payload_hash().to_string(),
        provider_guarantee_ref: e.metadata().provider_guarantee_ref.clone(),
        retained_until: e.metadata().guaranteed_until,
    })
    .unwrap()
}
fn disposition(s: &str) -> IdempotencyDisposition {
    match s {
        "CREATED" => IdempotencyDisposition::Created,
        "REUSED" => IdempotencyDisposition::Reused,
        "CONFLICT" => IdempotencyDisposition::Conflict,
        _ => panic!(),
    }
}
#[test]
fn fixed_oracle_identity_and_model_admission() {
    for (name, d, expected) in rows(include_str!("fixtures/idempotency-keys-oracle.tsv")) {
        let result = if name.starts_with("intent-") {
            idempotency_identity(&intent(&d, "FINANCE.CREATE_DRAFT")).map(|v| v.to_data())
        } else {
            IdempotencyKeyFactory
                .create(
                    get(&d, "tenant_id"),
                    get(&d, "business_operation_id"),
                    get(&d, "write_node_id"),
                )
                .map(|v| txt(v.as_str()))
        };
        check_result(&name, result, &expected);
    }
    for (name, d, expected) in rows(include_str!("fixtures/idempotency-models-oracle.tsv")) {
        let v = field(&d, "value");
        let result = match get(&d, "kind") {
            "identity" => IdempotencyIdentity::new(IdempotencyIdentitySpec {
                tenant_id: get(v, "tenant_id").into(),
                business_operation_id: get(v, "business_operation_id").into(),
                write_node_id: get(v, "write_node_id").into(),
                idempotency_key: get(v, "idempotency_key").into(),
                payload_hash: get(v, "payload_hash").into(),
            })
            .map(|i| i.to_data()),
            "provider" => provider(v).map(|e| e.to_data()),
            "record" => record(v).map(|r| r.to_data()),
            "reservation" => IdempotencyReservation::new(
                disposition(get(v, "disposition")),
                standard_record(),
                if field(v, "business_result_ref") == &ValueData::Null {
                    None
                } else {
                    Some(get(v, "business_result_ref").into())
                },
            )
            .map(|r| r.to_data()),
            _ => panic!(),
        };
        check_result(&name, result, &expected);
    }
}
#[test]
fn fixed_oracle_retention_boundaries() {
    for (name, d, expected) in rows(include_str!("fixtures/idempotency-retention-oracle.tsv")) {
        let c = contract(field(&d, "contract"));
        let e = provider(field(&d, "evidence")).unwrap();
        let i = intent(&empty(), "FINANCE.CREATE_DRAFT");
        let r = IdempotencyRetentionValidator.validate(
            &i,
            &c,
            &e,
            get(&d, "now").parse().unwrap(),
            get(&d, "required_until").parse().unwrap(),
        );
        if let Ok(v) = r {
            assert!(std::ptr::eq(v, &e));
        }
        check_result(&name, r.map(|v| v.to_data()), &expected);
    }
}
#[derive(Default)]
struct State {
    records: BTreeMap<(String, String, String), IdempotencyEvidenceRecord>,
    results: BTreeMap<(String, String, String), String>,
    calls: usize,
}
#[derive(Default)]
struct Durable {
    state: Mutex<State>,
    config: ValueDataConfig,
}
#[derive(Default)]
struct ValueDataConfig(Option<ValueData>);
fn record_key(r: &IdempotencyEvidenceRecord) -> (String, String, String) {
    (
        r.metadata().tenant_id.clone(),
        r.metadata().tool_id.clone(),
        r.idempotency_key().to_string(),
    )
}
impl IdempotencyEvidencePort for Durable {
    fn reserve<'a>(
        &'a self,
        incoming: &'a IdempotencyEvidenceRecord,
    ) -> IdempotencyEvidenceFuture<'a> {
        Box::pin(async move {
            let mut s = self.state.lock().unwrap();
            s.calls += 1;
            let blank = empty();
            let config = self.config.0.as_ref().unwrap_or(&blank);
            if string(config, "port", "") == "fail-before" {
                return Err(IdempotencyPortError);
            }
            let key = record_key(incoming);
            let previous = s.records.get(&key).cloned();
            let disp = match &previous {
                None => IdempotencyDisposition::Created,
                Some(v) if v.payload_hash() != incoming.payload_hash() => {
                    IdempotencyDisposition::Conflict
                }
                Some(_) => IdempotencyDisposition::Reused,
            };
            if previous.is_none() {
                s.records.insert(key.clone(), incoming.clone());
            }
            if string(config, "port", "") == "fail-after" {
                return Err(IdempotencyPortError);
            }
            let mut returned = if yes(config, "echo_old") {
                previous.unwrap_or_else(|| incoming.clone())
            } else {
                incoming.clone()
            };
            if let Some(a) = opt(config, "alter") {
                let a = arr(a);
                let ValueData::Object(mut data) = returned.to_data() else {
                    panic!()
                };
                data.insert(text(&a[0]).into(), a[1].clone());
                returned = record(&ValueData::Object(data)).unwrap();
            }
            let disp = if yes(config, "force_conflict") {
                IdempotencyDisposition::Conflict
            } else {
                disp
            };
            let result = if disp == IdempotencyDisposition::Conflict {
                None
            } else {
                s.results.get(&key).cloned()
            };
            Ok(IdempotencyReservation::new(disp, returned, result).unwrap())
        })
    }
}
fn ready<F: Future>(future: F) -> F::Output {
    let mut future = std::pin::pin!(future);
    match future
        .as_mut()
        .poll(&mut Context::from_waker(Waker::noop()))
    {
        Poll::Ready(v) => v,
        Poll::Pending => panic!("reference adapter must complete immediately"),
    }
}
#[derive(Default)]
struct Maps {
    tenant: String,
    business: Option<String>,
    keys: BTreeMap<String, String>,
    payloads: BTreeMap<String, String>,
}
fn reserve<'a>(
    coordinator: &'a IdempotencyCoordinator<'a>,
    i: &'a WriteIntent,
    c: &'a ToolContract,
    e: &'a ProviderIdempotencyEvidence,
    m: &'a mut Maps,
) -> impl Future<Output = Result<IdempotencyReservation, IdempotencyError>> + 'a {
    coordinator.reserve(IdempotencyRequest {
        intent: i,
        context: IdempotencyContext {
            tenant_id: &m.tenant,
            business_operation_id: m.business.as_deref(),
            keys_by_write_node: &mut m.keys,
            payload_hashes_by_write_node: &mut m.payloads,
        },
        contract: c,
        provider_evidence: e,
        now: now(),
        required_until: HORIZON.parse().unwrap(),
    })
}
fn maps() -> Maps {
    Maps {
        tenant: "tenant-a".into(),
        business: Some("business-0051".into()),
        ..Default::default()
    }
}
fn strings(d: &ValueData) -> BTreeMap<String, String> {
    let ValueData::Object(o) = d else { panic!() };
    o.iter().map(|(k, v)| (k.clone(), text(v).into())).collect()
}
fn map_data(m: &BTreeMap<String, String>) -> ValueData {
    ValueData::Object(m.iter().map(|(k, v)| (k.clone(), txt(v))).collect())
}
#[test]
fn fixed_oracle_reservation_state_and_failure_order() {
    for (name, d, expected) in rows(include_str!("fixtures/idempotency-reservations-oracle.tsv")) {
        let port = Durable {
            config: ValueDataConfig(Some(d.clone())),
            ..Default::default()
        };
        let coordinator = IdempotencyCoordinator::new(&port);
        let mut contexts: BTreeMap<String, Maps> = BTreeMap::new();
        let observations = arr(&arr(&expected)[1]);
        for (idx, step) in arr(field(&d, "steps")).iter().enumerate() {
            let blank = empty();
            let c = contract(opt(step, "contract").unwrap_or(&blank));
            let i = intent(opt(step, "intent").unwrap_or(&blank), c.tool_id());
            let e = standard_provider(&c, opt(step, "evidence").unwrap_or(&blank));
            let m = contexts
                .entry(string(step, "context", "default").into())
                .or_insert_with(|| {
                    let v = opt(step, "context_values").unwrap_or(&blank);
                    let mut m = maps();
                    m.tenant = string(v, "tenant_id", "tenant-a").into();
                    m.business = if opt(v, "business_operation_id") == Some(&ValueData::Null) {
                        None
                    } else {
                        Some(string(v, "business_operation_id", "business-0051").into())
                    };
                    m.keys = strings(opt(step, "keys").unwrap_or(&blank));
                    m.payloads = strings(opt(step, "payloads").unwrap_or(&blank));
                    m
                });
            if let Some(result) = opt(step, "complete") {
                let key = (
                    i.metadata().tenant_id.clone(),
                    i.metadata().tool_id.clone(),
                    idempotency_identity(&i)
                        .unwrap()
                        .idempotency_key()
                        .to_string(),
                );
                port.state
                    .lock()
                    .unwrap()
                    .results
                    .insert(key, text(result).into());
            }
            let before = (m.keys.clone(), m.payloads.clone());
            let result = ready(reserve(&coordinator, &i, &c, &e, m));
            if result.is_err() {
                assert_eq!((&m.keys, &m.payloads), (&before.0, &before.1), "{name}");
            }
            let o = &observations[idx];
            check_result(
                &format!("{name}-{idx}"),
                result.map(|v| v.to_data()),
                field(o, "result"),
            );
            assert_eq!(map_data(&m.keys), *field(o, "keys"), "{name}");
            assert_eq!(map_data(&m.payloads), *field(o, "payloads"), "{name}");
            let state = port.state.lock().unwrap();
            assert_eq!(
                state.calls.to_string(),
                match field(o, "calls") {
                    ValueData::Number(v) => v.as_str().to_owned(),
                    _ => panic!(),
                },
                "{name}"
            );
            assert_eq!(
                state.records.len().to_string(),
                match field(o, "records") {
                    ValueData::Number(v) => v.as_str().to_owned(),
                    _ => panic!(),
                },
                "{name}"
            );
        }
    }
}
#[test]
fn atomic_reservations_race_with_same_or_conflicting_payloads() {
    for count in [0, 1, 9, 10, 11, 32] {
        for conflict in [false, true] {
            let port = Arc::new(Durable::default());
            let barrier = Arc::new(Barrier::new(count.max(1)));
            let mut handles = vec![];
            for idx in 0..count {
                let port = Arc::clone(&port);
                let barrier = Arc::clone(&barrier);
                handles.push(std::thread::spawn(move || {
                    let cfg = if conflict && idx % 2 == 1 {
                        object([("amount", txt("84001"))])
                    } else {
                        empty()
                    };
                    let i = intent(&cfg, "FINANCE.CREATE_DRAFT");
                    let c = contract(&empty());
                    let e = standard_provider(&c, &empty());
                    let mut m = maps();
                    let coordinator = IdempotencyCoordinator::new(port.as_ref());
                    barrier.wait();
                    let result = ready(reserve(&coordinator, &i, &c, &e, &mut m));
                    match result {
                        Ok(r) => {
                            assert_eq!(m.keys.len(), 1);
                            assert_eq!(m.payloads["write0001"], i.payload_hash().as_str());
                            Some(r.disposition())
                        }
                        Err(e) => {
                            assert_eq!(e.kind, IdempotencyErrorKind::Conflict);
                            assert!(m.keys.is_empty());
                            assert!(m.payloads.is_empty());
                            None
                        }
                    }
                }));
            }
            let outcomes: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
            assert_eq!(
                outcomes
                    .iter()
                    .filter(|v| **v == Some(IdempotencyDisposition::Created))
                    .count(),
                usize::from(count > 0)
            );
            let state = port.state.lock().unwrap();
            assert_eq!(state.calls, count);
            assert_eq!(state.records.len(), usize::from(count > 0));
            if !conflict {
                assert!(outcomes.iter().all(Option::is_some));
            } else if count > 1 {
                let expected = if state.records.values().next().unwrap().payload_hash()
                    == intent(&empty(), "FINANCE.CREATE_DRAFT").payload_hash()
                {
                    count / 2
                } else {
                    count.div_ceil(2)
                };
                assert_eq!(outcomes.iter().filter(|v| v.is_none()).count(), expected);
            }
        }
    }
}
struct Interrupted {
    inner: Durable,
    accept: bool,
}
impl IdempotencyEvidencePort for Interrupted {
    fn reserve<'a>(&'a self, e: &'a IdempotencyEvidenceRecord) -> IdempotencyEvidenceFuture<'a> {
        Box::pin(async move {
            if self.accept {
                self.inner.reserve(e).await?;
            }
            std::future::pending().await
        })
    }
}
#[test]
fn dropping_pending_reservation_preserves_context_without_implying_rollback() {
    for accept in [false, true] {
        let p = Interrupted {
            inner: Durable::default(),
            accept,
        };
        let c = contract(&empty());
        let e = standard_provider(&c, &empty());
        let i = intent(&empty(), c.tool_id());
        let mut m = maps();
        m.keys.insert("other".into(), "raw".into());
        let before = (m.keys.clone(), m.payloads.clone());
        let coordinator = IdempotencyCoordinator::new(&p);
        {
            let f = reserve(&coordinator, &i, &c, &e, &mut m);
            let mut f = std::pin::pin!(f);
            assert!(
                f.as_mut()
                    .poll(&mut Context::from_waker(Waker::noop()))
                    .is_pending()
            );
        }
        assert_eq!((m.keys.clone(), m.payloads.clone()), before);
        assert_eq!(
            p.inner.state.lock().unwrap().records.len(),
            usize::from(accept)
        );
        let coordinator = IdempotencyCoordinator::new(&p.inner);
        let result = ready(reserve(&coordinator, &i, &c, &e, &mut m)).unwrap();
        assert_eq!(
            result.disposition(),
            if accept {
                IdempotencyDisposition::Reused
            } else {
                IdempotencyDisposition::Created
            }
        );
        assert_eq!(result.business_result_ref(), None);
    }
}
#[test]
fn native_types_safe_errors_and_immutable_evidence() {
    let i = idempotency_identity(&intent(&empty(), "FINANCE.CREATE_DRAFT")).unwrap();
    assert_eq!(i.business_operation_id().as_str(), "business-0051");
    assert!(i.payload_hash().as_str().starts_with("sha256:"));
    let mut spec = i.metadata().clone();
    spec.tenant_id = "changed".into();
    assert_eq!(i.metadata().tenant_id, "tenant-a");
    let r = standard_record();
    assert_eq!(r.business_operation_id(), i.business_operation_id());
    assert_eq!(
        r.contract_hash(),
        &contract(&empty()).contract_hash().unwrap()
    );
    let e = standard_provider(&contract(&empty()), &empty());
    let reservation = IdempotencyReservation::new(
        IdempotencyDisposition::Created,
        r.clone(),
        Some("result://original".into()),
    )
    .unwrap();
    assert_eq!(reservation.business_result_ref(), Some("result://original"));
    for debug in [
        format!("{i:?}"),
        format!("{e:?}"),
        format!("{r:?}"),
        format!("{reservation:?}"),
    ] {
        assert!(!debug.contains("tenant-a"));
        assert!(!debug.contains("sha256:"));
    }
    for invalid in [
        "2026-10-20T01:00:00",
        "2026-10-20T01:00:00+09:00",
        "10000-01-01T00:00:00Z",
        "0000-01-01T00:00:00Z",
    ] {
        assert!(invalid.parse::<UtcInstant>().is_err());
    }
    for code in [
        IdempotencyErrorKind::Identity,
        IdempotencyErrorKind::Type,
        IdempotencyErrorKind::Port,
        IdempotencyErrorKind::ContractEncoding,
    ] {
        let e = IdempotencyError {
            kind: code,
            path: "$.safe".into(),
            message: "safe",
        };
        assert_eq!(e.detail_code(), None);
        assert_eq!(e.to_string(), "$.safe: safe");
    }
    let _ = IdempotencyPortError;
    assert_eq!(
        format!("{:?}", IdempotencyPortError),
        "IdempotencyPortError"
    );
}
#[test]
fn contract_encoding_failure_has_no_reservation_or_state_mutation() {
    let i = intent(&empty(), "FINANCE.CREATE_DRAFT");
    let normal = contract(&empty());
    let e = standard_provider(&normal, &empty());
    let optional = TypeRef::new(TypeKind::Optional(Box::new(
        TypeRef::new(TypeKind::Primitive("Int".into())).unwrap(),
    )))
    .unwrap();
    let c = contract_type(&empty(), Some(optional));
    let p = Durable::default();
    let coordinator = IdempotencyCoordinator::new(&p);
    let mut m = maps();
    let err = ready(reserve(&coordinator, &i, &c, &e, &mut m)).unwrap_err();
    assert_eq!(err.kind, IdempotencyErrorKind::ContractEncoding);
    assert_eq!(err.path, "$.contract");
    assert_eq!(err.detail_code(), None);
    assert_eq!(p.state.lock().unwrap().calls, 0);
    assert!(m.keys.is_empty());
    assert!(m.payloads.is_empty());
}
#[test]
fn original_compatibility_observations_preserve_record_key_and_result() {
    let observation = parse(include_bytes!(
        "../../../../compatibility/v0_2_1/idempotency/idempotency-duplicate/observation.json"
    ));
    let expected = field(&observation, "stored_reservation");
    assert_eq!(standard_record().to_data(), *expected);
    let p = Durable::default();
    let coordinator = IdempotencyCoordinator::new(&p);
    let c = contract(&empty());
    let e = standard_provider(&c, &empty());
    let i = intent(&empty(), c.tool_id());
    let mut m = maps();
    let first = ready(reserve(&coordinator, &i, &c, &e, &mut m)).unwrap();
    assert_eq!(
        first.disposition().as_str(),
        get(&observation, "first_disposition")
    );
    p.state.lock().unwrap().results.insert(
        record_key(first.evidence()),
        get(&observation, "second_result_ref").into(),
    );
    let mut restart = maps();
    let second = ready(reserve(&coordinator, &i, &c, &e, &mut restart)).unwrap();
    assert_eq!(
        second.disposition().as_str(),
        get(&observation, "second_disposition")
    );
    assert_eq!(
        second.business_result_ref(),
        Some(get(&observation, "second_result_ref"))
    );
    let conflict = parse(include_bytes!(
        "../../../../compatibility/v0_2_1/idempotency/idempotency-conflict/observation.json"
    ));
    assert_eq!(get(&conflict, "second_disposition"), "CONFLICT");
    let changed = intent(&object([("amount", txt("84001"))]), c.tool_id());
    let mut fresh = maps();
    assert_eq!(
        ready(reserve(&coordinator, &changed, &c, &e, &mut fresh))
            .unwrap_err()
            .detail_code(),
        Some("IDEMPOTENCY_CONFLICT")
    );
    assert_eq!(p.state.lock().unwrap().records.len(), 1);
}
#[test]
fn admission_precedes_durable_effects_and_preserves_error_paths() {
    let i = intent(&empty(), "FINANCE.CREATE_DRAFT");
    let c = contract(&empty());
    let normal = standard_provider(&c, &empty());
    let invalid = standard_provider(
        &c,
        &object([("durable_duplicate_guarantee", ValueData::Bool(false))]),
    );
    let p = Durable::default();
    let coordinator = IdempotencyCoordinator::new(&p);
    for (tenant, business, key, payload, e, kind, path) in [
        (
            "other",
            Some("business-0051"),
            "bad",
            "bad",
            &invalid,
            IdempotencyErrorKind::State,
            "$.context.tenant_id",
        ),
        (
            "tenant-a",
            None,
            "bad",
            "bad",
            &invalid,
            IdempotencyErrorKind::State,
            "$.context.business_operation_id",
        ),
        (
            "tenant-a",
            Some("other"),
            "bad",
            "bad",
            &invalid,
            IdempotencyErrorKind::State,
            "$.context.business_operation_id",
        ),
        (
            "tenant-a",
            Some("business-0051"),
            "bad",
            "bad",
            &invalid,
            IdempotencyErrorKind::Retention,
            "$.provider_evidence.durable_duplicate_guarantee",
        ),
        (
            "tenant-a",
            Some("business-0051"),
            "bad",
            "bad",
            &normal,
            IdempotencyErrorKind::State,
            "$.context.keys_by_write_node[\"write0001\"]",
        ),
        (
            "tenant-a",
            Some("business-0051"),
            idempotency_identity(&i).unwrap().idempotency_key().as_str(),
            "bad",
            &normal,
            IdempotencyErrorKind::Conflict,
            "$.context.payload_hashes_by_write_node[\"write0001\"]",
        ),
    ] {
        let mut m = Maps {
            tenant: tenant.into(),
            business: business.map(String::from),
            keys: BTreeMap::from([("write0001".into(), key.into())]),
            payloads: BTreeMap::from([("write0001".into(), payload.into())]),
        };
        let before = (m.keys.clone(), m.payloads.clone());
        let err = ready(reserve(&coordinator, &i, &c, e, &mut m)).unwrap_err();
        assert_eq!(err.kind, kind);
        assert_eq!(err.path, path);
        assert_eq!((m.keys, m.payloads), before);
        assert_eq!(p.state.lock().unwrap().calls, 0);
    }
    let mut m = maps();
    drop(reserve(&coordinator, &i, &c, &normal, &mut m));
    assert_eq!(p.state.lock().unwrap().calls, 0);
    assert!(m.keys.is_empty());
    assert!(m.payloads.is_empty());
}
