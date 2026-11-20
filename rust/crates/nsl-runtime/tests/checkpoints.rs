mod checkpoint_support;
use checkpoint_support as h;
use nsl_core::*;
use nsl_runtime::*;
#[test]
fn capture_outer_state_bytes_integrity_and_recapture_match_frozen_oracle() {
    let limits = h::limits();
    let coordinator = CheckpointCoordinator::for_oracle(limits);
    let mut count = 0;
    for line in include_str!("fixtures/checkpoints-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let mut ctx = if f[1] == "empty" || f[1].starts_with("safe-") {
            h::context(f[0], f[1], f[2])
        } else {
            let seed = ExecutionCheckpoint::from_bytes(&h::decode(f[4]), limits).unwrap();
            let mut ctx = coordinator
                .restore(&seed, h::skill(f[0]), h::request(), h::Clock::default())
                .unwrap();
            ctx.checkpoint.checkpoint_version = f[2].parse().unwrap();
            assert_eq!(
                ctx.resolve("sym-input").unwrap().value(),
                &Value::Int(2026.into())
            );
            assert_eq!(ctx.usage().tool_calls, Integer::from(1));
            assert_eq!(ctx.usage().loop_iterations, Integer::from(2));
            ctx
        };
        let cp = coordinator.capture(&mut ctx).unwrap();
        assert_eq!(
            cp.state_payload(),
            h::decode(f[3]),
            "state {}/{}/{}",
            f[0],
            f[1],
            f[2]
        );
        assert_eq!(cp.integrity_hash(), f[5]);
        assert_eq!(cp.business_operation_id().as_str(), "business-a");
        assert_eq!(cp.to_bytes(limits).unwrap(), h::decode(f[4]));
        let cp = ExecutionCheckpoint::from_bytes(&h::decode(f[4]), limits).unwrap();
        let clock = h::Clock::default();
        let mut restored = coordinator
            .restore(&cp, h::skill(f[0]), h::request(), clock.clone())
            .unwrap();
        assert_eq!(clock.calls(), 1);
        assert_eq!(restored.usage(), ctx.usage());
        assert_eq!(restored.approvals, ctx.approvals);
        assert_eq!(restored.idempotency, ctx.idempotency);
        assert_eq!(restored.writes, ctx.writes);
        assert_eq!(restored.checkpoint, ctx.checkpoint);
        assert_eq!(restored.frame_count(), ctx.frame_count());
        assert_eq!(restored.data_completeness(), ctx.data_completeness());
        let next = coordinator.capture(&mut restored).unwrap();
        assert_eq!(next.state_payload(), h::decode(f[6]));
        assert_eq!(next.to_bytes(limits).unwrap(), h::decode(f[7]));
        assert!(!format!("{cp:?}").contains("TEAM-FINANCE"));
        assert_eq!(cp.metadata().runtime_version, "0.1.0");
        count += 1;
    }
    assert_eq!(count, 64);
}
#[test]
fn ordinary_malformed_checkpoints_and_resigned_binding_mismatches_are_rejected() {
    let coordinator = CheckpointCoordinator::for_oracle(h::limits());
    let mut count = 0;
    for line in include_str!("fixtures/checkpoints-negative-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let bytes = h::decode(f[2]);
        let expected = String::from_utf8(h::decode(f[3])).unwrap();
        assert!(expected.starts_with("[\"error\",\"CheckpointError\","));
        let result = ExecutionCheckpoint::from_bytes(&bytes, h::limits()).and_then(|cp| {
            coordinator.restore(&cp, h::skill("1-1"), h::request(), h::Clock::default())
        });
        let err = match result {
            Ok(_) => panic!("accepted {}/{}", f[0], f[1]),
            Err(e) => e,
        };
        assert!(err.path.starts_with('$'));
        assert!(std::error::Error::source(&err).is_none());
        assert!(!err.to_string().contains("synthetic"));
        if f[0] == "binding" {
            assert_eq!(err.path, format!("$.{}", f[1]));
            assert_eq!(err.kind, CheckpointErrorKind::Binding);
        }
        count += 1;
    }
    assert_eq!(count, 28);
}
#[test]
fn approved_strict_state_schema_types_credentials_and_depth_exceptions_are_codeless() {
    let coordinator = CheckpointCoordinator::for_oracle(h::limits());
    let mut count = 0;
    for line in include_str!("fixtures/checkpoints-exceptions.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        assert_eq!(String::from_utf8(h::decode(f[3])).unwrap(), "[\"ok\",null]");
        let clock = h::Clock::default();
        let result = ExecutionCheckpoint::from_bytes(&h::decode(f[2]), h::limits())
            .and_then(|cp| coordinator.restore(&cp, h::skill("1-1"), h::request(), clock.clone()));
        let err = match result {
            Ok(_) => panic!("accepted {}", f[1]),
            Err(e) => e,
        };
        assert!(
            matches!(
                err.kind,
                CheckpointErrorKind::Type | CheckpointErrorKind::Credential
            ),
            "case={} error={err:?}",
            f[1]
        );
        assert!(err.path.starts_with('$'));
        assert_eq!(clock.calls(), 0);
        assert!(std::error::Error::source(&err).is_none());
        assert!(!format!("{err:?}").contains("synthetic-only"));
        assert!(!err.to_string().contains("NSL-E"));
        if f[1] == "float" {
            assert_eq!(err.path, "$.symbols.inputs.sym-input.value");
        }
        if f[1].starts_with("depth") {
            assert_eq!(err.path, "$.execution.foreach_depth");
        }
        count += 1;
    }
    assert_eq!(count, 13);
}

