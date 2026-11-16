use std::collections::BTreeMap;

mod codec;
mod hash;
mod schema;

use nsl_core::{Integer, Money, TypeKind, TypeRef, Value, ValueData};
use nsl_ir::{
    ApprovalPolicy, ApprovalStatement, BuildMetadata, BuildSource, ContextSpec, Expression,
    ExpressionKind, InputSpec, IrVersion, LiteralValue, NamedExpression, OutputField, RequiredTool,
    RequiredToolKind, ResourceLimits, ResultPolicy, SkillObject, Statement, StatementKind,
    StaticAnalysis, SymbolSpec, UnsupportedIrVersion, WriteExpression,
};

include!("fixtures/oracle_models.rs");

fn ty(kind: TypeKind) -> TypeRef {
    TypeRef::new(kind).unwrap()
}

fn text(text: &str) -> ValueData {
    text.into()
}

fn number(value: &Integer) -> ValueData {
    ValueData::Number(value.clone())
}

fn array<T>(items: &[T], project: impl FnMut(&T) -> ValueData) -> ValueData {
    ValueData::Array(items.iter().map(project).collect())
}

macro_rules! object {
    ($($key:literal => $value:expr),* $(,)?) => {
        BTreeMap::from([$(($key.into(), $value)),*])
    };
}

fn literal(value: &LiteralValue) -> ValueData {
    let value = match value {
        LiteralValue::Bool(value) => Value::Bool(*value),
        LiteralValue::Int(value) => Value::Int(value.clone()),
        LiteralValue::String(value) => Value::String(value.clone()),
        LiteralValue::Decimal(value) => Value::Decimal(value.clone()),
        LiteralValue::Money(value) => Value::Money(value.clone()),
    };
    value.to_data(1024).unwrap()
}

fn named(values: &[NamedExpression]) -> ValueData {
    array(values, |item| {
        ValueData::Object(object!(
            "name" => text(&item.name), "value" => expression(&item.value),
        ))
    })
}

fn expression(node: &Expression) -> ValueData {
    let (kind, mut fields) = match &node.kind {
        ExpressionKind::Literal(value) => ("literal", object!("value" => literal(value))),
        ExpressionKind::SymbolRef { symbol_id } => {
            ("symbol_ref", object!("symbol_id" => text(symbol_id)))
        }
        ExpressionKind::Field { source, field } => (
            "field",
            object!("source" => expression(source), "field" => text(field)),
        ),
        ExpressionKind::Projection { source, field } => (
            "project",
            object!("source" => expression(source), "field" => text(field)),
        ),
        ExpressionKind::Call {
            function,
            arguments,
        } => (
            "call",
            object!(
                "function" => text(function), "arguments" => array(arguments, expression),
            ),
        ),
        ExpressionKind::Binary {
            operator,
            left,
            right,
        } => (
            "binary",
            object!(
                "operator" => text(operator), "left" => expression(left), "right" => expression(right),
            ),
        ),
        ExpressionKind::Read {
            tool_ref,
            arguments,
            result_policy,
        } => (
            "read",
            object!(
                "tool_ref" => text(tool_ref), "arguments" => named(arguments),
                "result_policy" => ValueData::Object(object!(
                    "required" => ValueData::Bool(result_policy.required),
                    "accept_partial" => ValueData::Bool(result_policy.accept_partial),
                    "empty_is_valid" => ValueData::Bool(result_policy.empty_is_valid),
                )),
            ),
        ),
        ExpressionKind::Write(write) => (
            "write",
            object!(
                "tool_ref" => text(&write.tool_ref), "arguments" => named(&write.arguments),
                "approval_ref" => text(&write.approval_ref),
            ),
        ),
    };
    fields.extend(object!("node_id" => text(&node.node_id), "kind" => text(kind), "type" => node.type_info.to_data().unwrap()));
    ValueData::Object(fields)
}

