#![allow(dead_code)]
use crate::{guard_support as g, invocation_support as i, requested_support as r, support::*};
use nsl_contract::*;
use nsl_core::*;
use nsl_write::*;
use std::{
    collections::BTreeMap,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};
pub fn optional(d: &ValueData, key: &str) -> Option<String> {
    g::opt(d, key)
        .filter(|v| **v != ValueData::Null)
        .map(|v| text(v).into())
}
fn bad(path: &str) -> VerificationError {
    VerificationError {
        kind: VerificationErrorKind::Model,
        path: path.into(),
        message: "invalid typed fixture field",
    }
}
fn schema(value: &ValueData) -> TypeRef {
    // A canonical projection may omit empty record/enum collections. These
    // fixtures construct owned models; they are not an external NSO decoder.
    match get(value, "kind") {
        "record" if g::opt(value, "fields").is_none() => TypeRef::new(TypeKind::Record {
            name: get(value, "name").into(),
            fields: vec![],
        })
        .unwrap(),
        "enum" if g::opt(value, "values").is_none() => TypeRef::new(TypeKind::Enum {
            name: get(value, "name").into(),
            values: vec![],
        })
        .unwrap(),
        _ => TypeRef::from_data(value).unwrap(),
    }
}
pub fn request_spec(d: &ValueData) -> VerificationRequestSpec {
    VerificationRequestSpec {
        identity: WriteOutcomeIdentity::new(i::identity_spec(field(d, "identity"))).unwrap(),
        verification_tool_id: get(d, "verification_tool_id").into(),
        business_reference: get(d, "business_reference").into(),
        provider_transaction_id: optional(d, "provider_transaction_id"),
        reference_schema: g::opt(d, "reference_schema")
            .filter(|v| **v != ValueData::Null)
            .map(schema),
        consistency_hint: optional(d, "consistency_hint"),
    }
}
pub fn request(d: &ValueData) -> Result<VerificationRequest, VerificationError> {
    for key in ["verification_tool_id", "business_reference"] {
        if !matches!(field(d, key), ValueData::String(_)) {
            return Err(bad(&format!("$.{key}")));
        }
    }
    VerificationRequest::new(request_spec(d))
}
pub fn observation(d: &ValueData) -> Result<VerificationObservation, VerificationError> {
    for key in [
        "request_hash",
        "business_reference",
        "evidence_ref",
        "observed_at",
    ] {
        if !matches!(field(d, key), ValueData::String(_)) {
            return Err(bad(&format!("$.{key}")));
        }
    }
    let observed = get(d, "observed_at")
        .parse()
        .map_err(|_| bad("$.observed_at"))?;
    VerificationObservation::new(VerificationObservationSpec {
        request_hash: get(d, "request_hash").into(),
        business_reference: get(d, "business_reference").into(),
        matched: g::yes(d, "matched"),
        evidence_ref: get(d, "evidence_ref").into(),
        observed_at: observed,
        reason_code: optional(d, "reason_code"),
    })
}
pub fn result(d: &ValueData) -> Result<VerificationResult, VerificationError> {
    let commit_state = get(d, "commit_state")
        .parse()
        .map_err(|_| bad("$.commit_state"))?;
    let status = get(d, "status").parse().map_err(|_| bad("$.status"))?;
    let observed_at = optional(d, "observed_at")
        .map(|s| s.parse().map_err(|_| bad("$.observed_at")))
        .transpose()?;
    VerificationResult::new(VerificationResultSpec {
        identity: WriteOutcomeIdentity::new(i::identity_spec(field(d, "identity"))).unwrap(),
        commit_state,
        status,
        business_reference: get(d, "business_reference").into(),
        verification_tool_id: optional(d, "verification_tool_id"),
        reason_code: optional(d, "reason_code"),
        evidence_ref: optional(d, "evidence_ref"),
        observed_at,
    })
}
pub fn hashed(r: &VerificationRequest) -> Result<ValueData, VerificationError> {
    Ok(object([
        ("request", r.to_data()?),
        ("canonical", txt(hex(&r.canonical_bytes()?))),
        ("request_hash", txt(r.request_hash()?.as_str())),
    ]))
}
pub fn contract(d: &ValueData) -> ToolContract {
    let write = if get(d, "capability") == "WRITE" {
        let w = field(d, "write_semantics");
        let idem = field(w, "idempotency");
        let v = g::opt(w, "verification").unwrap_or(&ValueData::Null);
        Some(
            WriteSemantics::new(WriteSemanticsSpec {
                side_effect: true,
                atomicity: get(w, "atomicity").into(),
                idempotency: Some(
                    IdempotencySemantics::new(
                        true,
                        get(idem, "scope"),
                        get(idem, "retention"),
                        get(idem, "duplicate_behavior"),
                        get(idem, "payload_conflict_behavior"),
                    )
                    .unwrap(),
                ),
                commit: Some(CommitSemantics::new(CommitConfirmation::Explicit)),
                approval_required: true,
                preconditions_supported: true,
                verification: if v == &ValueData::Null {
                    None
                } else {
                    Some(
                        PostWriteVerification::new(
                            optional(v, "tool_id"),
                            g::opt(v, "reference_schema")
                                .filter(|v| **v != ValueData::Null)
                                .map(|v| TypeRef::from_data(v).unwrap()),
                            optional(v, "consistency_hint"),
                        )
                        .unwrap(),
                    )
                },
            })
            .unwrap(),
        )
    } else {
        None
    };
    ToolContract::new(ToolContractSpec {
        tool_id: get(d, "tool_id").into(),
        version: get(d, "version").into(),
        capability: get(d, "capability").into(),
        input_types: arr(field(d, "inputs"))
            .iter()
            .map(|v| {
                (
                    get(v, "name").into(),
                    TypeRef::from_data(field(v, "type")).unwrap(),
                )
            })
            .collect(),
        output_type: TypeRef::from_data(field(d, "output")).unwrap(),
        required_scope: get(d, "required_scope").into(),
        output_classification: get(d, "output_classification").parse().unwrap(),
        options: ToolContractOptions {
            timeout_ms: match field(d, "timeout_ms") {
                ValueData::Number(n) => n.as_str().parse().unwrap(),
                _ => panic!(),
            },
            risk: get(d, "risk").into(),
            empty_is_valid: g::yes(d, "empty_is_valid"),
            operation_kind: optional(d, "operation_kind"),
            risk_level: optional(d, "risk_level"),
            write_semantics: write,
        },
    })
    .unwrap()
}
struct Provider {
    data: ValueData,
    calls: Arc<AtomicUsize>,
}
impl WriteExecutionPort for Provider {
    fn execute<'a>(
        &'a self,
        _: &'a WriteCallRequest,
        _: WriteCancellationSignal,
    ) -> WriteExecutionFuture<'a> {
        Box::pin(async move {
            self.calls.fetch_add(1, Ordering::SeqCst);
            Ok(i::parse_outcome(&self.data))
        })
    }
}
struct Store;
impl WriteOutcomeEvidencePort for Store {
    fn record_outcome<'a>(
        &'a self,
        record: &'a WriteOutcomeRecord,
    ) -> WriteOutcomeEvidenceFuture<'a> {
        Box::pin(async move {
            Ok(WriteOutcomeReceipt::new(WriteOutcomeReceiptSpec {
                record_hash: record.record_hash().to_string(),
                durable_ref: "outcome:verification".into(),
                persisted_at: "2026-10-20T03:00:02Z".parse().unwrap(),
            })
            .unwrap())
        })
    }
}
pub struct Setup {
    pub persisted: PersistedWriteOutcome,
    pub journal: Vec<ValueData>,
    pub write_calls: Arc<AtomicUsize>,
}
pub fn setup(d: &ValueData) -> Setup {
    let original = contract(field(d, "original_contract"));
    let hash = original.contract_hash().unwrap().to_string();
    let mut base = r::baseline();
    fn set(v: &mut ValueData, path: &[&str], hash: &str) {
        if path.is_empty() {
            *v = txt(hash);
            return;
        }
        let ValueData::Object(o) = v else { panic!() };
        set(o.get_mut(path[0]).unwrap(), &path[1..], hash);
    }
    for path in [
        vec!["contract", "contract_hash"],
        vec!["precondition", "contract_hash"],
        vec!["reservation", "evidence", "contract_hash"],
    ] {
        set(&mut base, &path, &hash);
    }
    let setup = r::Setup::new(base);
    let requested = r::Evidence::default();
    let mut flag = false;
    let durable = r::ready(WriteRequestedCoordinator::new(&requested).persist(
        setup.created(),
        &r::allowed(),
        r::context(&mut flag),
        r::now(),
    ))
    .unwrap();
    assert!(flag);
    let s = durable.request().metadata();
    let keys = BTreeMap::from([(s.node_id.clone(), s.idempotency_key.clone())]);
    let payloads = BTreeMap::from([(s.node_id.clone(), s.payload_hash.clone())]);
    let calls = Arc::new(AtomicUsize::new(0));
    let provider = Provider {
        data: field(d, "input_outcome").clone(),
        calls: calls.clone(),
    };
    let invocation = r::ready(
        WriteInvocationCoordinator::new(&provider)
            .invoke(durable, WriteCancellationSignal::default()),
    );
    let mut journal = Vec::new();
    let persisted = r::ready(WriteOutcomeCoordinator::new(&Store).persist(
        invocation,
        WriteOutcomeContext {
            tenant_id: "tenant-a",
            execution_id: "exec-0053",
            business_operation_id: Some("business-0053"),
            keys_by_write_node: &keys,
            payload_hashes_by_write_node: &payloads,
            journal: &mut journal,
        },
        "2026-10-20T03:00:02Z".parse().unwrap(),
    ))
    .unwrap();
    assert_eq!(
        persisted.invocation().request().metadata().contract_hash,
        hash
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    Setup {
        persisted,
        journal,
        write_calls: calls,
    }
}
pub fn string_map(d: &ValueData) -> BTreeMap<String, String> {
    let ValueData::Object(o) = d else { panic!() };
    o.iter().map(|(k, v)| (k.clone(), text(v).into())).collect()
}
pub struct Read {
    pub config: ValueData,
    pub requests: Mutex<Vec<ValueData>>,
}
impl PostWriteVerificationPort for Read {
    fn read<'a>(&'a self, request: &'a VerificationRequest) -> VerificationFuture<'a> {
        Box::pin(async move {
            self.requests.lock().unwrap().push(hashed(request).unwrap());
            let case = get(&self.config, "case");
            match case {
                "timeout" => return Err(VerificationPortError::Timeout),
                "dependency" => return Err(VerificationPortError::Dependency),
                "malformed" => return Err(VerificationPortError::Malformed),
                "cancelled" => return Err(VerificationPortError::Cancelled),
                _ => {}
            }
            let delta = match g::opt(&self.config, "delta") {
                Some(ValueData::Number(n)) => n.as_str().parse::<i64>().unwrap(),
                _ => 1_000_000,
            };
            let at = match delta {
                -1 => "2026-10-20T03:00:00.999999Z",
                0 => "2026-10-20T03:00:01Z",
                1 => "2026-10-20T03:00:01.000001Z",
                _ => "2026-10-20T03:00:02Z",
            };
            let matched = g::opt(&self.config, "matched") != Some(&ValueData::Bool(false));
            Ok(VerificationObservation::new(VerificationObservationSpec {
                request_hash: if matches!(case, "wrong-hash" | "wrong-hash-stale") {
                    format!("sha256:{}", "0".repeat(64))
                } else {
                    request.request_hash().unwrap().to_string()
                },
                business_reference: if case == "wrong-reference" {
                    "OTHER".into()
                } else {
                    request.metadata().business_reference.clone()
                },
                matched,
                evidence_ref: "verification:0053".into(),
                observed_at: at.parse().unwrap(),
                reason_code: if matched {
                    None
                } else {
                    Some("PROVIDER_STATE_MISMATCH".into())
                },
            })
            .unwrap())
        })
    }
}
pub fn rows(s: &str) -> impl Iterator<Item = (String, ValueData)> + '_ {
    s.lines().skip(1).map(|s| {
        let (n, h) = s.split_once('\t').unwrap();
        (n.into(), parse(&decode(h)))
    })
}
pub fn observed(d: &ValueData) -> (ValueData, Setup, usize) {
    let setup = setup(d);
    let original = crate::invocation_support::outcome_data(setup.persisted.invocation().outcome());
    let old = setup.journal.clone();
    let c = field(d, "context");
    let config = field(d, "config");
    let contract = contract(field(d, "selected_contract"));
    let keys = string_map(field(c, "keys_by_write_node"));
    let payloads = string_map(field(c, "payload_hashes_by_write_node"));
    let port = Read {
        config: config.clone(),
        requests: Mutex::new(vec![]),
    };
    let result = r::ready(PostWriteVerifier.verify(
        &setup.persisted,
        &contract,
        VerificationContext {
            tenant_id: get(c, "tenant_id"),
            business_operation_id: optional(c, "business_operation_id").as_deref(),
            keys_by_write_node: &keys,
            payload_hashes_by_write_node: &payloads,
        },
        if get(config, "case") == "missing-port" {
            None
        } else {
            Some(&port)
        },
    ));
    let (res, summary) = match result {
        Ok(r) => (
            ValueData::Array(vec![txt("ok"), r.to_data()]),
            r.to_summary_data(),
        ),
        Err(e) => {
            assert_eq!(e.detail_code(), None);
            let class = if e.kind == VerificationErrorKind::Cancelled {
                "CancelledError"
            } else if e.kind == VerificationErrorKind::Model {
                "TypeError"
            } else {
                "VerificationError"
            };
            (
                ValueData::Array(vec![txt("error"), txt(class)]),
                ValueData::Null,
            )
        }
    };
    assert_eq!(old, setup.journal);
    assert_eq!(
        crate::invocation_support::outcome_data(setup.persisted.invocation().outcome()),
        original
    );
    assert_eq!(setup.write_calls.load(Ordering::SeqCst), 1);
    let requests = port.requests.lock().unwrap();
    let calls = requests.len();
    (
        object([
            ("result", res),
            ("summary", summary),
            ("requests", ValueData::Array(requests.clone())),
            ("journal_unchanged", ValueData::Bool(true)),
        ]),
        setup,
        calls,
    )
}
