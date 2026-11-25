mod approval_gate_support;
mod checkpoint_support;
mod resume_support;
use approval_gate_support as g;
use checkpoint_support as h;
use nsl_core::*;
use nsl_runtime::*;
use resume_support as r;
fn fixtures(text: &str) -> impl Iterator<Item = (&str, &str, ValueData)> {
    text.lines().skip(1).map(|line| {
        let f = line.split('\t').collect::<Vec<_>>();
        (f[0], f[1], r::parse(&h::decode(f[2])))
    })
}
async fn run(ver: &str, case: &str, d: &ValueData, exception: bool) -> usize {
    let mut ctx = g::context(ver, case);
    let p = g::Ports::new(case);
    let clock = h::Clock::default();
    let initial = g::request(r::field(d, "request"));
    let e = g::evidence(&ctx);
    let mut count = 0;
    assert_eq!(
        r::items(r::field(d, "actions")).len(),
        r::items(r::field(d, "observations")).len()
    );
    for (step, (a, want)) in r::items(r::field(d, "actions"))
        .iter()
        .zip(r::items(r::field(d, "observations")))
        .enumerate()
    {
        let op = r::str_field(a, "op");
        if op == "suspend" {
            if let ValueData::String(field) = r::field(a, "remove") {
                match field.as_str() {
                    "payload_hashes_by_write_node" => {
                        ctx.checkpoint
                            .payload_hashes_by_write_node
                            .remove(&initial.metadata().write_node_id);
                    }
                    "write_intent_hashes_by_write_node" => {
                        ctx.checkpoint
                            .write_intent_hashes_by_write_node
                            .remove(&initial.metadata().write_node_id);
                    }
                    _ => panic!(),
                }
            }
        } else {
            p.apply(a);
        }
        let before_ctx = r::state(&mut ctx);
        let before_store = p.view();
        let expected = r::field(want, "outcome");
        let outcome = if op == "suspend" {
            let req = g::request(r::field(a, "request"));
            match ApprovalSuspensionCoordinator::new(CheckpointCoordinator::for_oracle(h::limits()))
                .suspend(&mut ctx, &req, &p, &p, Some(&e))
                .await
            {
                Ok(s) => r::arr([
                    r::text("ok"),
                    s.result().to_data(100_000).unwrap(),
                    s.result().semantic_view(100_000).unwrap(),
                    r::text(r::hex(&s.checkpoint().to_bytes(h::limits()).unwrap())),
                    r::text(s.checkpoint().integrity_hash()),
                ]),
                Err(err) => {
                    assert!(std::error::Error::source(&err).is_none());
                    if exception && step > 0 {
                        let map = if case.ends_with("payload") {
                            "payload_hashes_by_write_node"
                        } else {
                            "write_intent_hashes_by_write_node"
                        };
                        assert_eq!(err.kind, ApprovalSuspensionErrorKind::Type);
                        assert_eq!(err.path, format!("$.checkpoint.{map}[\"write0001\"]"));
                        assert_eq!(r::state(&mut ctx), before_ctx);
                        assert_eq!(p.view(), before_store);
                        // Original Oracle either overwrites and publishes, or saves a changed
                        // version before the idempotent publisher rejects. Keep those observations.
                        assert_ne!(r::field(want, "store"), &before_store);
                        assert_eq!(
                            r::items(r::field(r::field(want, "store"), "slots")).len(),
                            2
                        );
                        return count + 1;
                    }
                    assert_eq!(r::items(expected)[0], r::text("error"));
                    assert_eq!(
                        (err.kind, err.path.as_str()),
                        if case == "publish-retry" {
                            (ApprovalSuspensionErrorKind::Publish, "$.approval_request")
                        } else {
                            (
                                ApprovalSuspensionErrorKind::Type,
                                "$.pre_write_evidence.validation",
                            )
                        }
                    );
                    expected.clone()
                }
            }
        } else {
            let evidence = r::evidence(r::field(a, "evidence"));
            let version = r::integer(r::field(a, "version"));
            let token = ResumeToken::new(
                initial.bindings().execution_id().into(),
                initial.bindings().business_operation_id().to_string(),
                version,
                evidence,
            )
            .unwrap();
            let payload = r::str_field(a, "current_payload").parse().unwrap();
            let request = match case {
                "principal-subject" => r::request("principal-change-subject_id"),
                "principal-scopes" => r::request("principal-change-scopes"),
                _ => r::request(case),
            };
            let input = ApprovalResumeRequest {
                token: &token,
                skill: h::skill(ver),
                request,
                approval_id: initial.bindings().approval_id(),
                write_node_id: &initial.metadata().write_node_id,
                current_write_intent_hash: r::str_field(a, "current_intent"),
                current_payload_hash: &payload,
                claim_id: "claim-a",
            };
            match r::coordinator()
                .resume(input, &p, &p, &p, clock.clone())
                .await
            {
                Ok(o) => {
                    let val = o.validation();
                    let consumed = o.claim_consumed();
                    let cp = o.checkpoint().to_bytes(h::limits()).unwrap();
                    let mut restored = o.into_context();
                    r::arr([
                        r::text("ok"),
                        val.to_data(),
                        ValueData::Bool(val.approval_gate_passed()),
                        ValueData::Bool(val.requires_reapproval()),
                        ValueData::Bool(consumed),
                        r::text(r::hex(&canonical_json(&r::state(&mut restored)).unwrap())),
                        r::text(r::hex(&cp)),
                    ])
                }
                Err(err) => {
                    assert!(std::error::Error::source(&err).is_none());
                    assert!(!err.release_failed);
                    if exception {
                        assert_eq!(err.kind, ApprovalResumeErrorKind::Type);
                        assert_eq!(
                            err.path,
                            if case == "returned-version" {
                                "$.checkpoint.checkpoint_version"
                            } else {
                                "$.request.security.principal"
                            }
                        );
                        assert_eq!(clock.calls(), 0);
                        assert_eq!(r::state(&mut ctx), before_ctx);
                        let store = p.0.lock().unwrap();
                        assert_eq!(store.slots[0].state, "AVAILABLE");
                        assert_eq!(store.slots[0].owner, None);
                        assert_eq!(store.verifier_calls, 0);
                        assert_eq!(store.clock_calls, 0);
                        if case == "returned-version" {
                            assert_eq!(store.events, ["save", "publish", "claim", "release"]);
                        } else {
                            assert_eq!(store.events, ["save", "publish"]);
                        }
                        assert_eq!(r::items(expected)[0], r::text("ok"));
                        assert_eq!(r::items(expected)[2], ValueData::Bool(true));
                        return count + 1;
                    }
                    assert_eq!(
                        r::items(expected)[0],
                        r::text("error"),
                        "{ver}/{case}/{step}: {err}"
                    );
                    let kind = if case.starts_with("principal-") {
                        ApprovalResumeErrorKind::Checkpoint(CheckpointErrorKind::Binding)
                    } else if case == "tamper-wire" {
                        ApprovalResumeErrorKind::Claim(ResumeClaimError::Unavailable)
                    } else if matches!(case, "consume-before" | "consume-after") && step == 1 {
                        ApprovalResumeErrorKind::Consume(ResumeClaimError::Unavailable)
                    } else {
                        let claim =
                            if matches!(case, "suspend-retry" | "publish-retry") && step == 2 {
                                ResumeClaimError::Stale
                            } else if case == "consume-before" {
                                ResumeClaimError::Live
                            } else {
                                ResumeClaimError::Consumed
                            };
                        ApprovalResumeErrorKind::Claim(claim)
                    };
                    assert_eq!(err.kind, kind);
                    assert_eq!(
                        err.path,
                        if case.starts_with("principal-") {
                            "$.security"
                        } else {
                            "$.claim"
                        }
                    );
                    expected.clone()
                }
            }
        };
        assert_eq!(
            g::observe(&mut ctx, &p, outcome, &clock),
            *want,
            "{ver}/{case}/{step}"
        );
        count += 1;
    }
    count
}
#[test]
fn r7_exit_gate_matches_oracle_across_native_suspend_wire_store_and_resume() {
    r::runtime().block_on(async {
  let mut cases=0;let mut observations=0;
  for (ver,case,data) in fixtures(include_str!("fixtures/approval-gate-oracle.tsv")) {observations+=run(ver,case,&data,false).await;cases+=1;}
  assert_eq!(cases,54);assert!(observations>120);
  println!("R7 lifecycle differential PASS: {cases} scenarios / {observations} observations, Oracle 28a949e; no business provider invocation");
 });
}
#[test]
fn r7_exit_gate_keeps_approved_retry_principal_and_claim_identity_exceptions_explicit() {
    r::runtime().block_on(async {
        let mut cases = 0;
        for (ver, case, data) in fixtures(include_str!("fixtures/approval-gate-exceptions.tsv")) {
            assert_eq!(run(ver, case, &data, true).await, 2);
            cases += 1;
        }
        assert_eq!(cases, 14);
        println!("R7 approved-exception gate PASS: {cases} original Oracle scenarios retained");
    });
}

