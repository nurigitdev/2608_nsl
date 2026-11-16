use std::collections::BTreeMap;

use nsl_core::{Integer, Value, ValueData};

use crate::{
    Expression, ExpressionKind, LiteralValue, NamedExpression, SkillObject, Statement,
    StatementKind,
};

use super::{NsoCodecError, Result};

fn text(text: &str) -> ValueData {
    text.into()
}

fn number(value: &Integer) -> ValueData {
    ValueData::Number(value.clone())
}

fn array<T>(items: &[T], project: impl FnMut(&T) -> Result<ValueData>) -> Result<ValueData> {
    items
        .iter()
        .map(project)
        .collect::<Result<Vec<_>>>()
        .map(ValueData::Array)
}

macro_rules! object {
    ($($key:literal => $value:expr),* $(,)?) => {
        BTreeMap::from([$(($key.into(), $value)),*])
    };
}

pub(super) fn literal(value: &LiteralValue, max_decimal_bytes: usize) -> Result<ValueData> {
    let value = match value {
        LiteralValue::Bool(value) => Value::Bool(*value),
        LiteralValue::Int(value) => Value::Int(value.clone()),
        LiteralValue::String(value) => Value::String(value.clone()),
        LiteralValue::Decimal(value) => Value::Decimal(value.clone()),
        LiteralValue::Money(value) => Value::Money(value.clone()),
    };
    value
        .to_data(max_decimal_bytes)
        .map_err(NsoCodecError::Decimal)
}

fn named(values: &[NamedExpression], max_decimal_bytes: usize) -> Result<ValueData> {
    array(values, |item| {
        Ok(ValueData::Object(object!(
            "name" => text(&item.name), "value" => expression(&item.value, max_decimal_bytes)?,
        )))
    })
}

pub(super) fn expression(node: &Expression, max_decimal_bytes: usize) -> Result<ValueData> {
    let (kind, mut fields) = match &node.kind {
        ExpressionKind::Literal(value) => (
            "literal",
            object!("value" => literal(value, max_decimal_bytes)?),
        ),
        ExpressionKind::SymbolRef { symbol_id } => {
            ("symbol_ref", object!("symbol_id" => text(symbol_id)))
        }
        ExpressionKind::Field { source, field } => (
            "field",
            object!("source" => expression(source, max_decimal_bytes)?, "field" => text(field)),
        ),
        ExpressionKind::Projection { source, field } => (
            "project",
            object!("source" => expression(source, max_decimal_bytes)?, "field" => text(field)),
        ),
        ExpressionKind::Call {
            function,
            arguments,
        } => (
            "call",
            object!(
                "function" => text(function), "arguments" => array(arguments, |node| expression(node, max_decimal_bytes))?,
            ),
        ),
        ExpressionKind::Binary {
            operator,
            left,
            right,
        } => (
            "binary",
            object!(
                "operator" => text(operator), "left" => expression(left, max_decimal_bytes)?, "right" => expression(right, max_decimal_bytes)?,
            ),
        ),
        ExpressionKind::Read {
            tool_ref,
            arguments,
            result_policy,
        } => (
            "read",
            object!(
                "tool_ref" => text(tool_ref), "arguments" => named(arguments, max_decimal_bytes)?,
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
                "tool_ref" => text(&write.tool_ref), "arguments" => named(&write.arguments, max_decimal_bytes)?,
                "approval_ref" => text(&write.approval_ref),
            ),
        ),
    };
    fields.extend(object!("node_id" => text(&node.node_id), "kind" => text(kind), "type" => node.type_info.to_data().map_err(NsoCodecError::Type)?));
    Ok(ValueData::Object(fields))
}

