mod checkpoint_support;
mod resume_support;
use checkpoint_support as h;
use nsl_core::*;
use nsl_runtime::*;
use resume_support as r;

fn load(line: &str) -> (&str, &str, ValueData) {
    let f = line.split('\t').collect::<Vec<_>>();
    (f[0], f[1], r::parse(&h::decode(f[2])))
}
#[test]
fn resume_results_state_proof_ttl_and_claim_failures_match_frozen_oracle() {
    r::runtime().block_on(async {
        let mut count = 0;
        for line in include_str!("fixtures/resumes-oracle.tsv").lines().skip(1) {
            let (ver, case, data) = load(line);
            let i = r::field(&data, "input");
            let cp = ExecutionCheckpoint::from_bytes(
                &h::decode(r::str_field(i, "checkpoint")),
                h::limits(),
            )
            .unwrap();
            let before = cp.to_bytes(h::limits()).unwrap();
            let t = r::token(i);
            let p = r::Store::new(cp, case, r::str_field(i, "now").parse().unwrap());
            let clock = h::Clock::default();
            let payload = r::str_field(i, "current_payload").parse().unwrap();
            for (attempt, row) in r::items(r::field(&data, "observations")).iter().enumerate() {
                if attempt > 0 && case == "proof-retry" {
                    p.0.lock().unwrap().verifier_mode = "true";
                }
                let expected = r::field(row, "outcome");
                let outcome = match r::coordinator()
                    .resume(
                        r::input(i, &t, ver, case, &payload),
                        &p,
                        &p,
                        &p,
                        clock.clone(),
                    )
                    .await
                {
                    Ok(o) => {
                        assert_eq!(
                            r::items(expected)[0],
                            r::text("ok"),
                            "{ver}/{case}/{attempt}"
                        );
                        let validation = o.validation();
                        let validation_data = validation.to_data();
                        assert_eq!(
                            r::field(&validation_data, "validation"),
                            &r::text(validation.validation().as_str())
                        );
                        assert_eq!(
                            r::field(&validation_data, "approval_status"),
                            &r::text(validation.approval_status().as_str())
                        );
                        assert_eq!(
                            r::field(&validation_data, "reason_code"),
                            &validation.reason_code().map_or(ValueData::Null, r::text)
                        );
                        assert_eq!(o.context().skill().skill_id, h::skill(ver).skill_id);
                        let cp_bytes = o.checkpoint().to_bytes(h::limits()).unwrap();
                        let claim = o.claim_id().to_string();
                        let consumed = o.claim_consumed();
                        let mut ctx = o.into_context();
                        r::arr([
                            r::text("ok"),
                            validation_data,
                            ValueData::Bool(validation.approval_gate_passed()),
                            ValueData::Bool(validation.requires_reapproval()),
                            ValueData::Bool(consumed),
                            r::text(r::hex(&canonical_json(&r::state(&mut ctx)).unwrap())),
                            r::text(r::hex(&cp_bytes)),
                            r::text(claim),
                        ])
                    }
                    Err(e) => {
                        assert_eq!(
                            r::items(expected)[0],
                            r::text("error"),
                            "{ver}/{case}/{attempt}: {e}"
                        );
                        assert_eq!(
                            (e.kind, e.path.clone(), e.release_failed),
                            r::expected_error(case, attempt),
                            "{ver}/{case}/{attempt}: {e}"
                        );
                        assert!(std::error::Error::source(&e).is_none());
                        assert!(!format!("{e:?} {e}").contains("private"));
                        assert_eq!(e, e.clone());
                        expected.clone()
                    }
                };
                let mut observed = p.observe(outcome, clock.calls());
                if case == "blank-intent" {
                    // Inherited R7-S02: reject before runtime clock/context creation.
                    assert_eq!(clock.calls(), 0);
                    assert_eq!(r::field(row, "runtime_calls"), &r::number(1));
                    h::obj(&mut observed).insert("runtime_calls".into(), r::number(1));
                }
                assert_eq!(observed, *row, "{ver}/{case}/{attempt}");
                assert_eq!(
                    p.0.lock().unwrap().cp.to_bytes(h::limits()).unwrap(),
                    before
                );
                count += 1;
            }
        }
        assert!(count > 120);
    });
}
#[test]
fn approved_principal_and_claim_version_differences_release_before_restore_or_proof() {
    r::runtime().block_on(async {
        let mut count = 0;
        for line in include_str!("fixtures/resumes-exceptions.tsv")
            .lines()
            .skip(1)
        {
            let (ver, case, data) = load(line);
            let i = r::field(&data, "input");
            let cp = ExecutionCheckpoint::from_bytes(
                &h::decode(r::str_field(i, "checkpoint")),
                h::limits(),
            )
            .unwrap();
            let before = cp.clone();
            let t = r::token(i);
            let p = r::Store::new(cp, case, r::str_field(i, "now").parse().unwrap());
            let clock = h::Clock::default();
            let payload = r::str_field(i, "current_payload").parse().unwrap();
            let e = r::coordinator()
                .resume(
                    r::input(i, &t, ver, case, &payload),
                    &p,
                    &p,
                    &p,
                    clock.clone(),
                )
                .await
                .unwrap_err();
            assert_eq!(e.kind, ApprovalResumeErrorKind::Type);
            assert!(!e.release_failed);
            assert_eq!(
                e.path,
                if case == "returned-version" {
                    "$.checkpoint.checkpoint_version"
                } else {
                    "$.request.security.principal"
                }
            );
            assert_eq!(clock.calls(), 0);
            let state = p.0.lock().unwrap();
            assert_eq!(state.state, "AVAILABLE");
            assert_eq!(state.owner, None);
            assert_eq!(state.cp, before);
            assert_eq!(state.verifier_calls, 0);
            assert_eq!(state.clock_calls, 0);
            assert_eq!(
                state.events,
                if case == "returned-version" {
                    vec!["claim", "release"]
                } else {
                    vec![]
                }
            );
            let original = &r::items(r::field(&data, "observations"))[0];
            assert_eq!(r::items(r::field(original, "outcome"))[0], r::text("ok"));
            assert_eq!(r::field(original, "store_state"), &r::text("CONSUMED"));
            count += 1;
        }
        assert_eq!(count, 6);
    });
}

