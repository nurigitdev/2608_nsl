mod expression_support;
mod read_support;
mod support;
mod tool_support;
use expression_support::{arr, n, text};
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime::*;

fn statement(program: &str, severity: &str, on_fail: &str, id: &str, message: &str) -> Statement {
    Statement {
        node_id: "check-node".into(),
        kind: StatementKind::Check {
            check_id: id.into(),
            condition: expression_support::parse(program),
            severity: severity.into(),
            on_fail: on_fail.into(),
            message: message.into(),
            result_symbol_id: "check-result".into(),
        },
    }
}

#[test]
fn strict_rules_bool_presence_completeness_metadata_and_errors_match_oracle() {
    assert_eq!(StrictRuleEvaluator::SEMANTICS_PROFILE, "NSL-0.1-STRICT");
    let mut count = 0;
    for line in include_str!("fixtures/check-rules-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let predicate = ValueEnvelope::new(
            expression_support::value(f[0]),
            expression_support::ty(f[1]),
            f[2].parse().unwrap(),
            f[3].parse().unwrap(),
            f[4].parse().unwrap(),
            if f[5] == "-" {
                vec![]
            } else {
                f[5].split('+').map(Into::into).collect()
            },
        );
        let rule = statement(
            "L,true,Bool,condition",
            &support::decode(f[6]),
            &support::decode(f[7]),
            &support::decode(f[8]),
            &support::decode(f[9]),
        );
        let observed = StrictRuleEvaluator.evaluate(&rule, &predicate).map_or_else(
            |e| arr([text("CheckEvaluationError"), text(e.to_string())]),
            |check| {
                if predicate.completeness() != Completeness::Complete {
                    assert_eq!(check.status, CheckStatus::Unknown);
                }
                arr([text("ok"), check.to_data()])
            },
        );
        assert_eq!(support::json(&observed), support::decode(f[10]), "{line}");
        count += 1;
    }
    assert_eq!(count, 288);
}

fn error(e: CheckExecutionError) -> ValueData {
    match e {
        CheckExecutionError::Read(e) => read_support::error(e),
        CheckExecutionError::Binding(e) => read_support::error(ReadExecutionError::Expression(
            ExpressionRuntimeError::Binding(e),
        )),
        CheckExecutionError::Evaluation(e) => arr([
            text("RuntimeContractError"),
            text(e.to_string()),
            ValueData::Null,
            ValueData::Null,
            ValueData::Null,
            ValueData::Null,
        ]),
    }
}

#[test]
fn check_execution_read_aggregation_publication_errors_and_resources_match_oracle() {
    let rt = tool_support::runtime();
    let mut count = 0;
    for line in include_str!("fixtures/check-runtime-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let clock = expression_support::Clock::new(f[7]);
        let mut ctx = ExecutionContext::with_clock(
            read_support::skill(&f),
            read_support::request(f[6]),
            &clock,
        );
        ctx.invocation_counter = f[10].parse().unwrap();
        expression_support::bindings(&mut ctx, f[12]);
        let catalog = read_support::catalog(&f);
        let provider = read_support::Provider::new(&f);
        let authorizer = read_support::Auth::default();
        let rule = statement(f[1], f[13], f[14], "rule-가😀", "검증\n실패😀");
        let observed_result = rt
            .block_on(
                ReadEvaluator::new(&catalog, &provider, &authorizer, 100_000)
                    .execute_check(&mut ctx, &rule),
            )
            .map_or_else(error, |()| arr([text("ok")]));
        let usage = ctx.usage();
        let observed = arr([
            observed_result,
            arr(ctx.checks.iter().map(CheckResult::to_data)),
            ctx.resolve("check-result")
                .map_or(ValueData::Null, read_support::env),
            text(ctx.data_completeness().as_str()),
            arr([
                n(usage.tool_calls.as_str()),
                n(usage.loop_iterations.as_str()),
                n(usage.emitted_rows.as_str()),
                n(usage.max_collection_size_seen.as_str()),
            ]),
            n(ctx.invocation_counter.as_str()),
            n(clock.calls.get()),
            arr(authorizer.calls.lock().unwrap().clone()),
            arr(provider.requests.lock().unwrap().clone()),
        ]);
        assert_eq!(support::json(&observed), support::decode(f[15]), "{line}");
        count += 1;
    }
    assert_eq!(count, 114);
}
