#![allow(dead_code)]
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime::*;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};
#[derive(Clone, Default)]
pub struct Clock(pub Arc<AtomicUsize>);
impl Clock {
    pub fn calls(&self) -> usize {
        self.0.load(Ordering::SeqCst)
    }
}
impl RuntimeClock for Clock {
    fn monotonic_ns(&self) -> Integer {
        self.0.fetch_add(1, Ordering::SeqCst);
        1_000_000_000.into()
    }
}
pub fn limits() -> CheckpointLimits {
    CheckpointLimits {
        outer: JsonLimits {
            max_bytes: 1_000_000,
            max_depth: 128,
            max_nodes: 100_000,
        },
        state: JsonLimits {
            max_bytes: 500_000,
            max_depth: 128,
            max_nodes: 100_000,
        },
        max_decimal_bytes: 100_000,
    }
}
pub fn decode(hex: &str) -> Vec<u8> {
    (0..hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
        .collect()
}
pub fn skill(ver: &str) -> Arc<SkillObject> {
    let bytes = if ver == "1-0" {
        include_bytes!("../../../../../compatibility/v0_2_1/nso/ir-1-0-canonical/expected.nso")
            .as_slice()
    } else {
        include_bytes!("../../../../../compatibility/v0_2_1/nso/ir-1-1-canonical/expected.nso")
            .as_slice()
    };
    Arc::new(
        NsoCodec::decode(
            bytes,
            NsoLimits {
                max_bytes: 100_000,
                max_depth: 128,
                max_nodes: 100_000,
            },
        )
        .unwrap(),
    )
}
pub fn request() -> ExecutionRequest {
    ExecutionRequest::new(
        "exec-checkpoint".into(),
        BTreeMap::from([("year".into(), Value::Int(2026.into()))]),
        BTreeMap::from([(
            "user".into(),
            Value::Record(BTreeMap::from([(
                "team_id".into(),
                Value::String("TEAM-FINANCE".into()),
            )])),
        )]),
        ExecutionSecurityContext {
            principal: ExecutionPrincipal {
                tenant_id: "tenant-a".into(),
                subject_id: "subject-a".into(),
                actor_type: "USER".into(),
                roles: BTreeSet::from(["z".into(), "a".into()]),
                scopes: BTreeSet::from(["write".into(), "read".into()]),
                auth_context_ref: "auth:reference".into(),
                verification: PrincipalVerification::Verified,
                on_behalf_of: None,
            },
            data_policy: DataHandlingPolicy::default(),
        },
        Some("business-a".into()),
    )
    .unwrap()
}
pub fn ty(s: &str) -> TypeRef {
    TypeRef::new(TypeKind::Primitive(s.into())).unwrap()
}
pub fn env(
    value: Value,
    ty: TypeRef,
    class: DataClassification,
    provenance: &str,
) -> ValueEnvelope {
    ValueEnvelope::new(
        value,
        ty,
        Presence::Present,
        Completeness::Complete,
        class,
        vec![provenance.into()],
    )
}
pub fn context(ver: &str, recipe: &str, prior: &str) -> ExecutionContext<Clock> {
    let mut c = ExecutionContext::with_clock(skill(ver), request(), Clock::default());
    c.checkpoint.checkpoint_version = prior.parse().unwrap();
    if recipe == "empty" {
        return c;
    }
    let v = env(
        Value::Int(2026.into()),
        ty("Int"),
        DataClassification::Confidential,
        "input:year",
    );
    let t = env(
        Value::String("TEAM-FINANCE".into()),
        ty("String"),
        DataClassification::Internal,
        "context:team",
    );
    for (ns, name, value) in [
        (BindingNamespace::Input, "sym-input", v.clone()),
        (BindingNamespace::Context, "sym-context", t.clone()),
        (BindingNamespace::Variable, "sym-root", v.clone()),
        (BindingNamespace::Check, "check-root", t.clone()),
    ] {
        c.bind(ns, name.into(), value).unwrap();
    }
    c.push_frame();
    c.bind(BindingNamespace::Variable, "sym-inner".into(), t.clone())
        .unwrap();
    c.bind(BindingNamespace::Check, "check-inner".into(), v.clone())
        .unwrap();
    c.checks.push(CheckResult {
        check_id: "rule".into(),
        status: CheckStatus::Pass,
        severity: "ERROR".into(),
        message: "확인😀".into(),
        condition_node_id: "condition".into(),
        presence: Presence::Present,
        completeness: Completeness::Complete,
        provenance_refs: vec!["p:z".into(), "p:a".into()],
        reason_code: None,
    });
    c.outputs
        .push(EmitRecord::new(vec![("team".into(), t), ("year".into(), v)]).unwrap());
    c.guard_mut().before_tool_call().unwrap();
    for _ in 0..if recipe.starts_with("safe-") { 1 } else { 2 } {
        c.guard_mut().before_loop_iteration().unwrap();
    }
    c.guard_mut().record_emit();
    // Failed observations retain the observed maximum, as in the runtime.
    assert!(matches!(
        c.guard_mut().observe_collection(2.into()),
        Ok(()) | Err(LimitExceeded(ResourceLimit::CollectionSize))
    ));
    c.observe_completeness(if let Some(c) = recipe.strip_prefix("completeness-") {
        c.parse().unwrap()
    } else {
        Completeness::Partial
    });
    c.invocation_counter = 3.into();
    c.foreach_depth = 1;
    c.current_node_id = Some("approval0".into());
    c.approvals
        .records
        .insert("approval0".into(), ApprovalStatus::Approved);
    c.writes.entries.push(ValueData::Object(BTreeMap::from([
        (
            "event_type".into(),
            ValueData::String("WRITE_REQUESTED".into()),
        ),
        ("node_id".into(), ValueData::String("write0".into())),
    ])));
    c.idempotency
        .keys_by_write_node
        .insert("write0".into(), "idem-write0".into());
    c.idempotency
        .payload_hashes_by_write_node
        .insert("write0".into(), format!("sha256:{}", "a".repeat(64)));
    c.checkpoint.last_completed_node_id = Some("approval0".into());
    c.checkpoint.suspended_at_node_id = Some("write0".into());
    c.checkpoint.trace_sequence = 4.into();
    c.checkpoint.approval_request_published = true;
    c.checkpoint.write_request_durable = true;
    c.checkpoint
        .approval_request_ids_by_write_node
        .insert("write0".into(), "request0".into());
    c.checkpoint
        .write_intent_hashes_by_write_node
        .insert("write0".into(), "intent0".into());
    c.checkpoint
        .payload_hashes_by_write_node
        .insert("write0".into(), format!("sha256:{}", "b".repeat(64)));
    if recipe.ends_with("values") {
        let list = TypeRef::new(TypeKind::List(Box::new(ty("Int")))).unwrap();
        for (name, value, ty) in [
            (
                "list",
                Value::List(vec![Value::Int(1.into()), Value::Int(2.into())]),
                list.clone(),
            ),
            (
                "record",
                Value::Record(BTreeMap::from([(
                    "a".into(),
                    Value::List(vec![Value::Int(1.into()), Value::Int(2.into())]),
                )])),
                TypeRef::new(TypeKind::Record {
                    name: "R".into(),
                    fields: vec![("a".into(), list)],
                })
                .unwrap(),
            ),
            (
                "decimal",
                Value::Decimal("-0.000".parse().unwrap()),
                ty("Decimal"),
            ),
            (
                "money",
                Value::Money(Money::new(
                    "12.500".parse().unwrap(),
                    "USD".parse().unwrap(),
                )),
                TypeRef::new(TypeKind::Money("USD".parse().unwrap())).unwrap(),
            ),
            ("date", Value::Date("2026-11-20".into()), ty("Date")),
            (
                "time",
                Value::DateTime("2026-11-20T10:02:03.123456+09:00".into()),
                ty("DateTime"),
            ),
            ("bool", Value::Bool(false), ty("Bool")),
        ] {
            c.bind(
                BindingNamespace::Variable,
                format!("value-{name}"),
                ValueEnvelope::complete(value, ty, DataClassification::Internal),
            )
            .unwrap();
        }
    }
    c
}
pub fn raw_state(cp: &ExecutionCheckpoint) -> ValueData {
    parse_json_with_limits(cp.state_payload(), limits().state).unwrap()
}
pub fn obj(v: &mut ValueData) -> &mut BTreeMap<String, ValueData> {
    if let ValueData::Object(o) = v {
        o
    } else {
        panic!()
    }
}
pub fn at<'a>(v: &'a mut ValueData, keys: &[&str]) -> &'a mut ValueData {
    let mut v = v;
    for key in keys {
        v = obj(v).get_mut(*key).unwrap();
    }
    v
}
pub fn resign(cp: &ExecutionCheckpoint, state: ValueData) -> ExecutionCheckpoint {
    let mut spec = cp.metadata().clone();
    spec.state_payload = canonical_json(&state).unwrap();
    ExecutionCheckpoint::create(spec, limits()).unwrap()
}
pub fn base() -> ExecutionCheckpoint {
    ExecutionCheckpoint::from_bytes(
        &decode(
            include_str!("../fixtures/checkpoints-oracle.tsv")
                .lines()
                .find(|l| l.starts_with("1-1\tpopulated\t0\t"))
                .unwrap()
                .split('\t')
                .nth(4)
                .unwrap(),
        ),
        limits(),
    )
    .unwrap()
}
