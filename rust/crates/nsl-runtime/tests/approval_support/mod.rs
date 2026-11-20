#![allow(dead_code)]
use nsl_core::*;
use nsl_runtime::*;
pub fn txt(s: impl Into<String>) -> ValueData {
    ValueData::String(s.into())
}
pub fn arr(v: impl IntoIterator<Item = ValueData>) -> ValueData {
    ValueData::Array(v.into_iter().collect())
}
pub fn json(v: &ValueData) -> String {
    String::from_utf8(canonical_json(v).unwrap()).unwrap()
}
pub fn decode(s: &str) -> String {
    if s == "~empty" {
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
pub fn hash(c: char) -> String {
    format!("sha256:{}", c.to_string().repeat(64))
}
pub fn binding() -> ApprovalBindingsSpec {
    ApprovalBindingsSpec {
        tenant_id: "tenant-a".into(),
        execution_id: "exec-a".into(),
        business_operation_id: "business-a".into(),
        approval_id: "APPROVE".into(),
        approval_request_id: "request-a".into(),
        skill_semantic_hash: hash('a'),
        payload_hash: hash('b'),
        write_intent_hash: "write-intent-a".into(),
    }
}
pub fn set(s: &mut ApprovalBindingsSpec, field: &str, value: &str) -> bool {
    let dst = match field {
        "tenant_id" => &mut s.tenant_id,
        "execution_id" => &mut s.execution_id,
        "business_operation_id" => &mut s.business_operation_id,
        "approval_id" => &mut s.approval_id,
        "approval_request_id" => &mut s.approval_request_id,
        "skill_semantic_hash" => &mut s.skill_semantic_hash,
        "payload_hash" => &mut s.payload_hash,
        "write_intent_hash" => &mut s.write_intent_hash,
        _ => return false,
    };
    *dst = value.into();
    true
}
pub fn limits() -> ApprovalSummaryLimits {
    ApprovalSummaryLimits {
        max_depth: 128,
        max_nodes: 100_000,
        max_text_bytes: 100_000,
    }
}
fn text(s: &str) -> ApprovalSummaryInput {
    ApprovalSummaryInput::Text(s.into())
}
fn number(s: &str) -> ApprovalSummaryInput {
    ApprovalSummaryInput::Number(s.into())
}
pub fn map(
    items: impl IntoIterator<Item = (&'static str, ApprovalSummaryInput)>,
) -> ApprovalSummaryInput {
    ApprovalSummaryInput::Object(items.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
pub fn summary(recipe: &str) -> ApprovalSummaryInput {
    use ApprovalSummaryInput as I;
    match recipe {
        "empty" => map([]),
        "basic" => map([
            ("line_count", number("2")),
            ("flags", I::Array(vec![text("reviewed"), I::Bool(true)])),
        ]),
        "all" => map([
            ("none", I::Null),
            ("bool", I::Bool(false)),
            ("integer", number("-1234")),
            ("wide", number(&format!("1{}", "0".repeat(100)))),
            ("text", text("가😀")),
            ("decimal", text("0.5")),
            (
                "nested",
                map([(
                    "items",
                    I::Array(vec![number("0"), number("1"), I::Bool(true)]),
                )]),
            ),
        ]),
        "secret-null" => map([("api_key", I::Null), ("secret", text(""))]),
        "blank-key" => map([(" ", number("1"))]),
        "credential-key" => map([("api_key", text("synthetic-value"))]),
        "credential-text" => map([("note", text("Bearer synthetic-value"))]),
        "not-mapping" => I::Array(vec![]),
        r => {
            let index = r.chars().last().unwrap().to_digit(10).unwrap() as usize;
            let value = number(
                [
                    "0.5",
                    "1.0",
                    "-0.0",
                    "1e-07",
                    "NaN",
                    "Infinity",
                    "-Infinity",
                ][index],
            );
            let ratio = map([("ratio", value)]);
            if r.starts_with("nested-") {
                map([(
                    "nested",
                    map([("items", I::Array(vec![number("0"), ratio]))]),
                )])
            } else {
                ratio
            }
        }
    }
}
pub fn instant(s: &str) -> UtcInstant {
    s.parse().unwrap()
}
pub fn request_spec(recipe: &str) -> Result<ApprovalRequestSpec, ApprovalSummaryError> {
    Ok(ApprovalRequestSpec {
        bindings: binding(),
        skill_id: "TEST.SKILL".into(),
        skill_version: "1.0.0".into(),
        write_node_id: "write0".into(),
        tool_id: "TEST.WRITE".into(),
        risk_level: RiskLevel::R2,
        message: "Review draft".into(),
        expires_at: Some(instant("2026-10-20T02:02:03+00:00")),
        display_safe_summary: SafeApprovalSummary::new(&summary(recipe), limits())?,
        protected_payload_ref: Some("snapshot:payload-a".into()),
        data_classification: DataClassification::Internal,
    })
}
pub fn evidence_spec() -> ApprovalEvidenceSpec {
    ApprovalEvidenceSpec {
        bindings: binding(),
        decision: ApprovalDecision::Approved,
        approver_id: "approver-a".into(),
        decided_at: instant("2026-10-20T01:02:03+00:00"),
        expires_at: Some(instant("2026-10-20T02:02:03+00:00")),
        evidence_integrity: "opaque-proof-a".into(),
    }
}
pub fn optional_time(s: &str) -> Option<UtcInstant> {
    if s == "~" { None } else { Some(instant(s)) }
}
pub enum Failure {
    Model(ApprovalModelError),
    Summary(ApprovalSummaryError),
}
impl From<ApprovalModelError> for Failure {
    fn from(e: ApprovalModelError) -> Self {
        Self::Model(e)
    }
}
impl From<ApprovalSummaryError> for Failure {
    fn from(e: ApprovalSummaryError) -> Self {
        Self::Summary(e)
    }
}
pub fn model(f: &[&str]) -> Result<ValueData, Failure> {
    let value = if f[2] == "-" {
        "-".into()
    } else {
        decode(f[3])
    };
    if f[0] == "request" {
        let mut s = request_spec(f[1])?;
        s.risk_level = f[4].parse().unwrap();
        s.data_classification = f[5].parse().unwrap();
        s.expires_at = optional_time(f[6]);
        s.protected_payload_ref = if f[7] == "~" { None } else { Some(f[7].into()) };
        if f[2] == "required-order" {
            s.bindings.payload_hash = "invalid".into();
            s.skill_id.clear();
        } else if f[2] != "-" && !set(&mut s.bindings, f[2], &value) {
            match f[2] {
                "skill_id" => s.skill_id = value,
                "skill_version" => s.skill_version = value,
                "write_node_id" => s.write_node_id = value,
                "tool_id" => s.tool_id = value,
                "message" => s.message = value,
                "protected_payload_ref" => s.protected_payload_ref = Some(value),
                _ => panic!("{}", f[2]),
            }
        }
        Ok(ApprovalRequest::new(s)?.to_data())
    } else {
        let mut s = evidence_spec();
        if f[2] == "required-order" {
            s.bindings.payload_hash = "invalid".into();
            s.approver_id.clear();
        } else if f[2] != "-" && !set(&mut s.bindings, f[2], &value) {
            match f[2] {
                "decision" => s.decision = value.parse().unwrap(),
                "expires_at" => s.expires_at = optional_time(&value),
                "decided_at" => s.decided_at = instant(&value),
                "approver_id" => s.approver_id = value,
                "evidence_integrity" => s.evidence_integrity = value,
                _ => panic!("{}", f[2]),
            }
        }
        Ok(ApprovalEvidence::new(s)?.to_data())
    }
}
pub fn outcome(r: Result<ValueData, Failure>) -> ValueData {
    match r {
        Ok(d) => arr([txt("ok"), d]),
        Err(Failure::Model(ApprovalModelError::Credential(_)))
        | Err(Failure::Summary(ApprovalSummaryError {
            kind: ApprovalSummaryErrorKind::Credential,
            ..
        })) => arr([txt("error"), txt("CredentialMaterialError")]),
        Err(Failure::Model(e)) => arr([txt("error"), txt("ValueError"), txt(e.to_string())]),
        Err(Failure::Summary(e)) => match e.kind {
            ApprovalSummaryErrorKind::MappingRequired => arr([
                txt("error"),
                txt("TypeError"),
                txt("ApprovalRequest.display_safe_summary must be a mapping"),
            ]),
            ApprovalSummaryErrorKind::BlankKey => arr([
                txt("error"),
                txt("ValueError"),
                txt(format!("{} keys must be non-empty strings", e.path)),
            ]),
            _ => panic!("{e:?}"),
        },
    }
}
