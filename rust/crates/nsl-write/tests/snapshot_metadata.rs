mod support;
use nsl_core::*;
use nsl_write::*;
use std::collections::BTreeMap;
use support as h;
fn ty(name: &str) -> TypeRef {
    TypeRef::new(TypeKind::Primitive(name.into())).unwrap()
}
fn args(kind: TypeRef, provenance: Vec<String>) -> BTreeMap<String, ValueEnvelope> {
    let d = h::baseline();
    let a = h::arguments(h::field(&d, "arguments"));
    BTreeMap::from([(
        "detail".into(),
        ValueEnvelope::new(
            a["detail"].value().clone(),
            kind,
            Presence::Empty,
            Completeness::Unknown,
            DataClassification::Public,
            provenance,
        ),
    )])
}
fn spec(a: BTreeMap<String, ValueEnvelope>) -> WriteIntentSpec {
    let mut s = h::built().metadata().clone();
    s.canonical_arguments = a;
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
    s
}
fn checked(
    s: &WriteIntentSpec,
    limits: WriteSnapshotLimits,
) -> Result<WriteIntent, WriteIntentError> {
    WriteIntent::new(s, limits)
}
#[test]
fn metadata_has_independent_exact_depth_node_and_utf8_text_budgets() {
    // Map=1; envelope=1; type/status*3/provenance-list=5. Type names and argument
    // names count text bytes, not extra nodes; provenance/enum strings are leaves.
    let samples = [
        (args(ty("Int"), vec![]), 7usize, 2usize, 9usize),
        (
            args(
                TypeRef::new(TypeKind::Domain("Payload".into())).unwrap(),
                vec![],
            ),
            7,
            2,
            13,
        ),
        (
            args(
                TypeRef::new(TypeKind::Money("USD".parse().unwrap())).unwrap(),
                vec![],
            ),
            7,
            2,
            9,
        ),
        (
            args(
                TypeRef::new(TypeKind::List(Box::new(ty("Int")))).unwrap(),
                vec![],
            ),
            8,
            3,
            9,
        ),
        (
            args(
                TypeRef::new(TypeKind::Optional(Box::new(ty("Int")))).unwrap(),
                vec![],
            ),
            8,
            3,
            9,
        ),
        (
            args(
                TypeRef::new(TypeKind::Record {
                    name: "R".into(),
                    fields: vec![("a".into(), ty("Int")), ("b".into(), ty("String"))],
                })
                .unwrap(),
                vec![],
            ),
            9,
            3,
            18,
        ),
        (
            args(
                TypeRef::new(TypeKind::Enum {
                    name: "E".into(),
                    values: vec!["A".into(), "B".into()],
                })
                .unwrap(),
                vec![],
            ),
            9,
            3,
            9,
        ),
        (args(ty("Int"), vec!["가😀".into()]), 8, 3, 16),
        (BTreeMap::new(), 1, 0, 0),
    ];
    for (arguments, nodes, depth, text) in samples {
        let s = spec(arguments);
        let canonical = canonical_write_arguments(&s.canonical_arguments, h::limits()).unwrap();
        for (kind, max) in [("node", nodes), ("depth", depth), ("text byte", text)] {
            for budget in [0, 1, max.saturating_sub(1), max, max + 1] {
                let mut limits = h::snapshot(h::limits());
                match kind {
                    "node" => limits.metadata.max_nodes = budget,
                    "depth" => limits.metadata.max_depth = budget,
                    _ => limits.metadata.max_text_bytes = budget,
                }
                let result = checked(&s, limits);
                if budget < max {
                    let e = result.unwrap_err();
                    assert_eq!(e.kind, WriteIntentErrorKind::Type);
                    assert_eq!(e.detail_code(), None);
                    assert!(e.message.contains(kind), "{kind}/{budget}: {e}");
                    assert!(e.path.starts_with("$.canonical_arguments"));
                    assert!(std::error::Error::source(&e).is_none());
                } else {
                    let i = result.unwrap();
                    assert_eq!(i.canonical_bytes(), canonical);
                    assert_eq!(i.payload_hash().as_str(), s.payload_hash);
                }
            }
        }
    }
}
#[test]
fn original_oracle_metadata_hashes_are_retained_and_oversize_snapshot_differences_are_explicit() {
    let d = h::baseline();
    let (skill, expression, _) = h::setup(&d);
    let mut count = 0;
    for line in include_str!("fixtures/write-metadata-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let depth: usize = f[1].parse().unwrap();
        let refs = h::parse(&h::decode(f[2]));
        let refs = h::arr(&refs)
            .iter()
            .map(|v| h::text(v).to_owned())
            .collect();
        let mut kind = ty("Int");
        match f[0] {
            "primitive" => {}
            "domain" => kind = TypeRef::new(TypeKind::Domain("Payload".into())).unwrap(),
            "list" | "optional" => {
                for _ in 0..depth {
                    kind = TypeRef::new(if f[0] == "list" {
                        TypeKind::List(Box::new(kind))
                    } else {
                        TypeKind::Optional(Box::new(kind))
                    })
                    .unwrap();
                }
            }
            "record" => {
                kind = TypeRef::new(TypeKind::Record {
                    name: "R".into(),
                    fields: vec![
                        ("description".into(), ty("String")),
                        ("amount".into(), ty("Decimal")),
                    ],
                })
                .unwrap()
            }
            "enum" => {
                kind = TypeRef::new(TypeKind::Enum {
                    name: "E".into(),
                    values: (0..if depth == 0 { 3 } else { depth })
                        .map(|n| n.to_string())
                        .collect(),
                })
                .unwrap()
            }
            "money" => kind = TypeRef::new(TypeKind::Money("USD".parse().unwrap())).unwrap(),
            _ => panic!(),
        }
        let arguments = args(kind, refs);
        let original = h::parse(&h::decode(f[3]));
        assert_eq!(h::arr(&original)[0], h::txt("ok"));
        assert_eq!(
            h::hashes(
                &arguments,
                skill.semantic_hash.as_str(),
                &expression.node_id,
                &skill.required_tools[0].tool_id
            ),
            h::arr(&original)[1]
        );
        let mut limits = h::snapshot(h::limits());
        limits.metadata = SnapshotMetadataLimits {
            max_depth: 16,
            max_nodes: 64,
            max_text_bytes: 64,
        };
        let result =
            WriteIntentBuilder.build(&expression, &arguments, h::context(&d, &skill), limits);
        if depth == 256
            || h::arr(&h::parse(&h::decode(f[2])))
                .iter()
                .any(|v| h::text(v).len() > 64)
        {
            let error = result.unwrap_err();
            assert_eq!(error.kind, WriteIntentErrorKind::Type);
            assert_eq!(error.detail_code(), None);
            assert!(error.path.starts_with("$.canonical_arguments[\"detail\"]"));
            assert!(error.message.contains("snapshot metadata"));
        } else {
            assert_eq!(
                result.unwrap().payload_hash().as_str(),
                h::get(&h::arr(&original)[1], "payload_hash")
            );
        }
        count += 1;
    }
    assert_eq!(count, 10);
}
#[test]
fn deep_native_metadata_is_rejected_on_small_stack_before_builder_or_constructor_can_clone() {
    // Ownership/drop of an intentionally unbounded native fixture stays on a large
    // stack. Only the borrowed boundary runs on the original 512 KiB reproduction
    // stack; no leaks, deliberate abort or unbounded metadata clone is needed.
    std::thread::Builder::new()
        .stack_size(64 * 1024 * 1024)
        .spawn(|| {
            let d = h::baseline();
            let (skill, expression, _) = h::setup(&d);
            let mut nested = ty("Int");
            for _ in 0..8192 {
                nested = TypeRef::new(TypeKind::List(Box::new(nested))).unwrap();
            }
            let arguments = args(nested, vec![]);
            let s = spec(arguments);
            let before = canonical_write_arguments(&s.canonical_arguments, h::limits()).unwrap();
            let mut limits = h::snapshot(h::limits());
            limits.metadata.max_depth = 8;
            std::thread::scope(|scope| {
                std::thread::Builder::new()
                    .stack_size(512 * 1024)
                    .spawn_scoped(scope, || {
                        let e = WriteIntentBuilder
                            .build(
                                &expression,
                                &s.canonical_arguments,
                                h::context(&d, &skill),
                                limits,
                            )
                            .unwrap_err();
                        assert_eq!(e.kind, WriteIntentErrorKind::Type);
                        assert!(e.path.contains(".type_ref.item"));
                        assert!(e.message.contains("depth"));
                        let e = checked(&s, limits).unwrap_err();
                        assert_eq!(e.kind, WriteIntentErrorKind::Type);
                        assert!(e.message.contains("depth"));
                    })
                    .unwrap()
                    .join()
                    .unwrap();
            });
            assert_eq!(
                canonical_write_arguments(&s.canonical_arguments, h::limits()).unwrap(),
                before
            );
        })
        .unwrap()
        .join()
        .unwrap();
}
#[test]
fn wide_metadata_and_provenance_are_bounded_and_error_paths_are_safe() {
    let variants = [
        args(
            TypeRef::new(TypeKind::Record {
                name: "R".into(),
                fields: (0..256).map(|i| (i.to_string(), ty("Int"))).collect(),
            })
            .unwrap(),
            vec![],
        ),
        args(
            TypeRef::new(TypeKind::Enum {
                name: "E".into(),
                values: (0..256).map(|i| i.to_string()).collect(),
            })
            .unwrap(),
            vec![],
        ),
        args(ty("Int"), vec!["p".into(); 256]),
    ];
    for a in variants {
        let s = spec(a);
        let mut limits = h::snapshot(h::limits());
        limits.metadata.max_nodes = 32;
        let e = checked(&s, limits).unwrap_err();
        assert_eq!(e.kind, WriteIntentErrorKind::Type);
        assert!(e.message.contains("node"));
    }
    let variants = [
        args(ty(&"x".repeat(1024)), vec![]),
        args(ty("Int"), vec!["x".repeat(1024)]),
        args(
            TypeRef::new(TypeKind::Record {
                name: "x".repeat(1024),
                fields: vec![],
            })
            .unwrap(),
            vec![],
        ),
        args(
            TypeRef::new(TypeKind::Record {
                name: "R".into(),
                fields: vec![("x".repeat(1024), ty("Int"))],
            })
            .unwrap(),
            vec![],
        ),
        args(
            TypeRef::new(TypeKind::Enum {
                name: "E".into(),
                values: vec!["x".repeat(1024)],
            })
            .unwrap(),
            vec![],
        ),
    ];
    for a in variants {
        let s = spec(a);
        let mut limits = h::snapshot(h::limits());
        limits.metadata.max_text_bytes = 32;
        let e = checked(&s, limits).unwrap_err();
        assert_eq!(e.kind, WriteIntentErrorKind::Type);
        assert!(e.message.contains("text byte"));
    }
    let a = args(
        TypeRef::new(TypeKind::Record {
            name: "R".into(),
            fields: vec![("Bearer synthetic-only".into(), ty("Int"))],
        })
        .unwrap(),
        vec![],
    );
    let s = spec(a);
    let mut limits = h::snapshot(h::limits());
    limits.metadata.max_depth = 2;
    let e = checked(&s, limits).unwrap_err();
    assert!(!format!("{e:?} {e}").contains("synthetic"));
    assert_eq!(e.detail_code(), None);
    let mut a = args(ty("Int"), vec![]);
    let env = a.remove("detail").unwrap();
    a.insert("x".repeat(1024), env);
    let s = spec(a);
    let mut limits = h::snapshot(h::limits());
    limits.metadata.max_text_bytes = 32;
    assert_eq!(
        checked(&s, limits).unwrap_err().path,
        "$.canonical_arguments"
    );
}