fn restore_data(data: ValueData) -> Result<ExecutionContext<h::Clock>, CheckpointError> {
    let cp = h::resign(&h::base(), data);
    CheckpointCoordinator::for_oracle(h::limits()).restore(
        &cp,
        h::skill("1-1"),
        h::request(),
        h::Clock::default(),
    )
}
#[test]
fn malformed_state_fields_fail_before_publishing_a_context() {
    let cp = h::base();
    let base = h::raw_state(&cp);
    let mut cases = vec![];
    for key in [
        "symbols",
        "resources",
        "execution",
        "idempotency",
        "checkpoint",
        "security",
        "checks",
        "outputs",
        "writes",
        "approvals",
    ] {
        let mut data = base.clone();
        h::obj(&mut data).insert(key.into(), ValueData::Null);
        cases.push(data);
    }
    for path in [
        vec!["symbols", "inputs"],
        vec!["symbols", "contexts"],
        vec!["symbols", "frames"],
        vec!["symbols", "check_frames"],
    ] {
        let mut data = base.clone();
        *h::at(&mut data, &path) = ValueData::Null;
        cases.push(data);
    }
    for field in [
        "type",
        "presence",
        "completeness",
        "classification",
        "provenance_refs",
    ] {
        let mut data = base.clone();
        h::obj(h::at(&mut data, &["symbols", "inputs", "sym-input"]))
            .insert(field.into(), ValueData::Null);
        cases.push(data);
    }
    for field in [
        "tool_calls",
        "loop_iterations",
        "emitted_rows",
        "max_collection_size_seen",
    ] {
        let mut data = base.clone();
        h::obj(h::at(&mut data, &["resources"]))
            .insert(field.into(), ValueData::Number((-1).into()));
        cases.push(data);
    }
    for field in ["invocation_counter", "foreach_depth", "current_node_id"] {
        let mut data = base.clone();
        h::obj(h::at(&mut data, &["execution"])).insert(field.into(), ValueData::Bool(false));
        cases.push(data);
    }
    for field in [
        "last_completed_node_id",
        "suspended_at_node_id",
        "trace_sequence",
        "approval_request_published",
        "write_request_durable",
        "approval_request_ids_by_write_node",
        "write_intent_hashes_by_write_node",
        "payload_hashes_by_write_node",
    ] {
        let mut data = base.clone();
        h::obj(h::at(&mut data, &["checkpoint"]))
            .insert(field.into(), ValueData::Number((-1).into()));
        cases.push(data);
    }
    for ns in ["idempotency", "checkpoint"] {
        let fields = if ns == "idempotency" {
            vec!["keys_by_write_node", "payload_hashes_by_write_node"]
        } else {
            vec![
                "approval_request_ids_by_write_node",
                "write_intent_hashes_by_write_node",
                "payload_hashes_by_write_node",
            ]
        };
        for field in fields {
            for value in [ValueData::Null, ValueData::String(" ".into())] {
                let mut data = base.clone();
                h::obj(h::at(&mut data, &[ns, field])).insert("write0".into(), value);
                cases.push(data);
            }
        }
    }
    for field in [
        "check_id",
        "status",
        "severity",
        "condition_node_id",
        "presence",
        "completeness",
        "reason_code",
    ] {
        let mut data = base.clone();
        let ValueData::Array(checks) = h::at(&mut data, &["checks"]) else {
            panic!()
        };
        h::obj(&mut checks[0]).insert(field.into(), ValueData::String("".into()));
        cases.push(data);
    }
    for value in [
        ValueData::Array(vec![ValueData::Null]),
        ValueData::Array(vec![ValueData::String("".into())]),
    ] {
        let mut data = base.clone();
        h::obj(h::at(&mut data, &["symbols", "inputs", "sym-input"]))
            .insert("provenance_refs".into(), value);
        cases.push(data);
    }
    let mut data = base.clone();
    let sample = h::at(&mut data, &["symbols", "inputs", "sym-input"]).clone();
    h::obj(h::at(&mut data, &["symbols", "inputs"])).insert(" ".into(), sample);
    cases.push(data);
    for data in cases {
        let e = match restore_data(data) {
            Ok(_) => panic!("malformed state accepted"),
            Err(e) => e,
        };
        assert!(e.path.starts_with('$'));
        assert!(std::error::Error::source(&e).is_none());
    }
}
fn dimensions(data: &ValueData) -> (usize, usize) {
    let mut pending = vec![(data, 0)];
    let mut depth = 0;
    let mut nodes = 0;
    while let Some((v, d)) = pending.pop() {
        nodes += 1;
        depth = depth.max(d);
        match v {
            ValueData::Array(a) => pending.extend(a.iter().map(|v| (v, d + 1))),
            ValueData::Object(o) => pending.extend(o.values().map(|v| (v, d + 1))),
            _ => {}
        }
    }
    (depth, nodes)
}
#[test]
fn caller_budgets_cover_zero_one_max_minus_one_max_and_max_plus_one() {
    let cp = h::base();
    let wire = cp.to_bytes(h::limits()).unwrap();
    let outer = parse_json_with_limits(&wire, h::limits().outer).unwrap();
    let state = h::raw_state(&cp);
    for is_state in [false, true] {
        let (depth, nodes) = dimensions(if is_state { &state } else { &outer });
        let bytes = if is_state {
            cp.state_payload().len()
        } else {
            wire.len()
        };
        for (axis, max) in [bytes, depth, nodes].into_iter().enumerate() {
            for n in [0, 1, max - 1, max, max + 1] {
                let mut limits = h::limits();
                let l = if is_state {
                    &mut limits.state
                } else {
                    &mut limits.outer
                };
                match axis {
                    0 => l.max_bytes = n,
                    1 => l.max_depth = n,
                    _ => l.max_nodes = n,
                }
                let result = ExecutionCheckpoint::from_bytes(&wire, limits).and_then(|cp| {
                    CheckpointCoordinator::for_oracle(limits).restore(
                        &cp,
                        h::skill("1-1"),
                        h::request(),
                        h::Clock::default(),
                    )
                });
                if n < max {
                    assert!(
                        matches!(
                            result,
                            Err(CheckpointError {
                                kind: CheckpointErrorKind::Limit(_),
                                ..
                            })
                        ),
                        "state={is_state} axis={axis} n={n}"
                    );
                } else {
                    assert!(result.is_ok(), "state={is_state} axis={axis} n={n}");
                }
            }
        }
    }
    // Encoding uses the same inclusive limits; failed capture does not publish a version.
    for axis in 0..7 {
        let mut limits = h::limits();
        match axis {
            0 => limits.outer.max_bytes = 0,
            1 => limits.outer.max_depth = 0,
            2 => limits.outer.max_nodes = 0,
            3 => limits.state.max_bytes = 0,
            4 => limits.state.max_depth = 0,
            5 => limits.state.max_nodes = 0,
            _ => limits.max_decimal_bytes = 0,
        }
        let mut ctx = h::context("1-1", "empty", "0");
        if axis == 6 {
            ctx.bind(
                BindingNamespace::Variable,
                "decimal".into(),
                ValueEnvelope::complete(
                    Value::Decimal("1.2300".parse().unwrap()),
                    h::ty("Decimal"),
                    DataClassification::Internal,
                ),
            )
            .unwrap();
        }
        assert!(
            CheckpointCoordinator::for_oracle(limits)
                .capture(&mut ctx)
                .is_err()
        );
        assert_eq!(ctx.checkpoint.checkpoint_version, Integer::from(0));
    }
}
#[test]
fn restored_scope_meter_and_independent_contexts_retain_their_own_state() {
    let cp = h::base();
    let coord = CheckpointCoordinator::for_oracle(h::limits());
    let mut a = coord
        .restore(&cp, h::skill("1-1"), h::request(), h::Clock::default())
        .unwrap();
    let mut b = coord
        .restore_with_system_clock(&cp, h::skill("1-1"), h::request())
        .unwrap();
    assert_eq!(a.frame_count(), 2);
    assert!(a.resolve("sym-inner").is_ok());
    a.pop_frame().unwrap();
    assert!(a.resolve("sym-inner").is_err());
    assert!(b.resolve("sym-inner").is_ok());
    let original = b.usage();
    b.guard_mut().record_emit();
    assert_eq!(a.usage(), original);
    assert_ne!(b.usage(), original);
    let s = cp.metadata().clone();
    assert_eq!(ExecutionCheckpoint::create(s, h::limits()).unwrap(), cp);
    assert!(CheckpointCoordinator::new(" ".into(), h::limits()).is_err());
    assert!(CheckpointCoordinator::new("token=synthetic-only".into(), h::limits()).is_err());
    let mut ctx = h::context("1-1", "empty", "0");
    ctx.foreach_depth = 17;
    let e = coord.capture(&mut ctx).unwrap_err();
    assert_eq!(e.kind, CheckpointErrorKind::Type);
    assert_eq!(ctx.checkpoint.checkpoint_version, Integer::from(0));
    ctx.foreach_depth = 0;
    ctx.bind(
        BindingNamespace::Variable,
        "bad".into(),
        ValueEnvelope::complete(
            Value::Bool(true),
            h::ty("Int"),
            DataClassification::Internal,
        ),
    )
    .unwrap();
    assert!(coord.capture(&mut ctx).is_err());
}
#[test]
fn bounded_generated_corruptions_never_publish_a_partially_restored_context() {
    let cp = h::base();
    for i in 0..128 {
        let mut data = h::raw_state(&cp);
        if i % 2 == 0 {
            h::obj(h::at(&mut data, &["execution"])).insert(
                "foreach_depth".into(),
                ValueData::Number((17 + i as i64).into()),
            );
        } else {
            h::obj(h::at(&mut data, &["symbols", "inputs", "sym-input"]))
                .insert("value".into(), ValueData::Bool(i % 3 == 0));
        }
        assert!(restore_data(data).is_err());
    }
    let mut broken = cp.to_bytes(h::limits()).unwrap();
    let pos = broken.iter().position(|b| *b == b'a').unwrap();
    broken[pos] = b'b';
    assert!(ExecutionCheckpoint::from_bytes(&broken, h::limits()).is_err());
}