fn baseline() -> ValueData {
    load(
        include_str!("fixtures/resumes-oracle.tsv")
            .lines()
            .find(|l| l.starts_with("1-1\tpass\t"))
            .unwrap(),
    )
    .2
}
fn checkpoint(i: &ValueData) -> ExecutionCheckpoint {
    ExecutionCheckpoint::from_bytes(&h::decode(r::str_field(i, "checkpoint")), h::limits()).unwrap()
}

#[test]
fn concurrent_workers_and_overlapping_claims_publish_only_one_resumed_context() {
    use std::sync::{Arc, Barrier};
    let d = baseline();
    let i = r::field(&d, "input");
    let t = r::token(i);
    let payload: PayloadHash = r::str_field(i, "current_payload").parse().unwrap();
    let p = r::Store::new(
        checkpoint(i),
        "pass",
        r::str_field(i, "now").parse().unwrap(),
    );
    let barrier = Arc::new(Barrier::new(2));
    let results = std::thread::scope(|scope| {
        let mut handles = vec![];
        for claim in ["claim-a", "claim-b"] {
            let p = p.clone();
            let barrier = barrier.clone();
            let t = &t;
            let payload = &payload;
            handles.push(scope.spawn(move || {
                barrier.wait();
                r::runtime().block_on(async {
                    let mut input = r::input(i, t, "1-1", "pass", payload);
                    input.claim_id = claim;
                    r::coordinator()
                        .resume(input, &p, &p, &p, h::Clock::default())
                        .await
                })
            }));
        }
        handles
            .into_iter()
            .map(|h| h.join().unwrap())
            .collect::<Vec<_>>()
    });
    assert_eq!(results.iter().filter(|v| v.is_ok()).count(), 1);
    for v in results {
        match v {
            Ok(o) => {
                assert!(o.claim_consumed());
                assert!(o.validation().approval_gate_passed());
            }
            Err(e) => assert!(matches!(
                e.kind,
                ApprovalResumeErrorKind::Claim(ResumeClaimError::Live | ResumeClaimError::Consumed)
            )),
        }
    }
    let state = p.0.lock().unwrap();
    assert_eq!(state.state, "CONSUMED");
    assert_eq!(state.events.iter().filter(|e| *e == "consume").count(), 1);
    assert_eq!(state.verifier_calls, 1);
}

