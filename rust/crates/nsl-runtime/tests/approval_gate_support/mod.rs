use crate::{checkpoint_support as h, resume_support as r};
use nsl_core::*;
use nsl_runtime::*;
use std::{collections::BTreeMap, sync::Mutex};
fn get(v: &ValueData, k: &str) -> String {
    r::str_field(v, k).into()
}
fn summary(v: &ValueData) -> ApprovalSummaryInput {
    match v {
        ValueData::Null => ApprovalSummaryInput::Null,
        ValueData::Bool(b) => ApprovalSummaryInput::Bool(*b),
        ValueData::Number(n) => ApprovalSummaryInput::Number(n.as_str().into()),
        ValueData::String(s) => ApprovalSummaryInput::Text(s.clone()),
        ValueData::Array(a) => ApprovalSummaryInput::Array(a.iter().map(summary).collect()),
        ValueData::Object(m) => {
            ApprovalSummaryInput::Object(m.iter().map(|(k, v)| (k.clone(), summary(v))).collect())
        }
    }
}
pub fn request(v: &ValueData) -> ApprovalRequest {
    ApprovalRequest::new(ApprovalRequestSpec {
        bindings: ApprovalBindingsSpec {
            tenant_id: get(v, "tenant_id"),
            execution_id: get(v, "execution_id"),
            business_operation_id: get(v, "business_operation_id"),
            approval_id: get(v, "approval_id"),
            approval_request_id: get(v, "approval_request_id"),
            skill_semantic_hash: get(v, "skill_semantic_hash"),
            payload_hash: get(v, "payload_hash"),
            write_intent_hash: get(v, "write_intent_hash"),
        },
        skill_id: get(v, "skill_id"),
        skill_version: get(v, "skill_version"),
        write_node_id: get(v, "write_node_id"),
        tool_id: get(v, "tool_id"),
        risk_level: get(v, "risk_level").parse().unwrap(),
        message: get(v, "message"),
        expires_at: None,
        display_safe_summary: SafeApprovalSummary::new(
            &summary(r::field(v, "display_safe_summary")),
            ApprovalSummaryLimits {
                max_depth: 128,
                max_nodes: 100_000,
                max_text_bytes: 100_000,
            },
        )
        .unwrap(),
        protected_payload_ref: None,
        data_classification: get(v, "data_classification").parse().unwrap(),
    })
    .unwrap()
}
pub fn context(ver: &str, case: &str) -> ExecutionContext<h::Clock> {
    let mut c = ExecutionContext::with_clock(h::skill(ver), r::request(case), h::Clock::default());
    c.checkpoint.checkpoint_version = if case == "wide" {
        "10000000000000000000000000000000000000000".parse().unwrap()
    } else {
        0.into()
    };
    if case != "empty" {
        c.checks.push(CheckResult {
            check_id: "rule".into(),
            status: CheckStatus::Pass,
            severity: "ERROR".into(),
            message: "reviewed".into(),
            condition_node_id: "condition".into(),
            presence: Presence::Present,
            completeness: Completeness::Complete,
            provenance_refs: vec![],
            reason_code: None,
        });
    }
    if case == "populated" {
        let v = h::env(
            Value::Int(2026.into()),
            h::ty("Int"),
            DataClassification::Confidential,
            "input:year",
        );
        let t = h::env(
            Value::String("TEAM-FINANCE".into()),
            h::ty("String"),
            DataClassification::Internal,
            "context:team",
        );
        c.bind(BindingNamespace::Input, "sym-input".into(), v.clone())
            .unwrap();
        c.bind(BindingNamespace::Context, "sym-context".into(), t.clone())
            .unwrap();
        c.bind(BindingNamespace::Variable, "sym-root".into(), v.clone())
            .unwrap();
        c.push_frame();
        c.bind(BindingNamespace::Variable, "sym-inner".into(), t.clone())
            .unwrap();
        c.bind(BindingNamespace::Check, "check-inner".into(), v.clone())
            .unwrap();
        c.outputs
            .push(EmitRecord::new(vec![("team".into(), t), ("year".into(), v)]).unwrap());
        c.guard_mut().before_tool_call().unwrap();
        c.guard_mut().before_loop_iteration().unwrap();
        c.guard_mut().record_emit();
        c.guard_mut().observe_collection(1.into()).unwrap();
        c.invocation_counter = 3.into();
        c.foreach_depth = 1;
        c.current_node_id = Some("approval0001".into());
        c.checkpoint.last_completed_node_id = Some("approval0001".into());
        c.checkpoint.trace_sequence = 4.into();
    }
    c
}
pub fn evidence(c: &ExecutionContext<h::Clock>) -> PreWriteEvidence {
    let mut e = PreWriteEvidence::new(ValidationSummary::from_checks(&c.checks).unwrap());
    e.runtime_preflight_passed = true;
    e.trust_valid = true;
    e.current_policy_allowed = true;
    e.tool_preconditions_passed = true;
    e.business_preconditions_passed = true;
    e.intent_binding_valid = true;
    e.idempotency_ready = true;
    e.durability_ready = true;
    e.replay_live_write_allowed = true;
    e
}
pub struct Slot {
    pub execution: String,
    pub version: Integer,
    pub wire: Vec<u8>,
    pub state: &'static str,
    pub owner: Option<String>,
}
pub struct State {
    pub case: String,
    pub events: Vec<String>,
    pub slots: Vec<Slot>,
    pub latest: BTreeMap<String, Integer>,
    pub published: BTreeMap<String, ApprovalRequest>,
    pub verifier_calls: usize,
    pub clock_calls: usize,
    pub verify: bool,
    pub now: UtcInstant,
    pub publish_failed: bool,
    pub tampered: bool,
}
pub struct Ports(pub Mutex<State>);
impl Ports {
    pub fn new(case: &str) -> Self {
        Self(Mutex::new(State {
            case: case.into(),
            events: vec![],
            slots: vec![],
            latest: BTreeMap::new(),
            published: BTreeMap::new(),
            verifier_calls: 0,
            clock_calls: 0,
            verify: true,
            now: "2026-10-20T01:02:03+00:00".parse().unwrap(),
            publish_failed: false,
            tampered: false,
        }))
    }
    pub fn view(&self) -> ValueData {
        let p = self.0.lock().unwrap();
        r::obj([
            ("events", r::arr(p.events.iter().map(r::text))),
            (
                "slots",
                r::arr(p.slots.iter().map(|s| {
                    r::obj([
                        ("execution", r::text(&s.execution)),
                        ("version", ValueData::Number(s.version.clone())),
                        ("wire", r::text(r::hex(&s.wire))),
                        ("state", r::text(s.state)),
                        ("owner", s.owner.as_ref().map_or(ValueData::Null, r::text)),
                    ])
                })),
            ),
            (
                "latest",
                ValueData::Object(
                    p.latest
                        .iter()
                        .map(|(k, v)| (k.clone(), ValueData::Number(v.clone())))
                        .collect(),
                ),
            ),
            (
                "published",
                r::arr(p.published.values().map(ApprovalRequest::to_data)),
            ),
            ("verifier_calls", r::number(p.verifier_calls)),
            ("clock_calls", r::number(p.clock_calls)),
        ])
    }
    pub fn apply(&self, action: &ValueData) {
        let mut p = self.0.lock().unwrap();
        p.verify = r::field(action, "verify") == &ValueData::Bool(true);
        p.now = r::str_field(action, "now").parse().unwrap();
        if r::field(action, "tamper") == &ValueData::Bool(true) && !p.tampered {
            p.tampered = true;
            let slot = p.slots.last_mut().unwrap();
            let mut data = r::parse(&slot.wire);
            h::obj(&mut data).insert(
                "integrity_hash".into(),
                r::text(format!("sha256:{}", "0".repeat(64))),
            );
            slot.wire = canonical_json(&data).unwrap();
        }
    }
    fn finish(
        &self,
        op: &str,
        target: &'static str,
        execution: &str,
        version: &Integer,
        claim: &str,
    ) -> Result<(), ResumeClaimError> {
        let mut p = self.0.lock().unwrap();
        p.events.push(op.into());
        let s = p
            .slots
            .iter_mut()
            .find(|s| s.execution == execution && &s.version == version)
            .ok_or(ResumeClaimError::Owner)?;
        if s.state != "LIVE" || s.owner.as_deref() != Some(claim) {
            return Err(ResumeClaimError::Owner);
        }
        s.state = target;
        s.owner = None;
        Ok(())
    }
}
impl CheckpointPort for Ports {
    fn save<'a>(&'a self, cp: &'a ExecutionCheckpoint) -> SuspensionPortFuture<'a> {
        Box::pin(async move {
            let mut p = self.0.lock().unwrap();
            p.events.push("save".into());
            let wire = cp.to_bytes(h::limits()).unwrap();
            ExecutionCheckpoint::from_bytes(&wire, h::limits())
                .unwrap()
                .verify(h::limits())
                .unwrap();
            p.latest.insert(
                cp.metadata().execution_id.clone(),
                cp.metadata().checkpoint_version.clone(),
            );
            p.slots.push(Slot {
                execution: cp.metadata().execution_id.clone(),
                version: cp.metadata().checkpoint_version.clone(),
                wire,
                state: "AVAILABLE",
                owner: None,
            });
            Ok(())
        })
    }
}
impl ApprovalPort for Ports {
    fn publish<'a>(&'a self, req: &'a ApprovalRequest) -> SuspensionPortFuture<'a> {
        Box::pin(async move {
            let mut p = self.0.lock().unwrap();
            p.events.push("publish".into());
            if p.case == "publish-retry" && !p.publish_failed {
                p.publish_failed = true;
                return Err(SuspensionPortError);
            }
            let prior = p
                .published
                .entry(req.bindings().approval_request_id().to_string())
                .or_insert_with(|| req.clone());
            if prior != req {
                return Err(SuspensionPortError);
            }
            Ok(())
        })
    }
}
impl ResumeCheckpointPort for Ports {
    fn claim<'a>(
        &'a self,
        execution: &'a str,
        version: &'a Integer,
        claim: &'a str,
    ) -> ResumeClaimFuture<'a, ExecutionCheckpoint> {
        Box::pin(async move {
            let mut p = self.0.lock().unwrap();
            p.events.push("claim".into());
            if p.latest.get(execution) != Some(version) {
                return Err(ResumeClaimError::Stale);
            }
            let wrong = p.case == "returned-version";
            let s = p
                .slots
                .iter_mut()
                .find(|s| s.execution == execution && &s.version == version)
                .ok_or(ResumeClaimError::Stale)?;
            if s.state == "LIVE" {
                return Err(ResumeClaimError::Live);
            }
            if s.state == "CONSUMED" {
                return Err(ResumeClaimError::Consumed);
            }
            let mut cp = ExecutionCheckpoint::from_bytes(&s.wire, h::limits())
                .map_err(|_| ResumeClaimError::Unavailable)?;
            s.state = "LIVE";
            s.owner = Some(claim.into());
            if wrong {
                let next: Integer = (version.as_str().parse::<num_bigint::BigInt>().unwrap()
                    + 1_u8)
                    .to_string()
                    .parse()
                    .unwrap();
                let mut data = h::raw_state(&cp);
                h::obj(&mut data)
                    .insert("checkpoint_version".into(), ValueData::Number(next.clone()));
                h::obj(h::at(&mut data, &["checkpoint"]))
                    .insert("checkpoint_version".into(), ValueData::Number(next.clone()));
                let mut spec = cp.metadata().clone();
                spec.checkpoint_version = next;
                spec.state_payload = canonical_json(&data).unwrap();
                cp = ExecutionCheckpoint::create(spec, h::limits()).unwrap();
            }
            Ok(cp)
        })
    }
    fn release<'a>(
        &'a self,
        execution: &'a str,
        version: &'a Integer,
        claim: &'a str,
    ) -> ResumeClaimFuture<'a, ()> {
        Box::pin(async move { self.finish("release", "AVAILABLE", execution, version, claim) })
    }
    fn consume<'a>(
        &'a self,
        execution: &'a str,
        version: &'a Integer,
        claim: &'a str,
    ) -> ResumeClaimFuture<'a, ()> {
        Box::pin(async move {
            {
                let mut p = self.0.lock().unwrap();
                if p.case == "consume-before" {
                    p.events.push("consume".into());
                    return Err(ResumeClaimError::Unavailable);
                }
            }
            self.finish("consume", "CONSUMED", execution, version, claim)?;
            if self.0.lock().unwrap().case == "consume-after" {
                return Err(ResumeClaimError::Unavailable);
            }
            Ok(())
        })
    }
}
impl ApprovalEvidenceVerifier for Ports {
    fn verify(&self, _: &ApprovalEvidence) -> Result<bool, ApprovalVerifierError> {
        let mut p = self.0.lock().unwrap();
        p.events.push("verify".into());
        p.verifier_calls += 1;
        Ok(p.verify)
    }
}
impl ApprovalClock for Ports {
    fn now(&self) -> Result<UtcInstant, ApprovalClockError> {
        let mut p = self.0.lock().unwrap();
        p.events.push("clock".into());
        p.clock_calls += 1;
        Ok(p.now)
    }
}
pub fn observe(
    ctx: &mut ExecutionContext<h::Clock>,
    p: &Ports,
    outcome: ValueData,
    clock: &h::Clock,
) -> ValueData {
    r::obj([
        ("outcome", outcome),
        ("store", p.view()),
        (
            "suspend_context",
            r::text(r::hex(&canonical_json(&r::state(ctx)).unwrap())),
        ),
        ("runtime_calls", r::number(clock.calls())),
    ])
}
