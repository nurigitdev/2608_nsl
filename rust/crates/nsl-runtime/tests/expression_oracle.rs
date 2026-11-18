mod expression_support;
mod support;
use expression_support::*;
use nsl_core::{Value, ValueData};
use nsl_runtime::*;
use std::sync::Arc;

#[test]
fn frozen_pure_expression_let_and_guard_observations() {
    let mut count = 0;
    for line in include_str!("fixtures/expressions-oracle.tsv")
        .lines()
        .filter(|s| !s.starts_with('#'))
    {
        let f: Vec<_> = line.split('\t').collect();
        let clock = Clock::new(f[3]);
        let mut skill = (*support::skill()).clone();
        skill.limits.collection_size = f[4].parse().unwrap();
        skill.limits.duration_ms = 1.into();
        let mut ctx = ExecutionContext::with_clock(Arc::new(skill), support::request(), &clock);
        bindings(&mut ctx, f[2]);
        let expression = parse(f[1]);
        let result = if f[0] == "eval" {
            ExpressionEvaluator
                .evaluate(&mut ctx, &expression)
                .map(|v| snapshot(&v))
        } else {
            if f[0] == "let_nested" {
                ctx.push_frame();
            }
            ExpressionEvaluator
                .execute_let(&mut ctx, "target", &expression)
                .and_then(|()| {
                    if f[0] == "let_twice" {
                        ExpressionEvaluator.execute_let(&mut ctx, "target", &expression)?;
                    }
                    Ok(arr([text("ok"), ValueData::Null]))
                })
        }
        .unwrap_or_else(error);
        let r = ctx.usage();
        let observed = arr([
            result,
            text(ctx.data_completeness().as_str()),
            arr([
                n(r.tool_calls.as_str()),
                n(r.loop_iterations.as_str()),
                n(r.emitted_rows.as_str()),
                n(r.max_collection_size_seen.as_str()),
            ]),
            n(clock.calls.get()),
            ctx.resolve("target").map_or(ValueData::Null, snapshot),
        ]);
        assert_eq!(
            support::json(&observed),
            support::decode(f.last().unwrap()),
            "{line}"
        );
        assert_eq!(ctx.invocation_counter, 0.into());
        assert!(ctx.checks.is_empty() && ctx.outputs.is_empty());
        assert!(ctx.current_node_id.is_none());
        count += 1;
    }
    assert_eq!(count, 719);
}

#[test]
fn approved_int_division_exception_rejects_every_non_decimal_declaration() {
    let mut count = 0;
    for line in include_str!("fixtures/expression-division-exceptions.tsv")
        .lines()
        .filter(|s| !s.starts_with('#'))
    {
        let f: Vec<_> = line.split('\t').collect();
        let mut ctx = ExecutionContext::with_clock(
            support::skill(),
            support::request(),
            support::Clock::default(),
        );
        let expression = parse(f[1]);
        let error = ExpressionEvaluator
            .execute_let(&mut ctx, "target", &expression)
            .unwrap_err();
        let ExpressionRuntimeError::Evaluation(error) = error else {
            panic!()
        };
        assert_eq!(error.node_id, "divide");
        assert_eq!(error.detail_code, "EXPRESSION_TYPE_MISMATCH");
        assert_eq!(error.to_string(), "expression type mismatch at divide");
        assert!(ctx.resolve("target").is_err());
        let oracle = support::decode(f.last().unwrap());
        if f[1].contains("L,zero,") {
            assert!(oracle.contains("EXPRESSION_EVALUATION_ERROR"));
        } else {
            assert!(oracle.contains("oracle_float"));
        }
        count += 1;
    }
    assert_eq!(count, 24);
    for denominator in ["neg", "one", "two"] {
        let mut ctx = ExecutionContext::new(support::skill(), support::request());
        let expression = parse(&format!(
            "L,one,Int,left;L,{denominator},Int,right;B,DIV,Decimal,divide"
        ));
        assert!(matches!(
            ExpressionEvaluator
                .evaluate(&mut ctx, &expression)
                .unwrap()
                .value(),
            Value::Decimal(_)
        ));
    }
}
