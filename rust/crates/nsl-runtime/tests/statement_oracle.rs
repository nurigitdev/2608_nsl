mod expression_support;
mod read_support;
mod statement_support;
mod support;
mod tool_support;
use expression_support::{arr, text};
use nsl_ir::StatementKind;
use nsl_runtime::*;

#[test]
fn component_entrypoints_reject_wrong_statement_kinds_without_service_effects() {
    let line = include_str!("fixtures/statements-oracle.tsv")
        .lines()
        .nth(1)
        .unwrap();
    let f = line.split('\t').collect::<Vec<_>>();
    let clock = expression_support::Clock::new(f[10]);
    let skill = statement_support::skill(&f);
    let mut ctx = ExecutionContext::with_clock(skill, read_support::request(f[9]), &clock);
    let p = statement_support::provider_fields(&f);
    let catalog = read_support::catalog(&p);
    let provider = read_support::Provider::new(&p);
    let auth = read_support::Auth::default();
    let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
    let approval = nsl_ir::Statement {
        node_id: "approval".into(),
        kind: StatementKind::Approval(nsl_ir::ApprovalStatement {
            approval_id: "approval".into(),
            message: "approval".into(),
            policy: nsl_ir::ApprovalPolicy::default(),
        }),
    };
    let rt = tool_support::runtime();
    assert!(matches!(
        rt.block_on(evaluator.execute_foreach(&mut ctx, &approval)),
        Err(StatementExecutionError::Unsupported { .. })
    ));
    assert!(matches!(
        rt.block_on(evaluator.execute_emit(&mut ctx, &approval)),
        Err(StatementExecutionError::Unsupported { .. })
    ));
    assert_eq!(clock.calls.get(), 1);
    assert!(matches!(
        rt.block_on(evaluator.execute_block(&mut ctx, &[approval])),
        Err(StatementExecutionError::Unsupported { .. })
    ));
    assert!(provider.requests.lock().unwrap().is_empty() && auth.calls.lock().unwrap().is_empty());
    assert_eq!(ctx.usage().tool_calls, 0.into());
    assert_eq!(ctx.frame_count(), 1);
}

#[test]
fn foreach_emit_blocks_scope_resource_order_and_error_state_match_oracle() {
    let rt = tool_support::runtime();
    let mut count = 0;
    for line in include_str!("fixtures/statements-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let clock = expression_support::Clock::new(f[10]);
        let skill = statement_support::skill(&f);
        let mut ctx =
            ExecutionContext::with_clock(skill.clone(), read_support::request(f[9]), &clock);
        ctx.foreach_depth = f[12].parse().unwrap();
        ctx.current_node_id = Some("caller".into());
        statement_support::bindings(&mut ctx, f[4]);
        let p = statement_support::provider_fields(&f);
        let catalog = read_support::catalog(&p);
        let provider = read_support::Provider::new(&p);
        let auth = read_support::Auth::default();
        let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
        let result = rt.block_on(async {
            match f[2] {
                "block" => evaluator.execute_block(&mut ctx, &skill.body).await,
                "foreach" => evaluator.execute_foreach(&mut ctx, &skill.body[0]).await,
                "emit" => evaluator.execute_emit(&mut ctx, &skill.body[0]).await,
                _ => panic!(),
            }
        });
        let outcome = result.map_or_else(statement_support::error, |()| arr([text("ok")]));
        let observed =
            statement_support::observed(&ctx, outcome, clock.calls.get(), &auth, &provider);
        assert_eq!(
            support::json(&observed),
            support::decode(f[14]),
            "case={}; version={}; limits={}; depth={}; mutation={}",
            f[0],
            f[1],
            f[11],
            f[12],
            f[13]
        );
        count += 1;
    }
    assert_eq!(count, 306);
}

#[test]
fn approved_recursive_type_exceptions_stop_before_body_or_row_publication() {
    let rt = tool_support::runtime();
    let mut count = 0;
    for line in include_str!("fixtures/statements-type-exceptions.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        assert!(
            support::decode(f[14]).starts_with("[[\"ok\"],"),
            "unmodified Oracle accepted the malformed value"
        );
        let skill = statement_support::skill(&f);
        let clock = expression_support::Clock::new(f[10]);
        let mut ctx =
            ExecutionContext::with_clock(skill.clone(), read_support::request(f[9]), &clock);
        statement_support::bindings(&mut ctx, f[4]);
        let p = statement_support::provider_fields(&f);
        let catalog = read_support::catalog(&p);
        let provider = read_support::Provider::new(&p);
        let auth = read_support::Auth::default();
        let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
        let error = rt
            .block_on(async {
                if f[2] == "emit" {
                    evaluator.execute_emit(&mut ctx, &skill.body[0]).await
                } else {
                    evaluator.execute_block(&mut ctx, &skill.body).await
                }
            })
            .unwrap_err();
        let StatementExecutionError::Type(error) = error else {
            panic!("{error:?}")
        };
        assert_eq!(error.node_id, skill.body[0].node_id);
        assert_eq!(
            matches!(error.target, StatementTypeTarget::ForeachCollection),
            matches!(skill.body[0].kind, StatementKind::Foreach { .. })
        );
        assert!(ctx.outputs.is_empty() && ctx.checks.is_empty());
        assert_eq!(ctx.usage().loop_iterations, 0.into());
        assert_eq!(ctx.usage().emitted_rows, 0.into());
        assert_eq!(ctx.usage().tool_calls, 0.into());
        assert_eq!(ctx.invocation_counter, 0.into());
        assert_eq!(ctx.foreach_depth, 0);
        assert_eq!(ctx.frame_count(), 1);
        assert!(
            provider.requests.lock().unwrap().is_empty() && auth.calls.lock().unwrap().is_empty()
        );
        assert!(ctx.resolve("iterator").is_err() && ctx.resolve("local").is_err());
        count += 1;
    }
    assert_eq!(count, 68);
}
