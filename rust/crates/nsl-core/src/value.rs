use std::{collections::BTreeMap, fmt, str::FromStr};

use crate::{Completeness, DataClassification, DecimalError, Money, NslDecimal, Presence, TypeRef};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ValueError {
    InvalidInteger,
    InvalidCurrency,
}

impl fmt::Display for ValueError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidInteger => "expected a canonical base-10 integer",
            Self::InvalidCurrency => "expected a 3-letter uppercase ASCII currency code",
        })
    }
}

impl std::error::Error for ValueError {}

/// An exact integer token with no machine-integer width limit.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Integer(String);

impl Integer {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl From<i64> for Integer {
    fn from(value: i64) -> Self {
        Self(value.to_string())
    }
}

impl FromStr for Integer {
    type Err = ValueError;

    fn from_str(text: &str) -> Result<Self, Self::Err> {
        let digits = text.strip_prefix('-').unwrap_or(text);
        if !is_digits(digits) || (digits.starts_with('0') && text != "0") {
            return Err(ValueError::InvalidInteger);
        }
        Ok(Self(text.to_owned()))
    }
}

fn is_digits(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|byte| byte.is_ascii_digit())
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CurrencyCode(String);

impl CurrencyCode {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl FromStr for CurrencyCode {
    type Err = ValueError;

    fn from_str(text: &str) -> Result<Self, Self::Err> {
        if text.len() != 3 || !text.bytes().all(|byte| byte.is_ascii_uppercase()) {
            return Err(ValueError::InvalidCurrency);
        }
        Ok(Self(text.to_owned()))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(Integer),
    String(String),
    Decimal(NslDecimal),
    Money(Money),
    /// Already-encoded ISO date text from the temporal boundary.
    Date(String),
    /// Already-encoded ISO date-time text from the temporal boundary.
    DateTime(String),
    List(Vec<Value>),
    Record(BTreeMap<String, Value>),
}

/// Structured canonical data before the wire codec writes JSON bytes.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ValueData {
    Null,
    Bool(bool),
    Number(Integer),
    String(String),
    Array(Vec<ValueData>),
    Object(BTreeMap<String, ValueData>),
}

impl From<&str> for ValueData {
    fn from(text: &str) -> Self {
        Self::String(text.to_owned())
    }
}

impl Value {
    pub fn presence(&self) -> Presence {
        match self {
            Self::Null => Presence::Empty,
            Self::List(values) if values.is_empty() => Presence::Empty,
            Self::Record(fields) if fields.is_empty() => Presence::Empty,
            _ => Presence::Present,
        }
    }

    /// Encode canonical fields, bounding each expanded decimal token in bytes.
    pub fn to_data(&self, max_decimal_bytes: usize) -> Result<ValueData, DecimalError> {
        let encode = |value: &Self| value.to_data(max_decimal_bytes);
        Ok(match self {
            Self::Null => ValueData::Null,
            Self::Bool(value) => ValueData::Bool(*value),
            Self::Int(value) => ValueData::Number(value.clone()),
            Self::String(value) => ValueData::String(value.clone()),
            Self::Decimal(value) => {
                tagged("Decimal", "value", value.to_fixed_point(max_decimal_bytes)?)
            }
            Self::Money(money) => ValueData::Object(BTreeMap::from([
                ("$type".into(), "Money".into()),
                (
                    "amount".into(),
                    ValueData::String(money.amount().to_fixed_point(max_decimal_bytes)?),
                ),
                ("currency".into(), money.currency().as_str().into()),
            ])),
            Self::Date(value) => tagged("Date", "value", value.clone()),
            Self::DateTime(value) => tagged("DateTime", "value", value.clone()),
            Self::List(items) => {
                ValueData::Array(items.iter().map(encode).collect::<Result<_, _>>()?)
            }
            Self::Record(fields) => ValueData::Object(
                fields
                    .iter()
                    .map(|(name, value)| Ok((name.clone(), encode(value)?)))
                    .collect::<Result<_, _>>()?,
            ),
        })
    }
}

fn tagged(tag: &str, key: &str, value: String) -> ValueData {
    ValueData::Object(BTreeMap::from([
        ("$type".into(), tag.into()),
        (key.into(), ValueData::String(value)),
    ]))
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValueEnvelope {
    value: Value,
    type_ref: TypeRef,
    presence: Presence,
    completeness: Completeness,
    classification: DataClassification,
    provenance_refs: Vec<String>,
}

impl ValueEnvelope {
    pub fn new(
        value: Value,
        type_ref: TypeRef,
        presence: Presence,
        completeness: Completeness,
        classification: DataClassification,
        provenance_refs: Vec<String>,
    ) -> Self {
        Self {
            value,
            type_ref,
            presence,
            completeness,
            classification,
            provenance_refs,
        }
    }

    pub fn complete(value: Value, type_ref: TypeRef, classification: DataClassification) -> Self {
        let presence = value.presence();
        Self::new(
            value,
            type_ref,
            presence,
            Completeness::Complete,
            classification,
            Vec::new(),
        )
    }

    pub fn value(&self) -> &Value {
        &self.value
    }

    pub fn type_ref(&self) -> &TypeRef {
        &self.type_ref
    }

    pub fn presence(&self) -> Presence {
        self.presence
    }

    pub fn completeness(&self) -> Completeness {
        self.completeness
    }

    pub fn classification(&self) -> DataClassification {
        self.classification
    }

    pub fn provenance_refs(&self) -> &[String] {
        &self.provenance_refs
    }
}
