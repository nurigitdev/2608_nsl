use std::collections::BTreeMap;

use nsl_core::{
    Completeness, DataClassification, Money, Presence, TypeError, TypeKind, TypeRef, Value,
    ValueData, ValueEnvelope, ValueError,
};

const ORACLE_FIXTURE: &str = include_str!("fixtures/value-model.json");
const FROZEN_MONEY_CASE: &str =
    include_str!("../../../../compatibility/v0_2_1/values/decimal-money-exact/expected.json");

fn primitive(name: &str) -> TypeRef {
    TypeRef::new(TypeKind::Primitive(name.into())).unwrap()
}

fn list(item: TypeRef) -> TypeRef {
    TypeRef::new(TypeKind::List(Box::new(item))).unwrap()
}

fn string(value: &str) -> Value {
    Value::String(value.into())
}

fn project_type() -> TypeRef {
    let check = TypeRef::new(TypeKind::Enum {
        name: "CheckStatus".into(),
        values: ["PASS", "FAIL", "UNKNOWN"].map(str::to_owned).into(),
    })
    .unwrap();
    let amount = TypeRef::new(TypeKind::Money("KRW".parse().unwrap())).unwrap();
    let code = TypeRef::new(TypeKind::Domain("ProjectCode".into())).unwrap();
    TypeRef::new(TypeKind::Record {
        name: "Project".into(),
        fields: vec![
            ("statuses".into(), list(list(check))),
            ("amount".into(), amount),
            ("code".into(), code),
            ("year".into(), primitive("Int")),
        ],
    })
    .unwrap()
}

fn sample_value() -> Value {
    Value::Record(BTreeMap::from([
        ("code".into(), string("P-01")),
        ("flag".into(), Value::Bool(false)),
        ("null".into(), Value::Null),
        ("decimal".into(), Value::Decimal("-0.00".parse().unwrap())),
        (
            "money".into(),
            Value::Money(Money::new("1.250".parse().unwrap(), "KRW".parse().unwrap())),
        ),
        ("date".into(), Value::Date("2026-10-20".into())),
        (
            "timestamp".into(),
            Value::DateTime("2026-10-20T01:02:03.004000+09:00".into()),
        ),
        (
            "wide".into(),
            Value::Int("340282366920938463463374607431768211457".parse().unwrap()),
        ),
        (
            "nested".into(),
            Value::List(vec![
                Value::List(vec![string("PASS"), string("UNKNOWN")]),
                Value::List(vec![]),
            ]),
        ),
    ]))
}

// This fixture uses only literal ASCII text. The production JSON codec belongs to R3.
fn render_fixture(data: &ValueData) -> String {
    match data {
        ValueData::Null => "null".into(),
        ValueData::Bool(value) => value.to_string(),
        ValueData::Number(value) => value.as_str().into(),
        ValueData::String(text) => {
            assert!(
                text.bytes()
                    .all(|byte| (b' '..=b'~').contains(&byte) && !matches!(byte, b'"' | b'\\'))
            );
            format!("\"{text}\"")
        }
        ValueData::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(render_fixture)
                .collect::<Vec<_>>()
                .join(",")
        ),
        ValueData::Object(fields) => {
            let pairs = fields
                .iter()
                .map(|(key, value)| {
                    format!(
                        "{}:{}",
                        render_fixture(&key.as_str().into()),
                        render_fixture(value)
                    )
                })
                .collect::<Vec<_>>();
            format!("{{{}}}", pairs.join(","))
        }
    }
}

#[test]
fn decimal_and_money_match_frozen_value_corpus() {
    // Operands from compatibility/v0_2_1/values/decimal-money-exact/values.json.
    let left = "1E-3".parse::<nsl_core::NslDecimal>().unwrap();
    let right = "-0.00".parse().unwrap();
    let money = |amount: &str| Money::new(amount.parse().unwrap(), "KRW".parse().unwrap());
    let first = money("0.10");
    let second = money("0.20");
    let result = Value::Record(BTreeMap::from([
        (
            "decimal_sum".into(),
            Value::Decimal(left.checked_add(&right).unwrap()),
        ),
        (
            "money_sum".into(),
            Value::Money(first.checked_add(&second).unwrap()),
        ),
        (
            "round_trip".into(),
            Value::List(vec![
                Value::Decimal(left),
                Value::Decimal(right),
                Value::Money(first),
                Value::Money(second),
            ]),
        ),
    ]));
    // The frozen document's strings contain no spaces or line breaks.
    let expected: String = FROZEN_MONEY_CASE
        .chars()
        .filter(|c| !c.is_ascii_whitespace())
        .collect();
    assert_eq!(render_fixture(&result.to_data(1024).unwrap()), expected);
}

