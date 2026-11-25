#![allow(dead_code)]
use crate::checkpoint_support as h;
use nsl_core::*;
use nsl_runtime::*;
use std::{
    collections::BTreeSet,
    sync::{Arc, Mutex},
};
pub fn obj(p: impl IntoIterator<Item = (&'static str, ValueData)>) -> ValueData {
    ValueData::Object(p.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
pub fn text(s: impl Into<String>) -> ValueData {
    ValueData::String(s.into())
}
pub fn arr(p: impl IntoIterator<Item = ValueData>) -> ValueData {
    ValueData::Array(p.into_iter().collect())
}
pub fn number(n: usize) -> ValueData {
    ValueData::Number(n.to_string().parse().unwrap())
}
pub fn field<'a>(v: &'a ValueData, key: &str) -> &'a ValueData {
    let ValueData::Object(m) = v else {
        panic!("object")
    };
    &m[key]
}
pub fn string(v: &ValueData) -> &str {
    let ValueData::String(s) = v else {
        panic!("string")
    };
    s
}
pub fn str_field<'a>(v: &'a ValueData, key: &str) -> &'a str {
    string(field(v, key))
}
pub fn items(v: &ValueData) -> &[ValueData] {
    let ValueData::Array(a) = v else {
        panic!("array")
    };
    a
}
pub fn parse(v: &[u8]) -> ValueData {
    parse_json_with_limits(v, h::limits().outer).unwrap()
}
pub fn hex(v: &[u8]) -> String {
    v.iter().map(|v| format!("{v:02x}")).collect()
}
pub fn integer(v: &ValueData) -> Integer {
    let ValueData::Number(n) = v else {
        panic!("number")
    };
    n.clone()
}
pub fn evidence(v: &ValueData) -> ApprovalEvidence {
    let get = |k| str_field(v, k).to_string();
    ApprovalEvidence::new(ApprovalEvidenceSpec {
        bindings: ApprovalBindingsSpec {
            tenant_id: get("tenant_id"),
            execution_id: get("execution_id"),
            business_operation_id: get("business_operation_id"),
            approval_id: get("approval_id"),
            approval_request_id: get("approval_request_id"),
            skill_semantic_hash: get("skill_semantic_hash"),
            payload_hash: get("payload_hash"),
            write_intent_hash: get("write_intent_hash"),
        },
        decision: get("decision").parse().unwrap(),
        approver_id: get("approver_id"),
        decided_at: get("decided_at").parse().unwrap(),
        expires_at: if field(v, "expires_at") == &ValueData::Null {
            None
        } else {
            Some(get("expires_at").parse().unwrap())
        },
        evidence_integrity: get("evidence_integrity"),
    })
    .unwrap()
}
pub fn request(case: &str) -> ExecutionRequest {
    let base = h::request();
    let mut sec = base.security_context().clone();
    let p = &mut sec.principal;
    if case == "principal-unverified" {
        p.verification = PrincipalVerification::Unverified;
    }
    if case == "principal-actor" {
        p.actor_type = "INVALID-ACTOR".into();
    }
    if let Some(field) = case.strip_prefix("principal-change-") {
        match field {
            "tenant_id" => p.tenant_id = "other".into(),
            "subject_id" => p.subject_id = "other".into(),
            "actor_type" => p.actor_type = "SERVICE".into(),
            "roles" => p.roles = BTreeSet::from(["other".into()]),
            "scopes" => p.scopes = BTreeSet::from(["other".into()]),
            "auth_context_ref" => p.auth_context_ref = "other".into(),
            "on_behalf_of" => p.on_behalf_of = Some("other".into()),
            _ => panic!(),
        }
    }
    if case == "policy-change" {
        sec.data_policy =
            DataHandlingPolicy::new(DataClassification::Internal, 31.into(), 90.into()).unwrap();
    }
    ExecutionRequest::new(
        base.identity().execution_id().into(),
        base.inputs().clone(),
        base.runtime_context().clone(),
        sec,
        base.identity().business_operation_id().map(str::to_string),
    )
    .unwrap()
}
pub fn token(input: &ValueData) -> ResumeToken {
    ResumeToken::new(
        str_field(input, "token_execution").into(),
        str_field(input, "token_business").into(),
        integer(field(input, "token_version")),
        evidence(field(input, "evidence")),
    )
    .unwrap()
}
pub fn state(ctx: &mut ExecutionContext<h::Clock>) -> ValueData {
    let prior = ctx.checkpoint.checkpoint_version.clone();
    let cp = CheckpointCoordinator::for_oracle(h::limits())
        .capture(ctx)
        .unwrap();
    ctx.checkpoint.checkpoint_version = prior.clone();
    let mut d = parse(cp.state_payload());
    h::obj(&mut d).insert(
        "checkpoint_version".into(),
        ValueData::Number(prior.clone()),
    );
    h::obj(h::at(&mut d, &["checkpoint"]))
        .insert("checkpoint_version".into(), ValueData::Number(prior));
    d
}
#[derive(Clone)]
pub struct Store(pub Arc<Mutex<State>>);
pub struct State {
    pub cp: ExecutionCheckpoint,
    pub returned: ExecutionCheckpoint,
    pub mode: String,
    pub events: Vec<String>,
    pub state: &'static str,
    pub owner: Option<String>,
    pub latest: Integer,
    pub verifier_calls: usize,
    pub clock_calls: usize,
    pub now: UtcInstant,
    pub clock_failed: bool,
    pub verifier_mode: &'static str,
}
impl Store {
    pub fn new(cp: ExecutionCheckpoint, case: &str, now: UtcInstant) -> Self {
        let mut returned = cp.clone();
        if case == "returned-version" {
            let mut state = parse(cp.state_payload());
            h::obj(&mut state).insert("checkpoint_version".into(), ValueData::Number(2.into()));
            h::obj(h::at(&mut state, &["checkpoint"]))
                .insert("checkpoint_version".into(), ValueData::Number(2.into()));
            let mut spec = cp.metadata().clone();
            spec.checkpoint_version = 2.into();
            spec.state_payload = canonical_json(&state).unwrap();
            returned = ExecutionCheckpoint::create(spec, h::limits()).unwrap();
        }
        if matches!(case, "returned-execution" | "returned-business") {
            let mut spec = cp.metadata().clone();
            if case == "returned-execution" {
                spec.execution_id = "other".into();
            } else {
                spec.business_operation_id = "other".into();
            }
            returned = ExecutionCheckpoint::create(spec, h::limits()).unwrap();
        }
        let latest = if case == "stale" {
            2.into()
        } else {
            cp.metadata().checkpoint_version.clone()
        };
        Self(Arc::new(Mutex::new(State {
            cp,
            returned,
            mode: case.into(),
            events: vec![],
            state: if case == "live" {
                "LIVE"
            } else if case == "consumed" {
                "CONSUMED"
            } else {
                "AVAILABLE"
            },
            owner: if case == "live" {
                Some("other-claim".into())
            } else {
                None
            },
            latest,
            verifier_calls: 0,
            clock_calls: 0,
            now,
            clock_failed: case == "clock-error",
            verifier_mode: match case {
                "verifier-error" | "error-release-before" | "error-release-after" => "error",
                "invalid-proof" | "proof-retry" | "release-before" | "release-after" => "false",
                _ => "true",
            },
        })))
    }
    pub fn observe(&self, outcome: ValueData, runtime_calls: usize) -> ValueData {
        let p = self.0.lock().unwrap();
        obj([
            ("outcome", outcome),
            ("events", arr(p.events.iter().map(text))),
            ("store_state", text(p.state)),
            ("owner", p.owner.as_ref().map_or(ValueData::Null, text)),
            ("verifier_calls", number(p.verifier_calls)),
            ("clock_calls", number(p.clock_calls)),
            ("runtime_calls", number(runtime_calls)),
        ])
    }
    fn finish<'a>(
        &'a self,
        op: &'static str,
        target: &'static str,
        execution: &'a str,
        version: &'a Integer,
        claim: &'a str,
    ) -> ResumeClaimFuture<'a, ()> {
        Box::pin(async move {
            let pending = {
                let mut p = self.0.lock().unwrap();
                p.events.push(op.into());
                if p.mode == format!("{op}-before") || p.mode == format!("error-{op}-before") {
                    return Err(ResumeClaimError::Unavailable);
                }
                if execution != p.cp.metadata().execution_id
                    || version != &p.cp.metadata().checkpoint_version
                    || p.state != "LIVE"
                    || p.owner.as_deref() != Some(claim)
                {
                    return Err(ResumeClaimError::Owner);
                }
                let pending = p.mode == format!("{op}-pending");
                if !pending {
                    p.state = target;
                    p.owner = None;
                }
                if p.mode == format!("{op}-after") || p.mode == format!("error-{op}-after") {
                    return Err(ResumeClaimError::Unavailable);
                }
                pending
            };
            if pending {
                std::future::pending::<()>().await;
            }
            Ok(())
        })
    }
}
impl ResumeCheckpointPort for Store {
    fn claim<'a>(
        &'a self,
        execution: &'a str,
        version: &'a Integer,
        claim: &'a str,
    ) -> ResumeClaimFuture<'a, ExecutionCheckpoint> {
        Box::pin(async move {
            let (cp, pending) = {
                let mut p = self.0.lock().unwrap();
                p.events.push("claim".into());
                if p.mode == "claim-before" {
                    return Err(ResumeClaimError::Unavailable);
                }
                if execution != p.cp.metadata().execution_id
                    || version != &p.cp.metadata().checkpoint_version
                    || version != &p.latest
                {
                    return Err(ResumeClaimError::Stale);
                }
                if p.state == "LIVE" {
                    return Err(ResumeClaimError::Live);
                }
                if p.state == "CONSUMED" {
                    return Err(ResumeClaimError::Consumed);
                }
                p.state = "LIVE";
                p.owner = Some(claim.into());
                if p.mode == "claim-after" {
                    return Err(ResumeClaimError::Unavailable);
                }
                (p.returned.clone(), p.mode == "claim-pending")
            };
            if pending {
                std::future::pending::<()>().await;
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
        self.finish("release", "AVAILABLE", execution, version, claim)
    }
    fn consume<'a>(
        &'a self,
        execution: &'a str,
        version: &'a Integer,
        claim: &'a str,
    ) -> ResumeClaimFuture<'a, ()> {
        self.finish("consume", "CONSUMED", execution, version, claim)
    }
}
impl ApprovalEvidenceVerifier for Store {
    fn verify(&self, _: &ApprovalEvidence) -> Result<bool, ApprovalVerifierError> {
        let mut p = self.0.lock().unwrap();
        p.events.push("verify".into());
        p.verifier_calls += 1;
        if p.verifier_mode == "error" {
            p.verifier_mode = "true";
            return Err(ApprovalVerifierError);
        }
        Ok(p.verifier_mode != "false")
    }
}
impl ApprovalClock for Store {
    fn now(&self) -> Result<UtcInstant, ApprovalClockError> {
        let mut p = self.0.lock().unwrap();
        p.events.push("clock".into());
        p.clock_calls += 1;
        if p.clock_failed {
            p.clock_failed = false;
            return Err(ApprovalClockError);
        }
        Ok(p.now)
    }
}
pub fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()
        .unwrap()
}
pub fn coordinator() -> ApprovalResumeCoordinator {
    ApprovalResumeCoordinator::new(CheckpointCoordinator::for_oracle(h::limits()))
}
pub fn input<'a>(
    data: &'a ValueData,
    t: &'a ResumeToken,
    ver: &str,
    case: &str,
    payload: &'a PayloadHash,
) -> ApprovalResumeRequest<'a> {
    ApprovalResumeRequest {
        token: t,
        skill: h::skill(ver),
        request: request(case),
        approval_id: str_field(data, "approval_id"),
        write_node_id: str_field(data, "write_node_id"),
        current_write_intent_hash: str_field(data, "current_intent"),
        current_payload_hash: payload,
        claim_id: str_field(data, "claim_id"),
    }
}
pub fn expected_error(case: &str, attempt: usize) -> (ApprovalResumeErrorKind, String, bool) {
    use ApprovalResumeErrorKind::*;
    let mut failed = false;
    let (kind, path) = match case {
        "token-execution" => (Type, "$.token.execution_id".into()),
        "token-business" => (Type, "$.token.business_operation_id".into()),
        "returned-execution" => (Type, "$.checkpoint.execution_id".into()),
        "returned-business" => (Type, "$.checkpoint.business_operation_id".into()),
        "stale" => (Claim(ResumeClaimError::Stale), "$.claim".into()),
        "live" => (Claim(ResumeClaimError::Live), "$.claim".into()),
        "consumed" | "duplicate" => (Claim(ResumeClaimError::Consumed), "$.claim".into()),
        "claim-before" | "claim-after" => (Claim(ResumeClaimError::Unavailable), "$.claim".into()),
        "verifier-error" => (Verifier, "$.approval_evidence".into()),
        "clock-error" => (Clock, "$.clock.now".into()),
        "release-before" | "release-after" => {
            failed = true;
            (Release(ResumeClaimError::Unavailable), "$.claim".into())
        }
        "error-release-before" | "error-release-after" => {
            failed = true;
            (Verifier, "$.approval_evidence".into())
        }
        "consume-before" | "consume-after" => (
            if attempt == 0 {
                Consume(ResumeClaimError::Unavailable)
            } else {
                Claim(if case == "consume-before" {
                    ResumeClaimError::Live
                } else {
                    ResumeClaimError::Consumed
                })
            },
            "$.claim".into(),
        ),
        "missing-approval" => (Type, "$.approvals[\"PROJECT_DEACTIVATE\"]".into()),
        "bad-payload" => (Type, "$.checkpoint".into()),
        "blank-intent" => (
            Checkpoint(CheckpointErrorKind::Invalid),
            "$.checkpoint.write_intent_hashes_by_write_node.write0001".into(),
        ),
        "blank-approval" => (Type, "$.approval_id".into()),
        "blank-node" => (Type, "$.write_node_id".into()),
        "blank-claim" => (Type, "$.claim_id".into()),
        "blank-current-intent" => (Type, "$.current_write_intent_hash".into()),
        c if c.starts_with("principal-change-") || c == "policy-change" => (
            Checkpoint(CheckpointErrorKind::Binding),
            "$.security".into(),
        ),
        c if c.starts_with("pending-") => (Type, "$.approvals[\"PROJECT_DEACTIVATE\"]".into()),
        c if c.starts_with("missing-") => (
            Type,
            format!(
                "$.checkpoint.{}[\"write0001\"]",
                match c {
                    "missing-request" => "approval_request_ids_by_write_node",
                    "missing-intent" => "write_intent_hashes_by_write_node",
                    _ => "payload_hashes_by_write_node",
                }
            ),
        ),
        _ => panic!("unexpected error for {case}/{attempt}"),
    };
    (kind, path, failed)
}
