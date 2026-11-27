#![allow(dead_code)]
use crate::{guard_support as g, invocation_support as i, support::*};
use nsl_contract::*;
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime as rt;
use nsl_write::*;
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};
pub fn write_contract(d: &ValueData) -> ToolContract {
    let w = field(d, "write_semantics");
    let idem = field(w, "idempotency");
    let v = field(w, "verification");
    ToolContract::new(ToolContractSpec {
        tool_id: get(d, "tool_id").into(),
        version: get(d, "version").into(),
        capability: get(d, "capability").into(),
        input_types: arr(field(d, "inputs"))
            .iter()
            .map(|i| {
                (
                    get(i, "name").into(),
                    TypeRef::from_data(field(i, "type")).unwrap(),
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
            operation_kind: Some(get(d, "operation_kind").into()),
            risk_level: Some(get(d, "risk_level").into()),
            write_semantics: Some(
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
                                Some(get(v, "tool_id").into()),
                                Some(TypeRef::from_data(field(v, "reference_schema")).unwrap()),
                                Some(get(v, "consistency_hint").into()),
                            )
                            .unwrap(),
                        )
                    },
                })
                .unwrap(),
            ),
        },
    })
    .unwrap()
}
pub fn bindings(d: &ValueData) -> rt::ApprovalBindingsSpec {
    rt::ApprovalBindingsSpec {
        tenant_id: get(d, "tenant_id").into(),
        execution_id: get(d, "execution_id").into(),
        business_operation_id: get(d, "business_operation_id").into(),
        approval_id: get(d, "approval_id").into(),
        approval_request_id: get(d, "approval_request_id").into(),
        skill_semantic_hash: get(d, "skill_semantic_hash").into(),
        payload_hash: get(d, "payload_hash").into(),
        write_intent_hash: get(d, "write_intent_hash").into(),
    }
}
pub fn evidence(d: &ValueData) -> rt::ApprovalEvidence {
    rt::ApprovalEvidence::new(rt::ApprovalEvidenceSpec {
        bindings: bindings(d),
        decision: get(d, "decision").parse().unwrap(),
        approver_id: get(d, "approver_id").into(),
        decided_at: g::instant(d, "decided_at"),
        expires_at: Some(g::instant(d, "expires_at")),
        evidence_integrity: get(d, "evidence_integrity").into(),
    })
    .unwrap()
}
pub fn base() -> ValueData {
    parse(include_bytes!("../fixtures/controlled-runtime-base.json"))
}
pub fn catalog(b: &ValueData) -> ToolContractCatalog {
    ToolContractCatalog::new(
        arr(field(b, "contracts"))
            .iter()
            .map(|d| {
                if get(d, "capability") == "WRITE" {
                    return write_contract(d);
                }
                ToolContract::new(ToolContractSpec {
                    tool_id: get(d, "tool_id").into(),
                    version: get(d, "version").into(),
                    capability: "READ".into(),
                    input_types: arr(field(d, "inputs"))
                        .iter()
                        .map(|d| {
                            (
                                get(d, "name").into(),
                                TypeRef::from_data(field(d, "type")).unwrap(),
                            )
                        })
                        .collect(),
                    output_type: TypeRef::from_data(field(d, "output")).unwrap(),
                    required_scope: get(d, "required_scope").into(),
                    output_classification: get(d, "output_classification").parse().unwrap(),
                    options: ToolContractOptions {
                        timeout_ms: text_number(field(d, "timeout_ms")).parse().unwrap(),
                        risk: get(d, "risk").into(),
                        risk_level: Some(get(d, "risk_level").into()),
                        empty_is_valid: g::yes(d, "empty_is_valid"),
                        ..Default::default()
                    },
                })
                .unwrap()
            })
            .collect::<Vec<_>>(),
    )
    .unwrap()
}
fn text_number(v: &ValueData) -> &str {
    let ValueData::Number(n) = v else { panic!() };
    n.as_str()
}
pub fn skill(b: &ValueData) -> Arc<SkillObject> {
    Arc::new(NsoCodec::decode(&decode(get(b, "skill")), rt_options().nso_limits).unwrap())
}
pub fn rt_options() -> rt::RuntimeOptions {
    rt::RuntimeOptions {
        nso_limits: NsoLimits {
            max_bytes: 1_000_000,
            max_depth: 128,
            max_nodes: 100_000,
        },
        max_decimal_bytes: 100_000,
        environment: rt::RuntimeEnvironment::Production,
    }
}
pub fn checkpoint() -> rt::CheckpointCoordinator {
    rt::CheckpointCoordinator::for_oracle(rt::CheckpointLimits {
        outer: limits().canonical,
        state: limits().canonical,
        max_decimal_bytes: 100_000,
    })
}
pub fn request(b: &ValueData, mode: &str) -> rt::ExecutionRequest {
    let r = field(b, "request");
    let p = field(r, "principal");
    let mut principal = rt::ExecutionPrincipal {
        tenant_id: get(p, "tenant_id").into(),
        subject_id: get(p, "subject_id").into(),
        actor_type: get(p, "actor_type").into(),
        roles: g::strings(field(p, "roles")),
        scopes: g::strings(field(p, "scopes")),
        auth_context_ref: get(p, "auth_context_ref").into(),
        verification: rt::PrincipalVerification::Verified,
        on_behalf_of: None,
    };
    if mode == "scope" {
        principal.scopes.remove("finance:voucher:write");
    }
    if mode == "unauthorized" {
        principal.scopes.clear();
    }
    let record = |v: &ValueData| {
        let Value::Record(v) = value(v) else { panic!() };
        v
    };
    rt::ExecutionRequest::new(
        get(r, "execution_id").into(),
        record(field(r, "inputs")),
        record(field(r, "context")),
        rt::ExecutionSecurityContext {
            principal,
            data_policy: rt::DataHandlingPolicy::default(),
        },
        if mode == "no-business" {
            None
        } else {
            Some(get(r, "business_operation_id").into())
        },
    )
    .unwrap()
}
#[derive(Clone)]
pub struct Clock;
impl rt::RuntimeClock for Clock {
    fn monotonic_ns(&self) -> Integer {
        1_000_000_000.into()
    }
}
pub fn tools(b: &ValueData, c: &ToolContractCatalog, mode: &str) -> rt::MockToolExecutor {
    let mut handlers: BTreeMap<String, rt::FixtureHandler> = BTreeMap::new();
    for d in arr(field(field(b, "reads"), "tools")) {
        let case = field(d, "cases");
        let case = &arr(case)[0];
        let expected = value(field(case, "arguments"));
        let mut result = value(field(case, "result"));
        if mode == "check-fail" && get(d, "tool_id") == "ACCOUNT.GET_ACCOUNT" {
            let Value::Record(v) = &mut result else {
                panic!()
            };
            v.insert("status".into(), Value::String("CLOSED".into()));
        }
        handlers.insert(
            get(d, "tool_id").into(),
            Arc::new(move |args| {
                assert_eq!(Value::Record(args.clone()), expected);
                Ok(result.clone().into())
            }),
        );
    }
    rt::MockToolExecutor::new(c.clone(), handlers, 100_000)
}
pub fn approval(b: &ValueData) -> rt::ApprovalRequest {
    let d = field(b, "approval");
    rt::ApprovalRequest::new(rt::ApprovalRequestSpec {
        bindings: bindings(d),
        skill_id: get(d, "skill_id").into(),
        skill_version: get(d, "skill_version").into(),
        write_node_id: get(d, "write_node_id").into(),
        tool_id: get(d, "tool_id").into(),
        risk_level: get(d, "risk_level").parse().unwrap(),
        message: get(d, "message").into(),
        expires_at: Some(g::instant(d, "expires_at")),
        display_safe_summary: rt::SafeApprovalSummary::new(
            &rt::ApprovalSummaryInput::Object(BTreeMap::from([(
                "document_type".into(),
                rt::ApprovalSummaryInput::Text("voucher-draft".into()),
            )])),
            rt::ApprovalSummaryLimits {
                max_depth: 128,
                max_nodes: 100_000,
                max_text_bytes: 100_000,
            },
        )
        .unwrap(),
        protected_payload_ref: g::optional(d, "protected_payload_ref"),
        data_classification: get(d, "data_classification").parse().unwrap(),
    })
    .unwrap()
}
pub fn provider_evidence(b: &ValueData) -> ProviderIdempotencyEvidence {
    let d = field(b, "provider_evidence");
    ProviderIdempotencyEvidence::new(ProviderIdempotencyEvidenceSpec {
        tool_id: get(d, "tool_id").into(),
        contract_hash: get(d, "contract_hash").into(),
        retention_policy: get(d, "retention_policy").into(),
        provider_guarantee_ref: get(d, "provider_guarantee_ref").into(),
        verified_at: g::instant(d, "verified_at"),
        guaranteed_until: g::instant(d, "guaranteed_until"),
        durable_duplicate_guarantee: true,
    })
    .unwrap()
}
#[derive(Default)]
pub struct State {
    pub checkpoint: Option<rt::ExecutionCheckpoint>,
    pub consumed: bool,
    pub claimed: bool,
    pub events: Vec<String>,
    pub requested: Vec<ValueData>,
    pub outcomes: Vec<ValueData>,
    pub verification: usize,
    pub writes: usize,
}
pub struct Ports {
    pub b: ValueData,
    pub mode: String,
    pub state: Mutex<State>,
}
impl Ports {
    pub fn new(b: ValueData, mode: &str) -> Self {
        Self {
            b,
            mode: mode.into(),
            state: Mutex::new(State::default()),
        }
    }
    fn event(&self, e: &str) {
        self.state.lock().unwrap().events.push(e.into());
    }
}
impl rt::CheckpointPort for Ports {
    fn save<'a>(&'a self, c: &'a rt::ExecutionCheckpoint) -> rt::SuspensionPortFuture<'a> {
        Box::pin(async move {
            self.event("save");
            self.state.lock().unwrap().checkpoint = Some(c.clone());
            Ok(())
        })
    }
}
impl rt::ApprovalPort for Ports {
    fn publish<'a>(&'a self, _: &'a rt::ApprovalRequest) -> rt::SuspensionPortFuture<'a> {
        Box::pin(async move {
            self.event("publish");
            Ok(())
        })
    }
}
impl rt::ResumeCheckpointPort for Ports {
    fn claim<'a>(
        &'a self,
        _: &'a str,
        _: &'a Integer,
        _: &'a str,
    ) -> rt::ResumeClaimFuture<'a, rt::ExecutionCheckpoint> {
        Box::pin(async move {
            self.event("claim");
            let mut s = self.state.lock().unwrap();
            if s.consumed {
                return Err(rt::ResumeClaimError::Consumed);
            }
            if s.claimed {
                return Err(rt::ResumeClaimError::Live);
            }
            s.claimed = true;
            Ok(s.checkpoint.clone().unwrap())
        })
    }
    fn release<'a>(
        &'a self,
        _: &'a str,
        _: &'a Integer,
        _: &'a str,
    ) -> rt::ResumeClaimFuture<'a, ()> {
        Box::pin(async move {
            self.event("release");
            self.state.lock().unwrap().claimed = false;
            Ok(())
        })
    }
    fn consume<'a>(
        &'a self,
        _: &'a str,
        _: &'a Integer,
        _: &'a str,
    ) -> rt::ResumeClaimFuture<'a, ()> {
        Box::pin(async move {
            self.event("consume");
            self.state.lock().unwrap().consumed = true;
            Ok(())
        })
    }
}
impl rt::ApprovalEvidenceVerifier for Ports {
    fn verify(&self, e: &rt::ApprovalEvidence) -> Result<bool, rt::ApprovalVerifierError> {
        self.event("verify");
        Ok(e.metadata().evidence_integrity == "approval-signature:0063"
            && self.mode != "approval-invalid")
    }
}
impl RuntimePolicyPort for Ports {
    fn authorize_write(
        &self,
        r: &WritePolicyRequest,
    ) -> Result<PolicyDecision, RuntimePolicyError> {
        self.event("policy");
        assert_eq!(
            r.metadata().write_intent_hash,
            get(field(&self.b, "intent"), "write_intent_hash")
        );
        if self.mode == "policy-error" {
            return Err(RuntimePolicyError);
        }
        let mut p = g::policy(field(&self.b, "policy"))
            .unwrap()
            .metadata()
            .clone();
        if self.mode == "policy-deny" {
            p.effect = PolicyEffect::Deny;
        }
        Ok(PolicyDecision::new(p).unwrap())
    }
}
impl IdempotencyEvidencePort for Ports {
    fn reserve<'a>(&'a self, r: &'a IdempotencyEvidenceRecord) -> IdempotencyEvidenceFuture<'a> {
        Box::pin(async move {
            self.event("reserve");
            Ok(IdempotencyReservation::new(
                match self.mode.as_str() {
                    "reused" | "pending" => IdempotencyDisposition::Reused,
                    "conflict" => IdempotencyDisposition::Conflict,
                    _ => IdempotencyDisposition::Created,
                },
                r.clone(),
                if self.mode == "reused" {
                    Some("result:original".into())
                } else {
                    None
                },
            )
            .unwrap())
        })
    }
}
impl WriteEvidencePort for Ports {
    fn record_requested<'a>(&'a self, r: &'a WriteAttemptRecord) -> WriteEvidenceFuture<'a> {
        Box::pin(async move {
            self.event("requested");
            self.state.lock().unwrap().requested.push(r.to_data());
            if self.mode == "requested-error" {
                return Err(WriteEvidencePortError);
            }
            Ok(WriteRequestedReceipt::new(WriteRequestedReceiptSpec {
                record_hash: if self.mode == "requested-receipt" {
                    format!("sha256:{}", "f".repeat(64))
                } else {
                    r.record_hash().to_string()
                },
                durable_ref: "write-requested:0063".into(),
                persisted_at: r.metadata().requested_at,
                durable: true,
            })
            .unwrap())
        })
    }
}
impl WriteExecutionPort for Ports {
    fn execute<'a>(
        &'a self,
        r: &'a WriteCallRequest,
        c: WriteCancellationSignal,
    ) -> WriteExecutionFuture<'a> {
        Box::pin(async move {
            self.event("provider");
            self.state.lock().unwrap().writes += 1;
            if self.mode == "cleanup-committed" {
                c.cancelled().await;
            }
            if self.mode == "unknown-commit" || self.mode == "cleanup-error" {
                if self.mode == "cleanup-error" {
                    c.cancelled().await;
                }
                return Err(WriteProviderError::Timeout);
            }
            if self.mode == "not-committed" {
                return Ok(WriteExecutionOutcome::NotCommitted(
                    WriteNotCommitted::new(WriteFailureSpec {
                        identity: WriteOutcomeIdentity::from_request(r),
                        reason_code: "BUSINESS_REJECTED".into(),
                        message: "provider confirmed no mutation".into(),
                        provider_transaction_id: None,
                        evidence_refs: vec![],
                    })
                    .unwrap(),
                ));
            }
            let mut result = i::result(field(&self.b, "committed")).unwrap();
            if matches!(self.mode.as_str(), "result-type" | "result-value") {
                let old = result.metadata();
                let mut spec = WriteResultEnvelopeSpec {
                    identity: old.identity.clone(),
                    value: old.value.clone(),
                    business_reference: old.business_reference.clone(),
                    provider_transaction_id: old.provider_transaction_id.clone(),
                    committed_at: old.committed_at,
                    evidence_refs: old.evidence_refs.clone(),
                };
                spec.value = ValueEnvelope::complete(
                    Value::Int(42.into()),
                    if self.mode == "result-type" {
                        TypeRef::new(TypeKind::Primitive("Int".into())).unwrap()
                    } else {
                        spec.value.type_ref().clone()
                    },
                    DataClassification::Confidential,
                );
                result = WriteResultEnvelope::new(spec).unwrap();
            }
            assert_eq!(
                result.metadata().identity,
                WriteOutcomeIdentity::from_request(r)
            );
            Ok(WriteExecutionOutcome::Committed(result))
        })
    }
}
impl WriteOutcomeEvidencePort for Ports {
    fn record_outcome<'a>(&'a self, r: &'a WriteOutcomeRecord) -> WriteOutcomeEvidenceFuture<'a> {
        Box::pin(async move {
            self.event("outcome");
            self.state.lock().unwrap().outcomes.push(r.to_data());
            if self.mode == "outcome-error" {
                return Err(WriteOutcomeEvidencePortError::Storage);
            }
            Ok(WriteOutcomeReceipt::new(WriteOutcomeReceiptSpec {
                record_hash: if self.mode == "outcome-receipt" {
                    format!("sha256:{}", "f".repeat(64))
                } else {
                    r.record_hash().to_string()
                },
                durable_ref: "write-outcome:0063".into(),
                persisted_at: r.metadata().recorded_at,
            })
            .unwrap())
        })
    }
}
impl PostWriteVerificationPort for Ports {
    fn read<'a>(&'a self, r: &'a VerificationRequest) -> VerificationFuture<'a> {
        Box::pin(async move {
            self.event("verification");
            self.state.lock().unwrap().verification += 1;
            if self.mode == "verification-unknown" {
                return Err(VerificationPortError::Timeout);
            }
            if self.mode == "verification-cancelled" {
                return Err(VerificationPortError::Cancelled);
            }
            Ok(VerificationObservation::new(VerificationObservationSpec {
                request_hash: r.request_hash().unwrap().to_string(),
                business_reference: r.metadata().business_reference.clone(),
                matched: self.mode != "verification-failed",
                evidence_ref: "verification:0063".into(),
                observed_at: g::instant(
                    &self.b,
                    if self.mode == "stale" {
                        "now"
                    } else {
                        "observed_at"
                    },
                ),
                reason_code: if self.mode == "verification-failed" {
                    Some("PROVIDER_STATE_MISMATCH".into())
                } else {
                    None
                },
            })
            .unwrap())
        })
    }
}
pub fn set(d: &mut ValueData, k: &str, v: ValueData) {
    let ValueData::Object(o) = d else { panic!() };
    o.insert(k.into(), v);
}
pub fn at<'a>(d: &'a mut ValueData, k: &str) -> &'a mut ValueData {
    let ValueData::Object(o) = d else { panic!() };
    o.get_mut(k).unwrap()
}
pub fn rebind(b: &mut ValueData, s: &SkillObject, c: &ToolContractCatalog) {
    let w = s
        .required_tools
        .iter()
        .find(|r| r.kind.capability() == ToolCapability::Write)
        .unwrap();
    let contract = c.resolve(&w.tool_id, &w.version).unwrap();
    let hash = contract.contract_hash().unwrap().to_string();
    let i = field(b, "intent");
    let new_intent = compute_write_intent_hash(
        &s.semantic_hash.to_string(),
        get(i, "node_id"),
        get(i, "tool_id"),
        get(i, "payload_hash"),
    )
    .unwrap()
    .to_string();
    for name in ["intent", "approval", "proof"] {
        set(
            at(b, name),
            "skill_semantic_hash",
            txt(s.semantic_hash.to_string()),
        );
    }
    for name in [
        "intent",
        "approval",
        "proof",
        "precondition",
        "policy",
        "call",
    ] {
        set(at(b, name), "write_intent_hash", txt(&new_intent));
    }
    set(
        at(at(b, "committed"), "identity"),
        "write_intent_hash",
        txt(&new_intent),
    );
    for name in ["precondition", "provider_evidence", "call"] {
        set(at(b, name), "contract_hash", txt(&hash));
    }
}