#[test]
fn dropped_claim_release_and_consume_futures_fail_closed_without_background_cleanup() {
    use std::{
        future::Future,
        task::{Context, Poll, Waker},
    };
    r::runtime().block_on(async {
        let d = baseline();
        let i = r::field(&d, "input");
        let t = r::token(i);
        let payload = r::str_field(i, "current_payload").parse().unwrap();
        for mode in ["claim-pending", "release-pending", "consume-pending"] {
            let p = r::Store::new(checkpoint(i), mode, r::str_field(i, "now").parse().unwrap());
            if mode == "release-pending" {
                p.0.lock().unwrap().verifier_mode = "false";
            }
            let clock = h::Clock::default();
            let co = r::coordinator();
            let mut first = Box::pin(co.resume(
                r::input(i, &t, "1-1", "pass", &payload),
                &p,
                &p,
                &p,
                clock.clone(),
            ));
            assert!(matches!(
                first.as_mut().poll(&mut Context::from_waker(Waker::noop())),
                Poll::Pending
            ));
            drop(first);
            let events = {
                let state = p.0.lock().unwrap();
                assert_eq!(state.state, "LIVE");
                assert_eq!(state.owner.as_deref(), Some("claim-a"));
                state.events.clone()
            };
            tokio::task::yield_now().await;
            assert_eq!(p.0.lock().unwrap().events, events);
            let mut second = r::input(i, &t, "1-1", "pass", &payload);
            second.claim_id = "claim-b";
            let e = co
                .resume(second, &p, &p, &p, h::Clock::default())
                .await
                .unwrap_err();
            assert_eq!(
                e.kind,
                ApprovalResumeErrorKind::Claim(ResumeClaimError::Live)
            );
            // Explicit owner recovery by the host, never automatic release after an
            // unacknowledged claim/consume. The coordinator has returned no context.
            {
                let mut state = p.0.lock().unwrap();
                state.mode = "pass".into();
                state.verifier_mode = "true";
            }
            p.release(t.execution_id(), t.checkpoint_version(), "claim-a")
                .await
                .unwrap();
            let o = co
                .resume(
                    r::input(i, &t, "1-1", "pass", &payload),
                    &p,
                    &p,
                    &p,
                    h::Clock::default(),
                )
                .await
                .unwrap();
            assert!(o.validation().approval_gate_passed());
        }
    });
}

#[test]
fn all_claimed_identity_mismatches_release_even_when_cleanup_acknowledgement_fails() {
    r::runtime().block_on(async {
        let d = baseline();
        let i = r::field(&d, "input");
        let t = r::token(i);
        let payload = r::str_field(i, "current_payload").parse().unwrap();
        for field in [
            "execution_id",
            "business_operation_id",
            "checkpoint_version",
        ] {
            for mode in ["pass", "release-before", "release-after"] {
                let p = r::Store::new(
                    checkpoint(i),
                    if field == "checkpoint_version" {
                        "returned-version"
                    } else if field == "execution_id" {
                        "returned-execution"
                    } else {
                        "returned-business"
                    },
                    r::str_field(i, "now").parse().unwrap(),
                );
                p.0.lock().unwrap().mode = mode.into();
                let clock = h::Clock::default();
                let e = r::coordinator()
                    .resume(
                        r::input(i, &t, "1-1", "pass", &payload),
                        &p,
                        &p,
                        &p,
                        clock.clone(),
                    )
                    .await
                    .unwrap_err();
                assert_eq!(e.kind, ApprovalResumeErrorKind::Type);
                assert_eq!(e.path, format!("$.checkpoint.{field}"));
                assert_eq!(e.release_failed, mode != "pass");
                assert!(std::error::Error::source(&e).is_none());
                assert_eq!(clock.calls(), 0);
                let state = p.0.lock().unwrap();
                assert_eq!(state.events, ["claim", "release"]);
                assert_eq!(state.verifier_calls, 0);
                assert_eq!(
                    state.state,
                    if mode == "release-before" {
                        "LIVE"
                    } else {
                        "AVAILABLE"
                    }
                );
            }
        }
    });
}