#[test]
fn type_value_presence_and_classification_match_the_oracle() {
    let levels = DataClassification::ALL;
    let row = |values| ValueData::Array(values);
    let allowed = levels
        .iter()
        .map(|&maximum| {
            row(levels
                .iter()
                .map(|&actual| ValueData::Bool(maximum.allows(actual)))
                .collect())
        })
        .collect();
    let highest = levels
        .iter()
        .map(|&first| {
            row(levels
                .iter()
                .map(|&second| {
                    DataClassification::highest([first, second])
                        .unwrap()
                        .as_str()
                        .into()
                })
                .collect())
        })
        .collect();
    let presence = [
        Value::Null,
        Value::List(vec![]),
        Value::Record(BTreeMap::new()),
        Value::Int(0.into()),
        Value::Bool(false),
        string(""),
        Value::List(vec![Value::Int(0.into())]),
        Value::Record(BTreeMap::from([("x".into(), Value::Null)])),
    ]
    .map(|value| {
        let envelope =
            ValueEnvelope::complete(value, primitive("String"), DataClassification::Internal);
        assert_eq!(envelope.completeness(), Completeness::Complete);
        envelope.presence().as_str().into()
    });
    let observed = ValueData::Object(BTreeMap::from([
        ("type".into(), project_type().to_data().unwrap()),
        ("value".into(), sample_value().to_data(1024).unwrap()),
        ("presence".into(), ValueData::Array(presence.into())),
        (
            "classifications".into(),
            row(levels.iter().map(|level| level.as_str().into()).collect()),
        ),
        ("allowed".into(), row(allowed)),
        ("highest".into(), row(highest)),
    ]));
    assert_eq!(format!("{}\n", render_fixture(&observed)), ORACLE_FIXTURE);
    assert_eq!(DataClassification::highest([]), None);
}

#[test]
fn records_and_nested_lists_require_exact_types() {
    let project = project_type();
    assert_eq!(
        TypeRef::from_data(&project.to_data().unwrap()).unwrap(),
        project
    );
    let statuses = project.field("statuses").unwrap();
    assert!(project.field("missing").is_none());
    assert!(statuses.accepts(&Value::List(vec![])));
    assert!(statuses.accepts(&Value::List(vec![Value::List(vec![string("PASS")])])));
    assert!(!statuses.accepts(&Value::List(vec![Value::List(vec![string("pass")])])));
    assert!(!statuses.accepts(&Value::List(vec![string("PASS")])));
    assert!(!primitive("Int").accepts(&Value::Bool(false)));
    assert!(!primitive("Date").accepts(&Value::DateTime("2026-10-20T00:00:00".into())));

    let mut fields = BTreeMap::from([
        ("statuses".into(), Value::List(vec![])),
        (
            "amount".into(),
            Value::Money(Money::new("0".parse().unwrap(), "KRW".parse().unwrap())),
        ),
        ("code".into(), string("P-01")),
        ("year".into(), Value::Int(2026.into())),
    ]);
    assert!(project.accepts(&Value::Record(fields.clone())));
    fields.insert("extra".into(), Value::Null);
    assert!(!project.accepts(&Value::Record(fields.clone())));
    fields.remove("extra");
    fields.remove("year");
    assert!(!project.accepts(&Value::Record(fields)));
    let dollars = Value::Money(Money::new("0".parse().unwrap(), "USD".parse().unwrap()));
    assert!(!project.field("amount").unwrap().accepts(&dollars));
}

#[test]
fn optional_is_internal_and_preserves_explicit_null() {
    let optional = TypeRef::new(TypeKind::Optional(Box::new(primitive("Int")))).unwrap();
    assert!(optional.accepts(&Value::Null));
    assert!(optional.accepts(&Value::Int(7.into())));
    assert!(!optional.accepts(&Value::Bool(false)));
    assert!(!primitive("Int").accepts(&Value::Null));
    assert_eq!(optional.to_data(), Err(TypeError::InternalOptional));
    assert_eq!(
        list(optional.clone()).to_data(),
        Err(TypeError::InternalOptional)
    );
    let optional_data = ValueData::Object(BTreeMap::from([
        ("kind".into(), "optional".into()),
        ("item".into(), primitive("Int").to_data().unwrap()),
    ]));
    assert_eq!(
        TypeRef::from_data(&optional_data),
        Err(TypeError::InternalOptional)
    );
    let list_data = ValueData::Object(BTreeMap::from([
        ("kind".into(), "list".into()),
        ("item".into(), optional_data),
    ]));
    assert_eq!(
        TypeRef::from_data(&list_data),
        Err(TypeError::InternalOptional)
    );
    let envelope =
        ValueEnvelope::complete(Value::Null, optional.clone(), DataClassification::Internal);
    assert_eq!(envelope.value(), &Value::Null);
    assert_eq!(envelope.type_ref(), &optional);
    assert_eq!(envelope.presence(), Presence::Empty);

    let partial = ValueEnvelope::new(
        Value::Int(7.into()),
        optional,
        Presence::Present,
        Completeness::Partial,
        DataClassification::Confidential,
        vec!["record-1".into(), "record-2".into()],
    );
    assert_eq!(partial.completeness(), Completeness::Partial);
    assert_eq!(partial.classification(), DataClassification::Confidential);
    assert_eq!(partial.provenance_refs(), ["record-1", "record-2"]);
}

