mod support;
use nsl_core::*;
use nsl_ir::*;
use nsl_write::*;
use std::collections::BTreeMap;
use support as h;
#[test]
fn canonical_typed_values_bytes_and_domain_separated_hashes_match_frozen_oracle() {
    let mut count = 0;
    for line in include_str!("fixtures/write-values-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let a = h::arguments(&h::parse(&h::decode(f[1])));
        let expected = h::parse(&h::decode(f[2]));
        assert_eq!(h::arr(&expected)[0], h::txt("ok"));
        assert_eq!(
            h::hashes(
                &a,
                &format!("sha256:{}", "a".repeat(64)),
                "write0001",
                "FINANCE.CREATE_DRAFT"
            ),
            h::arr(&expected)[1],
            "{}",
            f[0]
        );
        count += 1;
    }
    assert_eq!(count, 36);
}
#[test]
fn builder_binds_identity_declared_contract_and_argument_names_like_fixed_oracle() {
    let mut count = 0;
    for line in include_str!("fixtures/write-models-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let d = h::parse(&h::decode(f[1]));
        let expected = h::parse(&h::decode(f[2]));
        let (skill, e, a) = h::setup(&d);
        match WriteIntentBuilder.build(&e, &a, h::context(&d, &skill), h::snapshot(h::limits())) {
            Ok(i) => {
                assert_eq!(h::arr(&expected)[0], h::txt("ok"));
                let mut observed = h::hashes(
                    &a,
                    &skill.semantic_hash.to_string(),
                    &e.node_id,
                    &i.metadata().tool_id,
                );
                let ValueData::Object(m) = &mut observed else {
                    panic!()
                };
                m.insert("model".into(), h::model(&i));
                assert_eq!(&observed, &h::arr(&expected)[1], "{}", f[0]);
                assert_eq!(
                    i.canonical_bytes(),
                    canonical_write_arguments(&a, h::limits()).unwrap()
                );
            }
            Err(err) => {
                assert_eq!(h::arr(&expected)[0], h::txt("error"), "{}: {err}", f[0]);
                let path = match f[0] {
                    "missing-business" => "$.business_operation_id",
                    "missing-args" | "extra-args" | "wrong-name" => "$.arguments",
                    _ => "$.tool_ref",
                };
                assert_eq!(err.path, path);
                assert_eq!(err.kind, WriteIntentErrorKind::Identity);
                assert!(std::error::Error::source(&err).is_none());
            }
        }
        count += 1;
    }
    assert_eq!(count, 19);
}
#[test]
fn all_recomputable_frozen_write_and_idempotency_goldens_keep_their_original_hashes() {
    let mut count = 0;
    for line in include_str!("fixtures/write-goldens-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let d = h::parse(&h::decode(f[2]));
        let expected = h::parse(&h::decode(f[3]));
        let a = h::arguments(h::field(&d, "canonical_arguments"));
        let observed = h::hashes(
            &a,
            h::get(&d, "skill_semantic_hash"),
            h::get(&d, "node_id"),
            h::get(&d, "tool_id"),
        );
        assert_eq!(observed, h::arr(&expected)[1], "{}/{}", f[0], f[1]);
        assert_eq!(
            h::get(&observed, "payload_hash"),
            h::get(&d, "payload_hash")
        );
        assert_eq!(
            h::get(&observed, "write_intent_hash"),
            h::get(&d, "write_intent_hash")
        );
        count += 1;
    }
    assert_eq!(count, 6);
}
#[test]
fn invalid_canonical_values_and_identity_fields_reject_at_the_typed_boundary() {
    let mut count = 0;
    for line in include_str!("fixtures/write-negative-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let expected = h::parse(&h::decode(f.last().unwrap()));
        if f.len() == 3 {
            let field = f[0];
            let v = h::parse(&h::decode(f[1]));
            let v = h::text(&v);
            let good = format!("sha256:{}", "a".repeat(64));
            let good_payload = format!("sha256:{}", "b".repeat(64));
            let input = [
                if field == "skill_semantic_hash" {
                    v
                } else {
                    &good
                },
                if field == "write_node_id" {
                    v
                } else {
                    "write0001"
                },
                if field == "tool_id" {
                    v
                } else {
                    "FINANCE.CREATE_DRAFT"
                },
                if field == "payload_hash" {
                    v
                } else {
                    &good_payload
                },
            ];
            match compute_write_intent_hash(input[0], input[1], input[2], input[3]) {
                Ok(hash) => {
                    assert_eq!(h::arr(&expected)[0], h::txt("ok"));
                    assert_eq!(h::txt(hash.as_str()), h::arr(&expected)[1]);
                }
                Err(e) => {
                    assert_eq!(h::arr(&expected)[0], h::txt("error"));
                    assert_eq!(e.kind, WriteIntentErrorKind::Identity);
                    assert_eq!(e.path, format!("$.{field}"));
                    assert!(!format!("{e:?} {e}").contains("synthetic"));
                }
            }
        } else {
            match f[0] {
                "nan" | "inf" | "neg-inf" => assert!(
                    ["NaN", "Infinity", "-Infinity"]
                        .iter()
                        .all(|v| v.parse::<NslDecimal>().is_err())
                ),
                "naive" => {
                    let e = canonical_write_arguments(
                        &h::one(Value::DateTime("2026-10-20T00:00:00".into())),
                        h::limits(),
                    )
                    .unwrap_err();
                    assert_eq!(e.kind, WriteIntentErrorKind::Type);
                    assert!(e.message.contains("timezone-aware"));
                }
                "int-4300" | "int-4301" => {
                    let digits: usize = f[0][4..].parse().unwrap();
                    let a = h::one(Value::Int(
                        format!("1{}", "0".repeat(digits - 1)).parse().unwrap(),
                    ));
                    let result = compute_payload_hash(&a, h::limits());
                    if digits == 4300 {
                        assert!(result.is_ok());
                        assert_eq!(h::arr(&expected)[0], h::txt("ok"));
                    } else {
                        assert_eq!(
                            result.unwrap_err().kind,
                            WriteIntentErrorKind::IntegerTooWide
                        );
                        assert_eq!(h::arr(&expected)[0], h::txt("error"));
                    }
                }
                // Float/bytes/set and cyclic containers cannot inhabit the owned Core Value
                // enum. Keep their original Oracle rejections; no string coercion is added.
                "float" | "bytes" | "set" => assert_eq!(h::arr(&expected)[0], h::txt("error")),
                _ => panic!(),
            }
        }
        count += 1;
    }
    assert_eq!(count, 25);
}

