use nsl_core::*;
fn limits() -> JsonLimits {
    JsonLimits {
        max_bytes: 100_000,
        max_depth: 128,
        max_nodes: 100_000,
    }
}
#[test]
fn shared_parser_preserves_nso_rules_and_reports_nested_float_paths() {
    let input = br#"{"a":[0,{"ratio":0.5}]}"#;
    let e = parse_json_with_limits(input, limits()).unwrap_err();
    let JsonDecodeError::Json {
        path,
        offset,
        reason,
    } = &e
    else {
        panic!("{e:?}")
    };
    assert_eq!(path, "$.a[1].ratio");
    assert!(*offset > 0);
    assert_eq!(*reason, "JSON number tokens must be integers");
    assert!(e.to_string().contains(path));
    assert!(std::error::Error::source(&e).is_none());
    for axis in 0..3 {
        let mut l = limits();
        match axis {
            0 => l.max_bytes = 0,
            1 => l.max_depth = 0,
            _ => l.max_nodes = 0,
        };
        let e = parse_json_with_limits(br#"[1]"#, l).unwrap_err();
        assert!(e.to_string().contains("limit"));
    }
    let d = parse_json_with_limits(
        br#"{"b":true,"a":[-0,"\ud55c\ud83d\ude00",false,null]}"#,
        limits(),
    )
    .unwrap();
    assert_eq!(
        String::from_utf8(canonical_json(&d).unwrap()).unwrap(),
        "{\"a\":[0,\"한😀\",false,null],\"b\":true}"
    );
}

#[test]
fn new_json_paths_redact_credential_material_and_keep_the_byte_offset() {
    let e = parse_json_with_limits(br#"{"token=synthetic-only":[0.5]}"#, limits()).unwrap_err();
    assert!(!format!("{e:?}").contains("synthetic-only"));
    assert!(!e.to_string().contains("synthetic-only"));
}

#[test]
fn large_ancestor_keys_and_many_children_use_bounded_iterative_admission() {
    let key = "x".repeat(16384);
    let text = format!("{{\"{key}\":[{}]}}", vec!["0"; 2000].join(","));
    let data = parse_json_with_limits(text.as_bytes(), limits()).unwrap();
    let ValueData::Object(fields) = data else {
        panic!()
    };
    let ValueData::Array(items) = &fields[&key] else {
        panic!()
    };
    assert_eq!(items.len(), 2000);
}
