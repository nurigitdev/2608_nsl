use std::{collections::BTreeMap, fmt};

use crate::{CurrencyCode, Value, ValueData};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TypeError {
    EmptyTypeName,
    EmptyFieldName,
    EmptyEnumValue,
    InternalOptional,
    InvalidData,
    UnknownKind,
    InvalidCurrency,
}

impl fmt::Display for TypeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::EmptyTypeName => "type name must not be empty",
            Self::EmptyFieldName => "record field name must not be empty",
            Self::EmptyEnumValue => "enum value must not be empty",
            Self::InternalOptional => {
                "optional is an internal type and has no NSO or Tool Contract representation"
            }
            Self::InvalidData => "invalid type document fields",
            Self::UnknownKind => "unknown type document kind",
            Self::InvalidCurrency => "expected a 3-letter uppercase ASCII currency code",
        })
    }
}

impl std::error::Error for TypeError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TypeRef(TypeKind);

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum TypeKind {
    Primitive(String),
    Domain(String),
    List(Box<TypeRef>),
    Record {
        name: String,
        fields: Vec<(String, TypeRef)>,
    },
    Enum {
        name: String,
        values: Vec<String>,
    },
    Optional(Box<TypeRef>),
    Money(CurrencyCode),
}

impl TypeRef {
    pub fn new(kind: TypeKind) -> Result<Self, TypeError> {
        match &kind {
            TypeKind::Primitive(name)
            | TypeKind::Domain(name)
            | TypeKind::Record { name, .. }
            | TypeKind::Enum { name, .. }
                if name.is_empty() =>
            {
                return Err(TypeError::EmptyTypeName);
            }
            TypeKind::Record { fields, .. } if fields.iter().any(|(name, _)| name.is_empty()) => {
                return Err(TypeError::EmptyFieldName);
            }
            TypeKind::Enum { values, .. } if values.iter().any(String::is_empty) => {
                return Err(TypeError::EmptyEnumValue);
            }
            _ => {}
        }
        Ok(Self(kind))
    }

    pub fn kind(&self) -> &TypeKind {
        &self.0
    }

    pub fn field(&self, name: &str) -> Option<&Self> {
        let TypeKind::Record { fields, .. } = self.kind() else {
            return None;
        };
        fields
            .iter()
            .find(|(field, _)| field == name)
            .map(|(_, ty)| ty)
    }

    pub fn accepts(&self, value: &Value) -> bool {
        match self.kind() {
            TypeKind::Primitive(name) => matches!(
                (name.as_str(), value),
                ("Bool", Value::Bool(_))
                    | ("Int" | "Year", Value::Int(_))
                    | ("String", Value::String(_))
                    | ("Decimal", Value::Decimal(_))
                    | ("Date", Value::Date(_))
                    | ("DateTime", Value::DateTime(_))
            ),
            TypeKind::Domain(_) => matches!(value, Value::String(_)),
            TypeKind::List(item) => {
                matches!(value, Value::List(values) if values.iter().all(|value| item.accepts(value)))
            }
            TypeKind::Record { fields, .. } => {
                let Value::Record(values) = value else {
                    return false;
                };
                // The Oracle uses a dict for conformance, with the last field of each name winning.
                let expected: BTreeMap<_, _> = fields.iter().map(|(name, ty)| (name, ty)).collect();
                expected.len() == values.len()
                    && expected
                        .iter()
                        .all(|(name, ty)| values.get(*name).is_some_and(|value| ty.accepts(value)))
            }
            TypeKind::Enum { values, .. } => {
                matches!(value, Value::String(value) if values.contains(value))
            }
            TypeKind::Optional(item) => matches!(value, Value::Null) || item.accepts(value),
            TypeKind::Money(currency) => {
                matches!(value, Value::Money(money) if money.currency() == currency)
            }
        }
    }

