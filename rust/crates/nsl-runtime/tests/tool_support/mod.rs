#![allow(dead_code)]
use nsl_contract::{ToolContract, ToolContractOptions, ToolContractSpec};
use nsl_core::*;
use nsl_runtime::*;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{Arc, Mutex},
};

pub fn ty(s: &str) -> TypeRef {
    let kind = match s {
        "Domain" => TypeKind::Domain("Customer".into()),
        "USD" | "EUR" => TypeKind::Money(s.parse().unwrap()),
        "ListInt" => TypeKind::List(Box::new(ty("Int"))),
        "Optional" => TypeKind::Optional(Box::new(ty("Int"))),
        "ListOptional" => TypeKind::List(Box::new(ty("Optional"))),
        "Record" | "Duplicate" | "EmptyRecord" | "RecordOptional" => TypeKind::Record {
            name: "R".into(),
            fields: match s {
                "EmptyRecord" => vec![],
                "Duplicate" => vec![("a".into(), ty("String")), ("a".into(), ty("Int"))],
                "RecordOptional" => vec![("a".into(), ty("Optional"))],
                _ => vec![("a".into(), ty("Int"))],
            },
        },
        "Enum" => TypeKind::Enum {
            name: "E".into(),
            values: vec!["YES".into(), "NO".into()],
        },
        _ => TypeKind::Primitive(s.into()),
    };
    TypeRef::new(kind).unwrap()
}
pub fn value(s: &str) -> Value {
    match s {
        "null" => Value::Null,
        "false" | "true" => Value::Bool(s == "true"),
        "zero" => Value::Int(0.into()),
        "negative" => Value::Int((-1).into()),
        "seven" => Value::Int(7.into()),
        "wide" => Value::Int(format!("1{}", "0".repeat(100)).parse().unwrap()),
        "string" => Value::String("가😀".into()),
        "empty_string" => Value::String("".into()),
        "enum" => Value::String("YES".into()),
        "decimal" => Value::Decimal("1.2300".parse().unwrap()),
        "negative_zero" => Value::Decimal("-0.00".parse().unwrap()),
        "money" | "eur" => Value::Money(Money::new(
            if s == "money" { "12.500" } else { "12.5" }
                .parse()
                .unwrap(),
            if s == "money" { "USD" } else { "EUR" }.parse().unwrap(),
        )),
        "date" => Value::Date("2026-11-18".into()),
        "datetime" => Value::DateTime("2026-11-18T01:02:03".into()),
        "list_empty" => Value::List(vec![]),
        "list_int" => Value::List(vec![Value::Int(1.into()), Value::Int(2.into())]),
        "list_bool" => Value::List(vec![Value::Bool(true)]),
        "list_null" => Value::List(vec![Value::Null]),
        "record_empty" => Value::Record(BTreeMap::new()),
        "record_int" => Value::Record(BTreeMap::from([("a".into(), value("seven"))])),
        "record_string" => Value::Record(BTreeMap::from([("a".into(), Value::String("x".into()))])),
        "record_other" => Value::Record(BTreeMap::from([("b".into(), value("seven"))])),
        "record_null" => Value::Record(BTreeMap::from([("a".into(), Value::Null)])),
        "record_extra" => Value::Record(BTreeMap::from([
            ("a".into(), value("seven")),
            ("b".into(), Value::Int(8.into())),
        ])),
        "nested" => Value::Record(BTreeMap::from([
            ("a".into(), Value::Bool(false)),
            (
                "z".into(),
                Value::List(vec![
                    Value::Decimal("0.0100".parse().unwrap()),
                    Value::Record(BTreeMap::from([(
                        "x".into(),
                        Value::String("확인😀".into()),
                    )])),
                ]),
            ),
        ])),
        _ => panic!("unknown value {s}"),
    }
}
pub fn contract(output: TypeRef) -> ToolContract {
    ToolContract::new(ToolContractSpec {
        tool_id: "TEST.ECHO".into(),
        version: "1.0.0".into(),
        capability: "READ".into(),
        input_types: vec![("z".into(), ty("Int")), ("a".into(), ty("Int"))],
        output_type: output,
        required_scope: "read".into(),
        output_classification: DataClassification::Internal,
        options: ToolContractOptions::default(),
    })
    .unwrap()
}
pub fn principal() -> ExecutionPrincipal {
    ExecutionPrincipal {
        tenant_id: "tenant-a".into(),
        subject_id: "subject-a".into(),
        actor_type: "USER".into(),
        roles: BTreeSet::from(["reader".into()]),
        scopes: BTreeSet::from(["read".into()]),
        auth_context_ref: "auth:reference".into(),
        verification: PrincipalVerification::Unverified,
        on_behalf_of: None,
    }
}
pub fn request(c: &ToolContract) -> ToolCallRequest {
    ToolCallRequest {
        execution_id: "exec-a".into(),
        invocation_id: "inv-a".into(),
        node_id: "read0001".into(),
        tool_id: c.tool_id().into(),
        tool_version: c.version().into(),
        contract_hash: c.contract_hash().unwrap().to_string(),
        arguments: BTreeMap::from([
            ("a".into(), env(value("seven"), ty("Int"))),
            ("z".into(), env(Value::Int(9.into()), ty("Int"))),
        ]),
        principal: principal(),
        authorization_decision_ref: "authz:a".into(),
    }
}
pub fn env(v: Value, t: TypeRef) -> ValueEnvelope {
    ValueEnvelope::complete(v, t, DataClassification::Internal)
}
pub fn result() -> ToolResultEnvelope {
    ToolResultEnvelope {
        invocation_id: "inv-a".into(),
        tool_id: "TEST.ECHO".into(),
        tool_version: "1.0.0".into(),
        value: value("seven"),
        type_info: ty("Int"),
        presence: Presence::Present,
        completeness: Completeness::Complete,
        classification: DataClassification::Internal,
        result_hash: tool_result_hash(&value("seven"), 100).unwrap().to_string(),
        snapshot_ref: None,
    }
}
pub fn s(s: impl Into<String>) -> ValueData {
    ValueData::String(s.into())
}
pub fn n(n: impl std::fmt::Display) -> ValueData {
    ValueData::Number(n.to_string().parse().unwrap())
}
pub fn arr(v: impl IntoIterator<Item = ValueData>) -> ValueData {
    ValueData::Array(v.into_iter().collect())
}
pub fn error(e: ToolCallError) -> ValueData {
    let (kind, message) = match e {
        ToolCallError::Execution(e) => (e.code, e.message),
        ToolCallError::Provider(message) | ToolCallError::Measurement(message) => (
            if message.starts_with("synthetic") {
                "RuntimeError".into()
            } else {
                "ValueError".into()
            },
            message,
        ),
        ToolCallError::Cancelled => ("CancelledError".into(), String::new()),
        e => panic!("unexpected error {e:?}"),
    };
    arr([s(kind), s(message)])
}
pub fn observe(r: Result<(), ToolCallError>) -> ValueData {
    r.map_or_else(error, |()| arr([s("ok"), ValueData::Null]))
}
pub fn returned(r: Result<ToolResultEnvelope, ToolCallError>) -> ValueData {
    r.map_or_else(error, |v| arr([s("ok"), v.value.to_data(100).unwrap()]))
}
pub fn json(v: &ValueData) -> String {
    String::from_utf8(canonical_json(v).unwrap()).unwrap()
}
pub fn decode(s: &str) -> String {
    if s == "-" {
        return String::new();
    }
    String::from_utf8(
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
            .collect(),
    )
    .unwrap()
}
pub fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .start_paused(true)
        .build()
        .unwrap()
}
#[derive(Clone)]
pub struct Clock(pub Arc<Mutex<Integer>>);
impl Default for Clock {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(0.into())))
    }
}
impl Clock {
    pub fn set(&self, n: i64) {
        *self.0.lock().unwrap() = n.into();
    }
}
impl RuntimeClock for Clock {
    fn monotonic_ns(&self) -> Integer {
        self.0.lock().unwrap().clone()
    }
}
#[derive(Default)]
pub struct TestSink {
    pub inner: InMemoryToolMeasurementSink,
    pub fail: bool,
}
impl ToolMeasurementSink for TestSink {
    fn record(&self, m: ToolExecutionMeasurement) -> Result<(), ToolCallError> {
        self.inner.record(m)?;
        if self.fail {
            Err(ToolCallError::Measurement("synthetic sink failure".into()))
        } else {
            Ok(())
        }
    }
}
pub fn measurements(sink: &TestSink) -> ValueData {
    arr(sink.inner.measurements().iter().map(|m| {
        arr([
            s(m.outcome.as_str()),
            n(m.duration_ns.as_str()),
            n(m.duration_ms().as_str()),
        ])
    }))
}
#[derive(Default)]
pub struct Control {
    pub events: Mutex<Vec<String>>,
    pub cancel_seen: CancellationSignal,
    pub release: CancellationSignal,
}
pub struct Provider {
    pub mode: String,
    pub clock: Clock,
    pub control: Arc<Control>,
    pub cleanup: bool,
}
impl ToolExecutionPort for Provider {
    fn execute<'a>(
        &'a self,
        _request: &'a ToolCallRequest,
        cancel: CancellationSignal,
    ) -> ToolExecutionFuture<'a> {
        Box::pin(async move {
            if self.cleanup {
                self.control.events.lock().unwrap().push("started".into());
                self.clock.set(1_000_000);
                cancel.cancelled().await;
                self.control
                    .events
                    .lock()
                    .unwrap()
                    .push("cancel_received".into());
                self.clock.set(2_000_000);
                self.control.cancel_seen.request();
                self.control.release.cancelled().await;
                self.control
                    .events
                    .lock()
                    .unwrap()
                    .push("cleanup_completed".into());
                self.clock.set(3_000_000);
                match self.mode.as_str() {
                    "propagate" | "sink_error" => Err(ToolCallError::Cancelled),
                    "raise_error" => {
                        Err(ToolCallError::Provider("synthetic cleanup failure".into()))
                    }
                    "provider_timeout" => Err(ToolCallError::ProviderTimeout),
                    "suppress" => Ok(result()),
                    _ => panic!(),
                }
            } else {
                self.clock.set(if self.mode == "backward" {
                    0
                } else {
                    2_999_999
                });
                match self.mode.as_str() {
                    "error" => Err(ToolCallError::Provider("synthetic provider failure".into())),
                    "cancelled" => Err(ToolCallError::Cancelled),
                    "provider_timeout" => Err(ToolCallError::ProviderTimeout),
                    _ => Ok(result()),
                }
            }
        })
    }
}