#[test]
fn forged_intents_and_approval_refresh_errors_match_oracle_without_exposing_payloads() {
    let mut count = 0;
    let base = h::built();
    for line in include_str!("fixtures/write-admission-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let input = h::parse(&h::decode(f[2]));
        let expected = h::parse(&h::decode(f[3]));
        let result = if f[0] == "model" {
            let mut spec = base.metadata().clone();
            let v = h::text(&input).to_string();
            match f[1] {
                "tenant_id" => spec.tenant_id = v,
                "business_operation_id" => spec.business_operation_id = v,
                "node_id" => spec.node_id = v,
                "tool_ref" => spec.tool_ref = v,
                "tool_id" => spec.tool_id = v,
                "approval_id" => spec.approval_id = v,
                "skill_semantic_hash" => spec.skill_semantic_hash = v,
                "payload_hash" => spec.payload_hash = v,
                "write_intent_hash" => spec.write_intent_hash = v,
                _ => panic!(),
            };
            WriteIntent::new(&spec, h::snapshot(h::limits())).map(|_| ())
        } else {
            base.require_approval_identity(&h::Proof {
                payload: h::get(&input, "payload").into(),
                intent: h::get(&input, "intent").into(),
            })
        };
        match result {
            Ok(()) => assert_eq!(h::arr(&expected), [h::txt("ok"), ValueData::Null]),
            Err(e) => {
                assert_eq!(h::arr(&expected)[0], h::txt("error"));
                assert!(!format!("{e:?} {e}").contains("synthetic"));
                assert!(std::error::Error::source(&e).is_none());
                if h::arr(&expected)[3] == h::txt("NEW_APPROVAL_REQUIRED") {
                    assert_eq!(e.kind, WriteIntentErrorKind::ApprovalRefreshRequired);
                    assert_eq!(e.detail_code(), Some("NEW_APPROVAL_REQUIRED"));
                    assert_eq!(e.path, "$.approval");
                } else {
                    assert_eq!(e.detail_code(), None);
                    assert!(matches!(
                        e.kind,
                        WriteIntentErrorKind::Identity | WriteIntentErrorKind::HashMismatch
                    ));
                    assert_eq!(
                        e.path,
                        if f[0] == "model" {
                            format!(
                                "$.{}",
                                if f[1] == "skill_semantic_hash"
                                    && e.kind == WriteIntentErrorKind::HashMismatch
                                {
                                    "write_intent_hash"
                                } else {
                                    f[1]
                                }
                            )
                        } else if f[1] == "malformed-payload" {
                            "$.approval.payload_hash".into()
                        } else {
                            "$.approval.write_intent_hash".into()
                        }
                    );
                }
            }
        }
        count += 1;
    }
    assert_eq!(count, 33);
}
fn shape(d: &ValueData) -> (usize, usize) {
    let mut stack = vec![(d, 0)];
    let mut nodes = 0;
    let mut max = 0;
    while let Some((d, depth)) = stack.pop() {
        nodes += 1;
        max = max.max(depth);
        match d {
            ValueData::Array(a) => stack.extend(a.iter().map(|v| (v, depth + 1))),
            ValueData::Object(o) => stack.extend(o.values().map(|v| (v, depth + 1))),
            _ => {}
        }
    }
    (nodes, max)
}
#[test]
fn exact_output_byte_depth_node_and_decimal_budgets_cover_zero_one_and_limit_neighbors() {
    let samples = [
        BTreeMap::new(),
        h::one(Value::String("\"\n\0😀".into())),
        h::one(Value::Decimal("1.2300".parse().unwrap())),
        h::one(Value::Money(Money::new(
            "-0.00".parse().unwrap(),
            "USD".parse().unwrap(),
        ))),
        h::one(Value::List(vec![
            Value::Record(BTreeMap::from([("a".into(), Value::Int(1.into()))])),
            Value::Null,
        ])),
    ];
    for a in samples {
        let bytes = canonical_write_arguments(&a, h::limits()).unwrap();
        let (nodes, depth) = shape(&h::parse(&bytes));
        for (kind, max) in [
            (JsonLimit::Bytes, bytes.len()),
            (JsonLimit::Depth, depth),
            (JsonLimit::Nodes, nodes),
        ] {
            for budget in [0, 1, max.saturating_sub(1), max, max + 1] {
                let mut l = h::limits();
                match kind {
                    JsonLimit::Bytes => l.canonical.max_bytes = budget,
                    JsonLimit::Depth => l.canonical.max_depth = budget,
                    JsonLimit::Nodes => l.canonical.max_nodes = budget,
                }
                let result = canonical_write_arguments(&a, l);
                if budget < max {
                    let e = result.unwrap_err();
                    assert_eq!(e.kind, WriteIntentErrorKind::Limit(kind));
                    assert!(e.path.starts_with('$'));
                } else {
                    assert_eq!(result.unwrap(), bytes);
                }
            }
        }
    }
    for amount in [
        Value::Decimal("1.2300".parse().unwrap()),
        Value::Money(Money::new(
            "12.500".parse().unwrap(),
            "USD".parse().unwrap(),
        )),
    ] {
        for budget in [0, 1, 5, 6, 7] {
            let mut limits = h::limits();
            limits.max_decimal_bytes = budget;
            let r = canonical_write_arguments(&h::one(amount.clone()), limits);
            assert_eq!(r.is_ok(), budget >= 6);
            if let Err(e) = r {
                assert_eq!(e.kind, WriteIntentErrorKind::Limit(JsonLimit::Bytes));
            }
        }
    }
}
#[test]
fn resource_preflight_bounds_borrowed_graphs_before_freezing_or_expanding_values() {
    let mut deep = Value::Null;
    for _ in 0..512 {
        deep = Value::List(vec![deep]);
    }
    let mut l = h::limits();
    l.canonical.max_depth = 8;
    assert_eq!(
        canonical_write_arguments(&h::one(deep), l)
            .unwrap_err()
            .kind,
        WriteIntentErrorKind::Limit(JsonLimit::Depth)
    );
    for value in [
        Value::List(vec![Value::Null; 32]),
        Value::Record((0..32).map(|i| (i.to_string(), Value::Null)).collect()),
    ] {
        let mut l = h::limits();
        l.canonical.max_nodes = 16;
        assert_eq!(
            canonical_write_arguments(&h::one(value), l)
                .unwrap_err()
                .kind,
            WriteIntentErrorKind::Limit(JsonLimit::Nodes)
        );
    }
    for value in [
        Value::Decimal("1e1000000".parse().unwrap()),
        Value::Money(Money::new(
            "1e1000000".parse().unwrap(),
            "USD".parse().unwrap(),
        )),
    ] {
        let mut l = h::limits();
        l.canonical.max_bytes = 100;
        assert_eq!(
            canonical_write_arguments(&h::one(value), l)
                .unwrap_err()
                .kind,
            WriteIntentErrorKind::Limit(JsonLimit::Bytes)
        );
    }
    for a in [
        BTreeMap::from([(
            "x".repeat(64),
            h::one(Value::Null).remove("payload").unwrap(),
        )]),
        h::one(Value::Record(BTreeMap::from([(
            "x".repeat(64),
            Value::Null,
        )]))),
        h::one(Value::String("x".repeat(64))),
        h::one(Value::Int("12345678901234567890".parse().unwrap())),
        h::one(Value::Record(BTreeMap::from([(
            "a".into(),
            Value::String("x".repeat(64)),
        )]))),
    ] {
        let mut l = h::limits();
        l.canonical.max_bytes = 16;
        assert_eq!(
            canonical_write_arguments(&a, l).unwrap_err().kind,
            WriteIntentErrorKind::Limit(JsonLimit::Bytes)
        );
    }
    let d = h::baseline();
    let (skill, expr, a) = h::setup(&d);
    let mut l = h::limits();
    l.canonical.max_bytes = 0;
    assert_eq!(
        WriteIntentBuilder
            .build(&expr, &a, h::context(&d, &skill), h::snapshot(l))
            .unwrap_err()
            .kind,
        WriteIntentErrorKind::Limit(JsonLimit::Bytes)
    );
}
#[test]
fn business_temporal_values_require_valid_calendar_and_aware_time_preserving_oracle_normalization()
{
    for value in [
        Value::Date("2026-02-29".into()),
        Value::DateTime("not-a-date".into()),
        Value::DateTime("2026-10-20T01:02:03+24:00".into()),
        Value::List(vec![Value::DateTime("2026-10-20T01:02:03".into())]),
    ] {
        let e = canonical_write_arguments(&h::one(value), h::limits()).unwrap_err();
        assert_eq!(e.kind, WriteIntentErrorKind::Type);
        assert!(e.path.starts_with("$[\"payload\"]"));
    }
    for (raw, canonical) in [
        ("20261020T010203Z", "2026-10-20T01:02:03+00:00"),
        (
            "2026-10-20T01:02:03.123456789-00:01:00.123456",
            "2026-10-20T01:02:03.123456-00:01:00.123456",
        ),
        (
            "2026-10-20T01:02:03+00:00:00.123456",
            "2026-10-20T01:02:03+00:00:00.123456",
        ),
    ] {
        let a =
            canonical_write_arguments(&h::one(Value::DateTime(raw.into())), h::limits()).unwrap();
        assert_eq!(
            a,
            canonical_write_arguments(&h::one(Value::DateTime(canonical.into())), h::limits())
                .unwrap()
        );
    }
    assert_eq!(
        canonical_write_arguments(&h::one(Value::Date("20261020".into())), h::limits()).unwrap(),
        canonical_write_arguments(&h::one(Value::Date("2026-10-20".into())), h::limits()).unwrap()
    );
}
#[test]
fn snapshots_are_owned_and_deeply_immutable_and_builder_rejects_non_write_or_duplicate_names() {
    let d = h::baseline();
    let (mut skill, mut expr, mut a) = h::setup(&d);
    let i = WriteIntentBuilder
        .build(&expr, &a, h::context(&d, &skill), h::snapshot(h::limits()))
        .unwrap();
    let before = i.clone();
    a.clear();
    assert_eq!(i, before);
    assert!(!i.canonical_arguments().is_empty());
    assert!(format!("{i:?} {:?}", i.metadata()).contains(".."));
    assert!(!format!("{i:?} {:?}", i.metadata()).contains("KTX"));
    assert!(format!("{:?}", WriteIntentBuilder).contains("WriteIntentBuilder"));
    let (_, _, a) = h::setup(&d);
    let ExpressionKind::Write(write) = &mut expr.kind else {
        panic!()
    };
    write.arguments.push(write.arguments[0].clone());
    assert_eq!(
        WriteIntentBuilder
            .build(&expr, &a, h::context(&d, &skill), h::snapshot(h::limits()))
            .unwrap_err()
            .path,
        "$.arguments"
    );
    expr.kind = ExpressionKind::Literal(LiteralValue::Bool(true));
    assert_eq!(
        WriteIntentBuilder
            .build(&expr, &a, h::context(&d, &skill), h::snapshot(h::limits()))
            .unwrap_err()
            .kind,
        WriteIntentErrorKind::Type
    );
    let (_, expr, a) = h::setup(&d);
    skill.required_tools[0].kind = RequiredToolKind::ReadV1_0;
    assert_eq!(
        WriteIntentBuilder
            .build(&expr, &a, h::context(&d, &skill), h::snapshot(h::limits()))
            .unwrap_err()
            .path,
        "$.tool_ref"
    );
    let mut spec = i.metadata().clone();
    spec.business_operation_id = "other-operation".into();
    spec.tenant_id = "other-tenant".into();
    spec.approval_id = "OTHER_APPROVAL".into();
    let other = WriteIntent::new(&spec, h::snapshot(h::limits())).unwrap();
    assert_eq!(other.payload_hash(), i.payload_hash());
    assert_eq!(other.write_intent_hash(), i.write_intent_hash());
    other
        .require_approval_identity(&h::Proof {
            payload: i.payload_hash().to_string(),
            intent: i.write_intent_hash().to_string(),
        })
        .unwrap(); // Hash-only check is not full approval authorization.
    let mut spec = i.metadata().clone();
    spec.tenant_id = "\u{1c}".into();
    assert_eq!(
        WriteIntent::new(&spec, h::snapshot(h::limits()))
            .unwrap_err()
            .kind,
        WriteIntentErrorKind::Identity
    );
}

