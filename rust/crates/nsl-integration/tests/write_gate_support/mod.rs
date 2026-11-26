use crate::{guard_support as g, support::*};
use nsl_contract::*;
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime as rt;
use nsl_write::*;
use std::{
    collections::BTreeMap,
    future::Future,
    sync::Mutex,
    task::{Context, Poll, Waker},
};
pub fn contract(d: &ValueData) -> ToolContract {
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
                    verification: Some(
                        PostWriteVerification::new(
                            Some(get(v, "tool_id").into()),
                            Some(TypeRef::from_data(field(v, "reference_schema")).unwrap()),
                            Some(get(v, "consistency_hint").into()),
                        )
                        .unwrap(),
                    ),
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
pub fn intent(d: &ValueData, skill: &SkillObject) -> WriteIntent {
    let expr = skill
        .body
        .iter()
        .find_map(|n| {
            if let StatementKind::Let { value, .. } = &n.kind {
                if matches!(value.kind, ExpressionKind::Write(_)) {
                    Some(value)
                } else {
                    None
                }
            } else {
                None
            }
        })
        .unwrap();
    let args = arguments(field(d, "canonical_arguments"));
    let i = WriteIntentBuilder
        .build(
            expr,
            &args,
            WriteIntentContext {
                tenant_id: get(d, "tenant_id"),
                business_operation_id: Some(get(d, "business_operation_id")),
                skill,
            },
            snapshot(limits()),
        )
        .unwrap();
    assert_eq!(i.payload_hash().as_str(), get(d, "payload_hash"));
    assert_eq!(i.write_intent_hash().as_str(), get(d, "write_intent_hash"));
    assert_eq!(
        i.metadata().skill_semantic_hash,
        get(d, "skill_semantic_hash")
    );
    i
}
pub fn summary(
    d: &ValueData,
    skill: &SkillObject,
) -> (rt::ValidationSummary, Vec<rt::CheckResult>) {
    let checks=arr(field(d,"predicates")).iter().map(|p|{
  let stmt=skill.body.iter().find(|n|matches!(&n.kind,StatementKind::Check{check_id,..} if check_id==get(p,"check_id"))).unwrap();
  rt::StrictRuleEvaluator.evaluate(stmt,&ValueEnvelope::new(Value::Bool(g::yes(p,"truth")),TypeRef::new(TypeKind::Primitive("Bool".into())).unwrap(),get(p,"presence").parse().unwrap(),get(p,"completeness").parse().unwrap(),DataClassification::Internal,arr(field(p,"provenance_refs")).iter().map(|v|text(v).into()).collect())).unwrap()
 }).collect::<Vec<_>>();
    (rt::ValidationSummary::from_checks(&checks).unwrap(), checks)
}
#[derive(Default)]
pub struct Counts {
    events: Vec<String>,
    policy: usize,
    reserve: usize,
    stored: usize,
    provider: usize,
}
pub struct Ports<'a> {
    pub case: &'a str,
    pub policy: PolicyDecision,
    pub expected: WritePolicyRequest,
    pub prior: UtcInstant,
    pub counts: Mutex<Counts>,
}
impl rt::ApprovalEvidenceVerifier for Ports<'_> {
    fn verify(&self, _: &rt::ApprovalEvidence) -> Result<bool, rt::ApprovalVerifierError> {
        self.counts.lock().unwrap().events.push("verify".into());
        Ok(self.case != "approval-proof")
    }
}
impl RuntimePolicyPort for Ports<'_> {
    fn authorize_write(
        &self,
        r: &WritePolicyRequest,
    ) -> Result<PolicyDecision, RuntimePolicyError> {
        assert_eq!(r, &self.expected);
        let mut c = self.counts.lock().unwrap();
        c.events.push("policy".into());
        c.policy += 1;
        Ok(self.policy.clone())
    }
}
impl IdempotencyEvidencePort for Ports<'_> {
    fn reserve<'a>(&'a self, r: &'a IdempotencyEvidenceRecord) -> IdempotencyEvidenceFuture<'a> {
        Box::pin(async move {
            let mut c = self.counts.lock().unwrap();
            c.events.push("reserve".into());
            c.reserve += 1;
            let disp = if self.case == "idempotency-conflict" {
                IdempotencyDisposition::Conflict
            } else if matches!(self.case, "pending" | "reused") {
                IdempotencyDisposition::Reused
            } else {
                IdempotencyDisposition::Created
            };
            Ok(IdempotencyReservation::new(
                disp,
                r.clone(),
                if self.case == "reused" {
                    Some("result:original".into())
                } else {
                    None
                },
            )
            .unwrap())
        })
    }
}
impl WriteEvidencePort for Ports<'_> {
    fn record_requested<'a>(&'a self, r: &'a WriteAttemptRecord) -> WriteEvidenceFuture<'a> {
        Box::pin(async move {
            let mut c = self.counts.lock().unwrap();
            c.events.push("WRITE_REQUESTED".into());
            c.stored += 1;
            if self.case == "storage-error" {
                return Err(WriteEvidencePortError);
            }
            let prior = self.prior;
            Ok(WriteRequestedReceipt::new(WriteRequestedReceiptSpec {
                record_hash: if self.case == "receipt-hash" {
                    format!("sha256:{}", "f".repeat(64))
                } else {
                    r.record_hash().to_string()
                },
                durable_ref: "gate-durable".into(),
                persisted_at: if self.case == "receipt-time" {
                    prior
                } else {
                    r.metadata().requested_at
                },
                durable: self.case != "receipt-durable",
            })
            .unwrap())
        })
    }
}
fn ready<F: Future>(f: F) -> F::Output {
    let mut f = std::pin::pin!(f);
    match f.as_mut().poll(&mut Context::from_waker(Waker::noop())) {
        Poll::Ready(v) => v,
        Poll::Pending => panic!("deterministic port must be ready"),
    }
}
fn approval_data(v: rt::ApprovalValidationResult) -> ValueData {
    v.to_data()
}
pub fn run(d: &ValueData, base: &ValueData) -> ValueData {
    let config = field(d, "config");
    let case = get(config, "case");
    let skill = NsoCodec::decode(
        &decode(get(d, "skill")),
        NsoLimits {
            max_bytes: 1_000_000,
            max_depth: 128,
            max_nodes: 100_000,
        },
    )
    .unwrap();
    let i = intent(field(d, "intent"), &skill);
    let c = contract(field(d, "contract"));
    let ce = g::contract_evidence(field(d, "contract_evidence")).unwrap();
    let p = field(d, "principal");
    let roles = g::strings(field(p, "roles"));
    let scopes = g::strings(field(p, "scopes"));
    let now = g::instant(base, "now");
    let ports = Ports {
        case,
        policy: g::policy(field(d, "policy")).unwrap(),
        expected: g::policy_request(field(d, "expected_policy_request")).unwrap(),
        prior: g::instant(base, "prior"),
        counts: Mutex::new(Counts::default()),
    };
    let av = rt::ApprovalEvidenceValidator
        .validate(
            &evidence(field(d, "approval")),
            &rt::ApprovalBindings::new(bindings(field(d, "approval_bindings"))).unwrap(),
            &ports,
            &rt::RecordedApprovalClock(now),
        )
        .unwrap();
    let (sum, checks) = summary(d, &skill);
    let mut e = g::execution(field(d, "execution"));
    e.validation = sum.spec().status.as_str().parse().unwrap();
    e.approval_status = av.approval_status();
    e.approval_evidence_valid = av.approval_gate_passed();
    let pre = if field(d, "precondition") == &ValueData::Null {
        None
    } else {
        Some(g::precondition(field(d, "precondition")).unwrap())
    };
    let context = WriteGuardContext {
        principal: WritePrincipal {
            tenant_id: get(p, "tenant_id"),
            subject_id: get(p, "subject_id"),
            actor_type: get(p, "actor_type"),
            roles: &roles,
            scopes: &scopes,
            auth_context_ref: get(p, "auth_context_ref"),
            verified: get(p, "verification") == "VERIFIED",
            on_behalf_of: None,
        },
        business_operation_id: Some(get(base, "business_operation_id")),
    };
    let mut phase = "guard";
    let mut code = None;
    let mut decision = None;
    let mut hash = None;
    let mut flag = g::opt(config, "old_flag") == Some(&ValueData::Bool(true));
    let mut keys = BTreeMap::new();
    let mut payloads = BTreeMap::new();
    let attempt: Result<(), ()> = (|| {
        let dec = WriteGuard::default()
            .authorize(
                WriteGuardRequest {
                    skill: &skill,
                    intent: &i,
                    context,
                    current_contract: &c,
                    contract_evidence: &ce,
                    execution_evidence: &e,
                    policy_decision: None,
                    business_preconditions: pre.as_ref(),
                },
                &ports,
                now,
            )
            .map_err(|_| ())?;
        let allowed = dec.eligibility() == PreWriteEligibility::Allowed;
        decision = Some(dec.clone());
        if !allowed {
            return Err(());
        }
        phase = "idempotency";
        let provider = ProviderIdempotencyEvidence::new(ProviderIdempotencyEvidenceSpec {
            tool_id: c.tool_id().into(),
            contract_hash: c.contract_hash().unwrap().to_string(),
            retention_policy: "PROVIDER_DEFINED".into(),
            provider_guarantee_ref: "gate-provider-guarantee".into(),
            verified_at: now,
            guaranteed_until: g::instant(base, "provider_until"),
            durable_duplicate_guarantee: case != "retention",
        })
        .unwrap();
        let res = ready(
            IdempotencyCoordinator::new(&ports).reserve(IdempotencyRequest {
                intent: &i,
                context: IdempotencyContext {
                    tenant_id: get(p, "tenant_id"),
                    business_operation_id: Some(get(base, "business_operation_id")),
                    keys_by_write_node: &mut keys,
                    payload_hashes_by_write_node: &mut payloads,
                },
                contract: &c,
                provider_evidence: &provider,
                now,
                required_until: g::instant(base, "required_until"),
            }),
        )
        .map_err(|e| {
            code = e.detail_code();
        })?;
        phase = "builder";
        let trace = vec!["gate-trace".into()];
        let preparation = WriteCallRequestBuilder
            .build(
                WriteCallBuildRequest {
                    intent: &i,
                    context: WriteCallContext {
                        tenant_id: get(p, "tenant_id"),
                        execution_id: get(base, "execution_id"),
                        business_operation_id: Some(get(base, "business_operation_id")),
                    },
                    contract_evidence: &ce,
                    reservation: &res,
                    approval_request_id: get(field(d, "approval_bindings"), "approval_request_id"),
                    invocation_id: "gate-invocation",
                    business_preconditions: pre.as_ref().unwrap(),
                    trace_refs: &trace,
                },
                snapshot(limits()),
            )
            .map_err(|e| {
                code = e.detail_code();
            })?;
        let prepared = match preparation {
            WriteCallPreparation::Created(p) => p,
            WriteCallPreparation::Pending(_) => {
                phase = "pending";
                return Ok(());
            }
            WriteCallPreparation::Reused(_) => {
                phase = "reused";
                return Ok(());
            }
        };
        phase = "evidence";
        let permit = ready(WriteRequestedCoordinator::new(&ports).persist(
            prepared,
            &dec,
            WriteEvidenceContext {
                tenant_id: get(p, "tenant_id"),
                execution_id: get(base, "execution_id"),
                business_operation_id: Some(get(base, "business_operation_id")),
                write_request_durable: &mut flag,
            },
            now,
        ))
        .map_err(|e| {
            code = e.detail_code();
        })?;
        assert!(flag);
        assert!(permit.receipt().metadata().durable);
        hash = Some(permit.record().record_hash().to_string());
        let mut counts = ports.counts.lock().unwrap();
        assert_eq!(counts.stored, 1);
        counts.events.push("PROVIDER_SPY".into());
        counts.provider += 1;
        phase = "provider";
        Ok(())
    })();
    let _ = attempt;
    let counts = ports.counts.lock().unwrap();
    object([
        ("phase", txt(phase)),
        (
            "checks",
            ValueData::Array(checks.iter().map(|c| c.to_data()).collect()),
        ),
        ("validation", sum.to_data()),
        ("approval", approval_data(av)),
        (
            "decision",
            decision.map_or(ValueData::Null, |v| v.to_data()),
        ),
        ("code", code.map_or(ValueData::Null, txt)),
        (
            "events",
            ValueData::Array(counts.events.iter().map(txt).collect()),
        ),
        (
            "policy_calls",
            ValueData::Number((counts.policy as i64).into()),
        ),
        (
            "reserve_calls",
            ValueData::Number((counts.reserve as i64).into()),
        ),
        ("stored", ValueData::Number((counts.stored as i64).into())),
        (
            "provider_calls",
            ValueData::Number((counts.provider as i64).into()),
        ),
        ("flag", ValueData::Bool(flag)),
        ("record_hash", hash.map_or(ValueData::Null, txt)),
    ])
}
