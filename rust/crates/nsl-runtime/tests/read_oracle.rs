mod expression_support;
mod read_support;
mod support;
mod tool_support;
use expression_support::n;
use nsl_runtime::*;
use read_support::*;

#[test]
fn canonical_read_policy_request_result_identity_and_resource_order_match_oracle() {
    let rt = tool_support::runtime();
    let mut count = 0;
    for line in include_str!("fixtures/reads-oracle.tsv")
        .lines()
        .filter(|l| !l.starts_with('#'))
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let clock = expression_support::Clock::new(f[7]);
        let mut ctx = ExecutionContext::with_clock(skill(&f), request(f[6]), &clock);
        ctx.invocation_counter = f[10].parse().unwrap();
        let catalog = catalog(&f);
        let provider = Provider::new(&f);
        let auth = Auth::default();
        let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
        let expression = expression_support::parse(f[1]);
        let result = rt
            .block_on(evaluator.evaluate(&mut ctx, &expression))
            .map_or_else(error, |v| arr([text("ok"), env(&v)]));
        let usage = ctx.usage();
        let observed = arr([
            result,
            text(ctx.data_completeness().as_str()),
            arr([
                n(usage.tool_calls.as_str()),
                n(usage.loop_iterations.as_str()),
                n(usage.emitted_rows.as_str()),
                n(usage.max_collection_size_seen.as_str()),
            ]),
            n(ctx.invocation_counter.as_str()),
            n(clock.calls.get()),
            arr(auth.calls.lock().unwrap().clone()),
            arr(provider.requests.lock().unwrap().clone()),
        ]);
        assert_eq!(
            support::json(&observed),
            support::decode(f.last().unwrap()),
            "{line}"
        );
        count += 1;
    }
    assert_eq!(count, 273);
}

#[test]
fn approved_read_capability_and_scope_exceptions_reject_before_authorization_or_children() {
    let rt = tool_support::runtime();
    let mut count = 0;
    for line in include_str!("fixtures/read-security-exceptions.tsv")
        .lines()
        .filter(|l| !l.starts_with('#'))
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let mut ctx =
            ExecutionContext::with_clock(skill(&f), request(f[6]), support::Clock::default());
        let catalog = catalog(&f);
        let provider = Provider::new(&f);
        let auth = Auth::default();
        let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
        let expression = expression_support::parse(f[1]);
        let error = rt
            .block_on(evaluator.execute_let(&mut ctx, "target", &expression))
            .unwrap_err();
        let ReadExecutionError::Tool(ToolCallError::Execution(error)) = error else {
            panic!()
        };
        assert_eq!(error.code, "TOOL_CONTRACT_MISMATCH");
        assert!(provider.requests.lock().unwrap().is_empty());
        assert!(auth.calls.lock().unwrap().is_empty());
        assert_eq!(ctx.usage().tool_calls, 0.into());
        assert_eq!(ctx.invocation_counter, 0.into());
        assert!(ctx.resolve("target").is_err());
        let observed = support::decode(f.last().unwrap());
        assert!(observed.contains("authz-") && observed.contains("inv0001"));
        count += 1;
    }
    assert_eq!(count, 22);
}