#[test]
fn binary_state_base64_padding_and_canonical_reencoding_match_oracle() {
    let limits = h::limits();
    let base = h::base();
    let mut count = 0;
    for line in include_str!("fixtures/checkpoints-wire-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        if f[0] == "binary" {
            let mut spec = base.metadata().clone();
            spec.state_payload = if f[1] == "-" { vec![] } else { h::decode(f[1]) };
            let cp = ExecutionCheckpoint::create(spec, limits).unwrap();
            assert_eq!(cp.to_bytes(limits).unwrap(), h::decode(f[2]));
            assert_eq!(cp.integrity_hash(), f[3]);
            assert_eq!(
                ExecutionCheckpoint::from_bytes(&h::decode(f[2]), limits).unwrap(),
                cp
            );
        } else {
            let result = ExecutionCheckpoint::from_bytes(&h::decode(f[1]), limits);
            let oracle = String::from_utf8(h::decode(f[4])).unwrap();
            if oracle.starts_with("[\"ok\",") {
                assert_eq!(result.unwrap().to_bytes(limits).unwrap(), h::decode(f[2]));
            } else {
                assert!(result.is_err(), "{}", f[1]);
            }
        }
        count += 1;
    }
    assert_eq!(count, 24);
}
#[test]
fn decoded_journal_value_and_temporal_normalization_match_oracle() {
    let limits = h::limits();
    let coord = CheckpointCoordinator::for_oracle(limits);
    let mut count = 0;
    for line in include_str!("fixtures/checkpoints-values-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let cp = ExecutionCheckpoint::from_bytes(&h::decode(f[2]), limits).unwrap();
        let expected = String::from_utf8(h::decode(f[3])).unwrap();
        let result = coord
            .restore(&cp, h::skill("1-1"), h::request(), h::Clock::default())
            .and_then(|mut ctx| coord.capture(&mut ctx));
        if expected.starts_with("[\"ok\",") {
            let hex = expected
                .strip_prefix("[\"ok\",\"")
                .unwrap()
                .strip_suffix("\"]")
                .unwrap();
            assert!(
                result
                    .unwrap_or_else(|e| panic!(
                        "case={} value={} err={e:?}",
                        f[0],
                        String::from_utf8(h::decode(f[1])).unwrap()
                    ))
                    .to_bytes(limits)
                    .unwrap()
                    == h::decode(hex),
                "value={}",
                String::from_utf8(h::decode(f[1])).unwrap()
            );
        } else {
            assert!(
                result.is_err(),
                "case={} value={}",
                f[0],
                String::from_utf8(h::decode(f[1])).unwrap()
            );
        }
        count += 1;
    }
    assert_eq!(count, 39);
}