pub(super) fn statement(node: &Statement, max_decimal_bytes: usize) -> Result<ValueData> {
    let (kind, mut fields) = match &node.kind {
        StatementKind::Let {
            target_symbol_id,
            value,
        } => (
            "let",
            object!(
                "target_symbol_id" => text(target_symbol_id), "value" => expression(value, max_decimal_bytes)?,
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
                "iterator_symbol_id" => text(iterator_symbol_id), "collection" => expression(collection, max_decimal_bytes)?,
                "max_iterations" => number(max_iterations), "body" => array(body, |node| statement(node, max_decimal_bytes))?,
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
                "check_id" => text(check_id), "condition" => expression(condition, max_decimal_bytes)?, "severity" => text(severity),
                "on_fail" => text(on_fail), "message" => text(message), "result_symbol_id" => text(result_symbol_id),
                "data_policy" => ValueData::Object(object!(
                    "require_complete" => ValueData::Bool(true), "on_partial" => text("UNKNOWN"), "on_unknown" => text("UNKNOWN"),
                )),
            ),
        ),
        StatementKind::Emit { fields } => (
            "emit",
            object!("fields" => named(fields, max_decimal_bytes)?),
        ),
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
    Ok(ValueData::Object(fields))
}

pub(super) fn project(skill: &SkillObject, max_decimal_bytes: usize) -> Result<ValueData> {
    Ok(ValueData::Object(object!(
        "format" => text("NSO"), "ir_version" => text(skill.ir_version.as_str()),
        "language" => ValueData::Object(object!("name" => text("NSL"), "version" => text(&skill.language_version))),
        "skill" => ValueData::Object(object!("id" => text(&skill.skill_id), "version" => text(&skill.skill_version), "risk" => text(&skill.risk))),
        "semantics_profile" => text(&skill.semantics_profile),
        "features" => ValueData::Array(skill.features.iter().map(|s| text(s)).collect()),
        "symbols" => array(&skill.symbols, |s| Ok(ValueData::Object(object!(
            "symbol_id" => text(&s.symbol_id), "name" => text(&s.name), "category" => text(&s.category),
            "type" => s.type_info.to_data().map_err(NsoCodecError::Type)?, "classification" => text(s.classification.as_str()),
        ))))?,
        "requires" => array(&skill.required_tools, |tool| {
            let mut fields = object!("tool_ref" => text(&tool.tool_ref), "tool_id" => text(&tool.tool_id),
                "version" => text(&tool.version), "capability" => text(tool.kind.capability().as_str()),
                "contract_hash" => text(tool.contract_hash.as_str()), "required_scope" => text(&tool.required_scope),
                "output_classification" => text(tool.output_classification.as_str()));
            if let Some(risk_level) = tool.kind.risk_level() {
                fields.insert("risk_level".into(), text(risk_level.as_str()));
            }
            Ok(ValueData::Object(fields))
        })?,
        "limits" => ValueData::Object(object!("tool_calls" => number(&skill.limits.tool_calls),
            "loop_iterations" => number(&skill.limits.loop_iterations), "emitted_rows" => number(&skill.limits.emitted_rows),
            "collection_size" => number(&skill.limits.collection_size), "duration_ms" => number(&skill.limits.duration_ms))),
        "inputs" => array(&skill.inputs, |s| Ok(ValueData::Object(object!(
            "symbol_id" => text(&s.symbol_id), "name" => text(&s.name), "type" => s.type_info.to_data().map_err(NsoCodecError::Type)?,
            "required" => ValueData::Bool(s.required), "classification" => text(s.classification.as_str()),
        ))))?,
        "contexts" => array(&skill.contexts, |s| Ok(ValueData::Object(object!(
            "symbol_id" => text(&s.symbol_id), "name" => text(&s.name), "type" => s.type_info.to_data().map_err(NsoCodecError::Type)?,
            "path" => ValueData::Array(s.path.iter().map(|part| text(part)).collect()), "classification" => text(s.classification.as_str()),
        ))))?,
        "output" => array(&skill.outputs, |s| Ok(ValueData::Object(object!(
            "name" => text(&s.name), "type" => s.type_info.to_data().map_err(NsoCodecError::Type)?, "classification" => text(s.classification.as_str()),
        ))))?,
        "body" => array(&skill.body, |node| statement(node, max_decimal_bytes))?,
        "analysis" => ValueData::Object(object!("max_tool_calls" => number(&skill.analysis.max_tool_calls),
            "max_loop_iterations" => number(&skill.analysis.max_loop_iterations), "max_emit_records" => number(&skill.analysis.max_emit_records),
            "bounded" => ValueData::Bool(skill.analysis.bounded))),
        "hashes" => ValueData::Object(object!("semantic_sha256" => text(skill.semantic_hash.as_str()),
            "source_bundle_sha256" => text(skill.build.source_bundle_sha256.as_str()))),
        "build" => ValueData::Object(object!("root_source" => text(&skill.build.root_source),
            "source_bundle_sha256" => text(skill.build.source_bundle_sha256.as_str()),
            "sources" => array(&skill.build.sources, |s| Ok(ValueData::Object(object!(
                "logical_path" => text(&s.logical_path), "sha256" => text(s.content_hash.as_str()),
                "size_bytes" => number(&s.size_bytes), "is_root" => ValueData::Bool(s.is_root),
            ))))?,
        )),
    )))
}
