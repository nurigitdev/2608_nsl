#![allow(dead_code)]
use crate::{expression_support as e, read_support as r, support};
use nsl_contract::*;
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime::*;
use std::{
    collections::BTreeMap,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};

pub struct Clock {
    values: Vec<Integer>,
    calls: AtomicUsize,
}
impl Clock {
    pub fn new(s: &str) -> Self {
        Self {
            values: s.split(',').map(|v| v.parse().unwrap()).collect(),
            calls: AtomicUsize::new(0),
        }
    }
    pub fn calls(&self) -> usize {
        self.calls.load(Ordering::SeqCst)
    }
}
impl RuntimeClock for Clock {
    fn monotonic_ns(&self) -> Integer {
        let i = self.calls.fetch_add(1, Ordering::SeqCst);
        self.values[i.min(self.values.len() - 1)].clone()
    }
}
pub fn options(environment: &str) -> RuntimeOptions {
    RuntimeOptions {
        nso_limits: NsoLimits {
            max_bytes: 100_000,
            max_depth: 128,
            max_nodes: 100_000,
        },
        max_decimal_bytes: 100_000,
        environment: if environment == "production" {
            RuntimeEnvironment::Production
        } else {
            RuntimeEnvironment::Development
        },
    }
}
pub fn val(s: &str) -> Value {
    match s {
        "secret" => Value::String("CONFIDENTIAL-PROJECT".into()),
        "int2" => Value::List(vec![Value::Int(1.into()), Value::Int(2.into())]),
        _ => e::value(s),
    }
}
pub fn skill(f: &[&str]) -> Arc<SkillObject> {
    let mut s = NsoCodec::decode(
        support::decode(f[3]).as_bytes(),
        options("production").nso_limits,
    )
    .unwrap();
    match f[12] {
        "profile" => s.semantics_profile = "UNSUPPORTED".into(),
        "language" => s.language_version = "9.9".into(),
        "unbounded" => s.analysis.bounded = false,
        "required-hash" => {
            s.required_tools[0].contract_hash =
                format!("sha256:{}", "0".repeat(64)).parse().unwrap()
        }
        "optional-missing" => s.inputs[0].required = false,
        "emit-declaration" => s.outputs[0].type_info = e::ty("String"),
        "no-approval" => s
            .body
            .retain(|n| !matches!(n.kind, StatementKind::Approval(_))),
        "approval-ref" => {
            for n in &mut s.body {
                if let StatementKind::Let { value, .. } = &mut n.kind
                    && let ExpressionKind::Write(w) = &mut value.kind
                {
                    w.approval_ref = "wrong".into();
                }
            }
        }
        "-" | "hash" | "input-missing" | "context-missing" | "business-missing" => {}
        m => panic!("mutation {m}"),
    }
    let limits = f[11]
        .split(',')
        .map(|v| v.parse::<Integer>().unwrap())
        .collect::<Vec<_>>();
    s.limits.collection_size = limits[0].clone();
    s.limits.loop_iterations = limits[1].clone();
    s.limits.emitted_rows = limits[2].clone();
    s.limits.tool_calls = limits[3].clone();
    s.limits.duration_ms = limits[4].clone();
    s.semantic_hash = f[19].parse().unwrap();
    Arc::new(s)
}
pub fn request(f: &[&str], s: &SkillObject) -> ExecutionRequest {
    let mut inputs = BTreeMap::new();
    let mut context = BTreeMap::new();
    let mut nested = BTreeMap::new();
    if f[4] != "-" {
        for token in f[4].split(';') {
            let p = token.split(',').collect::<Vec<_>>();
            if p[0] == "c" {
                nested.insert(p[1].into(), val(p[2]));
            } else {
                inputs.insert(p[1].into(), val(p[2]));
            }
        }
    }
    if !nested.is_empty() {
        context.insert("nested".into(), Value::Record(nested));
    }
    if matches!(f[12], "input-missing" | "optional-missing") {
        inputs.remove(&s.inputs[0].name);
    }
    if f[12] == "context-missing" {
        context.clear();
    }
    if f[16] == "input" {
        inputs.insert("extra".into(), Value::String("unused".into()));
    }
    if f[16] == "context" {
        context.insert("extra".into(), Value::String("unused".into()));
    }
    let mut p = support::principal();
    p.roles.clear();
    p.scopes = if f[9] == "-" {
        Default::default()
    } else {
        f[9].split('+').map(Into::into).collect()
    };
    p.verification = if f[13] == "unverified" {
        PrincipalVerification::Unverified
    } else {
        PrincipalVerification::Verified
    };
    if f[13] == "tenant" {
        p.tenant_id.clear();
    }
    if f[13] == "actor" {
        p.actor_type = "OTHER".into();
    }
    ExecutionRequest::new(
        "exec-a".into(),
        inputs,
        context,
        ExecutionSecurityContext {
            principal: p,
            data_policy: DataHandlingPolicy::default(),
        },
        if f[15] == "yes" {
            Some("business-a".into())
        } else {
            None
        },
    )
    .unwrap()
}
pub fn write_contract() -> ToolContract {
    let options = ToolContractOptions {
        operation_kind: Some("CREATE".into()),
        risk_level: Some("R2".into()),
        write_semantics: Some(
            WriteSemantics::new(WriteSemanticsSpec {
                side_effect: true,
                atomicity: "ATOMIC".into(),
                idempotency: Some(
                    IdempotencySemantics::new(true, "tenant", "P1D", "RETURN_PREVIOUS", "REJECT")
                        .unwrap(),
                ),
                commit: Some(CommitSemantics::new(CommitConfirmation::Explicit)),
                approval_required: true,
                preconditions_supported: true,
                verification: None,
            })
            .unwrap(),
        ),
        ..ToolContractOptions::default()
    };
    ToolContract::new(ToolContractSpec {
        tool_id: "TEST.WRITE".into(),
        version: "1.0.0".into(),
        capability: "WRITE".into(),
        input_types: vec![],
        output_type: e::ty("Int"),
        required_scope: "write".into(),
        output_classification: DataClassification::Internal,
        options,
    })
    .unwrap()
}
pub fn catalog(f: &[&str]) -> ToolContractCatalog {
    let setup = match f[17] {
        "write" => "write_scope",
        "missing" => "valid",
        v => v,
    };
    let mut contracts = vec![r::contract("Int", "valid", true), write_contract()];
    if f[17] != "missing" {
        contracts.insert(0, r::contract(f[5], setup, false));
    }
    ToolContractCatalog::new(contracts).unwrap()
}
pub struct Provider {
    pub base: r::Provider,
    pub mode: String,
}
impl Provider {
    pub fn new(f: &[&str]) -> Self {
        let mutation = if f[8] == "identity" {
            "invocation"
        } else {
            f[8]
        };
        let p = vec![
            f[1], "-", f[5], f[6], f[7], mutation, f[9], f[10], "100", "100", "0", "valid",
        ];
        Self {
            base: r::Provider::new(&p),
            mode: f[8].into(),
        }
    }
}
impl ToolExecutionPort for Provider {
    fn execute<'a>(
        &'a self,
        req: &'a ToolCallRequest,
        cancel: CancellationSignal,
    ) -> ToolExecutionFuture<'a> {
        match self.mode.as_str() {
            "error" | "sink-error" | "tool-error" | "timeout" | "cancelled" | "encoding"
            | "contract-mismatch" => Box::pin(async move {
                self.base.requests.lock().unwrap().push(r::capture(req));
                Err(match self.mode.as_str() {
                    "tool-error" => ToolExecutionError::new(
                        "CUSTOM_FAILURE",
                        "missing CONFIDENTIAL-PROJECT".into(),
                    )
                    .into(),
                    "timeout" => ToolCallError::ProviderTimeout,
                    "cancelled" => ToolCallError::Cancelled,
                    "encoding" => ToolCallError::Encoding(ResultEncodingError::Type(
                        TypeError::InternalOptional,
                    )),
                    "contract-mismatch" => {
                        ToolExecutionError::new("TOOL_CONTRACT_MISMATCH", "changed contract".into())
                            .into()
                    }
                    _ => ToolCallError::Provider(
                        "synthetic provider failure CONFIDENTIAL-PROJECT".into(),
                    ),
                })
            }),
            _ => self.base.execute(req, cancel),
        }
    }
}
#[derive(Default)]
pub struct Measurements {
    pub records: Mutex<Vec<ToolExecutionMeasurement>>,
    pub fail: bool,
}
impl ToolMeasurementSink for Measurements {
    fn record(&self, m: ToolExecutionMeasurement) -> Result<(), ToolCallError> {
        self.records.lock().unwrap().push(m);
        if self.fail {
            Err(ToolCallError::Measurement(
                "synthetic measurement failure".into(),
            ))
        } else {
            Ok(())
        }
    }
}
#[derive(Default)]
pub struct DebugSink {
    pub records: Mutex<Vec<ProtectedRuntimeDebugRecord>>,
    pub fail: bool,
}
impl ProtectedRuntimeDebugSink for DebugSink {
    fn record(&self, r: ProtectedRuntimeDebugRecord) -> Result<(), RuntimeDebugSinkError> {
        self.records.lock().unwrap().push(r);
        if self.fail {
            Err(RuntimeDebugSinkError)
        } else {
            Ok(())
        }
    }
}
pub fn measurement(m: &ToolExecutionMeasurement) -> ValueData {
    ValueData::Object(
        [
            ("execution_id", e::text(&m.execution_id)),
            ("invocation_id", e::text(&m.invocation_id)),
            ("node_id", e::text(&m.node_id)),
            ("tool_id", e::text(&m.tool_id)),
            ("tool_version", e::text(&m.tool_version)),
            ("tenant_id", e::text(&m.tenant_id)),
            ("outcome", e::text(m.outcome.as_str())),
            ("duration_ns", e::n(m.duration_ns.as_str())),
        ]
        .into_iter()
        .map(|(k, v)| (k.into(), v))
        .collect(),
    )
}
pub fn outcome(result: Result<ExecutionResult, RuntimeTerminalError>) -> ValueData {
    match result {
        Ok(r) => e::arr([
            e::text("ok"),
            r.to_data(100_000).unwrap(),
            r.semantic_view(100_000).unwrap(),
        ]),
        Err(e) => match e.kind {
            RuntimeTerminalKind::Contract(message) => e::arr([
                e::text("exception"),
                e::text("RuntimeContractError"),
                e::text(message),
            ]),
            RuntimeTerminalKind::InvalidValidationSummary(cause) => e::arr([
                e::text("exception"),
                e::text("ValueError"),
                e::text(cause.to_string()),
            ]),
            kind => panic!("unexpected terminal {kind:?}"),
        },
    }
}