#[test]
fn native_capture_preflights_large_values_types_journals_and_identity_before_allocating_wire() {
    use std::collections::BTreeMap;
    let cap = h::limits();
    for recipe in [
        "string",
        "list",
        "record",
        "money",
        "decimal",
        "domain",
        "enum",
        "record-type",
        "optional",
        "deep-value",
        "deep-data",
        "deep-type",
    ] {
        let mut c = h::context("1-1", "empty", "0");
        let mut l = cap;
        l.state.max_nodes = 20;
        l.state.max_bytes = 1000;
        l.state.max_depth = 16;
        let (value, ty) = match recipe {
            "string" => (Value::String("x".repeat(1001)), h::ty("String")),
            "list" => (
                Value::List(vec![Value::Int(1.into()); 21]),
                TypeRef::new(TypeKind::List(Box::new(h::ty("Int")))).unwrap(),
            ),
            "record" => (
                Value::Record(
                    (0..21)
                        .map(|i| (i.to_string(), Value::Int(1.into())))
                        .collect(),
                ),
                h::ty("Int"),
            ),
            "money" => (
                Value::Money(Money::new(
                    "1e10000".parse().unwrap(),
                    "USD".parse().unwrap(),
                )),
                TypeRef::new(TypeKind::Money("USD".parse().unwrap())).unwrap(),
            ),
            "decimal" => (Value::Decimal("1e10000".parse().unwrap()), h::ty("Decimal")),
            "domain" => (
                Value::String("x".into()),
                TypeRef::new(TypeKind::Domain("N".repeat(1001))).unwrap(),
            ),
            "enum" => (
                Value::String("x".into()),
                TypeRef::new(TypeKind::Enum {
                    name: "E".into(),
                    values: vec!["N".repeat(1001)],
                })
                .unwrap(),
            ),
            "record-type" => (
                Value::Record(BTreeMap::new()),
                TypeRef::new(TypeKind::Record {
                    name: "R".into(),
                    fields: (0..21).map(|i| (i.to_string(), h::ty("Int"))).collect(),
                })
                .unwrap(),
            ),
            "optional" => (
                Value::Null,
                TypeRef::new(TypeKind::Optional(Box::new(h::ty("Int")))).unwrap(),
            ),
            "deep-value" => {
                let mut v = Value::Int(1.into());
                for _ in 0..20 {
                    v = Value::List(vec![v]);
                }
                (v, h::ty("Int"))
            }
            "deep-type" => {
                let mut t = h::ty("Int");
                for _ in 0..20 {
                    t = TypeRef::new(TypeKind::List(Box::new(t))).unwrap();
                }
                (Value::Null, t)
            }
            _ => {
                let mut d = ValueData::Null;
                for _ in 0..20 {
                    d = ValueData::Array(vec![d]);
                }
                c.writes.entries.push(d);
                (Value::Int(1.into()), h::ty("Int"))
            }
        };
        c.bind(
            BindingNamespace::Variable,
            "value".into(),
            ValueEnvelope::complete(value, ty, DataClassification::Internal),
        )
        .unwrap();
        let err = CheckpointCoordinator::for_oracle(l)
            .capture(&mut c)
            .unwrap_err();
        assert!(err.path.starts_with('$'));
        assert_eq!(c.checkpoint.checkpoint_version, Integer::from(0));
    }
    let cp = h::base();
    for field in [
        "execution",
        "business",
        "skill",
        "version",
        "semantic",
        "ir",
        "runtime",
    ] {
        let mut s = cp.metadata().clone();
        match field {
            "execution" => s.execution_id.clear(),
            "business" => s.business_operation_id.clear(),
            "skill" => s.skill_id.clear(),
            "version" => s.skill_version.clear(),
            "semantic" => s.semantic_hash.clear(),
            "ir" => s.ir_version.clear(),
            _ => s.runtime_version.clear(),
        };
        assert!(ExecutionCheckpoint::create(s, cap).is_err());
    }
    let mut s = cp.metadata().clone();
    s.execution_id = "x".repeat(cap.outer.max_bytes + 1);
    assert!(ExecutionCheckpoint::create(s, cap).is_err());
    for version in ["0", "-1"] {
        let mut s = cp.metadata().clone();
        s.checkpoint_version = version.parse().unwrap();
        assert!(ExecutionCheckpoint::create(s, cap).is_err());
    }
    let mut s = cp.metadata().clone();
    s.checkpoint_version = "1".repeat(4301).parse().unwrap();
    assert_eq!(
        ExecutionCheckpoint::create(s, cap).unwrap_err().kind,
        CheckpointErrorKind::Encoding
    );
    for which in ["outer", "state"] {
        let mut l = cap;
        if which == "outer" {
            l.outer.max_bytes = 1
        } else {
            l.state.max_bytes = 1
        };
        assert!(cp.verify(l).is_err());
    }
    let request = h::request();
    let request = ExecutionRequest::new(
        "exec-checkpoint".into(),
        request.inputs().clone(),
        request.runtime_context().clone(),
        request.security_context().clone(),
        None,
    )
    .unwrap();
    let mut c = ExecutionContext::with_clock(h::skill("1-1"), request, h::Clock::default());
    assert_eq!(
        CheckpointCoordinator::for_oracle(cap)
            .capture(&mut c)
            .unwrap_err()
            .path,
        "$.business_operation_id"
    );
    let custom = CheckpointCoordinator::new("runtime-test".into(), cap).unwrap();
    let mut c = h::context("1-1", "empty", "0");
    let cp = custom.capture(&mut c).unwrap();
    assert_eq!(cp.metadata().runtime_version, "runtime-test");
    assert!(
        custom
            .restore(&cp, h::skill("1-1"), h::request(), h::Clock::default())
            .is_ok()
    );
}

