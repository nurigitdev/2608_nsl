use nsl_core::{DecimalError, TypeError, TypeKind};
use nsl_ir::{
    ExpressionKind, LiteralValue, NsoCodec, NsoCodecError, NsoLimit, NsoLimits, NsoValidationError,
    StatementKind,
};

use super::{ir_1_0, ir_1_1, literal_cases, ty};

const READ: &[u8] =
    include_bytes!("../../../../../compatibility/v0_2_1/nso/ir-1-0-canonical/expected.nso");
const WRITE: &[u8] =
    include_bytes!("../../../../../compatibility/v0_2_1/nso/ir-1-1-canonical/expected.nso");
const SCALARS: &[u8] = include_bytes!("../fixtures/canonical-scalars.nso");
const LIMITS: NsoLimits = NsoLimits {
    max_bytes: 24_000,
    max_depth: 64,
    max_nodes: 2_000,
};

#[test]
fn typed_model_encode_and_decode_match_both_frozen_nsos() {
    for (bytes, model) in [(READ, ir_1_0()), (WRITE, ir_1_1())] {
        assert_eq!(NsoCodec::encode(&model, 1024).unwrap(), bytes);
        let decoded = NsoCodec::decode(bytes, LIMITS).unwrap();
        assert_eq!(decoded, model);
        assert_eq!(NsoCodec::encode(&decoded, 1024).unwrap(), bytes);
    }
}

