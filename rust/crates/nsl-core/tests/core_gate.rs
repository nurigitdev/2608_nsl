use std::collections::BTreeMap;

use nsl_core::{CredentialMaterialError, Value, ValueData, ensure_no_credential_material};

const ORACLE: &str = include_str!("fixtures/core-gate.tsv");

fn rows() -> impl Iterator<Item = (&'static str, &'static str, &'static str)> {
    ORACLE.lines().skip(1).map(|line| {
        let (shape, columns) = line.split_once('\t').unwrap();
        let (input, expected) = columns.split_once('\t').unwrap_or((columns, ""));
        (shape, input, expected)
    })
}

fn unescape(input: &str) -> String {
    let mut chars = input.chars();
    let mut text = String::new();
    while let Some(character) = chars.next() {
        text.push(if character == '\\' {
            match chars.next().unwrap() {
                '\\' => '\\',
                'r' => '\r',
                'n' => '\n',
                't' => '\t',
                'x' => {
                    let high = chars.next().unwrap().to_digit(16).unwrap();
                    let low = chars.next().unwrap().to_digit(16).unwrap();
                    char::from_u32(16 * high + low).unwrap()
                }
                _ => panic!("invalid fixture escape"),
            }
        } else {
            character
        });
    }
    text
}

fn object(key: &str, value: ValueData) -> ValueData {
    ValueData::Object(BTreeMap::from([(key.into(), value)]))
}

#[test]
fn credential_rejection_matches_frozen_oracle() {
    for (shape, input, expected) in
        rows().filter(|(shape, ..)| !matches!(*shape, "Date" | "DateTime"))
    {
        let input = unescape(input);
        let value = match shape {
            "key" => object(
                "meta",
                ValueData::Array(vec![object(&input, "demo-placeholder".into())]),
            ),
            "text" => object(
                "note",
                ValueData::Array(vec!["normal".into(), ValueData::String(input)]),
            ),
            "null" => object(&input, ValueData::Null),
            "empty" => object(&input, "".into()),
            "false" => object(&input, ValueData::Bool(false)),
            "zero" => object(&input, ValueData::Number(0.into())),
            "array" => object(&input, ValueData::Array(vec![])),
            "record" => object(&input, ValueData::Object(BTreeMap::new())),
            _ => panic!("unknown fixture shape: {shape}"),
        };
        let expected = match expected {
            "accept" => Ok(()),
            "reject" => Err(CredentialMaterialError),
            _ => panic!("unknown fixture outcome: {expected}"),
        };
        assert_eq!(
            ensure_no_credential_material(&value),
            expected,
            "{shape}: {value:?}"
        );
    }
}

#[test]
fn credential_screening_is_explicit_and_errors_omit_input() {
    let text = "password=demo-placeholder";
    for value in [
        Value::String(text.into()),
        Value::Record(BTreeMap::from([(text.into(), Value::Null)])),
    ] {
        let data = value.to_data(1024).unwrap();
        let error = ensure_no_credential_material(&data).unwrap_err();
        assert_eq!(error.to_string(), "credential material is forbidden");
        assert_eq!(format!("{error:?}"), "CredentialMaterialError");
    }
}

#[test]
fn canonical_dates_and_datetimes_match_frozen_oracle() {
    // The temporal boundary supplies the ISO representation to Core.
    let values = [
        Value::Date("0001-01-01".into()),
        Value::Date("2024-02-29".into()),
        Value::Date("9999-12-31".into()),
        Value::DateTime("2026-11-16T00:00:00+00:00".into()),
        Value::DateTime("2026-11-16T00:00:00.000001+00:00".into()),
        Value::DateTime("2026-11-16T09:30:00.009000+09:30".into()),
        Value::DateTime("2026-11-16T00:00:00.123456-05:00".into()),
    ];
    let expected: Vec<_> = rows()
        .filter_map(|(tag, text, extra)| match tag {
            "Date" | "DateTime" if extra.is_empty() => Some(ValueData::Object(BTreeMap::from([
                ("$type".into(), tag.into()),
                ("value".into(), text.into()),
            ]))),
            _ => None,
        })
        .collect();
    let actual = Value::List(values.into()).to_data(1024).unwrap();
    assert_eq!(actual, ValueData::Array(expected));
    assert_eq!(ensure_no_credential_material(&actual), Ok(()));
}
