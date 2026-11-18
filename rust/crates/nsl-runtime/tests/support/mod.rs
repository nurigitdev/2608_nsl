#![allow(dead_code)]
use nsl_core::{
    ApprovalStatus, CheckStatus, CommitState, Completeness, DataClassification, ExecutionStatus,
    Integer, PreWriteEligibility, Presence, TypeKind, TypeRef, Value, ValueData, ValueEnvelope,
    VerificationStatus, canonical_json,
};
use nsl_ir::{NsoCodec, NsoLimits, SkillObject};
use nsl_runtime::*;
use std::{cell::RefCell, collections::BTreeSet, sync::Arc};
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
pub fn opt(s: &str) -> Option<String> {
    (s != "~").then(|| decode(s))
}
pub fn json(v: &ValueData) -> String {
    String::from_utf8(canonical_json(v).unwrap()).unwrap()
}
pub fn text(s: &str) -> ValueData {
    ValueData::String(s.into())
}
pub fn optional(s: Option<&str>) -> ValueData {
    s.map_or(ValueData::Null, text)
}
pub fn ty(s: &str) -> TypeRef {
    TypeRef::new(TypeKind::Primitive(s.into())).unwrap()
}
pub fn envelope(n: i64) -> ValueEnvelope {
    ValueEnvelope::complete(
        Value::Int(n.into()),
        ty("Int"),
        DataClassification::Internal,
    )
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
pub fn request() -> ExecutionRequest {
    ExecutionRequest::new(
        "exec-a".into(),
        Default::default(),
        Default::default(),
        ExecutionSecurityContext {
            principal: principal(),
            data_policy: DataHandlingPolicy::default(),
        },
        None,
    )
    .unwrap()
}
pub fn check(id: &str, status: CheckStatus) -> CheckResult {
    CheckResult {
        check_id: id.into(),
        status,
        severity: "WARN".into(),
        message: "확인😀".into(),
        condition_node_id: "expr0001".into(),
        presence: Presence::Present,
        completeness: Completeness::Complete,
        provenance_refs: vec!["trace:a".into()],
        reason_code: None,
    }
}
pub fn write(key: &str, payload: &str) -> WriteSummary {
    WriteSummary::new(write_spec(key, payload)).unwrap()
}
pub fn write_spec(key: &str, payload: &str) -> WriteSummarySpec {
    WriteSummarySpec {
        commit_state: CommitState::Committed,
        tool_id: "TEST.CREATE".into(),
        node_id: "write0001".into(),
        idempotency_key: key.into(),
        payload_hash: payload.into(),
        provider_transaction_id: None,
        business_reference: None,
    }
}
pub fn result(
    status: ExecutionStatus,
    completeness: Completeness,
    flags: usize,
) -> ExecutionResult {
    ExecutionResult {
        execution_id: "exec-a".into(),
        skill_id: "TEST".into(),
        skill_version: "1".into(),
        semantic_hash: format!("sha256:{}", "a".repeat(64)),
        status,
        completeness,
        checks: vec![check("C", CheckStatus::Pass)],
        outputs: vec![
            EmitRecord::new(vec![
                (
                    "z".into(),
                    ValueEnvelope::complete(
                        Value::Decimal("1.2300".parse().unwrap()),
                        ty("Decimal"),
                        DataClassification::Internal,
                    ),
                ),
                ("a".into(), envelope(7)),
            ])
            .unwrap(),
        ],
        resources: ResourceUsage {
            tool_calls: 1.into(),
            loop_iterations: 2.into(),
            emitted_rows: 3.into(),
            max_collection_size_seen: 4.into(),
        },
        error: Some(RuntimeErrorInfo {
            code: "NSL-E8001".into(),
            category: "EVALUATION".into(),
            message: "safe message".into(),
            node_id: Some("expr0001".into()),
            detail_code: Some("DETAIL".into()),
        }),
        validation: (flags & 1 != 0).then(|| ValidationSummary::from_checks(&[]).unwrap()),
        pre_write_eligibility: (flags & 2 != 0).then_some(PreWriteEligibility::Allowed),
        approval: (flags & 4 != 0).then(|| {
            ApprovalSummary::new(
                ApprovalStatus::Approved,
                "A".into(),
                Some("approval-1".into()),
            )
            .unwrap()
        }),
        write: (flags & 8 != 0).then(|| write("idem-short", "not-a-payload-hash")),
        verification: (flags & 16 != 0).then(|| {
            VerificationSummary::new(
                VerificationStatus::Unknown,
                Some("WAIT".into()),
                Some("evidence:a".into()),
            )
            .unwrap()
        }),
        suspension: (flags & 32 != 0).then(|| {
            SuspensionInfo::new("WAITING".into(), 1.into(), "A".into(), "request-a".into()).unwrap()
        }),
    }
}
pub fn skill() -> Arc<SkillObject> {
    Arc::new(
        NsoCodec::decode(
            include_bytes!("../../../../../compatibility/v0_2_1/nso/ir-1-0-canonical/expected.nso"),
            NsoLimits {
                max_bytes: 100_000,
                max_depth: 64,
                max_nodes: 10_000,
            },
        )
        .unwrap(),
    )
}
pub struct Clock(pub RefCell<Integer>);
impl Default for Clock {
    fn default() -> Self {
        Self::at(0.into())
    }
}
impl Clock {
    pub fn at(n: Integer) -> Self {
        Self(RefCell::new(n))
    }
    pub fn set(&self, n: Integer) {
        *self.0.borrow_mut() = n;
    }
}
impl RuntimeClock for Clock {
    fn monotonic_ns(&self) -> Integer {
        self.0.borrow().clone()
    }
}
