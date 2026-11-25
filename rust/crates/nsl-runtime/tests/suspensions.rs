mod checkpoint_support;
use checkpoint_support as h;
use nsl_core::*;
use nsl_runtime::*;
use std::{collections::BTreeMap, sync::Mutex};

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()
        .unwrap()
}
fn object(pairs: impl IntoIterator<Item = (&'static str, ValueData)>) -> ValueData {
    ValueData::Object(pairs.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
fn text(v: impl Into<String>) -> ValueData {
    ValueData::String(v.into())
}
fn number(v: impl Into<Integer>) -> ValueData {
    ValueData::Number(v.into())
}
fn array(v: impl IntoIterator<Item = ValueData>) -> ValueData {
    ValueData::Array(v.into_iter().collect())
}
fn field<'a>(v: &'a ValueData, key: &str) -> &'a ValueData {
    let ValueData::Object(m) = v else {
        panic!("object")
    };
    &m[key]
}
fn items(v: &ValueData) -> &[ValueData] {
    let ValueData::Array(a) = v else {
        panic!("array")
    };
    a
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn parse(bytes: &[u8]) -> ValueData {
    parse_json_with_limits(bytes, h::limits().outer).unwrap()
}
fn state(ctx: &mut ExecutionContext<h::Clock>) -> ValueData {
    let prior = ctx.checkpoint.checkpoint_version.clone();
    let cp = CheckpointCoordinator::for_oracle(h::limits())
        .capture(ctx)
        .unwrap();
    ctx.checkpoint.checkpoint_version = prior.clone();
    let mut d = parse(cp.state_payload());
    let ValueData::Object(m) = &mut d else {
        unreachable!()
    };
    m.insert("checkpoint_version".into(), number(prior.clone()));
    let ValueData::Object(c) = m.get_mut("checkpoint").unwrap() else {
        unreachable!()
    };
    c.insert("checkpoint_version".into(), number(prior));
    d
}
fn setup(
    ver: &str,
    case: &str,
) -> (
    ExecutionContext<h::Clock>,
    ApprovalRequest,
    Option<PreWriteEvidence>,
) {
    let mut ctx = h::context(
        ver,
        if case == "populated" {
            "safe-values"
        } else {
            "empty"
        },
        if case == "wide" {
            "10000000000000000000000000000000000000000"
        } else {
            "0"
        },
    );
    if case != "populated" {
        ctx.checks.push(CheckResult {
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
    // Empty contexts start complete. Populated helper deliberately records partial.
    if case == "populated" {
        // Rebuild via a signed state to preserve populated values while setting COMPLETE.
        let cp = CheckpointCoordinator::for_oracle(h::limits())
            .capture(&mut ctx)
            .unwrap();
        let mut d = parse(cp.state_payload());
        h::obj(h::at(&mut d, &["execution"])).insert("data_completeness".into(), text("COMPLETE"));
        let cp = h::resign(&cp, d);
        ctx = CheckpointCoordinator::for_oracle(h::limits())
            .restore(&cp, h::skill(ver), h::request(), h::Clock::default())
            .unwrap();
        ctx.checkpoint.checkpoint_version = 0.into();
    }
    if case == "partial" {
        ctx.observe_completeness(Completeness::Partial);
    }
    if case == "unknown" {
        ctx.observe_completeness(Completeness::Unknown);
    }
    if case == "no-check" {
        ctx.checks.clear();
    }
    if case == "fail" {
        ctx.checks[0].status = CheckStatus::Fail;
    }
    if case == "check-unknown" {
        ctx.checks[0].status = CheckStatus::Unknown;
    }
    let mut ev = PreWriteEvidence::new(ValidationSummary::from_checks(&ctx.checks).unwrap());
    ev.runtime_preflight_passed = true;
    ev.trust_valid = true;
    ev.current_policy_allowed = true;
    ev.tool_preconditions_passed = true;
    ev.business_preconditions_passed = true;
    ev.intent_binding_valid = true;
    ev.idempotency_ready = true;
    ev.durability_ready = true;
    ev.replay_live_write_allowed = true;
    if case == "duplicate-check" {
        ctx.checks.push(ctx.checks[0].clone());
    }
    if case == "validation-mismatch" {
        ev.validation = ValidationSummary::from_checks(&[]).unwrap();
    }
    match case.strip_prefix("control-").unwrap_or("") {
        "runtime_preflight_passed" => ev.runtime_preflight_passed = false,
        "trust_valid" => ev.trust_valid = false,
        "current_policy_allowed" => ev.current_policy_allowed = false,
        "tool_preconditions_passed" => ev.tool_preconditions_passed = false,
        "business_preconditions_passed" => ev.business_preconditions_passed = false,
        "intent_binding_valid" => ev.intent_binding_valid = false,
        "idempotency_ready" => ev.idempotency_ready = false,
        "durability_ready" => ev.durability_ready = false,
        "replay_live_write_allowed" => ev.replay_live_write_allowed = false,
        _ => {}
    }
    if case == "defaults" {
        ev = PreWriteEvidence::new(ev.validation);
    }
    if let Some(s) = case.strip_prefix("approval-") {
        ev.approval_status = s.parse().unwrap();
    }
    if case == "approved-valid" {
        ev.approval_status = ApprovalStatus::Approved;
        ev.approval_evidence_valid = true;
    }
    if case == "evidence-valid" {
        ev.approval_evidence_valid = true;
    }
    if case == "legacy-empty" {
        ctx.checks.clear();
    }
    let spec = ApprovalRequestSpec {
        bindings: ApprovalBindingsSpec {
            tenant_id: ctx.tenant_id().into(),
            execution_id: ctx.identity().execution_id().into(),
            business_operation_id: "business-a".into(),
            approval_id: "PROJECT_DEACTIVATE".into(),
            approval_request_id: "request-a".into(),
            skill_semantic_hash: ctx.skill().semantic_hash.to_string(),
            payload_hash: format!("sha256:{}", "b".repeat(64)),
            write_intent_hash: "intent-a".into(),
        },
        skill_id: ctx.skill().skill_id.clone(),
        skill_version: ctx.skill().skill_version.clone(),
        write_node_id: "write0001".into(),
        tool_id: "project.deactivate".into(),
        risk_level: RiskLevel::R2,
        message: "Review draft".into(),
        expires_at: None,
        display_safe_summary: SafeApprovalSummary::new(
            &ApprovalSummaryInput::Object(BTreeMap::from([(
                "line_count".into(),
                ApprovalSummaryInput::Number("1".into()),
            )])),
            ApprovalSummaryLimits {
                max_depth: 10,
                max_nodes: 10,
                max_text_bytes: 100,
            },
        )
        .unwrap(),
        protected_payload_ref: None,
        data_classification: DataClassification::Internal,
    };
    let mut req = spec;
    if let Some(name) = case.strip_prefix("binding-") {
        match name {
            "tenant_id" => req.bindings.tenant_id = "other".into(),
            "execution_id" => req.bindings.execution_id = "other".into(),
            "business_operation_id" => req.bindings.business_operation_id = "other".into(),
            "skill_id" => req.skill_id = "other".into(),
            "skill_version" => req.skill_version = "other".into(),
            "skill_semantic_hash" => {
                req.bindings.skill_semantic_hash = format!("sha256:{}", "d".repeat(64))
            }
            _ => panic!("binding"),
        }
    }
    if case == "prior-id" {
        ctx.checkpoint
            .approval_request_ids_by_write_node
            .insert(req.write_node_id.clone(), "other".into());
    }
    (
        ctx,
        ApprovalRequest::new(req).unwrap(),
        if matches!(case, "missing-evidence" | "legacy-empty") {
            None
        } else {
            Some(ev)
        },
    )
}
#[derive(Default)]
struct PortState {
    mode: String,
    events: Vec<String>,
    saved: Vec<ExecutionCheckpoint>,
    published: BTreeMap<String, ApprovalRequest>,
    calls: usize,
}
struct Ports(Mutex<PortState>);
impl Ports {
    fn new(mode: &str) -> Self {
        Self(Mutex::new(PortState {
            mode: mode.into(),
            ..Default::default()
        }))
    }
}
impl CheckpointPort for Ports {
    fn save<'a>(&'a self, cp: &'a ExecutionCheckpoint) -> SuspensionPortFuture<'a> {
        Box::pin(async move {
            let pending = {
                let mut p = self.0.lock().unwrap();
                p.events.push("save".into());
                if p.mode == "save-before" {
                    p.mode = "ok".into();
                    return Err(SuspensionPortError);
                }
                if p.mode != "save-pending" {
                    cp.verify(h::limits()).unwrap();
                    p.saved.push(cp.clone());
                }
                if p.mode == "save-after" {
                    p.mode = "ok".into();
                    return Err(SuspensionPortError);
                }
                p.mode == "save-pending"
            };
            if pending {
                std::future::pending::<()>().await;
            }
            Ok(())
        })
    }
}
impl ApprovalPort for Ports {
    fn publish<'a>(&'a self, req: &'a ApprovalRequest) -> SuspensionPortFuture<'a> {
        Box::pin(async move {
            let pending = {
                let mut p = self.0.lock().unwrap();
                p.events.push("publish".into());
                p.calls += 1;
                if p.mode == "publish-before" {
                    p.mode = "ok".into();
                    return Err(SuspensionPortError);
                }
                if p.mode != "publish-pending" {
                    let prior = p
                        .published
                        .entry(req.bindings().approval_request_id().to_string())
                        .or_insert_with(|| req.clone());
                    if prior != req {
                        return Err(SuspensionPortError);
                    }
                }
                if p.mode == "publish-after" {
                    p.mode = "ok".into();
                    return Err(SuspensionPortError);
                }
                p.mode == "publish-pending"
            };
            if pending {
                std::future::pending::<()>().await;
            }
            Ok(())
        })
    }
}
fn observation(ctx: &mut ExecutionContext<h::Clock>, p: &Ports, outcome: ValueData) -> ValueData {
    let p = p.0.lock().unwrap();
    object([
        ("outcome", outcome),
        ("events", array(p.events.iter().map(text))),
        (
            "saved",
            array(
                p.saved
                    .iter()
                    .map(|c| text(hex(&c.to_bytes(h::limits()).unwrap()))),
            ),
        ),
        (
            "published",
            array(p.published.values().map(ApprovalRequest::to_data)),
        ),
        ("publish_calls", number(i64::try_from(p.calls).unwrap())),
        ("version", number(ctx.checkpoint.checkpoint_version.clone())),
        (
            "published_flag",
            ValueData::Bool(ctx.checkpoint.approval_request_published),
        ),
        ("state", text(hex(&canonical_json(&state(ctx)).unwrap()))),
    ])
}
fn coordinator() -> ApprovalSuspensionCoordinator {
    ApprovalSuspensionCoordinator::new(CheckpointCoordinator::for_oracle(h::limits()))
}
fn expected_error(case: &str) -> (ApprovalSuspensionErrorKind, String) {
    use ApprovalSuspensionErrorKind::*;
    if let Some(f) = case.strip_prefix("binding-") {
        return (Binding, format!("$.request.{f}"));
    }
    (
        match case {
            "save-before" | "save-after" => Save,
            "publish-before" | "publish-after" => Publish,
            "prior-id" => Binding,
            _ => Type,
        },
        match case {
            "save-before" | "save-after" => "$.checkpoint",
            "publish-before" | "publish-after" => "$.approval_request",
            "duplicate-check" => "$.checks",
            "validation-mismatch"
            | "no-check"
            | "fail"
            | "check-unknown"
            | "partial"
            | "unknown" => "$.pre_write_evidence.validation",
            "prior-id" => "$.checkpoint.approval_request_ids_by_write_node[\"write0001\"]",
            _ => "$.pre_write_evidence",
        }
        .into(),
    )
}
#[test]
fn full_suspension_results_snapshots_retries_and_failures_match_fixed_oracle() {
    runtime().block_on(async {
        let mut count = 0;
        for line in include_str!("fixtures/suspensions-oracle.tsv")
            .lines()
            .skip(1)
        {
            let f = line.split('\t').collect::<Vec<_>>();
            let (ver, case) = (f[0], f[1]);
            let expected = parse(&h::decode(f[2]));
            let (mut ctx, req, ev) = setup(ver, case);
            let p = Ports::new(case);
            let co = coordinator();
            for row in items(&expected) {
                let before = state(&mut ctx);
                let outcome = match co.suspend(&mut ctx, &req, &p, &p, ev.as_ref()).await {
                    Ok(s) => {
                        assert_eq!(
                            field(row, "outcome").clone(),
                            array([
                                text("ok"),
                                s.result().to_data(100_000).unwrap(),
                                s.result().semantic_view(100_000).unwrap(),
                                text(hex(&s.checkpoint().to_bytes(h::limits()).unwrap()))
                            ]),
                            "{ver}/{case}"
                        );
                        field(row, "outcome").clone()
                    }
                    Err(e) => {
                        assert_eq!(
                            items(field(row, "outcome"))[0],
                            text("error"),
                            "{ver}/{case}: {e}"
                        );
                        let (kind, path) = expected_error(case);
                        assert_eq!((e.kind, e.path.clone()), (kind, path), "{ver}/{case}: {e}");
                        assert!(std::error::Error::source(&e).is_none());
                        assert!(!format!("{e:?} {e}").contains("synthetic"));
                        if !matches!(
                            e.kind,
                            ApprovalSuspensionErrorKind::Save
                                | ApprovalSuspensionErrorKind::Publish
                        ) {
                            assert_eq!(state(&mut ctx), before);
                        }
                        field(row, "outcome").clone()
                    }
                };
                assert_eq!(observation(&mut ctx, &p, outcome), *row, "{ver}/{case}");
                count += 1;
            }
        }
        assert!(count > 80);
    });
}

#[test]
fn approved_retry_binding_difference_rejects_before_all_mutation_and_io() {
    runtime().block_on(async {
        let mut count = 0;
        for line in include_str!("fixtures/suspensions-exceptions.tsv")
            .lines()
            .skip(1)
        {
            let f = line.split('\t').collect::<Vec<_>>();
            let (ver, case) = (f[0], f[1]);
            let original = parse(&h::decode(f[2]));
            let (mut ctx, req, ev) = setup(ver, "pass");
            let p = Ports::new("ok");
            let co = coordinator();
            let first = co
                .suspend(&mut ctx, &req, &p, &p, ev.as_ref())
                .await
                .unwrap();
            let row = &items(&original)[0];
            assert_eq!(
                observation(&mut ctx, &p, field(row, "outcome").clone()),
                *row
            );
            let mut spec = req.metadata().clone();
            let map = match case {
                "changed-payload" => {
                    spec.bindings.payload_hash = format!("sha256:{}", "c".repeat(64));
                    "payload_hashes_by_write_node"
                }
                "changed-intent" => {
                    spec.bindings.write_intent_hash = "intent-b".into();
                    "write_intent_hashes_by_write_node"
                }
                "missing-payload" => {
                    ctx.checkpoint
                        .payload_hashes_by_write_node
                        .remove(&spec.write_node_id);
                    "payload_hashes_by_write_node"
                }
                "missing-intent" => {
                    ctx.checkpoint
                        .write_intent_hashes_by_write_node
                        .remove(&spec.write_node_id);
                    "write_intent_hashes_by_write_node"
                }
                _ => panic!(),
            };
            let changed = ApprovalRequest::new(spec).unwrap();
            let before = state(&mut ctx);
            let err = co
                .suspend(&mut ctx, &changed, &p, &p, ev.as_ref())
                .await
                .unwrap_err();
            assert_eq!(err.kind, ApprovalSuspensionErrorKind::Type);
            assert_eq!(err.path, format!("$.checkpoint.{map}[\"write0001\"]"));
            assert_eq!(state(&mut ctx), before, "{ver}/{case}");
            assert!(std::error::Error::source(&err).is_none());
            assert!(
                !err.to_string()
                    .contains(changed.bindings().write_intent_hash())
            );
            {
                let pstate = p.0.lock().unwrap();
                assert_eq!(pstate.events, ["save", "publish"]);
                assert_eq!(pstate.saved.len(), 1);
                assert_eq!(pstate.calls, 1);
                assert_eq!(pstate.published.len(), 1);
            }
            assert_eq!(ctx.checkpoint.checkpoint_version, Integer::from(1));
            assert!(ctx.checkpoint.approval_request_published);
            // The fixed Oracle's *original* second outcome is kept, including its
            // mutation before publish failure, or silently repaired missing map.
            let second = &items(&original)[1];
            assert_eq!(field(second, "version"), &number(2));
            if case.starts_with("changed-") {
                assert_eq!(items(field(second, "outcome"))[0], text("error"));
                assert_eq!(field(second, "published_flag"), &ValueData::Bool(false));
            } else {
                assert_eq!(items(field(second, "outcome"))[0], text("ok"));
            }
            assert_eq!(items(field(second, "events")).len(), 4);
            // A valid retry from the saved unpublished checkpoint is supported.
            let mut restored = CheckpointCoordinator::for_oracle(h::limits())
                .restore(
                    first.checkpoint(),
                    h::skill(ver),
                    h::request(),
                    h::Clock::default(),
                )
                .unwrap();
            assert!(!restored.checkpoint.approval_request_published);
            let resumed = co
                .suspend(&mut restored, &req, &p, &p, ev.as_ref())
                .await
                .unwrap();
            assert_eq!(
                resumed.checkpoint().metadata().checkpoint_version,
                Integer::from(2)
            );
            assert!(restored.checkpoint.approval_request_published);
            assert_eq!(p.0.lock().unwrap().published.len(), 1);
            count += 1;
        }
        assert_eq!(count, 8);
    });
}

#[test]
fn missing_or_mismatched_retry_maps_preserve_both_published_states_and_prior_version() {
    runtime().block_on(async {
        for published in [false, true] {
            for case in [
                "payload-missing",
                "payload-wrong",
                "intent-missing",
                "intent-wrong",
                "both-missing",
                "both-wrong",
            ] {
                for prior in ["0", "1", "99999999999999999999999999999999999999999"] {
                    let (mut ctx, req, ev) = setup("1-1", "pass");
                    let b = req.bindings();
                    let n = &req.metadata().write_node_id;
                    ctx.checkpoint.checkpoint_version = prior.parse().unwrap();
                    ctx.checkpoint.approval_request_published = published;
                    ctx.checkpoint
                        .approval_request_ids_by_write_node
                        .insert(n.clone(), b.approval_request_id().to_string());
                    ctx.checkpoint
                        .payload_hashes_by_write_node
                        .insert(n.clone(), b.payload_hash().to_string());
                    ctx.checkpoint
                        .write_intent_hashes_by_write_node
                        .insert(n.clone(), b.write_intent_hash().into());
                    let payload = case.starts_with("payload") || case.starts_with("both");
                    let intent = case.starts_with("intent") || case.starts_with("both");
                    if payload {
                        if case.ends_with("missing") {
                            ctx.checkpoint.payload_hashes_by_write_node.remove(n);
                        } else {
                            ctx.checkpoint
                                .payload_hashes_by_write_node
                                .insert(n.clone(), "old-payload".into());
                        }
                    }
                    if intent {
                        if case.ends_with("missing") {
                            ctx.checkpoint.write_intent_hashes_by_write_node.remove(n);
                        } else {
                            ctx.checkpoint
                                .write_intent_hashes_by_write_node
                                .insert(n.clone(), "old-intent".into());
                        }
                    }
                    ctx.approvals
                        .records
                        .insert(b.approval_id().into(), ApprovalStatus::Expired);
                    ctx.checkpoint.suspended_at_node_id = Some("previous-node".into());
                    let before = state(&mut ctx);
                    let p = Ports::new("ok");
                    let e = coordinator()
                        .suspend(&mut ctx, &req, &p, &p, ev.as_ref())
                        .await
                        .unwrap_err();
                    assert_eq!(e.kind, ApprovalSuspensionErrorKind::Type);
                    assert_eq!(
                        e.path,
                        format!(
                            "$.checkpoint.{}[\"write0001\"]",
                            if payload {
                                "payload_hashes_by_write_node"
                            } else {
                                "write_intent_hashes_by_write_node"
                            }
                        )
                    );
                    assert_eq!(state(&mut ctx), before);
                    assert!(p.0.lock().unwrap().events.is_empty());
                }
            }
        }
    });
}

#[test]
fn dropping_pending_save_or_publish_keeps_staged_state_and_has_no_background_wait() {
    use std::{
        future::Future,
        task::{Context, Poll, Waker},
    };
    runtime().block_on(async {
        for stage in ["save-pending", "publish-pending"] {
            let (mut ctx, req, ev) = setup("1-1", "pass");
            let p = Ports::new(stage);
            let co = coordinator();
            let mut future = Box::pin(co.suspend(&mut ctx, &req, &p, &p, ev.as_ref()));
            assert!(matches!(
                future
                    .as_mut()
                    .poll(&mut Context::from_waker(Waker::noop())),
                Poll::Pending
            ));
            drop(future);
            assert_eq!(ctx.checkpoint.checkpoint_version, Integer::from(1));
            assert!(!ctx.checkpoint.approval_request_published);
            assert_eq!(
                ctx.approvals.records["PROJECT_DEACTIVATE"],
                ApprovalStatus::Pending
            );
            assert!(ctx.writes.entries.is_empty());
            assert!(ctx.idempotency.keys_by_write_node.is_empty());
            let events = p.0.lock().unwrap().events.clone();
            assert_eq!(
                events,
                if stage == "save-pending" {
                    vec!["save"]
                } else {
                    vec!["save", "publish"]
                }
            );
            tokio::task::yield_now().await;
            assert_eq!(p.0.lock().unwrap().events, events);
            p.0.lock().unwrap().mode = "ok".into();
            let s = co
                .suspend(&mut ctx, &req, &p, &p, ev.as_ref())
                .await
                .unwrap();
            assert_eq!(
                s.checkpoint().metadata().checkpoint_version,
                Integer::from(2)
            );
            assert!(ctx.checkpoint.approval_request_published);
        }
    });
}

#[test]
fn capture_budgets_preserve_staging_but_never_save_or_publish_and_allow_explicit_retry() {
    runtime().block_on(async {
        let (mut ctx, req, ev) = setup("1-1", "pass");
        let p = Ports::new("ok");
        let good = coordinator()
            .suspend(&mut ctx, &req, &p, &p, ev.as_ref())
            .await
            .unwrap();
        let wire_size = good.checkpoint().to_bytes(h::limits()).unwrap().len();
        let state_size = good.checkpoint().state_payload().len();
        for outer in [false, true] {
            for budget in [
                0,
                1,
                if outer { wire_size - 1 } else { state_size - 1 },
                if outer { wire_size } else { state_size },
                if outer { wire_size + 1 } else { state_size + 1 },
            ] {
                let (mut ctx, req, ev) = setup("1-1", "pass");
                let mut limits = h::limits();
                if outer {
                    limits.outer.max_bytes = budget;
                } else {
                    limits.state.max_bytes = budget;
                }
                let p = Ports::new("ok");
                let result =
                    ApprovalSuspensionCoordinator::new(CheckpointCoordinator::for_oracle(limits))
                        .suspend(&mut ctx, &req, &p, &p, ev.as_ref())
                        .await;
                if budget < if outer { wire_size } else { state_size } {
                    let e = result.unwrap_err();
                    assert_eq!(
                        e.kind,
                        ApprovalSuspensionErrorKind::Checkpoint(CheckpointErrorKind::Limit(
                            JsonLimit::Bytes
                        ))
                    );
                    assert_eq!(ctx.checkpoint.checkpoint_version, Integer::from(0));
                    assert_eq!(
                        ctx.approvals.records["PROJECT_DEACTIVATE"],
                        ApprovalStatus::Pending
                    );
                    assert!(!ctx.checkpoint.approval_request_published);
                    assert!(p.0.lock().unwrap().events.is_empty());
                    let next = coordinator()
                        .suspend(&mut ctx, &req, &p, &p, ev.as_ref())
                        .await
                        .unwrap();
                    assert_eq!(
                        next.checkpoint().metadata().checkpoint_version,
                        Integer::from(1)
                    );
                } else {
                    assert!(result.is_ok());
                }
            }
        }
    });
}

#[test]
fn request_binding_precedence_wrapper_status_and_errors_are_safe_and_typed() {
    runtime().block_on(async {
        let (mut ctx, req, ev) = setup("1-1", "pass");
        let p = Ports::new("ok");
        let co = coordinator();
        let s = co
            .suspend(&mut ctx, &req, &p, &p, ev.as_ref())
            .await
            .unwrap();
        assert_eq!(s, s.clone());
        assert!(!format!("{s:?}").contains("tenant-a"));
        assert!(format!("{co:?}").contains("ApprovalSuspensionCoordinator"));
        for status in [
            ExecutionStatus::Completed,
            ExecutionStatus::Failed,
            ExecutionStatus::Cancelled,
        ] {
            let mut r = s.result().clone();
            r.status = status;
            let e = ApprovalSuspension::new(s.checkpoint().clone(), r).unwrap_err();
            assert_eq!(e.path, "$.result.status");
            assert_eq!(e.kind, ApprovalSuspensionErrorKind::Type);
            assert!(e.to_string().contains("WAITING_FOR_APPROVAL"));
        }
        let mut spec = req.metadata().clone();
        spec.bindings.tenant_id = "other".into();
        spec.bindings.execution_id = "other".into();
        let wrong = ApprovalRequest::new(spec).unwrap();
        ctx.checkpoint.payload_hashes_by_write_node.clear();
        let before = state(&mut ctx);
        let e = co
            .suspend(&mut ctx, &wrong, &p, &p, None)
            .await
            .unwrap_err();
        assert_eq!(e.path, "$.request.tenant_id");
        assert_eq!(state(&mut ctx), before);
        // Sensitive logical identifiers are screened even on an error path from
        // an untrusted map key. The admitted request itself carries no credential.
        let mut spec = req.metadata().clone();
        spec.write_node_id = "node.\"😀".into();
        let req = ApprovalRequest::new(spec).unwrap();
        ctx.checkpoint
            .approval_request_ids_by_write_node
            .insert(req.metadata().write_node_id.clone(), "request-a".into());
        let e = co
            .suspend(&mut ctx, &req, &p, &p, ev.as_ref())
            .await
            .unwrap_err();
        assert!(e.path.contains("\\\"😀"));
        assert!(std::error::Error::source(&e).is_none());
    });
}