#[test]
fn final_schema_and_preflight_edges_reject_before_serialization_or_restore() {
    use std::collections::BTreeMap;
    let cp = h::base();
    let base = cp.to_data(h::limits()).unwrap();
    for (field, value) in [
        ("integrity_hash", ValueData::String("bad".into())),
        ("integrity_hash", ValueData::String("sha256:x".into())),
        ("state_payload", ValueData::Number(1.into())),
        ("checkpoint_version", ValueData::Number(0.into())),
        ("execution_id", ValueData::Bool(true)),
    ] {
        let mut data = base.clone();
        h::obj(&mut data).insert(field.into(), value);
        assert!(
            ExecutionCheckpoint::from_bytes(&canonical_json(&data).unwrap(), h::limits()).is_err()
        );
    }
    let mut wrong = base.clone();
    let old = h::obj(&mut wrong).remove("execution_id").unwrap();
    h::obj(&mut wrong).insert("unknown".into(), old);
    assert!(
        ExecutionCheckpoint::from_bytes(&canonical_json(&wrong).unwrap(), h::limits()).is_err()
    );
    let mut data = h::raw_state(&cp);
    let ValueData::Array(checks) = h::at(&mut data, &["checks"]) else {
        panic!()
    };
    h::obj(&mut checks[0]).insert(
        "provenance_refs".into(),
        ValueData::Array(vec![ValueData::Null]),
    );
    assert!(restore_data(data).is_err());
    for recipe in ["envelopes", "counter", "journal", "journal-object"] {
        let mut l = h::limits();
        l.state.max_nodes = match recipe {
            "envelopes" | "journal" => 1,
            "counter" => 6,
            _ => 20,
        };
        let mut c = h::context("1-1", "empty", "0");
        match recipe {
            "envelopes" => c
                .bind(
                    BindingNamespace::Variable,
                    "v".into(),
                    ValueEnvelope::complete(
                        Value::Int(1.into()),
                        h::ty("Int"),
                        DataClassification::Internal,
                    ),
                )
                .unwrap(),
            "counter" => {
                for i in 0..3 {
                    c.bind(
                        BindingNamespace::Variable,
                        format!("v{i}"),
                        ValueEnvelope::complete(
                            Value::Int(1.into()),
                            h::ty("Int"),
                            DataClassification::Internal,
                        ),
                    )
                    .unwrap();
                }
            }
            "journal" => c.writes.entries = vec![ValueData::Null, ValueData::Null],
            _ => c.writes.entries.push(ValueData::Object(
                (0..21)
                    .map(|i| (i.to_string(), ValueData::Null))
                    .collect::<BTreeMap<_, _>>(),
            )),
        }
        let e = CheckpointCoordinator::for_oracle(l)
            .capture(&mut c)
            .unwrap_err();
        assert_eq!(e.kind, CheckpointErrorKind::Limit(JsonLimit::Nodes));
    }
}