#[test]
fn changed_arguments_reject_stale_hashes_empty_arguments_remain_valid_and_errors_are_redacted() {
    let base = h::built();
    let mut s = base.metadata().clone();
    s.canonical_arguments = h::one(Value::Bool(false));
    let e = WriteIntent::new(&s, h::snapshot(h::limits())).unwrap_err();
    assert_eq!(e.kind, WriteIntentErrorKind::HashMismatch);
    assert_eq!(e.path, "$.payload_hash");
    assert_eq!(e, e.clone());
    let mut s = base.metadata().clone();
    s.canonical_arguments.clear();
    s.payload_hash = compute_payload_hash(&s.canonical_arguments, h::limits())
        .unwrap()
        .to_string();
    s.write_intent_hash = compute_write_intent_hash(
        &s.skill_semantic_hash,
        &s.node_id,
        &s.tool_id,
        &s.payload_hash,
    )
    .unwrap()
    .to_string();
    let empty = WriteIntent::new(&s, h::snapshot(h::limits())).unwrap();
    assert_eq!(empty.canonical_bytes(), b"{}");
    let envelope = h::one(Value::DateTime("2026-10-20T01:02:03".into()))
        .remove("payload")
        .unwrap();
    let a = BTreeMap::from([("Bearer synthetic-only".into(), envelope)]);
    let e = canonical_write_arguments(&a, h::limits()).unwrap_err();
    assert_eq!(e.kind, WriteIntentErrorKind::Type);
    assert!(!format!("{e:?} {e}").contains("synthetic"));
    assert!(std::error::Error::source(&e).is_none());
}