fn statement(node: &Statement) -> ValueData {
    let (kind, mut fields) = match &node.kind {
        StatementKind::Let {
            target_symbol_id,
            value,
        } => (
            "let",
            object!(
                "target_symbol_id" => text(target_symbol_id), "value" => expression(value),
            ),
        ),
        StatementKind::Foreach {
            iterator_symbol_id,
            collection,
            max_iterations,
            body,
        } => (
            "foreach",
            object!(
                "iterator_symbol_id" => text(iterator_symbol_id), "collection" => expression(collection),
                "max_iterations" => number(max_iterations), "body" => array(body, statement),
            ),
        ),
        StatementKind::Check {
            check_id,
            condition,
            severity,
            on_fail,
            message,
            result_symbol_id,
        } => (
            "check",
            object!(
                "check_id" => text(check_id), "condition" => expression(condition), "severity" => text(severity),
                "on_fail" => text(on_fail), "message" => text(message), "result_symbol_id" => text(result_symbol_id),
                "data_policy" => ValueData::Object(object!(
                    "require_complete" => ValueData::Bool(true), "on_partial" => text("UNKNOWN"), "on_unknown" => text("UNKNOWN"),
                )),
            ),
        ),
        StatementKind::Emit { fields } => ("emit", object!("fields" => named(fields))),
        StatementKind::Approval(approval) => {
            let mut policy = object!("required" => ValueData::Bool(true));
            if let Some(ttl) = &approval.policy.ttl_seconds {
                policy.insert("ttl_seconds".into(), number(ttl));
            }
            (
                "approval",
                object!("approval_id" => text(&approval.approval_id), "message" => text(&approval.message),
                "policy" => ValueData::Object(policy)),
            )
        }
    };
    fields.extend(object!("node_id" => text(&node.node_id), "kind" => text(kind)));
    ValueData::Object(fields)
}

fn project(skill: &SkillObject) -> ValueData {
    ValueData::Object(object!(
        "format" => text("NSO"), "ir_version" => text(skill.ir_version.as_str()),
        "language" => ValueData::Object(object!("name" => text("NSL"), "version" => text(&skill.language_version))),
        "skill" => ValueData::Object(object!("id" => text(&skill.skill_id), "version" => text(&skill.skill_version), "risk" => text(&skill.risk))),
        "semantics_profile" => text(&skill.semantics_profile),
        "features" => ValueData::Array(skill.features.iter().map(|s| text(s)).collect()),
        "symbols" => array(&skill.symbols, |s| ValueData::Object(object!(
            "symbol_id" => text(&s.symbol_id), "name" => text(&s.name), "category" => text(&s.category),
            "type" => s.type_info.to_data().unwrap(), "classification" => text(s.classification.as_str()),
        ))),
        "requires" => array(&skill.required_tools, |tool| {
            let mut fields = object!("tool_ref" => text(&tool.tool_ref), "tool_id" => text(&tool.tool_id),
                "version" => text(&tool.version), "capability" => text(tool.kind.capability().as_str()),
                "contract_hash" => text(tool.contract_hash.as_str()), "required_scope" => text(&tool.required_scope),
                "output_classification" => text(tool.output_classification.as_str()));
            if let Some(risk_level) = tool.kind.risk_level() {
                fields.insert("risk_level".into(), text(risk_level.as_str()));
            }
            ValueData::Object(fields)
        }),
        "limits" => ValueData::Object(object!("tool_calls" => number(&skill.limits.tool_calls),
            "loop_iterations" => number(&skill.limits.loop_iterations), "emitted_rows" => number(&skill.limits.emitted_rows),
            "collection_size" => number(&skill.limits.collection_size), "duration_ms" => number(&skill.limits.duration_ms))),
        "inputs" => array(&skill.inputs, |s| ValueData::Object(object!(
            "symbol_id" => text(&s.symbol_id), "name" => text(&s.name), "type" => s.type_info.to_data().unwrap(),
            "required" => ValueData::Bool(s.required), "classification" => text(s.classification.as_str()),
        ))),
        "contexts" => array(&skill.contexts, |s| ValueData::Object(object!(
            "symbol_id" => text(&s.symbol_id), "name" => text(&s.name), "type" => s.type_info.to_data().unwrap(),
            "path" => array(&s.path, |part| text(part)), "classification" => text(s.classification.as_str()),
        ))),
        "output" => array(&skill.outputs, |s| ValueData::Object(object!(
            "name" => text(&s.name), "type" => s.type_info.to_data().unwrap(), "classification" => text(s.classification.as_str()),
        ))),
        "body" => array(&skill.body, statement),
        "analysis" => ValueData::Object(object!("max_tool_calls" => number(&skill.analysis.max_tool_calls),
            "max_loop_iterations" => number(&skill.analysis.max_loop_iterations), "max_emit_records" => number(&skill.analysis.max_emit_records),
            "bounded" => ValueData::Bool(skill.analysis.bounded))),
        "hashes" => ValueData::Object(object!("semantic_sha256" => text(skill.semantic_hash.as_str()),
            "source_bundle_sha256" => text(skill.build.source_bundle_sha256.as_str()))),
        "build" => ValueData::Object(object!("root_source" => text(&skill.build.root_source),
            "source_bundle_sha256" => text(skill.build.source_bundle_sha256.as_str()),
            "sources" => array(&skill.build.sources, |s| ValueData::Object(object!(
                "logical_path" => text(&s.logical_path), "sha256" => text(s.content_hash.as_str()),
                "size_bytes" => number(&s.size_bytes), "is_root" => ValueData::Bool(s.is_root),
            ))))),
    ))
}

