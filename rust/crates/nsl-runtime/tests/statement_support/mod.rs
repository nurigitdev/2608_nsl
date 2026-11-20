#![allow(dead_code)]
use crate::{expression_support as e, read_support as r, support};
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime::*;
use std::{collections::BTreeMap, sync::Arc};

pub fn skill(f: &[&str]) -> Arc<SkillObject> {
    let mut s = NsoCodec::decode(
        support::decode(f[3]).as_bytes(),
        NsoLimits {
            max_bytes: 100_000,
            max_depth: 64,
            max_nodes: 10_000,
        },
    )
    .unwrap();
    match f[13] {
        "-" => {}
        "duplicate_output" => s.outputs.push(s.outputs[0].clone()),
        "duplicate_fields" => {
            let StatementKind::Emit { fields } = &mut s.body[0].kind else {
                panic!()
            };
            fields.push(fields[0].clone());
        }
        "empty_name" => {
            s.outputs[0].name.clear();
            let StatementKind::Emit { fields } = &mut s.body[0].kind else {
                panic!()
            };
            fields[0].name.clear();
        }
        mutation => {
            let StatementKind::Foreach { max_iterations, .. } = &mut s.body[0].kind else {
                panic!()
            };
            *max_iterations = mutation.strip_prefix("max=").unwrap().parse().unwrap();
        }
    }
    let limits = f[11]
        .split(',')
        .map(|s| s.parse::<Integer>().unwrap())
        .collect::<Vec<_>>();
    s.limits.collection_size = limits[0].clone();
    s.limits.loop_iterations = limits[1].clone();
    s.limits.emitted_rows = limits[2].clone();
    s.limits.tool_calls = limits[3].clone();
    s.limits.duration_ms = limits[4].clone();
    Arc::new(s)
}

pub fn provider_fields<'a>(f: &[&'a str]) -> Vec<&'a str> {
    vec![
        f[1], "-", f[5], f[6], f[7], f[8], f[9], f[10], "100", "100", "0", "valid",
    ]
}

pub fn bindings<C: RuntimeClock>(ctx: &mut ExecutionContext<C>, script: &str) {
    if script == "-" {
        return;
    }
    for binding in script.split(';') {
        let f = binding.split(',').collect::<Vec<_>>();
        let value = if f[2] == "record_extra" {
            Value::Record(BTreeMap::from([
                ("a".into(), Value::Int(7.into())),
                ("extra".into(), Value::Int(9.into())),
            ]))
        } else {
            e::value(f[2])
        };
        ctx.bind(
            match f[0] {
                "v" => BindingNamespace::Variable,
                "i" => BindingNamespace::Input,
                "c" => BindingNamespace::Context,
                "k" => BindingNamespace::Check,
                _ => panic!(),
            },
            f[1].into(),
            ValueEnvelope::new(
                value,
                e::ty(f[3]),
                f[4].parse().unwrap(),
                f[5].parse().unwrap(),
                f[6].parse().unwrap(),
                if f[7] == "-" {
                    vec![]
                } else {
                    f[7].split('+').map(Into::into).collect()
                },
            ),
        )
        .unwrap();
    }
}

pub fn error(error: StatementExecutionError) -> ValueData {
    let (class, message) = match error {
        StatementExecutionError::Read(e) => return r::error(e),
        StatementExecutionError::Check(CheckExecutionError::Read(e)) => return r::error(e),
        StatementExecutionError::Check(CheckExecutionError::Binding(e))
        | StatementExecutionError::Binding(e) => {
            return r::error(ReadExecutionError::Expression(
                ExpressionRuntimeError::Binding(e),
            ));
        }
        StatementExecutionError::Check(CheckExecutionError::Evaluation(e)) => {
            ("RuntimeContractError", e.to_string())
        }
        StatementExecutionError::Contract(message) => ("RuntimeContractError", message),
        StatementExecutionError::Type(e) => ("RuntimeContractError", e.to_string()),
        StatementExecutionError::Limit(e) => ("LimitExceeded", e.to_string()),
        StatementExecutionError::StatementLimit(e) => ("LimitExceeded", e.to_string()),
        StatementExecutionError::Model(e) => ("ValueError", e.to_string()),
        e => panic!("outside matching Oracle observations: {e}"),
    };
    e::arr([
        e::text(class),
        e::text(message),
        ValueData::Null,
        ValueData::Null,
        ValueData::Null,
        ValueData::Null,
    ])
}

pub fn observed<C: RuntimeClock>(
    ctx: &ExecutionContext<C>,
    outcome: ValueData,
    calls: usize,
    auth: &r::Auth,
    provider: &r::Provider,
) -> ValueData {
    let usage = ctx.usage();
    e::arr([
        outcome,
        e::arr(ctx.outputs.iter().map(|record| {
            e::arr(
                record
                    .fields()
                    .iter()
                    .map(|(name, value)| e::arr([e::text(name), r::env(value)])),
            )
        })),
        e::arr(ctx.checks.iter().map(CheckResult::to_data)),
        e::text(ctx.data_completeness().as_str()),
        e::arr([
            e::n(usage.tool_calls.as_str()),
            e::n(usage.loop_iterations.as_str()),
            e::n(usage.emitted_rows.as_str()),
            e::n(usage.max_collection_size_seen.as_str()),
        ]),
        e::n(ctx.foreach_depth),
        e::n(ctx.frame_count()),
        ctx.current_node_id
            .as_deref()
            .map_or(ValueData::Null, e::text),
        e::arr(
            [
                "source",
                "iterator",
                "inner",
                "local",
                "target",
                "check-result",
            ]
            .map(|name| ctx.resolve(name).map_or(ValueData::Null, r::env)),
        ),
        e::n(calls),
        e::arr(auth.calls.lock().unwrap().clone()),
        e::arr(provider.requests.lock().unwrap().clone()),
    ])
}