#[test]
fn invalid_type_shapes_and_numeric_text_are_rejected() {
    assert_eq!(
        TypeRef::from_data(&ValueData::Null),
        Err(TypeError::InvalidData)
    );
    for (kind, error) in [
        ("unknown", TypeError::UnknownKind),
        ("list", TypeError::InvalidData),
    ] {
        let data = ValueData::Object(BTreeMap::from([("kind".into(), kind.into())]));
        assert_eq!(TypeRef::from_data(&data), Err(error));
    }
    assert_eq!(
        TypeRef::new(TypeKind::Domain(String::new())),
        Err(TypeError::EmptyTypeName)
    );
    assert_eq!(
        TypeRef::new(TypeKind::Record {
            name: "Project".into(),
            fields: vec![(String::new(), primitive("Int"))],
        }),
        Err(TypeError::EmptyFieldName)
    );
    assert_eq!(
        TypeRef::new(TypeKind::Enum {
            name: "State".into(),
            values: vec![String::new()],
        }),
        Err(TypeError::EmptyEnumValue)
    );
    for currency in ["", "krw", "KR", "KRWW", "K0W", "한글A"] {
        assert_eq!(
            currency.parse::<nsl_core::CurrencyCode>(),
            Err(ValueError::InvalidCurrency)
        );
    }
    for value in ["", "-", "-0", "01", "+1", "1.0", "1e3", " 7", "４"] {
        assert_eq!(
            value.parse::<nsl_core::Integer>(),
            Err(ValueError::InvalidInteger)
        );
    }
}

#[test]
fn type_documents_distinguish_empty_metadata_and_malformed_members() {
    let data = |items: &[(&str, ValueData)]| {
        ValueData::Object(
            items
                .iter()
                .cloned()
                .map(|(name, v)| (name.into(), v))
                .collect(),
        )
    };
    for (kind, collection) in [("record", "fields"), ("enum", "values")] {
        let valid = data(&[
            ("kind", kind.into()),
            ("name", "Empty".into()),
            (collection, ValueData::Array(vec![])),
        ]);
        let ty = TypeRef::from_data(&valid).unwrap();
        assert_eq!(
            ty.to_data().unwrap(),
            data(&[("kind", kind.into()), ("name", "Empty".into())])
        );
        assert_eq!(
            TypeRef::from_data(&ty.to_data().unwrap()),
            Err(TypeError::InvalidData)
        );
        assert_eq!(
            TypeRef::from_data(&data(&[
                ("kind", kind.into()),
                ("name", "Empty".into()),
                (collection, "wrong".into())
            ])),
            Err(TypeError::InvalidData),
        );
    }
    for invalid_field in [ValueData::Null, data(&[("name", "a".into())])] {
        let invalid = data(&[
            ("kind", "record".into()),
            ("name", "Bad".into()),
            ("fields", ValueData::Array(vec![invalid_field])),
        ]);
        assert_eq!(TypeRef::from_data(&invalid), Err(TypeError::InvalidData));
    }
    let invalid_enum = data(&[
        ("kind", "enum".into()),
        ("name", "Bad".into()),
        ("values", ValueData::Array(vec![ValueData::Null])),
    ]);
    assert_eq!(
        TypeRef::from_data(&invalid_enum),
        Err(TypeError::InvalidData)
    );
    assert_eq!(TypeRef::from_data(&data(&[])), Err(TypeError::InvalidData));
    assert_eq!(
        TypeRef::from_data(&data(&[
            ("kind", "money".into()),
            ("currency", "krw".into())
        ])),
        Err(TypeError::InvalidCurrency),
    );
    let ty = TypeRef::new(TypeKind::Record {
        name: "Internal".into(),
        fields: vec![(
            "present".into(),
            TypeRef::new(TypeKind::Optional(Box::new(primitive("Int")))).unwrap(),
        )],
    })
    .unwrap();
    assert_eq!(ty.to_data(), Err(TypeError::InternalOptional));
    assert!(!ty.accepts(&Value::Null));
    assert!(primitive("Int").field("present").is_none());
    assert_eq!(
        TypeRef::new(TypeKind::Enum {
            name: String::new(),
            values: vec!["READY".into()]
        }),
        Err(TypeError::EmptyTypeName),
    );
}

#[test]
fn type_and_value_errors_have_stable_diagnostics() {
    for (error, message) in [
        (TypeError::EmptyTypeName, "type name must not be empty"),
        (
            TypeError::EmptyFieldName,
            "record field name must not be empty",
        ),
        (TypeError::EmptyEnumValue, "enum value must not be empty"),
        (
            TypeError::InternalOptional,
            "optional is an internal type and has no NSO or Tool Contract representation",
        ),
        (TypeError::InvalidData, "invalid type document fields"),
        (TypeError::UnknownKind, "unknown type document kind"),
        (
            TypeError::InvalidCurrency,
            "expected a 3-letter uppercase ASCII currency code",
        ),
    ] {
        assert_eq!(error.to_string(), message);
    }
    assert_eq!(
        ValueError::InvalidInteger.to_string(),
        "expected a canonical base-10 integer"
    );
    assert_eq!(
        ValueError::InvalidCurrency.to_string(),
        "expected a 3-letter uppercase ASCII currency code"
    );
}