#[test]
fn native_counter_and_header_extremes_are_bounded_before_copy_or_increment() {
    let mut limits = h::limits();
    limits.state.max_bytes = 1000;
    let mut ctx = h::context("1-1", "empty", "0");
    ctx.invocation_counter = "1".repeat(1001).parse().unwrap();
    assert!(
        CheckpointCoordinator::for_oracle(limits)
            .capture(&mut ctx)
            .is_err()
    );
    let mut ctx = h::context("1-1", "empty", "0");
    ctx.checkpoint.checkpoint_version = "1".repeat(4301).parse().unwrap();
    assert_eq!(
        CheckpointCoordinator::for_oracle(h::limits())
            .capture(&mut ctx)
            .unwrap_err()
            .kind,
        CheckpointErrorKind::Encoding
    );
    let req = h::request();
    let req = ExecutionRequest::new(
        "exec-checkpoint".into(),
        req.inputs().clone(),
        req.runtime_context().clone(),
        req.security_context().clone(),
        Some("b".repeat(1001)),
    )
    .unwrap();
    let mut ctx = ExecutionContext::with_clock(h::skill("1-1"), req, h::Clock::default());
    let mut limits = h::limits();
    limits.outer.max_bytes = 1000;
    assert!(
        CheckpointCoordinator::for_oracle(limits)
            .capture(&mut ctx)
            .is_err()
    );
}