// Restricted to the strings in these test inputs. A general codec belongs to R3-S03.
fn render_fixture(data: &ValueData) -> String {
    match data {
        ValueData::Null => "null".into(),
        ValueData::Bool(value) => value.to_string(),
        ValueData::Number(value) => value.as_str().into(),
        ValueData::String(value) => {
            assert!(
                !value
                    .chars()
                    .any(|c| c <= '\u{1f}' || matches!(c, '"' | '\\'))
            );
            format!("\"{value}\"")
        }
        ValueData::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(render_fixture)
                .collect::<Vec<_>>()
                .join(",")
        ),
        ValueData::Object(fields) => format!(
            "{{{}}}",
            fields
                .iter()
                .map(|(key, value)| {
                    format!("{}:{}", render_fixture(&text(key)), render_fixture(value))
                })
                .collect::<Vec<_>>()
                .join(",")
        ),
    }
}

#[test]
fn read_only_ir_1_0_matches_oracle_object() {
    let skill = ir_1_0();
    assert_eq!(skill.ir_version, IrVersion::V1_0);
    assert!(
        skill
            .required_tools
            .iter()
            .all(|tool| tool.kind == RequiredToolKind::ReadV1_0)
    );
    assert_eq!(
        render_fixture(&project(&skill)),
        include_str!("../../../../compatibility/v0_2_1/nso/ir-1-0-canonical/expected.nso")
    );
}

#[test]
fn controlled_write_ir_1_1_matches_oracle_object() {
    let skill = ir_1_1();
    assert_eq!(skill.ir_version, IrVersion::V1_1);
    let mut approval = None;
    let mut linked = false;
    for node in &skill.body {
        match &node.kind {
            StatementKind::Approval(node) => approval = Some(node.approval_id.as_str()),
            StatementKind::Let { value, .. } => {
                if let ExpressionKind::Write(write) = &value.kind {
                    assert_eq!(Some(write.approval_ref.as_str()), approval);
                    assert_eq!(write.tool_ref, skill.required_tools[0].tool_ref);
                    linked = true;
                }
            }
            _ => {}
        }
    }
    assert!(linked);
    assert_eq!(
        render_fixture(&project(&skill)),
        include_str!("../../../../compatibility/v0_2_1/nso/ir-1-1-canonical/expected.nso")
    );
}

#[test]
fn unsupported_version_spellings_are_rejected() {
    for token in ["", "1", "1.2", "2.0", "1.00", " 1.1", "1.1\n"] {
        assert_eq!(token.parse::<IrVersion>(), Err(UnsupportedIrVersion));
    }
}

#[test]
fn typed_literals_match_the_oracle_without_float_conversion() {
    for (literal, expected) in literal_cases() {
        assert_eq!(render_fixture(&expression(&literal)), expected);
    }
}

#[test]
fn policy_defaults_and_optional_approval_ttl_match_the_oracle() {
    assert_eq!(ResultPolicy::default(), oracle_default_result_policy());
    assert_eq!(ApprovalPolicy::default().ttl_seconds, None);
    for (approval, expected) in approval_cases() {
        assert_eq!(render_fixture(&statement(&approval)), expected);
    }
}
