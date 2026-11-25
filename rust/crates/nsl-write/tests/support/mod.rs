#![allow(dead_code)]
use nsl_core::*;
use nsl_ir::*;
use nsl_write::*;
use std::collections::BTreeMap;
pub fn limits() -> WriteLimits {
    WriteLimits {
        canonical: JsonLimits {
            max_bytes: 1_000_000,
            max_depth: 128,
            max_nodes: 100_000,
        },
        max_decimal_bytes: 100_000,
    }
}
pub fn snapshot(canonical: WriteLimits) -> WriteSnapshotLimits {
    WriteSnapshotLimits {
        canonical,
        metadata: SnapshotMetadataLimits {
            max_depth: 128,
            max_nodes: 100_000,
            max_text_bytes: 1_000_000,
        },
    }
}
pub fn parse(bytes: &[u8]) -> ValueData {
    parse_json_with_limits(bytes, limits().canonical).unwrap()
}
pub fn decode(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}
pub fn hex(b: &[u8]) -> String {
    b.iter().map(|b| format!("{b:02x}")).collect()
}
pub fn field<'a>(v: &'a ValueData, k: &str) -> &'a ValueData {
    let ValueData::Object(o) = v else { panic!() };
    &o[k]
}
pub fn text(v: &ValueData) -> &str {
    let ValueData::String(s) = v else { panic!() };
    s
}
pub fn get<'a>(v: &'a ValueData, k: &str) -> &'a str {
    text(field(v, k))
}
pub fn object(items: impl IntoIterator<Item = (&'static str, ValueData)>) -> ValueData {
    ValueData::Object(items.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
pub fn txt(v: impl Into<String>) -> ValueData {
    ValueData::String(v.into())
}
pub fn arr(v: &ValueData) -> &[ValueData] {
    let ValueData::Array(v) = v else { panic!() };
    v
}
pub fn value(d: &ValueData) -> Value {
    match d {
        ValueData::Null => Value::Null,
        ValueData::Bool(v) => Value::Bool(*v),
        ValueData::Number(n) => Value::Int(n.clone()),
        ValueData::String(s) => Value::String(s.clone()),
        ValueData::Array(a) => Value::List(a.iter().map(value).collect()),
        ValueData::Object(o) => match o.get("$type") {
            Some(ValueData::String(t)) if t == "Decimal" => {
                Value::Decimal(get(d, "value").parse().unwrap())
            }
            Some(ValueData::String(t)) if t == "Money" => Value::Money(Money::new(
                get(d, "amount").parse().unwrap(),
                get(d, "currency").parse().unwrap(),
            )),
            Some(ValueData::String(t)) if t == "Date" => Value::Date(get(d, "value").into()),
            Some(ValueData::String(t)) if t == "DateTime" => {
                Value::DateTime(get(d, "value").into())
            }
            _ => Value::Record(o.iter().map(|(k, v)| (k.clone(), value(v))).collect()),
        },
    }
}
pub fn arguments(d: &ValueData) -> BTreeMap<String, ValueEnvelope> {
    let ValueData::Object(o) = d else { panic!() };
    o.iter()
        .map(|(k, v)| {
            let ValueData::Object(e) = v else { panic!() };
            let ty = e.get("type").or_else(|| e.get("type_info")).unwrap();
            (
                k.clone(),
                ValueEnvelope::new(
                    value(field(v, "value")),
                    TypeRef::from_data(ty).unwrap(),
                    get(v, "presence").parse().unwrap(),
                    get(v, "completeness").parse().unwrap(),
                    get(v, "classification").parse().unwrap(),
                    arr(field(v, "provenance_refs"))
                        .iter()
                        .map(|v| text(v).into())
                        .collect(),
                ),
            )
        })
        .collect()
}
pub fn encoded(a: &BTreeMap<String, ValueEnvelope>) -> ValueData {
    ValueData::Object(
        a.iter()
            .map(|(k, e)| {
                (
                    k.clone(),
                    object([
                        ("value", e.value().to_data(100_000).unwrap()),
                        ("type", e.type_ref().to_data().unwrap()),
                        ("presence", txt(e.presence().as_str())),
                        ("completeness", txt(e.completeness().as_str())),
                        ("classification", txt(e.classification().as_str())),
                        (
                            "provenance_refs",
                            ValueData::Array(e.provenance_refs().iter().map(txt).collect()),
                        ),
                    ]),
                )
            })
            .collect(),
    )
}
pub fn model(i: &WriteIntent) -> ValueData {
    let s = i.metadata();
    object([
        ("tenant_id", txt(&s.tenant_id)),
        (
            "business_operation_id",
            txt(i.business_operation_id().as_str()),
        ),
        ("node_id", txt(&s.node_id)),
        ("tool_ref", txt(&s.tool_ref)),
        ("tool_id", txt(&s.tool_id)),
        ("approval_id", txt(&s.approval_id)),
        ("skill_semantic_hash", txt(&s.skill_semantic_hash)),
        ("canonical_arguments", encoded(i.canonical_arguments())),
        ("payload_hash", txt(i.payload_hash().as_str())),
        ("write_intent_hash", txt(i.write_intent_hash().as_str())),
    ])
}
pub fn hashes(
    a: &BTreeMap<String, ValueEnvelope>,
    skill: &str,
    node: &str,
    tool: &str,
) -> ValueData {
    let canonical = canonical_write_arguments(a, limits()).unwrap();
    let payload = compute_payload_hash(a, limits()).unwrap();
    object([
        ("canonical", txt(hex(&canonical))),
        ("payload_hash", txt(payload.as_str())),
        (
            "write_intent_hash",
            txt(
                compute_write_intent_hash(skill, node, tool, payload.as_str())
                    .unwrap()
                    .as_str(),
            ),
        ),
    ])
}
pub fn skill() -> SkillObject {
    NsoCodec::decode(
        include_bytes!("../../../../../compatibility/v0_2_1/nso/ir-1-1-canonical/expected.nso"),
        NsoLimits {
            max_bytes: 100_000,
            max_depth: 128,
            max_nodes: 100_000,
        },
    )
    .unwrap()
}
pub fn expression(d: &ValueData) -> Expression {
    Expression {
        node_id: get(d, "node").into(),
        type_info: TypeRef::new(TypeKind::Domain("DraftRecord".into())).unwrap(),
        kind: ExpressionKind::Write(WriteExpression {
            tool_ref: get(d, "tool_ref").into(),
            approval_ref: get(d, "approval").into(),
            arguments: arr(field(d, "names"))
                .iter()
                .map(|v| NamedExpression {
                    name: text(v).into(),
                    value: Expression {
                        node_id: "literal0001".into(),
                        type_info: TypeRef::new(TypeKind::Primitive("String".into())).unwrap(),
                        kind: ExpressionKind::Literal(LiteralValue::String("draft".into())),
                    },
                })
                .collect(),
        }),
    }
}
pub fn setup(d: &ValueData) -> (SkillObject, Expression, BTreeMap<String, ValueEnvelope>) {
    let mut skill = skill();
    skill.semantic_hash = get(d, "semantic").parse().unwrap();
    skill.required_tools = arr(field(d, "tools"))
        .iter()
        .map(|t| RequiredTool {
            tool_ref: get(t, "tool_ref").into(),
            tool_id: get(t, "tool_id").into(),
            version: "1.0.0".into(),
            kind: RequiredToolKind::V1_1 {
                capability: get(t, "capability").parse().unwrap(),
                risk_level: RiskLevel::R3,
            },
            contract_hash: get(t, "contract_hash").parse().unwrap(),
            required_scope: "finance:draft:write".into(),
            output_classification: DataClassification::Confidential,
        })
        .collect();
    (skill, expression(d), arguments(field(d, "arguments")))
}
pub fn context<'a>(d: &'a ValueData, skill: &'a SkillObject) -> WriteIntentContext<'a> {
    WriteIntentContext {
        tenant_id: get(d, "tenant"),
        business_operation_id: if field(d, "business") == &ValueData::Null {
            None
        } else {
            Some(get(d, "business"))
        },
        skill,
    }
}
pub fn baseline() -> ValueData {
    let line = include_str!("../fixtures/write-models-oracle.tsv")
        .lines()
        .find(|l| l.starts_with("base\t"))
        .unwrap();
    parse(&decode(line.split('\t').nth(1).unwrap()))
}
pub fn built() -> WriteIntent {
    let d = baseline();
    let (skill, e, a) = setup(&d);
    WriteIntentBuilder
        .build(&e, &a, context(&d, &skill), snapshot(limits()))
        .unwrap()
}
pub fn one(v: Value) -> BTreeMap<String, ValueEnvelope> {
    BTreeMap::from([(
        "payload".into(),
        ValueEnvelope::complete(
            v,
            TypeRef::new(TypeKind::Domain("Payload".into())).unwrap(),
            DataClassification::Confidential,
        ),
    )])
}
pub struct Proof {
    pub payload: String,
    pub intent: String,
}
impl ApprovalIdentityEvidence for Proof {
    fn payload_hash(&self) -> &str {
        &self.payload
    }
    fn write_intent_hash(&self) -> &str {
        &self.intent
    }
}
