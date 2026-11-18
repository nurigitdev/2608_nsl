use super::*;
use crate::NsoCodec;
use nsl_core::canonical_json;
use std::collections::BTreeMap;

const LIMITS: NsoLimits = NsoLimits {
    max_bytes: 100_000,
    max_depth: 64,
    max_nodes: 10_000,
};
fn model() -> SkillObject {
    NsoCodec::decode(
        include_bytes!("../../../../../compatibility/v0_2_1/nso/ir-1-1-canonical/expected.nso"),
        LIMITS,
    )
    .unwrap()
}

#[test]
fn exact_json_units_match_canonical_writer_for_scalars_and_collections() {
    let document = ValueData::Object(BTreeMap::from([
        (
            "a\"\\\n".into(),
            ValueData::Array(vec![
                ValueData::Null,
                ValueData::Bool(true),
                ValueData::Bool(false),
                ValueData::Number((-123).into()),
            ]),
        ),
        (
            "unicode".into(),
            ValueData::String("한글😀\\\"\n\r\t\u{8}\u{c}\0\u{1}\u{1f}\u{2028}".into()),
        ),
        ("z".into(), ValueData::Object(BTreeMap::new())),
    ]));
    let expected = canonical_json(&document).unwrap();
    let exact = NsoLimits {
        max_bytes: expected.len(),
        max_depth: 2,
        max_nodes: 8,
    };
    assert_eq!(canonical(&document, exact).unwrap(), expected);
    for (limits, kind) in [
        (
            NsoLimits {
                max_bytes: exact.max_bytes - 1,
                ..exact
            },
            NsoLimit::Bytes,
        ),
        (
            NsoLimits {
                max_depth: 1,
                ..exact
            },
            NsoLimit::Depth,
        ),
        (
            NsoLimits {
                max_nodes: 7,
                ..exact
            },
            NsoLimit::Nodes,
        ),
        (
            NsoLimits {
                max_nodes: 0,
                ..exact
            },
            NsoLimit::Nodes,
        ),
    ] {
        assert_eq!(
            canonical(&document, limits),
            Err(NsoCodecError::Limit(kind))
        );
    }
    for scalar in [
        ValueData::Null,
        ValueData::Bool(true),
        ValueData::Array(vec![]),
        ValueData::Object(BTreeMap::new()),
        ValueData::String(String::new()),
    ] {
        let expected = canonical_json(&scalar).unwrap();
        assert_eq!(
            canonical(
                &scalar,
                NsoLimits {
                    max_bytes: expected.len(),
                    max_depth: 0,
                    max_nodes: 1
                }
            )
            .unwrap(),
            expected
        );
    }
    // All byte cutoffs, including the middle of UTF-8 and six-byte escapes,
    // must fail instead of yielding a partial artifact.
    for max_bytes in 0..expected.len() {
        assert_eq!(
            canonical(
                &document,
                NsoLimits {
                    max_bytes,
                    ..LIMITS
                }
            ),
            Err(NsoCodecError::Limit(NsoLimit::Bytes))
        );
    }
    let integer = ValueData::Number("1".repeat(4301).parse().unwrap());
    assert_eq!(
        canonical(&integer, LIMITS),
        Err(NsoCodecError::IntegerTooWide)
    );
}

#[test]
fn bounded_encoder_preflights_models_and_keeps_validation_errors() {
    let skill = model();
    for (limits, kind) in [
        (
            NsoLimits {
                max_bytes: 0,
                ..LIMITS
            },
            NsoLimit::Bytes,
        ),
        (
            NsoLimits {
                max_nodes: 0,
                ..LIMITS
            },
            NsoLimit::Nodes,
        ),
        (
            NsoLimits {
                max_depth: 3,
                ..LIMITS
            },
            NsoLimit::Depth,
        ),
        (
            NsoLimits {
                max_nodes: 1,
                ..LIMITS
            },
            NsoLimit::Nodes,
        ),
        (
            NsoLimits {
                max_nodes: 2,
                ..LIMITS
            },
            NsoLimit::Nodes,
        ),
    ] {
        assert_eq!(
            NsoCodec::encode_with_limits(&skill, limits),
            Err(NsoCodecError::Limit(kind))
        );
    }
    let mut skill = skill;
    skill.body = vec![Statement {
        node_id: "stmt0001".into(),
        kind: StatementKind::Let {
            target_symbol_id: "s0001".into(),
            value: Expression {
                node_id: "expr0001".into(),
                type_info: TypeRef::new(TypeKind::Primitive("String".into())).unwrap(),
                kind: ExpressionKind::Literal(LiteralValue::String("x".repeat(200))),
            },
        },
    }];
    assert_eq!(
        preflight(
            &skill,
            NsoLimits {
                max_bytes: 100,
                ..LIMITS
            }
        ),
        Err(NsoCodecError::Limit(NsoLimit::Bytes))
    );
    skill.inputs.clear();
    skill.contexts.clear();
    skill.outputs.clear();
    skill.symbols.clear();
    assert_eq!(
        preflight(
            &skill,
            NsoLimits {
                max_nodes: 1,
                ..LIMITS
            }
        ),
        Err(NsoCodecError::Limit(NsoLimit::Nodes))
    );
    let StatementKind::Let { value, .. } = &mut skill.body[0].kind else {
        unreachable!()
    };
    value.type_info = TypeRef::new(TypeKind::Optional(Box::new(
        TypeRef::new(TypeKind::Primitive("Int".into())).unwrap(),
    )))
    .unwrap();
    assert!(matches!(
        NsoCodec::encode_with_limits(&skill, LIMITS),
        Err(NsoCodecError::Type(_))
    ));
    value_type_to_int(&mut skill);
    skill.limits.tool_calls = (-1).into();
    let error = NsoCodec::encode_with_limits(&skill, LIMITS).unwrap_err();
    assert!(matches!(error, NsoCodecError::Validation(_)), "{error:?}");
}
fn value_type_to_int(skill: &mut SkillObject) {
    let StatementKind::Let { value, .. } = &mut skill.body[0].kind else {
        unreachable!()
    };
    value.type_info = TypeRef::new(TypeKind::Primitive("String".into())).unwrap();
}