#[test]
fn r7_corpus_inventory_pins_all_oracle_fixtures_and_approved_difference_groups() {
    use sha2::{Digest, Sha256};
    let manifest = r::parse(include_bytes!("fixtures/approval-gate-manifest.json"));
    let pin = "28a949ec96581cbdc52c8133527b23bb5b72ee0a";
    // The baseline evidence document contains fractional coverage metadata;
    // it is not an integer-only NSL value. Bind its complete immutable bytes.
    let lock = include_bytes!("../../../../rust-transition/baseline/python-oracle-lock.json");
    assert_eq!(r::str_field(&manifest, "oracle_commit"), pin);
    assert_eq!(
        r::str_field(&manifest, "oracle_lock_sha256"),
        r::hex(&Sha256::digest(lock))
    );
    assert!(
        std::str::from_utf8(lock)
            .unwrap()
            .lines()
            .any(|line| line.trim() == format!("\"commit\": \"{pin}\","))
    );
    assert_eq!(r::str_field(&manifest, "gate_id"), "R7-EXIT");
    assert_eq!(
        r::field(&manifest, "comparisons"),
        &r::arr(
            [
                "suspend_result",
                "checkpoint_state",
                "resume_success",
                "resume_rejection_code",
                "approval_validation_outcome"
            ]
            .into_iter()
            .map(r::text)
        )
    );
    assert_eq!(
        r::field(&manifest, "approved_difference_ids"),
        &r::arr(
            [
                "R7-S01-INTEGER-SUMMARY",
                "R7-S02-STRICT-STATE-ADMISSION",
                "R7-S03-RETRY-BINDING",
                "R7-S04-VERIFIED-PRINCIPAL",
                "R7-S04-CLAIM-IDENTITY"
            ]
            .into_iter()
            .map(r::text)
        )
    );
    let fixtures: [(&str, &[u8]); 15] = [
        (
            "approval-models-oracle.tsv",
            include_bytes!("fixtures/approval-models-oracle.tsv"),
        ),
        (
            "approval-bindings-oracle.tsv",
            include_bytes!("fixtures/approval-bindings-oracle.tsv"),
        ),
        (
            "approval-time-oracle.tsv",
            include_bytes!("fixtures/approval-time-oracle.tsv"),
        ),
        (
            "approval-summary-exceptions.tsv",
            include_bytes!("fixtures/approval-summary-exceptions.tsv"),
        ),
        (
            "checkpoints-oracle.tsv",
            include_bytes!("fixtures/checkpoints-oracle.tsv"),
        ),
        (
            "checkpoints-negative-oracle.tsv",
            include_bytes!("fixtures/checkpoints-negative-oracle.tsv"),
        ),
        (
            "checkpoints-exceptions.tsv",
            include_bytes!("fixtures/checkpoints-exceptions.tsv"),
        ),
        (
            "checkpoints-wire-oracle.tsv",
            include_bytes!("fixtures/checkpoints-wire-oracle.tsv"),
        ),
        (
            "checkpoints-values-oracle.tsv",
            include_bytes!("fixtures/checkpoints-values-oracle.tsv"),
        ),
        (
            "suspensions-oracle.tsv",
            include_bytes!("fixtures/suspensions-oracle.tsv"),
        ),
        (
            "suspensions-exceptions.tsv",
            include_bytes!("fixtures/suspensions-exceptions.tsv"),
        ),
        (
            "resumes-oracle.tsv",
            include_bytes!("fixtures/resumes-oracle.tsv"),
        ),
        (
            "resumes-exceptions.tsv",
            include_bytes!("fixtures/resumes-exceptions.tsv"),
        ),
        (
            "approval-gate-oracle.tsv",
            include_bytes!("fixtures/approval-gate-oracle.tsv"),
        ),
        (
            "approval-gate-exceptions.tsv",
            include_bytes!("fixtures/approval-gate-exceptions.tsv"),
        ),
    ];
    assert_eq!(
        r::items(r::field(&manifest, "fixtures")).len(),
        fixtures.len()
    );
    for (record, (name, bytes)) in r::items(r::field(&manifest, "fixtures"))
        .iter()
        .zip(fixtures)
    {
        assert_eq!(r::str_field(record, "name"), name);
        assert_eq!(
            r::str_field(record, "sha256"),
            r::hex(&Sha256::digest(bytes))
        );
        let text = std::str::from_utf8(bytes).unwrap();
        assert!(text.lines().next().unwrap().contains(pin));
        assert_eq!(
            r::field(record, "rows"),
            &r::number(text.lines().count() - 1)
        );
    }
    println!("R7 corpus inventory PASS: 15 pinned fixtures, 5 explicit approved-difference groups");
}
