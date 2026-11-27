mod controlled_support;
#[path = "../../nsl-write/tests/guard_support/mod.rs"]
mod guard_support;
#[path = "../../nsl-write/tests/invocation_support/mod.rs"]
mod invocation_support;
#[path = "../../nsl-write/tests/support/mod.rs"]
mod support;
use controlled_support as h;
use nsl_core::*;
use nsl_runtime::*;
use nsl_write::*;
use support::*;

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
}
async fn run(mode: &str) -> (ControlledExecution<h::Clock>, h::Ports) {
    let mut b = h::base();
    if mode == "not-requested" {
        let ValueData::Array(contracts) = h::at(&mut b, "contracts") else {
            panic!()
        };
        let write = contracts
            .iter_mut()
            .find(|c| get(c, "capability") == "WRITE")
            .unwrap();
        h::set(
            h::at(write, "write_semantics"),
            "verification",
            ValueData::Null,
        );
    }
    let catalog = h::catalog(&b);
    let mut model = (*h::skill(&b)).clone();
    if let Some(n) = mode.strip_prefix("emit-") {
        model.limits.emitted_rows = n.parse().unwrap();
    }
    if mode == "suffix-type" {
        model.outputs[0].type_info = TypeRef::new(TypeKind::Primitive("Int".into())).unwrap();
    }
    if mode == "not-requested" {
        for r in &mut model.required_tools {
            if r.kind.capability() == ToolCapability::Write {
                r.contract_hash = catalog
                    .resolve(&r.tool_id, &r.version)
                    .unwrap()
                    .contract_hash()
                    .unwrap();
            }
        }
    }
    if mode.starts_with("emit-") || matches!(mode, "not-requested" | "suffix-type") {
        model.semantic_hash = model
            .compute_semantic_sha256_with_limits(h::rt_options().nso_limits)
            .unwrap();
        h::rebind(&mut b, &model, &catalog);
    }
    let skill = std::sync::Arc::new(model);
    let tools = h::tools(&b, &catalog, mode);
    let auth = StaticReadAuthorizer::default();
    let sink = NullToolMeasurementSink;
    let engine = RuntimeEngine::new(&catalog, &tools, &auth, &sink, h::rt_options());
    let checkpoints = h::checkpoint();
    let clock = h::Clock;
    let mut prefix = engine
        .prepare_controlled(
            skill.clone(),
            h::request(&b, mode),
            &clock,
            &checkpoints,
            snapshot(limits()),
        )
        .await
        .unwrap();
    if !mode.starts_with("emit-") && !matches!(mode, "not-requested" | "suffix-type") {
        assert_eq!(
            prefix.result().to_data(100_000).unwrap(),
            *field(&b, "prefix")
        );
    }
    assert_eq!(
        prefix.checkpoint().unwrap().metadata().checkpoint_version,
        1.into()
    );
    let intent = prefix.intent().unwrap();
    assert_eq!(
        intent.payload_hash().as_str(),
        get(field(&b, "intent"), "payload_hash")
    );
    assert_eq!(
        intent.write_intent_hash().as_str(),
        get(field(&b, "intent"), "write_intent_hash")
    );
    let mut pre = PreWriteEvidence::new(prefix.result().validation.clone().unwrap());
    pre.runtime_preflight_passed = true;
    pre.current_policy_allowed = true;
    pre.tool_preconditions_passed = true;
    pre.business_preconditions_passed = true;
    pre.intent_binding_valid = true;
    pre.idempotency_ready = true;
    pre.durability_ready = true;
    pre.trust_valid = true;
    pre.replay_live_write_allowed = true;
    let ports = h::Ports::new(b.clone(), mode);
    let suspension = prefix
        .suspend(
            &ApprovalSuspensionCoordinator::new(checkpoints.clone()),
            &h::approval(&b),
            &ports,
            &ports,
            &pre,
        )
        .await
        .unwrap();
    if !mode.starts_with("emit-") && !matches!(mode, "not-requested" | "suffix-type") {
        assert_eq!(
            suspension.result().to_data(100_000).unwrap(),
            *field(&b, "suspension")
        );
    }
    if mode == "approval-rejected" {
        h::set(h::at(&mut b, "proof"), "decision", txt("REJECTED"));
    }
    let proof = h::evidence(field(&b, "proof"));
    let token = ResumeToken::new(
        get(field(&b, "request"), "execution_id").into(),
        get(field(&b, "request"), "business_operation_id").into(),
        suspension
            .checkpoint()
            .metadata()
            .checkpoint_version
            .clone(),
        proof,
    )
    .unwrap();
    let node = get(field(&b, "intent"), "node_id");
    let approval_id = get(field(&b, "intent"), "approval_id");
    let payload: PayloadHash = get(field(&b, "intent"), "payload_hash").parse().unwrap();
    let coordinator = ApprovalResumeCoordinator::new(checkpoints);
    let resume = || ApprovalResumeRequest {
        token: &token,
        skill: skill.clone(),
        request: h::request(&b, mode),
        approval_id,
        write_node_id: node,
        current_write_intent_hash: get(field(&b, "intent"), "write_intent_hash"),
        current_payload_hash: &payload,
        claim_id: "claim-voucher-0063",
    };
    let resumed = coordinator
        .resume(
            resume(),
            &ports,
            &ports,
            &RecordedApprovalClock(guard_support::instant(&b, "now")),
            clock.clone(),
        )
        .await
        .unwrap();
    assert_eq!(
        resumed.validation().approval_gate_passed(),
        !matches!(mode, "approval-rejected" | "approval-invalid")
    );
    assert_eq!(resumed.claim_consumed(), mode != "approval-invalid");
    if mode != "approval-invalid" {
        assert!(matches!(
            coordinator
                .resume(
                    resume(),
                    &ports,
                    &ports,
                    &RecordedApprovalClock(guard_support::instant(&b, "now")),
                    clock
                )
                .await
                .unwrap_err()
                .kind,
            ApprovalResumeErrorKind::Claim(ResumeClaimError::Consumed)
        ));
    }
    let precondition = guard_support::precondition(field(&b, "precondition")).unwrap();
    let provider_evidence = h::provider_evidence(&b);
    let mut host = ControlledHostEvidence {
        trust_valid: true,
        replay_live_write_allowed: true,
        durability_ready: true,
        idempotency_ready: true,
    };
    match mode {
        "trust" => host.trust_valid = false,
        "replay" => host.replay_live_write_allowed = false,
        "durability" => host.durability_ready = false,
        "idempotency" => host.idempotency_ready = false,
        _ => {}
    }
    let mut late_base = b.clone();
    if mode == "contract-drift" {
        let ValueData::Array(c) = h::at(&mut late_base, "contracts") else {
            panic!()
        };
        let w = c
            .iter_mut()
            .find(|c| get(c, "capability") == "WRITE")
            .unwrap();
        h::set(w, "timeout_ms", ValueData::Number(1.into()));
    }
    let late_catalog = h::catalog(&late_base);
    let mut options = h::rt_options();
    if mode == "preflight-limit" {
        options.nso_limits.max_bytes = 0;
    }
    let runner = RuntimeEngine::new(&late_catalog, &tools, &auth, &sink, options);
    let output = runner
        .execute_approved(
            resumed,
            ControlledWriteServices {
                policy: &ports,
                idempotency: &ports,
                requested: &ports,
                provider: &ports,
                outcomes: &ports,
                verification: if mode == "no-verifier" {
                    None
                } else {
                    Some(&ports)
                },
            },
            ControlledWriteOptions {
                now: guard_support::instant(
                    &b,
                    if mode == "expired" {
                        "required_until"
                    } else {
                        "now"
                    },
                ),
                required_until: guard_support::instant(&b, "required_until"),
                requested_at: guard_support::instant(&b, "now"),
                recorded_at: guard_support::instant(&b, "recorded_at"),
                invocation_id: "write-invocation-0063",
                trace_refs: &["trace-voucher:0063".into()],
                business_preconditions: &precondition,
                provider_evidence: &provider_evidence,
                host,
                cancellation: WriteCancellationSignal::default(),
                limits: snapshot(limits()),
            },
        )
        .await;
    assert_eq!(tools.call_count(), 3.into());
    (output, ports)
}
#[test]
fn actual_controlled_runtime_matches_frozen_prefix_suspension_write_and_result() {
    let rt = runtime();
    let mut count = 0;
    for row in include_str!("fixtures/controlled-runtime-oracle.tsv")
        .lines()
        .skip(1)
    {
        let (mode, bytes) = row.split_once('\t').unwrap();
        let oracle = parse(&decode(bytes));
        let (observed, ports) = rt.block_on(run(mode));
        assert!(
            observed.failure().is_none(),
            "{mode}: {:?}",
            observed.failure()
        );
        assert!(!observed.automatic_retry_allowed());
        let result = observed.result().unwrap();
        let old = field(&oracle, "result");
        if matches!(mode, "verification-failed" | "verification-unknown") {
            assert_eq!(get(old, "status"), "COMPLETED");
            assert_eq!(result.status, ExecutionStatus::ReconciliationRequired);
            assert_eq!(result.completeness, Completeness::Partial);
            assert!(result.outputs.is_empty());
            assert!(result.error.is_none());
            let mut expected = old.clone();
            let ValueData::Object(d) = &mut expected else {
                panic!()
            };
            d.insert("status".into(), txt("RECONCILIATION_REQUIRED"));
            d.insert("completeness".into(), txt("PARTIAL"));
            assert_eq!(result.to_data(100_000).unwrap(), expected);
        } else {
            assert_eq!(result.to_data(100_000).unwrap(), *old);
            assert_eq!(
                result.semantic_view(100_000).unwrap(),
                *field(&oracle, "semantic")
            );
        }
        assert_eq!(observed.context().writes.entries.len(), 1);
        let s = ports.state.lock().unwrap();
        assert_eq!(s.writes, 1);
        assert_eq!(s.requested.len(), 1);
        assert_eq!(s.outcomes.len(), 1);
        assert_eq!(s.requested, arr(field(&oracle, "requested")));
        assert_eq!(s.outcomes, arr(field(&oracle, "outcomes")));
        assert_eq!(
            s.verification,
            if matches!(mode, "not-committed" | "unknown-commit") {
                0
            } else {
                1
            }
        );
        let events = &s.events;
        assert!(
            events.iter().position(|s| s == "requested").unwrap()
                < events.iter().position(|s| s == "provider").unwrap()
        );
        assert!(
            events.iter().position(|s| s == "provider").unwrap()
                < events.iter().position(|s| s == "outcome").unwrap()
        );
        count += 1;
    }
    assert_eq!(count, 5);
}
#[test]
fn guard_storage_and_dedup_boundaries_never_issue_an_unauthorized_or_second_write() {
    let rt = runtime();
    for mode in [
        "scope",
        "approval-rejected",
        "approval-invalid",
        "contract-drift",
        "preflight-limit",
        "expired",
        "trust",
        "replay",
        "durability",
        "idempotency",
        "policy-error",
        "policy-deny",
        "reused",
        "pending",
        "conflict",
        "requested-error",
        "requested-receipt",
    ] {
        let (observed, ports) = rt.block_on(run(mode));
        assert!(observed.failure().is_some(), "{mode}");
        assert!(observed.result().is_none());
        let error = observed.failure().unwrap();
        assert!(error.path().starts_with("$."));
        assert!(error.detail_code().is_none());
        assert!(!format!("{error:?} {error}").contains("EXPENSE-TRAVEL"));
        assert!(!observed.automatic_retry_allowed());
        assert!(observed.context().outputs.is_empty());
        assert_eq!(ports.state.lock().unwrap().writes, 0, "{mode}");
        assert!(observed.persisted().is_none());
        if matches!(mode, "reused" | "pending") {
            let ControlledExecutionError::ExistingReservation(r) = observed.failure().unwrap()
            else {
                panic!()
            };
            assert_eq!(
                r.business_result_ref(),
                if mode == "reused" {
                    Some("result:original")
                } else {
                    None
                }
            );
        }
    }
}
#[test]
fn post_provider_failures_preserve_commit_evidence_and_never_emit_or_retry() {
    let rt = runtime();
    for mode in [
        "outcome-error",
        "outcome-receipt",
        "verification-cancelled",
        "stale",
        "no-verifier",
        "result-type",
        "result-value",
        "suffix-type",
    ] {
        let (observed, ports) = rt.block_on(run(mode));
        assert_eq!(ports.state.lock().unwrap().writes, 1);
        assert!(observed.context().outputs.is_empty());
        assert!(!observed.automatic_retry_allowed());
        let error = observed.failure();
        if let Some(e) = error {
            assert!(e.path().starts_with("$."));
            assert!(e.detail_code().is_none());
            assert!(!format!("{e:?} {e}").contains("V-20261021-0063"));
        }
        if mode.starts_with("outcome-") {
            let ControlledExecutionError::Outcome(e) = observed.failure().unwrap() else {
                panic!()
            };
            assert_eq!(
                e.invocation().outcome().commit_state(),
                CommitState::Committed
            );
            assert!(observed.context().writes.entries.is_empty());
        } else {
            assert_eq!(
                observed
                    .persisted()
                    .unwrap()
                    .invocation()
                    .outcome()
                    .commit_state(),
                CommitState::Committed
            );
            if matches!(
                mode,
                "verification-cancelled"
                    | "no-verifier"
                    | "result-type"
                    | "result-value"
                    | "suffix-type"
            ) {
                assert!(observed.failure().is_some());
            } else {
                assert_eq!(
                    observed.result().unwrap().status,
                    ExecutionStatus::ReconciliationRequired
                );
            }
        }
    }
}
#[test]
fn prefix_admission_validation_limits_and_suspension_bindings_fail_before_publication() {
    let rt = runtime();
    rt.block_on(async{
  let base=h::base();let catalog=h::catalog(&base);let auth=StaticReadAuthorizer::default();let sink=NullToolMeasurementSink;let clock=h::Clock;
  for mode in ["no-business","no-approval","duplicate-write","wrong-approval","old-ir","no-check","check-fail","unauthorized","canonical-limit","checkpoint-limit","metadata-limit"] {
   let mut s=(*h::skill(&base)).clone();
   match mode {
    "no-approval"=>s.body.retain(|n|!matches!(n.kind,nsl_ir::StatementKind::Approval(_))),
    "duplicate-write"=>{let write=s.body.iter().find(|n|matches!(&n.kind,nsl_ir::StatementKind::Let{value,..} if matches!(value.kind,nsl_ir::ExpressionKind::Write(_)))).unwrap().clone();s.body.push(write);},
    "wrong-approval"=>{for n in &mut s.body {if let nsl_ir::StatementKind::Approval(a)=&mut n.kind{a.approval_id="wrong".into()}}},
    "old-ir"=>s.ir_version=nsl_ir::IrVersion::V1_0,
    "no-check"=>s.body.retain(|n|!matches!(n.kind,nsl_ir::StatementKind::Check{..})),_=>{}
   }
   if mode=="no-check" {s.semantic_hash=s.compute_semantic_sha256_with_limits(h::rt_options().nso_limits).unwrap();}
   let tools=h::tools(&base,&catalog,mode);let engine=RuntimeEngine::new(&catalog,&tools,&auth,&sink,h::rt_options());let mut limits=snapshot(limits());
   if mode=="canonical-limit" {limits.canonical.canonical.max_bytes=0;}
   if mode=="metadata-limit" {limits.metadata.max_nodes=0;}
   let checkpoints=if mode=="checkpoint-limit" {CheckpointCoordinator::for_oracle(CheckpointLimits{outer:JsonLimits{max_nodes:0,..limits.canonical.canonical},state:limits.canonical.canonical,max_decimal_bytes:100_000})}else{h::checkpoint()};
   match engine.prepare_controlled(std::sync::Arc::new(s),h::request(&base,mode),&clock,&checkpoints,limits).await {
    Ok(mut p)=>{assert!(matches!(mode,"no-check"|"check-fail"|"unauthorized"),"{mode}");if mode=="unauthorized"{assert!(p.intent().is_none());assert!(p.checkpoint().is_none());}else{assert!(p.intent().is_some());assert_eq!(p.checkpoint().unwrap().metadata().checkpoint_version,1.into());}
    if mode=="unauthorized"{assert!(p.context().is_none());}else{assert!(p.context().is_some());}
     let ports=h::Ports::new(base.clone(),mode);let pre=PreWriteEvidence::new(ValidationSummary::from_checks(&[]).unwrap());let e=p.suspend(&ApprovalSuspensionCoordinator::new(h::checkpoint()),&h::approval(&base),&ports,&ports,&pre).await.unwrap_err();assert!(e.path().starts_with("$."));assert!(ports.state.lock().unwrap().events.is_empty());},
    Err(e)=>{assert!(!matches!(mode,"no-check"|"check-fail"|"unauthorized"),"{mode}: {e}");assert!(e.detail_code().is_none());assert!(std::error::Error::source(&e).is_none());assert!(!format!("{e:?} {e}").contains("EXPENSE-TRAVEL"));}
   }
  }
  let tools=h::tools(&base,&catalog,"normal");let engine=RuntimeEngine::new(&catalog,&tools,&auth,&sink,h::rt_options());
  let mut p=engine.prepare_controlled(h::skill(&base),h::request(&base,"normal"),&clock,&h::checkpoint(),snapshot(limits())).await.unwrap();
  let pre=PreWriteEvidence::new(p.result().validation.clone().unwrap());let ports=h::Ports::new(base.clone(),"normal");
  for field_name in ["write_node_id","tool_id","approval_id","payload_hash","write_intent_hash"] {
   let mut spec=h::approval(&base).metadata().clone();match field_name {"write_node_id"=>spec.write_node_id="other".into(),"tool_id"=>spec.tool_id="other".into(),"approval_id"=>spec.bindings.approval_id="other".into(),"payload_hash"=>spec.bindings.payload_hash=format!("sha256:{}","f".repeat(64)),"write_intent_hash"=>spec.bindings.write_intent_hash=format!("sha256:{}","f".repeat(64)),_=>unreachable!()}
   let request=ApprovalRequest::new(spec).unwrap();let e=p.suspend(&ApprovalSuspensionCoordinator::new(h::checkpoint()),&request,&ports,&ports,&pre).await.unwrap_err();assert!(e.path().starts_with("$.approval_request"));assert_eq!(p.context().unwrap().checkpoint.checkpoint_version,1.into());assert!(ports.state.lock().unwrap().events.is_empty());
  }
  // A correct request still cannot bypass R7 evidence admission.
  assert!(matches!(p.suspend(&ApprovalSuspensionCoordinator::new(h::checkpoint()),&h::approval(&base),&ports,&ports,&pre).await.unwrap_err(),ControlledExecutionError::Suspension(_)));
 });
}
#[test]
fn timeout_waits_for_cleanup_and_preserves_explicit_commit_or_uncertainty() {
    let rt = runtime();
    rt.block_on(async {
        tokio::time::pause();
        for mode in ["cleanup-committed", "cleanup-error"] {
            let (e, ports) = run(mode).await;
            assert!(e.failure().is_none());
            assert_eq!(ports.state.lock().unwrap().writes, 1);
            let p = e.persisted().unwrap();
            assert!(p.invocation().deadline_elapsed());
            assert!(!e.automatic_retry_allowed());
            assert_eq!(
                e.result().unwrap().status,
                if mode == "cleanup-committed" {
                    ExecutionStatus::Completed
                } else {
                    ExecutionStatus::ReconciliationRequired
                }
            );
            assert_eq!(
                e.context().outputs.len(),
                usize::from(mode == "cleanup-committed")
            );
        }
    });
}
#[test]
fn corpus_manifest_pins_both_deterministic_files_and_original_oracle() {
    use sha2::{Digest, Sha256};
    let m = parse(include_bytes!("fixtures/controlled-runtime-manifest.json"));
    assert_eq!(
        get(&m, "oracle_commit"),
        "28a949ec96581cbdc52c8133527b23bb5b72ee0a"
    );
    assert_eq!(
        get(&m, "approved_difference"),
        "R9-S05-VERIFICATION-TERMINAL-STATUS"
    );
    for (name, bytes) in [
        (
            "controlled-runtime-base.json",
            include_bytes!("fixtures/controlled-runtime-base.json").as_slice(),
        ),
        (
            "controlled-runtime-oracle.tsv",
            include_bytes!("fixtures/controlled-runtime-oracle.tsv").as_slice(),
        ),
    ] {
        assert_eq!(get(field(&m, "files"), name), hex(&Sha256::digest(bytes)));
    }
}
#[test]
fn suffix_emit_budget_zero_one_two_and_original_not_requested_keep_real_commit() {
    let rt = runtime();
    for mode in ["emit-0", "emit-1", "emit-2", "not-requested"] {
        let (e, ports) = rt.block_on(run(mode));
        assert!(e.failure().is_none(), "{mode}: {:?}", e.failure());
        let result = e.result().unwrap();
        assert_eq!(
            e.persisted().unwrap().invocation().outcome().commit_state(),
            CommitState::Committed
        );
        assert_eq!(ports.state.lock().unwrap().writes, 1);
        assert!(!e.automatic_retry_allowed());
        assert_eq!(result.outputs.len(), usize::from(mode != "emit-0"));
        assert_eq!(
            result.status,
            if mode == "emit-0" {
                ExecutionStatus::LimitExceeded
            } else {
                ExecutionStatus::Completed
            }
        );
        if mode == "not-requested" {
            assert_eq!(
                e.verification().unwrap().metadata().status,
                VerificationStatus::NotRequested
            );
            assert_eq!(ports.state.lock().unwrap().verification, 0);
        }
    }
}