    /// Return the Oracle's structured type representation for a wire codec.
    pub fn to_data(&self) -> Result<ValueData, TypeError> {
        let (kind, property) = match self.kind() {
            TypeKind::Primitive(name) => ("primitive", Some(("name", name.as_str().into()))),
            TypeKind::Domain(name) => ("domain", Some(("name", name.as_str().into()))),
            TypeKind::List(item) => ("list", Some(("item", item.to_data()?))),
            TypeKind::Money(currency) => ("money", Some(("currency", currency.as_str().into()))),
            TypeKind::Record { name, fields } => {
                let mut data = object([("kind", "record".into()), ("name", name.as_str().into())]);
                if !fields.is_empty() {
                    let items = fields
                        .iter()
                        .map(|(field, ty)| {
                            Ok(ValueData::Object(object([
                                ("name", field.as_str().into()),
                                ("type", ty.to_data()?),
                            ])))
                        })
                        .collect::<Result<Vec<_>, TypeError>>()?;
                    data.insert("fields".into(), ValueData::Array(items));
                }
                return Ok(ValueData::Object(data));
            }
            TypeKind::Enum { name, values } => {
                let mut data = object([("kind", "enum".into()), ("name", name.as_str().into())]);
                if !values.is_empty() {
                    data.insert(
                        "values".into(),
                        ValueData::Array(
                            values.iter().map(|value| value.as_str().into()).collect(),
                        ),
                    );
                }
                return Ok(ValueData::Object(data));
            }
            TypeKind::Optional(_) => return Err(TypeError::InternalOptional),
        };
        let mut data = object([("kind", kind.into())]);
        if let Some((name, value)) = property {
            data.insert(name.into(), value);
        }
        Ok(ValueData::Object(data))
    }

    /// Validate and convert a structured NSO or Tool Contract type document.
    pub fn from_data(data: &ValueData) -> Result<Self, TypeError> {
        let ValueData::Object(fields) = data else {
            return Err(TypeError::InvalidData);
        };
        let kind = match fields.get("kind").map(text).transpose()? {
            Some("optional") => return Err(TypeError::InternalOptional),
            Some(kind @ ("primitive" | "domain" | "list" | "record" | "enum" | "money")) => kind,
            Some(_) => return Err(TypeError::UnknownKind),
            None => return Err(TypeError::InvalidData),
        };
        let name = || text(required(fields, "name")?).map(str::to_owned);
        let kind = match (kind, fields.len()) {
            ("primitive", 2) => TypeKind::Primitive(name()?),
            ("domain", 2) => TypeKind::Domain(name()?),
            ("list", 2) => TypeKind::List(Box::new(Self::from_data(required(fields, "item")?)?)),
            ("money", 2) => TypeKind::Money(
                text(required(fields, "currency")?)?
                    .parse()
                    .map_err(|_| TypeError::InvalidCurrency)?,
            ),
            ("record", 3) => TypeKind::Record {
                name: name()?,
                fields: array(required(fields, "fields")?)?
                    .iter()
                    .map(|field| {
                        let ValueData::Object(field) = field else {
                            return Err(TypeError::InvalidData);
                        };
                        if field.len() != 2 {
                            return Err(TypeError::InvalidData);
                        }
                        Ok((
                            text(required(field, "name")?)?.to_owned(),
                            Self::from_data(required(field, "type")?)?,
                        ))
                    })
                    .collect::<Result<_, TypeError>>()?,
            },
            ("enum", 3) => TypeKind::Enum {
                name: name()?,
                values: array(required(fields, "values")?)?
                    .iter()
                    .map(|value| text(value).map(str::to_owned))
                    .collect::<Result<_, _>>()?,
            },
            _ => return Err(TypeError::InvalidData),
        };
        Self::new(kind)
    }
}

fn required<'a>(
    fields: &'a BTreeMap<String, ValueData>,
    key: &str,
) -> Result<&'a ValueData, TypeError> {
    fields.get(key).ok_or(TypeError::InvalidData)
}

fn text(data: &ValueData) -> Result<&str, TypeError> {
    match data {
        ValueData::String(value) => Ok(value),
        _ => Err(TypeError::InvalidData),
    }
}

fn array(data: &ValueData) -> Result<&[ValueData], TypeError> {
    match data {
        ValueData::Array(values) => Ok(values),
        _ => Err(TypeError::InvalidData),
    }
}

fn object<const N: usize>(fields: [(&str, ValueData); N]) -> BTreeMap<String, ValueData> {
    fields
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value))
        .collect()
}
