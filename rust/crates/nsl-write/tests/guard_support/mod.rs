#![allow(dead_code)]
use crate::support::*;
use nsl_contract::*;
use nsl_core::*;
use nsl_write::*;
use std::collections::{BTreeMap, BTreeSet};
pub fn opt<'a>(d: &'a ValueData, k: &str) -> Option<&'a ValueData> {
    let ValueData::Object(o) = d else { panic!() };
    o.get(k)
}
pub fn yes(d: &ValueData, k: &str) -> bool {
    field(d, k) == &ValueData::Bool(true)
}
pub fn instant(d: &ValueData, k: &str) -> UtcInstant {
    get(d, k).parse().unwrap()
}
pub fn optional(d: &ValueData, k: &str) -> Option<String> {
    if field(d, k) == &ValueData::Null {
        None
    } else {
        Some(get(d, k).into())
    }
}
pub fn strings(d: &ValueData) -> BTreeSet<String> {
    arr(d).iter().map(|v| text(v).into()).collect()
}
pub fn rows(s: &str) -> impl Iterator<Item = (String, ValueData, ValueData)> + '_ {
    s.lines().filter(|s| !s.starts_with('#')).map(|s| {
        let p: Vec<_> = s.split('\t').collect();
        (p[0].into(), parse(&decode(p[1])), parse(&decode(p[2])))
    })
}
pub fn baseline() -> ValueData {
    rows(include_str!("../fixtures/write-guard-decisions-oracle.tsv"))
        .next()
        .unwrap()
        .1
}
pub fn intent(d: &ValueData) -> WriteIntent {
    let args = BTreeMap::from([(
        "detail".into(),
        ValueEnvelope::complete(
            Value::Record(BTreeMap::from([
                ("description".into(), Value::String("KTX".into())),
                ("amount".into(), Value::Decimal("84000".parse().unwrap())),
            ])),
            TypeRef::new(TypeKind::Domain("DraftPayload".into())).unwrap(),
            DataClassification::Confidential,
        ),
    )]);
    let semantic = format!("sha256:{}", "a".repeat(64));
    let payload = compute_payload_hash(&args, limits()).unwrap();
    WriteIntent::new(
        &WriteIntentSpec {
            tenant_id: get(d, "tenant_id").into(),
            business_operation_id: get(d, "business_operation_id").into(),
            node_id: get(d, "node_id").into(),
            tool_ref: "write_tool".into(),
            tool_id: get(d, "tool_id").into(),
            approval_id: "APPROVE_DRAFT".into(),
            skill_semantic_hash: semantic.clone(),
            canonical_arguments: args,
            payload_hash: payload.to_string(),
            write_intent_hash: compute_write_intent_hash(
                &semantic,
                get(d, "node_id"),
                get(d, "tool_id"),
                payload.as_str(),
            )
            .unwrap()
            .to_string(),
        },
        snapshot(limits()),
    )
    .unwrap()
}
pub fn contract(d: &ValueData, output: Option<TypeRef>) -> ToolContract {
    let domain = |n: &str| TypeRef::new(TypeKind::Domain(n.into())).unwrap();
    let write = get(d, "capability") == "WRITE";
    ToolContract::new(ToolContractSpec {
        tool_id: get(d, "tool_id").into(),
        version: get(d, "version").into(),
        capability: get(d, "capability").into(),
        required_scope: get(d, "required_scope").into(),
        input_types: vec![("detail".into(), domain("DraftPayload"))],
        output_type: output.unwrap_or_else(|| domain("DraftRecord")),
        output_classification: DataClassification::Confidential,
        options: if write {
            ToolContractOptions {
                operation_kind: Some("CREATE".into()),
                risk_level: Some(get(d, "risk_level").into()),
                write_semantics: Some(
                    WriteSemantics::new(WriteSemanticsSpec {
                        side_effect: true,
                        atomicity: "ATOMIC".into(),
                        idempotency: Some(
                            IdempotencySemantics::new(
                                true,
                                "TOOL_AND_TENANT",
                                "PROVIDER_DEFINED",
                                "RETURN_ORIGINAL_RESULT",
                                "IDEMPOTENCY_CONFLICT",
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
pub fn contract_evidence(d: &ValueData) -> Result<WriteContractEvidence, WriteGuardError> {
    WriteContractEvidence::new(WriteContractEvidenceSpec {
        tool_id: get(d, "tool_id").into(),
        contract_hash: get(d, "contract_hash").into(),
        capability: get(d, "capability").parse().unwrap(),
        risk_level: get(d, "risk_level").parse().unwrap(),
        preconditions_supported: yes(d, "preconditions_supported"),
    })
}
pub fn policy_request(d: &ValueData) -> Result<WritePolicyRequest, WriteGuardError> {
    WritePolicyRequest::new(WritePolicyRequestSpec {
        tenant_id: get(d, "tenant_id").into(),
        subject_id: get(d, "subject_id").into(),
        action: get(d, "action").into(),
        tool_id: get(d, "tool_id").into(),
        risk_level: get(d, "risk_level").parse().unwrap(),
        business_operation_id: get(d, "business_operation_id").into(),
        write_node_id: get(d, "write_node_id").into(),
        write_intent_hash: get(d, "write_intent_hash").into(),
    })
}
pub fn policy(d: &ValueData) -> Result<PolicyDecision, WriteGuardError> {
    PolicyDecision::new(PolicyDecisionSpec {
        decision_id: get(d, "decision_id").into(),
        policy_id: get(d, "policy_id").into(),
        policy_version: get(d, "policy_version").into(),
        effect: get(d, "effect").parse().unwrap(),
        tenant_id: get(d, "tenant_id").into(),
        subject_id: get(d, "subject_id").into(),
        action: get(d, "action").into(),
        tool_id: get(d, "tool_id").into(),
        risk_level: get(d, "risk_level").parse().unwrap(),
        business_operation_id: get(d, "business_operation_id").into(),
        write_node_id: get(d, "write_node_id").into(),
        write_intent_hash: get(d, "write_intent_hash").into(),
        evidence_ref: get(d, "evidence_ref").into(),
        evaluated_at: instant(d, "evaluated_at"),
        expires_at: instant(d, "expires_at"),
    })
}
pub fn precondition(d: &ValueData) -> Result<BusinessPreconditionEvidence, WriteGuardError> {
    BusinessPreconditionEvidence::new(BusinessPreconditionEvidenceSpec {
        evidence_id: get(d, "evidence_id").into(),
        tenant_id: get(d, "tenant_id").into(),
        business_operation_id: get(d, "business_operation_id").into(),
        write_node_id: get(d, "write_node_id").into(),
        tool_id: get(d, "tool_id").into(),
        contract_hash: get(d, "contract_hash").into(),
        payload_hash: get(d, "payload_hash").into(),
        write_intent_hash: get(d, "write_intent_hash").into(),
        mechanism: get(d, "mechanism").parse().unwrap(),
        evidence_ref: get(d, "evidence_ref").into(),
        observed_at: instant(d, "observed_at"),
        expires_at: instant(d, "expires_at"),
        provider_atomic_enforcement: yes(d, "provider_atomic_enforcement"),
    })
}
pub fn execution(d: &ValueData) -> WriteExecutionEvidence {
    WriteExecutionEvidence {
        validation: get(d, "validation").parse().unwrap(),
        approval_status: get(d, "approval_status").parse().unwrap(),
        approval_evidence_valid: yes(d, "approval_evidence_valid"),
        runtime_preflight_passed: yes(d, "runtime_preflight_passed"),
        current_policy_allowed: yes(d, "current_policy_allowed"),
        tool_preconditions_passed: yes(d, "tool_preconditions_passed"),
        business_preconditions_passed: yes(d, "business_preconditions_passed"),
        intent_binding_valid: yes(d, "intent_binding_valid"),
        idempotency_ready: yes(d, "idempotency_ready"),
        durability_ready: yes(d, "durability_ready"),
        trust_valid: yes(d, "trust_valid"),
        replay_live_write_allowed: yes(d, "replay_live_write_allowed"),
    }
}
pub struct Principal {
    pub tenant: String,
    pub subject: String,
    pub actor: String,
    pub roles: BTreeSet<String>,
    pub scopes: BTreeSet<String>,
    pub auth: String,
    pub verified: bool,
    pub behalf: Option<String>,
}
impl Principal {
    pub fn view(&self) -> WritePrincipal<'_> {
        WritePrincipal {
            tenant_id: &self.tenant,
            subject_id: &self.subject,
            actor_type: &self.actor,
            roles: &self.roles,
            scopes: &self.scopes,
            auth_context_ref: &self.auth,
            verified: self.verified,
            on_behalf_of: self.behalf.as_deref(),
        }
    }
}
pub struct Setup {
    pub data: ValueData,
    pub i: WriteIntent,
    pub c: ToolContract,
    pub ce: WriteContractEvidence,
    pub p: Option<PolicyDecision>,
    pub b: Option<BusinessPreconditionEvidence>,
    pub e: WriteExecutionEvidence,
    pub principal: Principal,
    pub business: Option<String>,
    pub now: UtcInstant,
}
impl Setup {
    pub fn new(d: ValueData) -> Self {
        let ctx = field(&d, "context");
        let p = field(ctx, "principal");
        Self {
            i: intent(field(&d, "intent")),
            c: contract(field(&d, "current_contract"), None),
            ce: contract_evidence(field(&d, "contract_evidence")).unwrap(),
            p: if field(&d, "policy_decision") == &ValueData::Null {
                None
            } else {
                Some(policy(field(&d, "policy_decision")).unwrap())
            },
            b: if field(&d, "business_preconditions") == &ValueData::Null {
                None
            } else {
                Some(precondition(field(&d, "business_preconditions")).unwrap())
            },
            e: execution(field(&d, "execution")),
            principal: Principal {
                tenant: get(p, "tenant_id").into(),
                subject: get(p, "subject_id").into(),
                actor: get(p, "actor_type").into(),
                roles: strings(field(p, "roles")),
                scopes: strings(field(p, "scopes")),
                auth: get(p, "auth_context_ref").into(),
                verified: get(p, "verification") == "VERIFIED",
                behalf: optional(p, "on_behalf_of"),
            },
            business: optional(ctx, "business_operation_id"),
            now: instant(&d, "now"),
            data: d,
        }
    }
    pub fn context(&self) -> WriteGuardContext<'_> {
        WriteGuardContext {
            principal: self.principal.view(),
            business_operation_id: self.business.as_deref(),
        }
    }
    pub fn request(&self) -> WriteGuardRequest<'_> {
        WriteGuardRequest {
            intent: &self.i,
            context: self.context(),
            current_contract: &self.c,
            contract_evidence: &self.ce,
            execution_evidence: &self.e,
            policy_decision: self.p.as_ref(),
            business_preconditions: self.b.as_ref(),
        }
    }
    pub fn guard(&self) -> WriteGuard {
        match opt(&self.data, "accepted_levels") {
            None | Some(ValueData::Null) => WriteGuard::default(),
            Some(v) => WriteGuard::new(
                RiskAcceptancePolicy::new(arr(v).iter().map(|v| text(v).parse().unwrap())).unwrap(),
            ),
        }
    }
}