#[test]
fn malformed_unverified_and_credential_bearing_current_principals_never_claim() {
    r::runtime().block_on(async {
        let d = baseline();
        let i = r::field(&d, "input");
        let t = r::token(i);
        let payload = r::str_field(i, "current_payload").parse().unwrap();
        for case in [
            "tenant",
            "subject",
            "actor",
            "auth",
            "role",
            "scope",
            "behalf",
            "unverified",
            "credential",
        ] {
            let base = h::request();
            let mut sec = base.security_context().clone();
            let p = &mut sec.principal;
            match case {
                "tenant" => p.tenant_id = " ".into(),
                "subject" => p.subject_id = "".into(),
                "actor" => p.actor_type = "INVALID".into(),
                "auth" => p.auth_context_ref = "".into(),
                "role" => {
                    p.roles.insert("".into());
                }
                "scope" => {
                    p.scopes.insert(" ".into());
                }
                "behalf" => p.on_behalf_of = Some("".into()),
                "unverified" => p.verification = PrincipalVerification::Unverified,
                _ => p.auth_context_ref = "Bearer synthetic-only".into(),
            }
            let request = ExecutionRequest::new(
                base.identity().execution_id().into(),
                base.inputs().clone(),
                base.runtime_context().clone(),
                sec,
                Some("business-a".into()),
            )
            .unwrap();
            let p = r::Store::new(
                checkpoint(i),
                "pass",
                r::str_field(i, "now").parse().unwrap(),
            );
            let clock = h::Clock::default();
            let mut input = r::input(i, &t, "1-1", "pass", &payload);
            input.request = request;
            let e = r::coordinator()
                .resume(input, &p, &p, &p, clock.clone())
                .await
                .unwrap_err();
            assert_eq!(e.kind, ApprovalResumeErrorKind::Type);
            assert_eq!(e.path, "$.request.security.principal");
            assert!(!format!("{e:?} {e}").contains("synthetic"));
            assert_eq!(clock.calls(), 0);
            let state = p.0.lock().unwrap();
            assert!(state.events.is_empty());
            assert_eq!(state.state, "AVAILABLE");
            assert_eq!(state.owner, None);
        }
    });
}

#[test]
fn token_text_version_boundaries_and_arbitrary_precision_checkpoint_versions_are_admitted_explicitly()
 {
    r::runtime().block_on(async {
        let d = baseline();
        let i = r::field(&d, "input");
        let evidence = r::evidence(r::field(i, "evidence"));
        for (exec, business, version, path) in [
            ("", "business-a", "1", "$.token.execution_id"),
            ("exec-checkpoint", " ", "1", "$.token.business_operation_id"),
            (
                "exec-checkpoint",
                "business-a",
                "0",
                "$.token.checkpoint_version",
            ),
            (
                "exec-checkpoint",
                "business-a",
                "-1",
                "$.token.checkpoint_version",
            ),
            (
                "Bearer synthetic-only",
                "business-a",
                "1",
                "$.token.execution_id",
            ),
        ] {
            let e = ResumeToken::new(
                exec.into(),
                business.into(),
                version.parse().unwrap(),
                evidence.clone(),
            )
            .unwrap_err();
            assert_eq!(e.kind, ApprovalResumeErrorKind::Type);
            assert_eq!(e.path, path);
            assert!(!e.to_string().contains("synthetic"));
        }
        let payload = r::str_field(i, "current_payload").parse().unwrap();
        for version in ["1", "2", "10000000000000000000000000000000000000000"] {
            let version: Integer = version.parse().unwrap();
            let mut spec = checkpoint(i).metadata().clone();
            let mut state = r::parse(&spec.state_payload);
            h::obj(&mut state).insert(
                "checkpoint_version".into(),
                ValueData::Number(version.clone()),
            );
            h::obj(h::at(&mut state, &["checkpoint"])).insert(
                "checkpoint_version".into(),
                ValueData::Number(version.clone()),
            );
            spec.checkpoint_version = version.clone();
            spec.state_payload = canonical_json(&state).unwrap();
            let cp = ExecutionCheckpoint::create(spec, h::limits()).unwrap();
            let p = r::Store::new(cp, "pass", r::str_field(i, "now").parse().unwrap());
            let token = ResumeToken::new(
                "exec-checkpoint".into(),
                "business-a".into(),
                version.clone(),
                evidence.clone(),
            )
            .unwrap();
            assert_eq!(token, token.clone());
            assert!(!format!("{token:?}").contains("opaque"));
            let o = r::coordinator()
                .resume(
                    r::input(i, &token, "1-1", "pass", &payload),
                    &p,
                    &p,
                    &p,
                    h::Clock::default(),
                )
                .await
                .unwrap();
            assert_eq!(o.checkpoint().metadata().checkpoint_version, version);
            assert!(o.validation().approval_gate_passed());
            assert!(!format!("{o:?}").contains("TEAM-FINANCE"));
        }
    });
}

