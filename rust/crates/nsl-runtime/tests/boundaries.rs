mod support;
use nsl_core::{
    CanonicalJsonError, CheckStatus, Completeness, DataClassification, ExecutionStatus,
    IdempotencyKey, IdentityError, Integer, TypeKind, TypeRef, Value, ValueEnvelope,
};
use nsl_ir::ResourceLimits;
use nsl_runtime::*;
use std::{collections::BTreeMap, error::Error};
use support::*;

#[test]
fn raw_summary_and_state_identity_round_trip_requires_explicit_write_conversion() {
    let canonical_key = format!("idem:sha256:{}", "a".repeat(64));
    let canonical_hash = format!("sha256:{}", "b".repeat(64));
    for key in [
        "idem-short".to_owned(),
        canonical_key.clone(),
        format!("idem:sha256:{}", "가".repeat(64)),
    ] {
        for hash in [
            "not-a-payload-hash".to_owned(),
            canonical_hash.clone(),
            format!("sha256:{}", "B".repeat(64)),
        ] {
            let summary = write(&key, &hash);
            let before = summary.clone();
            assert_eq!(summary.spec().idempotency_key, key);
            assert_eq!(summary.spec().payload_hash, hash);
            let expected = key.parse::<IdempotencyKey>().and_then(|k| {
                hash.parse().map(|h| ValidatedWriteIdentities {
                    idempotency_key: k,
                    payload_hash: h,
                })
            });
            assert_eq!(summary.validate_for_write(), expected);
            assert_eq!(summary, before);
            let mut state = IdempotencyState::default();
            state.keys_by_write_node.insert("W".into(), key.clone());
            state
                .payload_hashes_by_write_node
                .insert("W".into(), hash.clone());
            let before = state.clone();
            assert_eq!(
                state.validate_for_write("W"),
                expected.map_err(WriteIdentityError::Invalid)
            );
            assert_eq!(state, before);
            if let Err(e) = state.validate_for_write("W") {
                assert!(!e.to_string().is_empty());
                assert!(e.source().is_some());
            }
        }
    }
    let mut state = IdempotencyState::default();
    assert_eq!(
        state.validate_for_write("W"),
        Err(WriteIdentityError::MissingKey)
    );
    state.keys_by_write_node.insert("W".into(), canonical_key);
    assert_eq!(
        state.validate_for_write("W"),
        Err(WriteIdentityError::MissingPayload)
    );
    for e in [
        WriteIdentityError::MissingKey,
        WriteIdentityError::MissingPayload,
    ] {
        assert!(!e.to_string().is_empty());
        assert!(e.source().is_none());
    }
    assert!(matches!(
        ValidatedWriteIdentities::parse("bad", "bad"),
        Err(IdentityError::InvalidIdempotencyKey)
    ));
}
#[test]
fn contexts_separate_identity_security_namespaces_and_mutable_state() {
    assert_eq!(PrincipalVerification::Unverified.as_str(), "UNVERIFIED");
    assert_eq!(PrincipalVerification::Verified.as_str(), "VERIFIED");
    let original_skill = skill();
    let req = request();
    let mut a = ExecutionContext::new(original_skill.clone(), req.clone());
    let b = ExecutionContext::new(original_skill, req.clone());
    assert_eq!(a.skill().skill_id, b.skill().skill_id);
    assert_eq!(a.identity(), req.identity());
    assert_eq!(a.tenant_id(), "tenant-a");
    assert_eq!(a.security_context(), req.security_context());
    assert_eq!(a.request(), &req);
    assert_eq!(a.request().validate_for_execution(false), Ok(()));
    assert_eq!(
        a.request().validate_for_execution(true),
        Err(AuthorizationError(
            "verified execution principal is required"
        ))
    );
    assert_eq!(
        a.security_context()
            .data_policy
            .snapshot_retention_days()
            .as_str(),
        "30"
    );
    assert_eq!(
        a.security_context()
            .data_policy
            .audit_retention_days()
            .as_str(),
        "90"
    );
    a.bind(BindingNamespace::Input, "I".into(), envelope(1))
        .unwrap();
    a.bind(BindingNamespace::Context, "C".into(), envelope(2))
        .unwrap();
    assert_eq!(a.input_values().len(), 1);
    assert_eq!(a.context_values().len(), 1);
    assert!(b.input_values().is_empty());
    assert!(b.context_values().is_empty());
    assert!(req.inputs().is_empty());
    assert!(req.runtime_context().is_empty());
    a.approvals
        .records
        .insert("A".into(), nsl_core::ApprovalStatus::Pending);
    a.writes.entries.push(text("event"));
    a.idempotency
        .keys_by_write_node
        .insert("W".into(), "legacy-key".into());
    a.checkpoint.checkpoint_version = 1.into();
    a.checkpoint
        .payload_hashes_by_write_node
        .insert("W".into(), "legacy-payload".into());
    assert!(b.approvals.records.is_empty());
    assert!(b.writes.entries.is_empty());
    assert!(b.idempotency.keys_by_write_node.is_empty());
    assert_eq!(b.checkpoint.checkpoint_version.as_str(), "0");
    assert!(b.checkpoint.payload_hashes_by_write_node.is_empty());
    assert_eq!(a.invocation_counter.as_str(), "0");
    assert_eq!(a.foreach_depth, 0);
    assert!(a.current_node_id.is_none());
    assert_eq!(MAX_FOREACH_NESTING_DEPTH, 16);
    assert_eq!(a.usage(), ResourceUsage::default());
    assert_eq!(a.guard().limits(), &a.skill().limits);
    a.guard_mut().before_tool_call().unwrap();
    assert_eq!(a.usage().tool_calls.as_str(), "1");
    assert_eq!(b.usage().tool_calls.as_str(), "0");
    for e in [
        BindingError::Immutable("I".into()),
        BindingError::Unknown("X".into()),
        BindingError::RootFrame,
    ] {
        assert_eq!(
            e.detail_code(),
            if matches!(e, BindingError::Immutable(_)) {
                Some("IMMUTABLE_BINDING_ERROR")
            } else {
                None
            }
        );
        assert!(e.source().is_none());
        assert!(!e.to_string().is_empty());
    }
}
#[test]
fn large_frame_stacks_and_failed_bindings_leave_siblings_reusable() {
    let clock = Clock::default();
    let mut ctx = ExecutionContext::with_clock(skill(), request(), &clock);
    for count in [0, 1, 255, 256, 257] {
        for i in 0..count {
            ctx.push_frame();
            ctx.bind(BindingNamespace::Variable, format!("S{i}"), envelope(1))
                .unwrap();
        }
        for i in (0..count).rev() {
            assert_eq!(ctx.resolve(&format!("S{i}")).unwrap(), &envelope(1));
            ctx.pop_frame().unwrap();
        }
        assert_eq!(ctx.frame_count(), 1);
    }
    for _ in 0..2 {
        ctx.push_frame();
        ctx.bind(BindingNamespace::Check, "same".into(), envelope(1))
            .unwrap();
        assert!(
            ctx.bind(BindingNamespace::Variable, "same".into(), envelope(2))
                .is_err()
        );
        assert_eq!(ctx.resolve("same").unwrap(), &envelope(1));
        ctx.pop_frame().unwrap();
    }
}
#[test]
fn summary_rejects_duplicate_empty_ids_and_counts_remain_arbitrary_precision() {
    for ids in [["A", "A"], [" ", "B"], ["", "B"]] {
        let checks = ids.map(|id| check(id, CheckStatus::Pass));
        assert!(ValidationSummary::from_checks(&checks).is_err());
    }
    let big: Integer = format!("1{}", "0".repeat(1000)).parse().unwrap();
    let s = ValidationSummary::new(ValidationSummarySpec {
        status: ValidationSummaryStatus::AllPass,
        total_count: big.clone(),
        pass_count: big.clone(),
        fail_count: 0.into(),
        unknown_count: 0.into(),
        failed_check_ids: vec![],
        unknown_check_ids: vec![],
    })
    .unwrap();
    assert_eq!(s.spec().total_count, big);
    for value in [0, 1, 2] {
        let checks = (0..value)
            .map(|i| check(&format!("C{i}"), CheckStatus::Unknown))
            .collect::<Vec<_>>();
        assert_eq!(
            ValidationSummary::from_checks(&checks)
                .unwrap()
                .spec()
                .unknown_count
                .as_str(),
            value.to_string()
        );
    }
}
#[test]
fn explicit_model_validation_does_not_expand_decimal_tokens_or_treat_metadata_as_authority() {
    let data = BTreeMap::from([(
        "numbers".into(),
        Value::List(vec![
            Value::Decimal("1e1000000".parse().unwrap()),
            Value::Date("2026-01-01".into()),
            Value::DateTime("2026-01-01T00:00:00".into()),
            Value::Bool(true),
            Value::Money(nsl_core::Money::new(
                "1".parse().unwrap(),
                "KRW".parse().unwrap(),
            )),
        ]),
    )]);
    let mut p = principal();
    p.verification = PrincipalVerification::Verified;
    p.actor_type = "SERVICE".into();
    p.on_behalf_of = Some("host-actor".into());
    let request = ExecutionRequest::new(
        "exec-a".into(),
        data,
        BTreeMap::from([("wall_clock".into(), Value::Int(0.into()))]),
        ExecutionSecurityContext {
            principal: p,
            data_policy: DataHandlingPolicy::default(),
        },
        Some("op-a".into()),
    )
    .unwrap();
    request.validate_for_execution(true).unwrap();
    assert_eq!(request.identity().business_operation_id(), Some("op-a"));
    let clock = Clock::default();
    let mut ctx = ExecutionContext::with_clock(skill(), request, &clock);
    clock.set(60_000_000_000i64.into());
    assert_eq!(
        ctx.guard_mut().before_tool_call(),
        Err(LimitExceeded(ResourceLimit::Duration))
    );
    assert_eq!(ctx.usage(), ResourceUsage::default());
    let system = SystemRuntimeClock::default();
    let a = system.monotonic_ns();
    let b = system.monotonic_ns();
    assert!(a.as_str().parse::<u128>().unwrap() <= b.as_str().parse::<u128>().unwrap());
    let mut empty_record = BTreeMap::new();
    empty_record.insert("token".into(), Value::Record(BTreeMap::new()));
    assert!(
        ExecutionRequest::new(
            "exec-a".into(),
            empty_record,
            Default::default(),
            request_security(),
            None
        )
        .is_err()
    );
}
fn request_security() -> ExecutionSecurityContext {
    ExecutionSecurityContext {
        principal: principal(),
        data_policy: DataHandlingPolicy::default(),
    }
}
#[test]
fn result_encoding_reports_typed_budget_and_model_errors_with_error_chains() {
    let mut r = result(ExecutionStatus::Completed, Completeness::Complete, 0);
    let err = r.to_data(1).unwrap_err();
    assert!(matches!(err, ResultEncodingError::Decimal(_)));
    assert!(err.source().is_some());
    assert!(!err.to_string().is_empty());
    let optional = TypeRef::new(TypeKind::Optional(Box::new(ty("Int")))).unwrap();
    r.outputs = vec![
        EmitRecord::new(vec![(
            "x".into(),
            ValueEnvelope::complete(Value::Int(1.into()), optional, DataClassification::Internal),
        )])
        .unwrap(),
    ];
    let err = r.to_data(100).unwrap_err();
    assert!(matches!(err, ResultEncodingError::Type(_)));
    assert!(err.source().is_some());
    assert!(!err.to_string().is_empty());
    r.outputs.clear();
    r.resources.tool_calls = "1".repeat(4301).parse().unwrap();
    let err = r.to_json(100).unwrap_err();
    assert_eq!(
        err,
        ResultEncodingError::Json(CanonicalJsonError::IntegerTooWide)
    );
    assert!(err.source().is_some());
    assert!(!err.to_string().is_empty());
    r.resources = ResourceUsage::default();
    r.error = None;
    r.checks.clear();
    assert!(r.to_json(100).is_ok());
    assert!(r.semantic_view(100).is_ok());
    assert!(
        EmitRecord::new(vec![
            ("duplicate".into(), envelope(1)),
            ("duplicate".into(), envelope(2))
        ])
        .is_err()
    );
}
#[test]
fn guard_exact_large_counts_never_wrap_and_all_errors_are_codeless() {
    let clock = Clock::default();
    let wide: Integer = "1".repeat(5000).parse().unwrap();
    let limits = ResourceLimits {
        tool_calls: wide.clone(),
        loop_iterations: wide.clone(),
        emitted_rows: wide.clone(),
        collection_size: wide.clone(),
        duration_ms: wide.clone(),
    };
    let mut guard = ResourceGuard::new(limits, &clock);
    guard.before_tool_call().unwrap();
    guard.before_loop_iteration().unwrap();
    guard.before_emit().unwrap();
    guard.record_emit();
    guard.observe_collection(wide.clone()).unwrap();
    assert_eq!(guard.usage().max_collection_size_seen, wide);
    for kind in [
        ResourceLimit::Duration,
        ResourceLimit::ToolCalls,
        ResourceLimit::LoopIterations,
        ResourceLimit::EmittedRows,
        ResourceLimit::CollectionSize,
    ] {
        let error = LimitExceeded(kind);
        assert!(!error.to_string().contains("NSL-E"));
        assert!(error.source().is_none());
    }
}
