#![allow(dead_code)]
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime::*;
use std::{cell::Cell, collections::BTreeMap};

pub fn ty(name: &str) -> TypeRef {
    let kind = if let Some(item) = name.strip_prefix("List") {
        TypeKind::List(Box::new(ty(item)))
    } else {
        match name {
            "USD" | "EUR" => TypeKind::Money(name.parse().unwrap()),
            "Domain" => TypeKind::Domain("Customer".into()),
            "Enum" => TypeKind::Enum {
                name: "E".into(),
                values: vec!["A".into(), "B".into()],
            },
            "Record" | "Nested" | "EmptyRecord" => TypeKind::Record {
                name: if name == "Nested" { "N" } else { "R" }.into(),
                fields: if name == "EmptyRecord" {
                    vec![]
                } else {
                    vec![(
                        "a".into(),
                        ty(if name == "Nested" { "ListInt" } else { "Int" }),
                    )]
                },
            },
            _ => TypeKind::Primitive(name.into()),
        }
    };
    TypeRef::new(kind).unwrap()
}
fn int(n: i64) -> Value {
    Value::Int(n.into())
}
fn decimal(s: &str) -> Value {
    Value::Decimal(s.parse().unwrap())
}
fn money(s: &str, c: &str) -> Value {
    Value::Money(Money::new(s.parse().unwrap(), c.parse().unwrap()))
}
fn list(v: impl IntoIterator<Item = Value>) -> Value {
    Value::List(v.into_iter().collect())
}
fn record(v: Value) -> Value {
    Value::Record(BTreeMap::from([("a".into(), v)]))
}
pub fn value(name: &str) -> Value {
    match name {
        "neg" => int(-1),
        "zero" => int(0),
        "one" => int(1),
        "two" => int(2),
        "seven" => int(7),
        "wide" => Value::Int(format!("1{}", "0".repeat(100)).parse().unwrap()),
        "false" => Value::Bool(false),
        "true" => Value::Bool(true),
        "empty" => Value::String("".into()),
        "a" => Value::String("A".into()),
        "b" => Value::String("B".into()),
        "unicode" => Value::String("가😀".into()),
        "null" => Value::Null,
        "dm" => decimal("-1.2300"),
        "dz" => decimal("-0.00"),
        "dp" => decimal("1.2300"),
        "tie" => decimal("9999999999999999999999999999"),
        "half" => decimal("0.5"),
        "high" => decimal("1e999999"),
        "tiny" => decimal("1e-1000026"),
        "usd" => money("12.500", "USD"),
        "usdm" => money("-1", "USD"),
        "usdz" => money("0", "USD"),
        "eur" => money("12.5", "EUR"),
        "date0" => Value::Date("2026-11-18".into()),
        "date1" => Value::Date("2026-11-19".into()),
        "time0" | "time1" | "utc" | "offset" | "offset_second" | "ancient" | "leap" | "end" => {
            Value::DateTime(
                match name {
                    "time0" => "2026-11-18T01:02:03",
                    "time1" => "2026-11-18T01:02:04.000001",
                    "utc" => "2026-11-18T01:02:03+00:00",
                    "offset" => "2026-11-18T10:02:03+09:00",
                    "offset_second" => "2026-11-18T01:03:03.123456+00:01:00.123456",
                    "ancient" => "0001-01-01T00:00:00-23:59:59.999999",
                    "leap" => "2000-02-29T00:00:00+00:00",
                    _ => "9999-12-31T23:59:59.999999+23:59:59.999999",
                }
                .into(),
            )
        }
        "list0" => list([]),
        "ints" => list([-1, 0, 2].map(int)),
        "int1" => list([int(7)]),
        "bools" => list([Value::Bool(true), Value::Bool(false)]),
        "decimals" => list(["0.1", "-0.2", "0.3"].map(decimal)),
        "money" => list([value("usd"), value("usdm")]),
        "money_mixed" => list([value("usd"), money("1", "EUR")]),
        "money_wrong" => list([money("1", "USD"), int(7)]),
        "rounding" => list([
            value("tie"),
            value("half"),
            decimal("-9999999999999999999999999999"),
        ]),
        "strings" => list(["가", "😀", "A"].map(|s| Value::String(s.into()))),
        "domains" | "enums" => list([value("b"), value("a")]),
        "dates" => list([value("date1"), value("date0")]),
        "times" => list([
            Value::DateTime("2026-11-18T01:02:04+00:00".into()),
            value("offset"),
        ]),
        "times_mixed" => list([
            Value::DateTime("2026-11-18T01:02:04".into()),
            value("offset"),
        ]),
        "records" => list([-1, 0, 2].map(|n| record(int(n)))),
        "record1" => list([record(int(7))]),
        "records_bad" => list([record(int(7)), value("record_empty")]),
        "records_wrong" => list([int(7)]),
        "record" => record(int(7)),
        "record_other" => Value::Record(BTreeMap::from([("b".into(), int(7))])),
        "list_one" => list([int(1)]),
        "list_true" => list([Value::Bool(true)]),
        "money_high" => money("9e999999", "USD"),
        "money_high_list" => list([money("9e999999", "USD"), money("9e999999", "USD")]),
        "record_null" => record(Value::Null),
        "record_list" => record(list([])),
        "record_empty" => Value::Record(BTreeMap::new()),
        "nested" | "nested_equal" | "nested_short" => list([
            list([int(1), int(2)]),
            match name {
                "nested" => list([int(1), int(3)]),
                "nested_equal" => list([int(1), int(2)]),
                _ => list([int(1)]),
            },
        ]),
        "nested_bool" => list([list([Value::Bool(true)]), list([int(1)])]),
        "records_same" => list([value("record_empty"), value("record_empty")]),
        _ => panic!("unknown value {name}"),
    }
}
pub fn parse(program: &str) -> Expression {
    let mut stack = Vec::<Expression>::new();
    for token in program.split(';') {
        let p: Vec<_> = token.split(',').collect();
        let node_id = p[p.len() - 1].into();
        let type_info = ty(p[p.len() - 2]);
        let kind = match p[0] {
            "L" => ExpressionKind::Literal(match value(p[1]) {
                Value::Bool(v) => LiteralValue::Bool(v),
                Value::Int(v) => LiteralValue::Int(v),
                Value::String(v) => LiteralValue::String(v),
                Value::Decimal(v) => LiteralValue::Decimal(v),
                Value::Money(v) => LiteralValue::Money(v),
                _ => panic!("not an IR literal"),
            }),
            "S" => ExpressionKind::SymbolRef {
                symbol_id: p[1].into(),
            },
            "F" => ExpressionKind::Field {
                source: Box::new(stack.pop().unwrap()),
                field: p[1].into(),
            },
            "P" => ExpressionKind::Projection {
                source: Box::new(stack.pop().unwrap()),
                field: p[1].into(),
            },
            "C" => {
                let n: usize = p[2].parse().unwrap();
                let arguments = stack.split_off(stack.len() - n);
                ExpressionKind::Call {
                    function: p[1].into(),
                    arguments,
                }
            }
            "B" => {
                let right = Box::new(stack.pop().unwrap());
                let left = Box::new(stack.pop().unwrap());
                ExpressionKind::Binary {
                    operator: p[1].into(),
                    left,
                    right,
                }
            }
            _ => panic!(),
        };
        stack.push(Expression {
            node_id,
            type_info,
            kind,
        });
    }
    assert_eq!(stack.len(), 1);
    stack.pop().unwrap()
}
pub struct Clock {
    script: Vec<Integer>,
    pub calls: Cell<usize>,
}
impl Clock {
    pub fn new(script: &str) -> Self {
        Self {
            script: script.split(',').map(|s| s.parse().unwrap()).collect(),
            calls: Cell::new(0),
        }
    }
}
impl RuntimeClock for Clock {
    fn monotonic_ns(&self) -> Integer {
        let n = self.calls.get();
        self.calls.set(n + 1);
        self.script[n.min(self.script.len() - 1)].clone()
    }
}
pub fn bindings<C: RuntimeClock>(ctx: &mut ExecutionContext<C>, script: &str) {
    if script == "-" {
        return;
    }
    for token in script.split(';') {
        let p: Vec<_> = token.split(',').collect();
        let value = ValueEnvelope::new(
            value(p[2]),
            ty(p[3]),
            p[4].parse().unwrap(),
            p[5].parse().unwrap(),
            p[6].parse().unwrap(),
            if p[7] == "-" {
                vec![]
            } else {
                p[7].split('+').map(Into::into).collect()
            },
        );
        ctx.bind(
            match p[0] {
                "i" => BindingNamespace::Input,
                "c" => BindingNamespace::Context,
                "v" => BindingNamespace::Variable,
                "k" => BindingNamespace::Check,
                _ => panic!(),
            },
            p[1].into(),
            value,
        )
        .unwrap();
    }
}
pub fn text(s: impl Into<String>) -> ValueData {
    ValueData::String(s.into())
}
pub fn arr(v: impl IntoIterator<Item = ValueData>) -> ValueData {
    ValueData::Array(v.into_iter().collect())
}
pub fn n(n: impl std::fmt::Display) -> ValueData {
    ValueData::Number(n.to_string().parse().unwrap())
}
pub fn snapshot(v: &ValueEnvelope) -> ValueData {
    arr([
        text("ok"),
        v.value().to_data(1_000_100).unwrap(),
        v.type_ref().to_data().unwrap(),
        text(v.presence().as_str()),
        text(v.completeness().as_str()),
        text(v.classification().as_str()),
        arr(v.provenance_refs().iter().map(text)),
    ])
}
pub fn error(e: ExpressionRuntimeError) -> ValueData {
    if let ExpressionRuntimeError::Unexpected { kind, detail } = e {
        return arr([text(kind), text(detail), ValueData::Null, ValueData::Null]);
    }
    let message = e.to_string();
    let (class, node, detail) = match e {
        ExpressionRuntimeError::Evaluation(e) => (
            "ExpressionEvaluationError",
            text(e.node_id),
            text(e.detail_code),
        ),
        ExpressionRuntimeError::Contract(_) => {
            ("RuntimeContractError", ValueData::Null, ValueData::Null)
        }
        ExpressionRuntimeError::Money { kind, .. } => (kind, ValueData::Null, ValueData::Null),
        ExpressionRuntimeError::Limit(_) => ("LimitExceeded", ValueData::Null, ValueData::Null),
        ExpressionRuntimeError::Binding(e) => (
            "ImmutableBindingError",
            ValueData::Null,
            e.detail_code().map_or(ValueData::Null, text),
        ),
        ExpressionRuntimeError::Unsupported { .. } => panic!("outside pure Oracle corpus"),
        ExpressionRuntimeError::Unexpected { .. } => unreachable!(),
    };
    arr([text(class), text(message), node, detail])
}