#[test]
fn decode_enforces_each_inclusive_limit() {
    for (bytes, depth, nodes) in [(READ, 14, 620), (WRITE, 11, 482)] {
        let exact = NsoLimits {
            max_bytes: bytes.len(),
            max_depth: depth,
            max_nodes: nodes,
        };
        for limits in [
            exact,
            NsoLimits {
                max_bytes: exact.max_bytes + 1,
                ..exact
            },
            NsoLimits {
                max_depth: depth + 1,
                ..exact
            },
            NsoLimits {
                max_nodes: nodes + 1,
                ..exact
            },
        ] {
            NsoCodec::decode(bytes, limits).unwrap();
        }
        for (limits, expected) in [
            (
                NsoLimits {
                    max_bytes: exact.max_bytes - 1,
                    ..exact
                },
                NsoLimit::Bytes,
            ),
            (
                NsoLimits {
                    max_depth: depth - 1,
                    ..exact
                },
                NsoLimit::Depth,
            ),
            (
                NsoLimits {
                    max_nodes: nodes - 1,
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
                NsoCodec::decode(bytes, limits),
                Err(NsoCodecError::Limit(expected))
            );
        }
    }
}

#[test]
fn noncanonical_json_syntax_normalizes_to_canonical_bytes() {
    let variant = format!(
        " \t{}\r\n",
        std::str::from_utf8(READ)
            .unwrap()
            .replace(r#""NSO""#, r#""\u004eSO""#)
            .replace(r#""is_root":true"#, r#""is_root" : true"#),
    );
    let decoded = NsoCodec::decode(variant.as_bytes(), LIMITS).unwrap();
    assert_eq!(decoded, ir_1_0());
    assert_eq!(NsoCodec::encode(&decoded, 1024).unwrap(), READ);
}

#[test]
fn unicode_escapes_and_typed_literals_match_frozen_oracle() {
    let decoded = NsoCodec::decode(SCALARS, LIMITS).unwrap();
    for (index, (expected, _)) in literal_cases().into_iter().enumerate() {
        let StatementKind::Let { value, .. } = &decoded.body[index].kind else {
            panic!("fixture should use a LET statement");
        };
        assert_eq!(value.kind, expected.kind);
    }
    let StatementKind::Let { value, .. } = &decoded.body[5].kind else {
        panic!("expected text literal");
    };
    assert_eq!(
        value.kind,
        ExpressionKind::Literal(LiteralValue::String(
            "\"\\/\u{8}\u{c}\n\r\t\u{0}\u{1f}\u{7f}한글🌏\u{2028}".into()
        )),
    );
    assert_eq!(NsoCodec::encode(&decoded, 1024).unwrap(), SCALARS);
    assert_eq!(
        NsoCodec::encode(&decoded, 4),
        Err(NsoCodecError::Decimal(DecimalError::EncodingLimit)),
    );

    let escaped = std::str::from_utf8(SCALARS)
        .unwrap()
        .replace('🌏', r#"\uD83C\uDF0F"#)
        .replace('/', r#"\/"#);
    let decoded = NsoCodec::decode(escaped.as_bytes(), LIMITS).unwrap();
    assert_eq!(NsoCodec::encode(&decoded, 1024).unwrap(), SCALARS);
}

#[test]
fn malformed_json_and_duplicate_keys_fail_before_schema() {
    for bytes in [
        b"".as_slice(),
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"{}{}",
        b"nul",
        b"tru",
        b"fals",
        b"{x:1}",
        b"{\"x\" 1}",
        b"{\"x\":1,}",
        b"[1,]",
        b"[1 2]",
        b"{\"x\":0 \"y\":1}",
        br#"{"x":[{"name":1,"\u006eame":2}]}"#,
        b"\"a\nb\"",
        br#""\q""#,
        br#""\uabcX""#,
        br#""\uDC00""#,
        br#""\uD800""#,
        br#""\uD800\u0041""#,
        br#""\uD800\x""#,
        b"\"",
        b"\"\\",
        b"-",
        b"01",
        b"NaN",
        b"Infinity",
        b"-Infinity",
        b"1e1",
        b"0.1",
    ] {
        assert!(
            matches!(
                NsoCodec::decode(bytes, LIMITS),
                Err(NsoCodecError::Json { .. })
            ),
            "{bytes:?}",
        );
    }
    for bytes in [
        b"null".as_slice(),
        b"[]",
        "\"👋\"".as_bytes(),
        b"{}",
        b"true",
        b"false",
        b"\t-0\n",
    ] {
        assert!(matches!(
            NsoCodec::decode(bytes, LIMITS),
            Err(NsoCodecError::Validation(NsoValidationError::Schema(_)))
        ));
    }
}

#[test]
fn codec_keeps_schema_and_credential_checks_on_both_paths() {
    let mut model = ir_1_1();
    model.skill_id = "Bearer DEMO.STUB".into();
    assert!(matches!(
        NsoCodec::encode(&model, 1024),
        Err(NsoCodecError::Validation(
            NsoValidationError::CredentialMaterial(_)
        ))
    ));
    assert!(matches!(
        NsoCodec::decode(
            br#"{"notes":[{"\u0061pi_key":"demo-placeholder"}]}"#,
            LIMITS
        ),
        Err(NsoCodecError::Validation(
            NsoValidationError::CredentialMaterial(_)
        ))
    ));

    let mut model = ir_1_0();
    model.outputs[0].type_info = ty(TypeKind::Optional(Box::new(ty(TypeKind::Primitive(
        "Int".into(),
    )))));
    assert_eq!(
        NsoCodec::encode(&model, 1024),
        Err(NsoCodecError::Type(TypeError::InternalOptional))
    );
    model.outputs[0].type_info = ty(TypeKind::Primitive("Int".into()));
    model.limits.tool_calls = 0.into();
    assert!(matches!(
        NsoCodec::encode(&model, 1024),
        Err(NsoCodecError::Validation(NsoValidationError::Schema(_)))
    ));
}

#[test]
fn decoder_rejects_inconsistent_source_bundle_fields() {
    let mut changed = WRITE.to_vec();
    let key = br#""source_bundle_sha256":"sha256:"#;
    let index = WRITE
        .windows(key.len())
        .position(|bytes| bytes == key)
        .unwrap()
        + key.len();
    changed[index] = if changed[index] == b'a' { b'b' } else { b'a' };
    assert_eq!(
        NsoCodec::decode(&changed, LIMITS),
        Err(NsoCodecError::InconsistentSourceBundle)
    );
}

#[test]
fn json_integer_boundary_matches_the_frozen_cpython_default() {
    let mut model = ir_1_0();
    model.limits.tool_calls = "9".repeat(4300).parse().unwrap();
    let at_limit = NsoCodec::encode(&model, 1024).unwrap();
    assert_eq!(NsoCodec::decode(&at_limit, LIMITS).unwrap(), model);
    let too_wide = std::str::from_utf8(&at_limit)
        .unwrap()
        .replace(&"9".repeat(4300), &"9".repeat(4301));
    assert!(matches!(
        NsoCodec::decode(too_wide.as_bytes(), LIMITS),
        Err(NsoCodecError::Json { .. })
    ));
    model.limits.tool_calls = "9".repeat(4301).parse().unwrap();
    assert_eq!(
        NsoCodec::encode(&model, 1024),
        Err(NsoCodecError::IntegerTooWide)
    );
}
