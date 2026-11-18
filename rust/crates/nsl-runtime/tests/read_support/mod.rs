#![allow(dead_code)]
use crate::{expression_support as e, support};
pub use e::{arr, text, ty, value};
use nsl_contract::*;
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime::*;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{Arc, Mutex},
};

pub fn contract(output: &str, setup: &str, inner: bool) -> ToolContract {
    let mut options = ToolContractOptions::default();
    let capability = match setup {
        "write" | "write_scope" => "WRITE",
        "custom" => "CUSTOM",
        "transaction" => "TRANSACTION",
        _ => "READ",
    };
    if matches!(setup, "write" | "write_scope") {
        options.operation_kind = Some("CREATE".into());
        options.risk_level = Some("R2".into());
        options.write_semantics = Some(
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
        );
    }
    if setup == "short_timeout" {
        options.timeout_ms = 1;
    }
    ToolContract::new(ToolContractSpec {
        tool_id: if inner { "TEST.INNER" } else { "TEST.READ" }.into(),
        version: if setup == "version" && !inner {
            "2.0.0"
        } else {
            "1.0.0"
        }
        .into(),
        capability: capability.into(),
        input_types: if inner {
            vec![]
        } else {
            vec![("z".into(), ty("Int")), ("a".into(), ty("Int"))]
        },
        output_type: ty(output),
        required_scope: match setup {
            "scope" => "admin",
            "write_scope" => "write",
            "empty_scope" => "",
            _ => "read",
        }
        .into(),
        output_classification: DataClassification::Internal,
        options,
    })
    .unwrap()
}
pub fn required(c: &ToolContract, version: &str, setup: &str, inner: bool) -> RequiredTool {
    let capability = if setup == "declared_write" {
        ToolCapability::Write
    } else {
        ToolCapability::Read
    };
    RequiredTool {
        tool_ref: if inner { "t1" } else { "t0" }.into(),
        tool_id: c.tool_id().into(),
        version: "1.0.0".into(),
        kind: if version == "1-0" {
            RequiredToolKind::ReadV1_0
        } else {
            RequiredToolKind::V1_1 {
                capability,
                risk_level: RiskLevel::R0,
            }
        },
        contract_hash: if setup == "hash" {
            format!("sha256:{}", "0".repeat(64)).parse().unwrap()
        } else {
            c.contract_hash().unwrap()
        },
        required_scope: if matches!(setup, "scope" | "write_scope") {
            "read"
        } else {
            c.required_scope()
        }
        .into(),
        output_classification: DataClassification::Internal,
    }
}
pub fn skill(f: &[&str]) -> Arc<SkillObject> {
    let bytes = if f[0] == "1-0" {
        include_bytes!("../../../../../compatibility/v0_2_1/nso/ir-1-0-canonical/expected.nso")
            .as_slice()
    } else {
        include_bytes!("../../../../../compatibility/v0_2_1/nso/ir-1-1-canonical/expected.nso")
            .as_slice()
    };
    let mut skill = NsoCodec::decode(
        bytes,
        NsoLimits {
            max_bytes: 100_000,
            max_depth: 64,
            max_nodes: 10_000,
        },
    )
    .unwrap();
    skill.required_tools = vec![
        required(&contract(f[2], f[11], false), f[0], f[11], false),
        required(&contract("Int", "valid", true), f[0], "valid", true),
    ];
    skill.limits.collection_size = f[8].parse().unwrap();
    skill.limits.tool_calls = f[9].parse().unwrap();
    skill.limits.duration_ms = 10.into();
    Arc::new(skill)
}
pub fn catalog(f: &[&str]) -> ToolContractCatalog {
    ToolContractCatalog::new(if f[11] == "missing_catalog" {
        vec![]
    } else {
        vec![contract(f[2], f[11], false), contract("Int", "valid", true)]
    })
    .unwrap()
}
pub fn request(scopes: &str) -> ExecutionRequest {
    let mut principal = support::principal();
    principal.roles.clear();
    principal.scopes = if scopes == "-" {
        BTreeSet::new()
    } else {
        scopes.split('+').map(Into::into).collect()
    };
    principal.verification = PrincipalVerification::Verified;
    ExecutionRequest::new(
        "exec-a".into(),
        BTreeMap::new(),
        BTreeMap::new(),
        ExecutionSecurityContext {
            principal,
            data_policy: Default::default(),
        },
        None,
    )
    .unwrap()
}
pub fn env(v: &ValueEnvelope) -> ValueData {
    let ValueData::Array(mut data) = e::snapshot(v) else {
        panic!()
    };
    data.remove(0);
    arr(data)
}
pub fn capture(r: &ToolCallRequest) -> ValueData {
    arr([
        text(&r.execution_id),
        text(&r.invocation_id),
        text(&r.node_id),
        text(&r.tool_id),
        text(&r.tool_version),
        text(&r.contract_hash),
        ValueData::Object(
            r.arguments
                .iter()
                .map(|(k, v)| (k.clone(), env(v)))
                .collect(),
        ),
        text(&r.principal.tenant_id),
        text(&r.principal.subject_id),
        arr(r.principal.scopes.iter().map(text)),
        text(&r.authorization_decision_ref),
    ])
}
#[derive(Default)]
pub struct Auth {
    pub calls: Mutex<Vec<ValueData>>,
}
impl ReadAuthorizationPort for Auth {
    fn authorize(
        &self,
        p: &ExecutionPrincipal,
        action: &str,
        scopes: &BTreeSet<String>,
    ) -> Result<String, ReadAuthorizationError> {
        self.calls
            .lock()
            .unwrap()
            .push(arr([text(action), arr(scopes.iter().map(text))]));
        StaticReadAuthorizer::default().authorize(p, action, scopes)
    }
}
pub struct Provider {
    pub output: TypeRef,
    pub value: Value,
    pub completeness: Completeness,
    pub mutation: String,
    pub requests: Mutex<Vec<ValueData>>,
}
impl Provider {
    pub fn new(f: &[&str]) -> Self {
        Self {
            output: ty(f[2]),
            value: value(f[3]),
            completeness: f[4].parse().unwrap(),
            mutation: f[5].into(),
            requests: Mutex::new(vec![]),
        }
    }
}
impl ToolExecutionPort for Provider {
    fn execute<'a>(
        &'a self,
        r: &'a ToolCallRequest,
        _c: CancellationSignal,
    ) -> ToolExecutionFuture<'a> {
        Box::pin(async move {
            self.requests.lock().unwrap().push(capture(r));
            match self.mutation.as_str() {
                "provider_error" => {
                    return Err(ToolCallError::Provider("synthetic provider failure".into()));
                }
                "provider_timeout" => return Err(ToolCallError::ProviderTimeout),
                "tool_timeout" => {
                    return Err(ToolExecutionError::new(
                        "TOOL_TIMEOUT",
                        "synthetic timeout".into(),
                    )
                    .into());
                }
                _ => {}
            }
            let inner = r.tool_id == "TEST.INNER";
            let value = if inner {
                Value::Int(7.into())
            } else {
                self.value.clone()
            };
            let presence = if matches!(&value, Value::Null)
                || matches!(&value,Value::List(v) if v.is_empty())
            {
                Presence::Empty
            } else {
                Presence::Present
            };
            let mut result = ToolResultEnvelope {
                invocation_id: r.invocation_id.clone(),
                tool_id: r.tool_id.clone(),
                tool_version: r.tool_version.clone(),
                result_hash: tool_result_hash(&value, 100_000).unwrap().to_string(),
                value,
                type_info: if inner {
                    ty("Int")
                } else {
                    self.output.clone()
                },
                presence,
                completeness: self.completeness,
                classification: DataClassification::Internal,
                snapshot_ref: None,
            };
            match self.mutation.as_str() {
                "invocation" => result.invocation_id = "wrong".into(),
                "tool" => result.tool_id = "wrong".into(),
                "version" => result.tool_version = "2.0.0".into(),
                "type" => result.type_info = ty("String"),
                "class" => result.classification = DataClassification::Restricted,
                "bad_value" => result.value = Value::Bool(true),
                "hash" => result.result_hash = format!("sha256:{}", "0".repeat(64)),
                "hash_empty" => result.result_hash.clear(),
                "snapshot_empty" => result.snapshot_ref = Some("".into()),
                "snapshot_blank" => result.snapshot_ref = Some(" ".into()),
                "snapshot" => result.snapshot_ref = Some("snapshot:a".into()),
                "force_empty" => result.presence = Presence::Empty,
                _ => {}
            }
            Ok(result)
        })
    }
}
pub fn error(e: ReadExecutionError) -> ValueData {
    match e {
        ReadExecutionError::Expression(e) => {
            let ValueData::Array(mut a) = e::error(e) else {
                panic!()
            };
            a.extend([ValueData::Null, ValueData::Null]);
            arr(a)
        }
        ReadExecutionError::Tool(e) => {
            let (class, message, code) = match e {
                ToolCallError::Execution(e) => ("ToolExecutionError", e.message, text(e.code)),
                ToolCallError::Provider(message) => ("RuntimeError", message, ValueData::Null),
                ToolCallError::Cancelled => ("CancelledError", String::new(), ValueData::Null),
                e => panic!("unexpected {e:?}"),
            };
            arr([
                text(class),
                text(message),
                ValueData::Null,
                ValueData::Null,
                code,
                ValueData::Null,
            ])
        }
        ReadExecutionError::Authorization(e) => arr([
            text("AuthorizationError"),
            text(e.message),
            ValueData::Null,
            ValueData::Null,
            ValueData::Null,
            e.decision_ref.map_or(ValueData::Null, text),
        ]),
        ReadExecutionError::Catalog(e) => {
            let (class, message) = match e.kind {
                CatalogErrorKind::UnknownToolContract => {
                    ("UnknownToolContractError", "unknown tool contract")
                }
                CatalogErrorKind::IncompatibleToolVersion => {
                    ("IncompatibleToolVersionError", "incompatible tool version")
                }
                _ => panic!(),
            };
            arr([
                text(class),
                text(format!("'{message}: {}@{}'", e.tool_id, e.version)),
                ValueData::Null,
                ValueData::Null,
                ValueData::Null,
                ValueData::Null,
            ])
        }
        e => panic!("unexpected {e:?}"),
    }
}