#[test]
fn claim_adapter_contract_enforces_latest_version_and_owner_on_release_and_consume() {
    r::runtime().block_on(async {
        let d = baseline();
        let i = r::field(&d, "input");
        let t = r::token(i);
        let p = r::Store::new(
            checkpoint(i),
            "pass",
            r::str_field(i, "now").parse().unwrap(),
        );
        assert_eq!(
            p.release(t.execution_id(), t.checkpoint_version(), "claim-a")
                .await,
            Err(ResumeClaimError::Owner)
        );
        p.claim(t.execution_id(), t.checkpoint_version(), "claim-a")
            .await
            .unwrap();
        assert_eq!(
            p.release(t.execution_id(), t.checkpoint_version(), "claim-b")
                .await,
            Err(ResumeClaimError::Owner)
        );
        assert_eq!(
            p.consume(t.execution_id(), t.checkpoint_version(), "claim-b")
                .await,
            Err(ResumeClaimError::Owner)
        );
        assert_eq!(p.0.lock().unwrap().owner.as_deref(), Some("claim-a"));
        p.release(t.execution_id(), t.checkpoint_version(), "claim-a")
            .await
            .unwrap();
        assert_eq!(
            p.consume(t.execution_id(), t.checkpoint_version(), "claim-a")
                .await,
            Err(ResumeClaimError::Owner)
        );
        p.claim(t.execution_id(), t.checkpoint_version(), "claim-b")
            .await
            .unwrap();
        p.consume(t.execution_id(), t.checkpoint_version(), "claim-b")
            .await
            .unwrap();
        assert_eq!(
            p.claim(t.execution_id(), t.checkpoint_version(), "claim-c")
                .await,
            Err(ResumeClaimError::Consumed)
        );
    });
}

#[test]
fn recorded_utc_clock_and_proof_verification_order_preserve_expiry_semantics() {
    let d = baseline();
    let i = r::field(&d, "input");
    let evidence = r::evidence(r::field(i, "evidence"));
    let bindings = evidence.bindings().clone();
    let before: UtcInstant = "2026-10-20T02:02:02.999999+00:00".parse().unwrap();
    let at: UtcInstant = "2026-10-20T02:02:03+00:00".parse().unwrap();
    for (instant, expired) in [(before, false), (at, true)] {
        let clock = RecordedApprovalClock(instant);
        assert_eq!(clock.now().unwrap(), instant);
        let p = r::Store::new(checkpoint(i), "pass", instant);
        let result = ApprovalEvidenceValidator
            .validate(&evidence, &bindings, &p, &clock)
            .unwrap();
        assert_eq!(result.requires_reapproval(), expired);
        assert_eq!(result.approval_gate_passed(), !expired);
    }
    // Multiple binding differences retain verifier-first, business/request/intent/
    // payload precedence; a rejected opaque proof never reaches wall-clock use.
    let p = r::Store::new(checkpoint(i), "invalid-proof", at);
    let result = ApprovalEvidenceValidator
        .validate(&evidence, &bindings, &p, &p)
        .unwrap();
    assert_eq!(result.reason_code(), Some("APPROVAL_EVIDENCE_INVALID"));
    assert_eq!(p.0.lock().unwrap().clock_calls, 0);
}

#[test]
fn claimed_checkpoint_resource_limits_release_without_proof_or_context_construction() {
    r::runtime().block_on(async {
        let d = baseline();
        let i = r::field(&d, "input");
        let t = r::token(i);
        let cp = checkpoint(i);
        let payload = r::str_field(i, "current_payload").parse().unwrap();
        for outer in [false, true] {
            let max = if outer {
                // Native restore verifies the canonical integrity material;
                // full transport-envelope parsing is the adapter's from_bytes boundary.
                let mut data = cp.to_data(h::limits()).unwrap();
                h::obj(&mut data).remove("integrity_hash");
                canonical_json(&data).unwrap().len()
            } else {
                cp.state_payload().len()
            };
            for budget in [0, 1, max - 1, max, max + 1] {
                let mut limits = h::limits();
                if outer {
                    limits.outer.max_bytes = budget;
                } else {
                    limits.state.max_bytes = budget;
                }
                let p = r::Store::new(cp.clone(), "pass", r::str_field(i, "now").parse().unwrap());
                let runtime_clock = h::Clock::default();
                let result =
                    ApprovalResumeCoordinator::new(CheckpointCoordinator::for_oracle(limits))
                        .resume(
                            r::input(i, &t, "1-1", "pass", &payload),
                            &p,
                            &p,
                            &p,
                            runtime_clock.clone(),
                        )
                        .await;
                if budget < max {
                    let e = result.unwrap_err();
                    assert_eq!(
                        e.kind,
                        ApprovalResumeErrorKind::Checkpoint(CheckpointErrorKind::Limit(
                            JsonLimit::Bytes
                        ))
                    );
                    assert!(!e.release_failed);
                    assert_eq!(runtime_clock.calls(), 0);
                    let state = p.0.lock().unwrap();
                    assert_eq!(state.events, ["claim", "release"]);
                    assert_eq!(state.state, "AVAILABLE");
                    assert_eq!(state.verifier_calls, 0);
                } else {
                    assert!(result.unwrap().validation().approval_gate_passed());
                }
            }
        }
    });
}
