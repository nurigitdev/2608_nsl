mod guard_support;
mod support;
use guard_support::*;
use nsl_core::*;
use nsl_write::*;
use std::sync::Mutex;
use support::{arr, field, get, txt};
fn check_model(name: &str, result: Result<ValueData, WriteGuardError>, expected: &ValueData) {
    let e = arr(expected);
    match result {
        Ok(value) => {
            assert_eq!(
                nsl_core::canonical_json(&value).unwrap(),
                nsl_core::canonical_json(&e[1]).unwrap(),
                "{name}"
            );
            assert_eq!(support::text(&e[0]), "ok", "{name}");
        }
        Err(err) => {
            assert_eq!(support::text(&e[0]), "error", "{name}: {err:?}");
            assert_eq!(err.kind, WriteGuardErrorKind::Model);
            assert!(err.path.starts_with("$."));
            assert!(!err.to_string().contains("Bearer"));
            assert!(std::error::Error::source(&err).is_none());
        }
    }
}
#[test]
fn frozen_policy_contract_and_precondition_models_match_exactly() {
    for (name, d, expected) in rows(include_str!("fixtures/write-guard-models-oracle.tsv")) {
        let v = field(&d, "value");
        let r = match get(&d, "kind") {
            "contract" => contract_evidence(v).map(|v| v.to_data()),
            "request" => policy_request(v).map(|v| v.to_data()),
            "policy" => policy(v).map(|v| v.to_data()),
            "precondition" => precondition(v).map(|v| v.to_data()),
            "decision" => WriteGuardDecision::new(
                get(v, "eligibility").parse().unwrap(),
                optional(v, "reason_code"),
            )
            .map(|v| v.to_data()),
            _ => panic!(),
        };
        check_model(&name, r, &expected);
    }
}
#[test]
fn frozen_write_guard_decisions_and_priority_match_after_native_admission() {
    for (name, d, expected) in rows(include_str!("fixtures/write-guard-decisions-oracle.tsv")) {
        let s = Setup::new(d);
        let before = s.e.clone();
        let r = s.guard().evaluate(s.request(), s.now).unwrap();
        assert_eq!(r.to_data(), arr(&expected)[1], "{name}");
        assert_eq!(s.e, before, "{name}");
        if r.eligibility() == PreWriteEligibility::Allowed {
            require_write_allowed(&r).unwrap();
        } else {
            assert_eq!(
                require_write_allowed(&r).unwrap_err().kind,
                WriteGuardErrorKind::NotAllowed
            );
        }
    }
}
#[test]
fn all_legacy_prewrite_axes_and_no_check_semantics_match_oracle() {
    for (name, d, expected) in rows(include_str!("fixtures/write-guard-prewrite-oracle.tsv")) {
        let e = execution(&d);
        let decision = PreWriteEvaluator.evaluate(&e);
        assert_eq!(decision.to_data(), arr(&expected)[1], "{name}");
        assert_eq!(e.validation.as_str(), get(&d, "validation"));
    }
    let empty = WriteExecutionEvidence::new(WriteValidationStatus::NoCheck);
    assert_eq!(empty.approval_status, ApprovalStatus::NotRequested);
    assert_eq!(
        PreWriteEvaluator.evaluate(&empty).reason_code(),
        Some("RUNTIME_PREFLIGHT_FAILED")
    );
    for status in [
        WriteValidationStatus::HasFail,
        WriteValidationStatus::HasUnknown,
        WriteValidationStatus::HasFailAndUnknown,
    ] {
        let mut s = Setup::new(baseline());
        s.e.validation = status;
        let actual = WriteGuard::default().evaluate(s.request(), s.now).unwrap();
        assert_eq!(actual, PreWriteEvaluator.evaluate(&s.e));
    }
}
#[test]
fn approved_native_admission_differences_keep_original_oracle_observations() {
    for (name, d, oracle) in rows(include_str!("fixtures/write-guard-exceptions-oracle.tsv")) {
        assert_eq!(get(&arr(&oracle)[1], "eligibility"), "ALLOWED", "{name}");
        let native = field(&d, "native").clone();
        let s = Setup::new(d);
        let before = s.e.clone();
        let port = RecordingPolicy::new(s.p.clone().unwrap());
        let result = WriteGuard::default().authorize(s.request(), &port, s.now);
        if opt(&native, "error").is_some() {
            let e = result.unwrap_err();
            assert_eq!(e.kind, WriteGuardErrorKind::Principal, "{name}");
            assert_eq!(e.path, get(&native, "path"), "{name}");
            assert!(std::error::Error::source(&e).is_none());
        } else {
            assert_eq!(result.unwrap().to_data(), native, "{name}");
        }
        assert!(port.calls.lock().unwrap().is_empty(), "{name}");
        assert_eq!(s.e, before);
    }
}
struct RecordingPolicy {
    calls: Mutex<Vec<WritePolicyRequest>>,
    decision: PolicyDecision,
    fail: bool,
}
impl RecordingPolicy {
    fn new(decision: PolicyDecision) -> Self {
        Self {
            calls: Mutex::new(vec![]),
            decision,
            fail: false,
        }
    }
}
impl RuntimePolicyPort for RecordingPolicy {
    fn authorize_write(
        &self,
        r: &WritePolicyRequest,
    ) -> Result<PolicyDecision, RuntimePolicyError> {
        self.calls.lock().unwrap().push(r.clone());
        if self.fail {
            Err(RuntimePolicyError)
        } else {
            Ok(self.decision.clone())
        }
    }
}
#[test]
fn current_policy_port_is_called_once_after_admission_and_checked_before_allow() {
    let mut s = Setup::new(baseline());
    let port = RecordingPolicy::new(s.p.clone().unwrap());
    let before = s.e.clone();
    let guard = WriteGuard::default();
    let expected = write_policy_request(&s.i, s.context(), &s.ce).unwrap();
    assert_eq!(expected.metadata().subject_id, s.principal.subject);
    s.p = None;
    assert_eq!(
        guard.evaluate(s.request(), s.now).unwrap().reason_code(),
        Some("POLICY_EVIDENCE_MISSING")
    );
    for n in 1..=2 {
        assert_eq!(
            guard
                .authorize(s.request(), &port, s.now)
                .unwrap()
                .eligibility(),
            PreWriteEligibility::Allowed
        );
        assert_eq!(port.calls.lock().unwrap().len(), n);
        assert_eq!(port.calls.lock().unwrap()[n - 1], expected);
    }
    assert_eq!(s.e, before);
    for (change, reason) in [
        ("deny", "POLICY_DENIED"),
        ("subject", "POLICY_BINDING_MISMATCH"),
        ("future", "POLICY_NOT_CURRENT"),
        ("expired", "POLICY_NOT_CURRENT"),
    ] {
        let mut spec = port.decision.metadata().clone();
        match change {
            "deny" => spec.effect = PolicyEffect::Deny,
            "subject" => spec.subject_id = "other".into(),
            "future" => {
                spec.evaluated_at = "2026-10-20T02:00:00.000001Z".parse().unwrap();
                spec.expires_at = "2026-10-20T02:00:01Z".parse().unwrap();
            }
            "expired" => spec.expires_at = s.now,
            _ => panic!(),
        };
        let current = RecordingPolicy::new(PolicyDecision::new(spec).unwrap());
        assert_eq!(
            guard
                .authorize(s.request(), &current, s.now)
                .unwrap()
                .reason_code(),
            Some(reason)
        );
        assert_eq!(current.calls.lock().unwrap().len(), 1);
    }
    let mut failed = RecordingPolicy::new(port.decision.clone());
    failed.fail = true;
    let err = guard.authorize(s.request(), &failed, s.now).unwrap_err();
    assert_eq!(err.kind, WriteGuardErrorKind::PolicyPort);
    assert_eq!(err.path, "$.policy_decision");
    assert!(std::error::Error::source(&err).is_none());
    assert!(!err.to_string().contains("secret"));
    assert_eq!(format!("{RuntimePolicyError:?}"), "RuntimePolicyError");
    // A cached ALLOW cannot override a current DENY; a cached DENY cannot veto a
    // new matching ALLOW. Every authorize call resolves current authority again.
    s.p = Some(failed.decision.clone());
    let mut deny = s.p.as_ref().unwrap().metadata().clone();
    deny.effect = PolicyEffect::Deny;
    let deny = PolicyDecision::new(deny).unwrap();
    let denied = RecordingPolicy::new(deny.clone());
    assert_eq!(
        guard
            .authorize(s.request(), &denied, s.now)
            .unwrap()
            .reason_code(),
        Some("POLICY_DENIED")
    );
    s.p = Some(deny);
    assert_eq!(
        guard
            .authorize(s.request(), &port, s.now)
            .unwrap()
            .eligibility(),
        PreWriteEligibility::Allowed
    );
}
#[test]
fn current_principal_is_checked_without_losing_runtime_identity_rules() {
    for (field, value, path) in [
        ("tenant", "", "tenant_id"),
        ("subject", "", "subject_id"),
        ("actor", "OTHER", "actor_type"),
        ("auth", "", "auth_context_ref"),
        ("role", " ", "roles"),
        ("scope", " ", "scopes"),
        ("behalf", "", "on_behalf_of"),
        ("auth", "Bearer synthetic-only", "auth_context_ref"),
        ("role", "Bearer synthetic-only", "roles"),
        ("scope", "Bearer synthetic-only", "scopes"),
        ("behalf", "Bearer synthetic-only", "on_behalf_of"),
        ("tenant", "Bearer synthetic-only", "tenant_id"),
        ("subject", "Bearer synthetic-only", "subject_id"),
    ] {
        let mut s = Setup::new(baseline());
        match field {
            "tenant" => s.principal.tenant = value.into(),
            "subject" => s.principal.subject = value.into(),
            "actor" => s.principal.actor = value.into(),
            "auth" => s.principal.auth = value.into(),
            "role" => {
                s.principal.roles.insert(value.into());
            }
            "scope" => {
                s.principal.scopes.insert(value.into());
            }
            "behalf" => s.principal.behalf = Some(value.into()),
            _ => panic!(),
        };
        let port = RecordingPolicy::new(s.p.clone().unwrap());
        let e = WriteGuard::default()
            .authorize(s.request(), &port, s.now)
            .unwrap_err();
        assert_eq!(e.kind, WriteGuardErrorKind::Principal);
        assert_eq!(e.path, format!("$.context.principal.{path}"));
        assert!(port.calls.lock().unwrap().is_empty());
        assert!(!e.to_string().contains("Bearer"));
    }
    let mut s = Setup::new(baseline());
    s.principal.actor = "SERVICE".into();
    s.principal.roles.clear();
    s.principal.behalf = Some("delegating-user".into());
    assert_eq!(
        WriteGuard::default()
            .evaluate(s.request(), s.now)
            .unwrap()
            .eligibility(),
        PreWriteEligibility::Allowed
    );
}
#[test]
fn risk_policy_set_boundaries_and_scope_collections_do_not_grant_authority() {
    assert_eq!(
        RiskAcceptancePolicy::new([]).unwrap_err().path,
        "$.risk_policy.accepted_levels"
    );
    for count in [1, 5, 6, 7, 64, 65] {
        let policy =
            RiskAcceptancePolicy::new(RiskLevel::ALL.iter().cycle().take(count).copied()).unwrap();
        assert_eq!(policy.accepted_levels().len(), count.min(6));
        for level in policy.accepted_levels() {
            assert!(policy.accepts(*level));
        }
    }
    assert_eq!(
        RiskAcceptancePolicy::initial_v0_2().accepted_levels(),
        &[RiskLevel::R3]
    );
    for count in [0, 1, 63, 64, 65] {
        let mut s = Setup::new(baseline());
        s.principal.scopes = (0..count).map(|idx| format!("scope:{idx}")).collect();
        let port = RecordingPolicy::new(s.p.clone().unwrap());
        assert_eq!(
            WriteGuard::default()
                .authorize(s.request(), &port, s.now)
                .unwrap()
                .reason_code(),
            Some("WRITE_SCOPE_DENIED")
        );
        assert!(port.calls.lock().unwrap().is_empty());
        s.principal.scopes.insert(s.c.required_scope().into());
        assert_eq!(
            WriteGuard::default()
                .authorize(s.request(), &port, s.now)
                .unwrap()
                .eligibility(),
            PreWriteEligibility::Allowed
        );
        assert_eq!(port.calls.lock().unwrap().len(), 1);
    }
}
#[test]
fn live_contract_scope_principal_and_encoding_are_rechecked_on_every_call() {
    let mut s = Setup::new(baseline());
    let guard = WriteGuard::default();
    let port = RecordingPolicy::new(s.p.clone().unwrap());
    assert_eq!(
        guard
            .authorize(s.request(), &port, s.now)
            .unwrap()
            .eligibility(),
        PreWriteEligibility::Allowed
    );
    s.principal.scopes.remove(s.c.required_scope());
    assert_eq!(
        guard
            .authorize(s.request(), &port, s.now)
            .unwrap()
            .reason_code(),
        Some("WRITE_SCOPE_DENIED")
    );
    assert_eq!(port.calls.lock().unwrap().len(), 1);
    s.principal.scopes.insert(s.c.required_scope().into());
    s.principal.subject = "different-current-user".into();
    assert_eq!(
        guard
            .authorize(s.request(), &port, s.now)
            .unwrap()
            .reason_code(),
        Some("POLICY_BINDING_MISMATCH")
    );
    assert_eq!(port.calls.lock().unwrap().len(), 2);
    let optional = TypeRef::new(TypeKind::Optional(Box::new(
        TypeRef::new(TypeKind::Primitive("Int".into())).unwrap(),
    )))
    .unwrap();
    s.c = contract(field(&s.data, "current_contract"), Some(optional));
    let e = guard.authorize(s.request(), &port, s.now).unwrap_err();
    assert_eq!(e.kind, WriteGuardErrorKind::ContractEncoding);
    assert_eq!(e.path, "$.current_contract");
    assert_eq!(port.calls.lock().unwrap().len(), 2);
    for capability in ["READ", "TRANSACTION", "FUTURE_CAPABILITY"] {
        let mut d = field(&s.data, "current_contract").clone();
        let ValueData::Object(ref mut map) = d else {
            panic!()
        };
        map.insert("capability".into(), txt(capability));
        s.c = contract(&d, None);
        assert_eq!(
            guard
                .authorize(s.request(), &port, s.now)
                .unwrap()
                .reason_code(),
            Some("WRITE_CONTRACT_INVALID")
        );
        assert_eq!(port.calls.lock().unwrap().len(), 2);
    }
}
#[test]
fn evidence_snapshots_are_immutable_and_invalid_representations_stay_typed() {
    let s = Setup::new(baseline());
    let p = s.p.as_ref().unwrap();
    let b = s.b.as_ref().unwrap();
    let mut copy = p.metadata().clone();
    copy.policy_version = "updated".into();
    assert_eq!(p.metadata().policy_version, "7");
    for debug in [
        format!("{p:?}"),
        format!("{:?}", p.metadata()),
        format!("{b:?}"),
        format!("{:?}", b.metadata()),
        format!("{:?}", s.ce),
        format!("{:?}", s.ce.metadata()),
        format!(
            "{:?}",
            write_policy_request(&s.i, s.context(), &s.ce).unwrap()
        ),
        format!(
            "{:?}",
            write_policy_request(&s.i, s.context(), &s.ce)
                .unwrap()
                .metadata()
        ),
    ] {
        assert!(!debug.contains("tenant-a"));
        assert!(!debug.contains("sha256:"));
    }
    for e in PolicyEffect::ALL {
        assert_eq!(e.as_str().parse::<PolicyEffect>().unwrap(), *e);
    }
    for m in BusinessPreconditionMechanism::ALL {
        assert_eq!(
            m.as_str().parse::<BusinessPreconditionMechanism>().unwrap(),
            *m
        );
    }
    assert!("allow".parse::<PolicyEffect>().is_err());
    assert!(
        "UNSUPPORTED"
            .parse::<BusinessPreconditionMechanism>()
            .is_err()
    );
    assert_eq!(
        "UNSUPPORTED"
            .parse::<WriteValidationStatus>()
            .unwrap_err()
            .kind,
        WriteGuardErrorKind::Model
    );
    for v in [
        "2026-10-20T02:00:00",
        "2026-10-20T02:00:00+09:00",
        "2026-10-20T02:00:00.1234567Z",
    ] {
        assert!(v.parse::<UtcInstant>().is_err());
    }
    for reason in ["", " ", "\u{1c}"] {
        assert!(
            WriteGuardDecision::new(PreWriteEligibility::Blocked, Some(reason.into())).is_err()
        );
    }
    let opaque = WriteGuardDecision::new(
        PreWriteEligibility::Blocked,
        Some("Bearer synthetic-only".into()),
    )
    .unwrap();
    assert_eq!(opaque.reason_code(), Some("Bearer synthetic-only"));
    let allowed = WriteGuardDecision::new(PreWriteEligibility::Allowed, None).unwrap();
    require_write_allowed(&allowed).unwrap();
    assert_eq!(allowed.reason_code(), None);
    let blocked =
        WriteGuardDecision::new(PreWriteEligibility::Blocked, Some("SAFE_REASON".into())).unwrap();
    let err = require_write_allowed(&blocked).unwrap_err();
    assert_eq!(
        err.to_string(),
        "$.decision: WRITE eligibility is not ALLOWED"
    );
    assert!(std::error::Error::source(&err).is_none());
    let generic = WriteGuardDecision::new(PreWriteEligibility::RequiresApproval, None).unwrap();
    assert_eq!(
        require_write_allowed(&generic).unwrap_err().kind,
        WriteGuardErrorKind::NotAllowed
    );
}
#[test]
fn updated_scope_must_be_present_even_when_all_current_evidence_agrees() {
    let mut s = Setup::new(baseline());
    let mut config = field(&s.data, "current_contract").clone();
    let ValueData::Object(ref mut m) = config else {
        panic!()
    };
    for scope in ["other:write:scope", "Bearer synthetic-only"] {
        m.insert("required_scope".into(), txt(scope));
        s.c = contract(&ValueData::Object(m.clone()), None);
        let mut ce = s.ce.metadata().clone();
        ce.contract_hash = s.c.contract_hash().unwrap().to_string();
        s.ce = WriteContractEvidence::new(ce).unwrap();
        let mut pre = s.b.as_ref().unwrap().metadata().clone();
        pre.contract_hash = s.c.contract_hash().unwrap().to_string();
        s.b = Some(BusinessPreconditionEvidence::new(pre).unwrap());
        let port = RecordingPolicy::new(s.p.clone().unwrap());
        assert_eq!(
            WriteGuard::default()
                .authorize(s.request(), &port, s.now)
                .unwrap()
                .reason_code(),
            Some("WRITE_SCOPE_DENIED")
        );
        assert!(port.calls.lock().unwrap().is_empty());
    }
}
