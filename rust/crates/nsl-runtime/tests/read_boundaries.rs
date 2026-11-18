mod expression_support;
mod read_support;
mod support;
mod tool_support;
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime::*;
use read_support::*;
use std::{
    collections::{BTreeMap, BTreeSet},
    error::Error,
    future::{Future, poll_fn},
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
    task::Poll,
    time::Duration,
};

fn fields(setup: &str) -> Vec<&str> {
    vec![
        "1-0",
        "L,seven,Int,z;L,one,Int,a;R,t0,z+a,5,Int,read0",
        "Int",
        "seven",
        "COMPLETE",
        "valid",
        "read",
        "0",
        "100",
        "10",
        "0",
        setup,
    ]
}

#[test]
fn authorizer_reference_and_failure_rules_match_frozen_oracle() {
    let mut count = 0;
    for line in include_str!("fixtures/read-authorization-oracle.tsv")
        .lines()
        .filter(|l| !l.starts_with('#'))
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let mut principal = support::principal();
        let action = support::decode(f[1]);
        let required = support::decode(f[2]);
        let granted = support::decode(f[3]);
        principal.scopes = if granted == "-" {
            BTreeSet::new()
        } else {
            granted.split('+').map(Into::into).collect()
        };
        match f[0] {
            "tenant" => principal.tenant_id.clear(),
            "actor" => principal.actor_type = "OTHER".into(),
            "credential" => principal.auth_context_ref = "Bearer synthetic".into(),
            _ => {}
        }
        let scopes = if required == "-" {
            BTreeSet::new()
        } else {
            required.split('+').map(Into::into).collect()
        };
        let observed = StaticReadAuthorizer::default()
            .authorize(&principal, &action, &scopes)
            .map_or_else(
                |e| {
                    assert!(e.source().is_none());
                    arr([
                        text("AuthorizationError"),
                        text(e.to_string()),
                        e.decision_ref.map_or(ValueData::Null, text),
                    ])
                },
                |reference| arr([text("ok"), text(reference)]),
            );
        assert_eq!(support::json(&observed), support::decode(f[4]));
        count += 1;
    }
    assert_eq!(count, 10);
}

#[test]
fn forged_metadata_admitted_in_both_ir_versions_never_invokes_a_write_or_nested_read() {
    let rt = tool_support::runtime();
    let limits = NsoLimits {
        max_bytes: 100_000,
        max_depth: 64,
        max_nodes: 10_000,
    };
    for version in ["1-0", "1-1"] {
        for setup in ["write_scope", "scope"] {
            let mut f = fields(setup);
            f[0] = version;
            let mut skill = (*skill(&f)).clone();
            skill.inputs.clear();
            skill.contexts.clear();
            skill.outputs.clear();
            let expression =
                expression_support::parse("R,t1,-,5,Int,inner;L,one,Int,a;R,t0,z+a,5,Int,outer");
            skill.body = vec![Statement {
                node_id: "stmt".into(),
                kind: StatementKind::Let {
                    target_symbol_id: "target".into(),
                    value: expression,
                },
            }];
            skill.symbols.push(SymbolSpec {
                symbol_id: "target".into(),
                name: "result".into(),
                category: "VARIABLE".into(),
                type_info: ty("Int"),
                classification: DataClassification::Internal,
            });
            skill.semantic_hash = skill.compute_semantic_sha256_with_limits(limits).unwrap();
            let wire = NsoCodec::encode_with_limits(&skill, limits).unwrap();
            let decoded = NsoCodec::decode(&wire, limits).unwrap();
            let expression = if let StatementKind::Let { value, .. } = &decoded.body[0].kind {
                value.clone()
            } else {
                panic!()
            };
            let catalog = catalog(&f);
            let provider = Provider::new(&f);
            let auth = Auth::default();
            let mut ctx = ExecutionContext::with_clock(
                Arc::new(decoded),
                request("read"),
                support::Clock::default(),
            );
            let error = rt
                .block_on(
                    ReadEvaluator::new(&catalog, &provider, &auth, 100).execute_let(
                        &mut ctx,
                        "target",
                        &expression,
                    ),
                )
                .unwrap_err();
            assert!(
                matches!(error,ReadExecutionError::Tool(ToolCallError::Execution(e)) if e.code=="TOOL_CONTRACT_MISMATCH")
            );
            assert!(provider.requests.lock().unwrap().is_empty());
            assert!(auth.calls.lock().unwrap().is_empty());
            assert_eq!(ctx.usage().tool_calls, 0.into());
            assert!(ctx.resolve("target").is_err());
        }
    }
}

#[test]
fn injected_authorizer_uses_actual_scope_and_current_denial_prevents_all_children() {
    struct Deny(Mutex<Vec<(String, BTreeSet<String>)>>);
    impl ReadAuthorizationPort for Deny {
        fn authorize(
            &self,
            _p: &ExecutionPrincipal,
            a: &str,
            s: &BTreeSet<String>,
        ) -> Result<String, ReadAuthorizationError> {
            self.0.lock().unwrap().push((a.into(), s.clone()));
            Err(ReadAuthorizationError {
                message: "current authority denied".into(),
                decision_ref: Some("authz:denied".into()),
            })
        }
    }
    let f = fields("valid");
    let catalog = catalog(&f);
    let provider = Provider::new(&f);
    let deny = Deny(Mutex::new(vec![]));
    let mut ctx =
        ExecutionContext::with_clock(skill(&f), request("read"), support::Clock::default());
    let expression =
        expression_support::parse("R,t1,-,5,Int,inner;L,one,Int,a;R,t0,z+a,5,Int,outer");
    let e = tool_support::runtime()
        .block_on(
            ReadEvaluator::new(&catalog, &provider, &deny, 100).evaluate(&mut ctx, &expression),
        )
        .unwrap_err();
    assert_eq!(e.to_string(), "current authority denied");
    assert!(e.source().is_none());
    assert_eq!(
        *deny.0.lock().unwrap(),
        vec![(
            "tool:TEST.READ:execute".into(),
            BTreeSet::from(["read".into()])
        )]
    );
    assert!(provider.requests.lock().unwrap().is_empty());
    assert_eq!(ctx.usage().tool_calls, 0.into());
}

#[test]
fn complete_argument_set_with_a_duplicate_is_rejected_before_nested_reads() {
    let f = fields("valid");
    let catalog = catalog(&f);
    let provider = Provider::new(&f);
    let auth = Auth::default();
    let mut ctx =
        ExecutionContext::with_clock(skill(&f), request("read"), support::Clock::default());
    let expression = expression_support::parse(
        "R,t1,-,5,Int,child0;L,one,Int,a;R,t1,-,5,Int,child1;R,t0,z+a+z,5,Int,outer",
    );
    let error = tool_support::runtime()
        .block_on(
            ReadEvaluator::new(&catalog, &provider, &auth, 100).evaluate(&mut ctx, &expression),
        )
        .unwrap_err();
    assert!(
        matches!(error,ReadExecutionError::Tool(ToolCallError::Execution(e)) if e.code=="TOOL_ARGUMENT_MISMATCH")
    );
    assert!(auth.calls.lock().unwrap().is_empty());
    assert!(provider.requests.lock().unwrap().is_empty());
    assert_eq!(ctx.usage().tool_calls, 0.into());
}

#[test]
fn encoding_budgets_preserve_charged_attempts_and_do_not_expose_payloads() {
    let rt = tool_support::runtime();
    for v in [
        Value::Decimal("1e1000000".parse().unwrap()),
        Value::Int("9".repeat(4301).parse().unwrap()),
    ] {
        let f = fields("valid");
        let catalog = catalog(&f);
        let provider = Provider::new(&f);
        let auth = Auth::default();
        let mut ctx =
            ExecutionContext::with_clock(skill(&f), request("read"), support::Clock::default());
        ctx.bind(
            BindingNamespace::Variable,
            "payload".into(),
            ValueEnvelope::complete(v, ty("Decimal"), DataClassification::Restricted),
        )
        .unwrap();
        let expression =
            expression_support::parse("S,payload,Decimal,z;L,one,Int,a;R,t0,z+a,5,Int,read0");
        let error = rt
            .block_on(
                ReadEvaluator::new(&catalog, &provider, &auth, 64).execute_let(
                    &mut ctx,
                    "target",
                    &expression,
                ),
            )
            .unwrap_err();
        assert!(matches!(error, ReadExecutionError::Encoding(_)));
        assert!(!error.to_string().contains("999999999"));
        assert!(error.source().is_none());
        assert_eq!(ctx.usage().tool_calls, 1.into());
        assert_eq!(ctx.invocation_counter, 1.into());
        assert!(provider.requests.lock().unwrap().is_empty());
        assert!(ctx.resolve("target").is_err());
    }
    let mut f = fields("valid");
    f[2] = "Decimal";
    f[3] = "dp";
    let catalog = catalog(&f);
    let provider = Provider::new(&f);
    let auth = Auth::default();
    let mut ctx =
        ExecutionContext::with_clock(skill(&f), request("read"), support::Clock::default());
    let expression =
        expression_support::parse("L,seven,Int,z;L,one,Int,a;R,t0,z+a,5,Decimal,read0");
    let error = rt
        .block_on(ReadEvaluator::new(&catalog, &provider, &auth, 1).evaluate(&mut ctx, &expression))
        .unwrap_err();
    assert!(matches!(
        error,
        ReadExecutionError::Tool(ToolCallError::Encoding(_))
    ));
    assert!(error.source().is_none());
    assert!(error.to_string().contains("byte budget"));
    assert_eq!(provider.requests.lock().unwrap().len(), 1);
}

#[test]
fn typed_read_errors_have_safe_display_and_do_not_chain_provider_details() {
    let rt = tool_support::runtime();
    for setup in ["missing_catalog", "scope", "valid"] {
        let mut f = fields(setup);
        if setup == "valid" {
            f[9] = "0";
        }
        let catalog = catalog(&f);
        let provider = Provider::new(&f);
        let auth = Auth::default();
        let mut ctx =
            ExecutionContext::with_clock(skill(&f), request("read"), support::Clock::default());
        let error = rt
            .block_on(
                ReadEvaluator::new(&catalog, &provider, &auth, 100)
                    .evaluate(&mut ctx, &expression_support::parse(f[1])),
            )
            .unwrap_err();
        assert!(error.source().is_none());
        let message = error.to_string();
        assert!(!message.contains("synthetic"));
        assert_eq!(
            message,
            match setup {
                "missing_catalog" => "unknown tool contract",
                "scope" => "read contract metadata mismatch: TEST.READ",
                _ => "tool call limit exceeded",
            }
        );
    }
    let e = ReadExecutionError::from(ToolCallError::Provider("password=synthetic".into()));
    assert_eq!(e.to_string(), "tool provider failed");
    assert!(e.source().is_none());
    let f = fields("valid");
    let catalog = catalog(&f);
    let provider = Provider::new(&f);
    let auth = Auth::default();
    let mut ctx =
        ExecutionContext::with_clock(skill(&f), request("read"), support::Clock::default());
    let write = Expression {
        node_id: "write".into(),
        type_info: ty("Int"),
        kind: ExpressionKind::Write(WriteExpression {
            tool_ref: "t0".into(),
            arguments: vec![],
            approval_ref: "A".into(),
        }),
    };
    let e = rt
        .block_on(ReadEvaluator::new(&catalog, &provider, &auth, 100).evaluate(&mut ctx, &write))
        .unwrap_err();
    assert!(matches!(
        e,
        ReadExecutionError::Expression(ExpressionRuntimeError::Unsupported { .. })
    ));
    assert!(provider.requests.lock().unwrap().is_empty());
}

#[test]
fn mock_integration_read_let_is_immutable_and_contexts_are_isolated() {
    let rt = tool_support::runtime();
    let f = fields("valid");
    let c = contract("Int", "valid", false);
    let catalog = catalog(&f);
    let handler: FixtureHandler = Arc::new(|raw| {
        assert_eq!(raw["z"], Value::Int(7.into()));
        Ok(Value::Int(8.into()).into())
    });
    let mock = MockToolExecutor::new(
        nsl_contract::ToolContractCatalog::new([c]).unwrap(),
        BTreeMap::from([("TEST.READ".into(), handler)]),
        100,
    );
    let auth = StaticReadAuthorizer::default();
    let driver = ReadEvaluator::new(&catalog, &mock, &auth, 100);
    let mut ctx =
        ExecutionContext::with_clock(skill(&f), request("read"), support::Clock::default());
    let expression = expression_support::parse(f[1]);
    rt.block_on(driver.execute_let(&mut ctx, "target", &expression))
        .unwrap();
    assert_eq!(
        ctx.resolve("target").unwrap().value(),
        &Value::Int(8.into())
    );
    let original = ctx.resolve("target").unwrap().clone();
    let error = rt
        .block_on(driver.execute_let(&mut ctx, "target", &expression))
        .unwrap_err();
    assert!(matches!(
        error,
        ReadExecutionError::Expression(ExpressionRuntimeError::Binding(_))
    ));
    assert_eq!(ctx.resolve("target").unwrap(), &original);
    assert_eq!(mock.call_count(), 2.into());
    let other = ExecutionContext::with_clock(skill(&f), request("read"), support::Clock::default());
    assert!(other.resolve("target").is_err());
    assert_eq!(other.usage().tool_calls, 0.into());
    let v = rt
        .block_on(driver.evaluate(&mut ctx, &expression_support::parse("L,one,Int,pure")))
        .unwrap();
    assert_eq!(v.value(), &Value::Int(1.into()));
}

#[derive(Default)]
struct CleanupControl {
    started: CancellationSignal,
    cancel_seen: CancellationSignal,
    release: CancellationSignal,
    signals: Mutex<Vec<bool>>,
    calls: AtomicUsize,
}
struct CleanupProvider {
    mode: &'static str,
    clock: tool_support::Clock,
    control: Arc<CleanupControl>,
}
impl ToolExecutionPort for CleanupProvider {
    fn execute<'a>(
        &'a self,
        r: &'a ToolCallRequest,
        c: CancellationSignal,
    ) -> ToolExecutionFuture<'a> {
        Box::pin(async move {
            self.control.signals.lock().unwrap().push(c.is_requested());
            let call = self.control.calls.fetch_add(1, Ordering::SeqCst);
            if call == 0 {
                self.clock.set(1_000_000);
                self.control.started.request();
                c.cancelled().await;
                self.clock.set(2_000_000);
                self.control.cancel_seen.request();
                self.control.release.cancelled().await;
                self.clock.set(3_000_000);
                match self.mode {
                    "cancel" => return Err(ToolCallError::Cancelled),
                    "error" => {
                        return Err(ToolCallError::Provider("synthetic cleanup failure".into()));
                    }
                    _ => {}
                }
            }
            Ok(ToolResultEnvelope {
                invocation_id: r.invocation_id.clone(),
                tool_id: r.tool_id.clone(),
                tool_version: r.tool_version.clone(),
                value: Value::Int(7.into()),
                type_info: ty("Int"),
                presence: Presence::Present,
                completeness: Completeness::Complete,
                classification: DataClassification::Internal,
                result_hash: tool_result_hash(&Value::Int(7.into()), 100)
                    .unwrap()
                    .to_string(),
                snapshot_ref: None,
            })
        })
    }
}

#[test]
fn read_timeout_awaits_cleanup_preserves_measurements_and_has_no_sibling_cancellation_leak() {
    let rt = tool_support::runtime();
    rt.block_on(async {
        for (mode,duration,timeout,two_reads) in [("cancel",100,1,false),("return",100,1,false),("error",100,1,false),("cancel",1,2,false),("return",1,2,false),("return",100,1,true)] {
            let f=fields("short_timeout");
            let c=if timeout==1 {contract("Int","short_timeout",false)} else {
                nsl_contract::ToolContract::new(nsl_contract::ToolContractSpec {tool_id:"TEST.READ".into(),version:"1.0.0".into(),capability:"READ".into(),input_types:vec![("z".into(),ty("Int")),("a".into(),ty("Int"))],output_type:ty("Int"),required_scope:"read".into(),output_classification:DataClassification::Internal,options:nsl_contract::ToolContractOptions {timeout_ms:timeout,..Default::default()}}).unwrap()
            };
            let mut model=(*skill(&f)).clone();model.required_tools[0]=required(&c,"1-0","valid",false);model.limits.duration_ms=duration.into();
            let catalog=nsl_contract::ToolContractCatalog::new([c]).unwrap();let clock=tool_support::Clock::default();let mut ctx=ExecutionContext::with_clock(Arc::new(model),request("read"),clock.clone());
            let control=Arc::new(CleanupControl::default());let provider=MeasuringToolExecutor {delegate:CleanupProvider {mode,clock:clock.clone(),control:control.clone()},clock,sink:InMemoryToolMeasurementSink::default()};let auth=StaticReadAuthorizer::default();let driver=ReadEvaluator::new(&catalog,&provider,&auth,100);
            let program=if two_reads {format!("{};{};B,ADD,Int,total",f[1],f[1].replace("read0","read1"))} else {f[1].into()};let expression=expression_support::parse(&program);
            let future=driver.evaluate(&mut ctx,&expression);let mut future=std::pin::pin!(future);
            poll_fn(|cx|{assert!(future.as_mut().poll(cx).is_pending());Poll::Ready(())}).await;assert!(control.started.is_requested());tokio::time::advance(Duration::from_millis(1)).await;
            poll_fn(|cx|{assert!(future.as_mut().poll(cx).is_pending());Poll::Ready(())}).await;assert!(control.cancel_seen.is_requested());assert!(provider.sink.measurements().is_empty());
            tokio::time::advance(Duration::from_secs(60)).await;poll_fn(|cx|{assert!(future.as_mut().poll(cx).is_pending());Poll::Ready(())}).await;control.release.request();let result=future.await;
            if duration==1 {assert!(matches!(result,Err(ReadExecutionError::Expression(ExpressionRuntimeError::Limit(LimitExceeded(ResourceLimit::Duration))))));}
            else {match mode {"cancel"=>assert!(matches!(result,Err(ReadExecutionError::Tool(ToolCallError::Execution(e))) if e.code=="TOOL_TIMEOUT")),"error"=>assert!(matches!(result,Err(ReadExecutionError::Tool(ToolCallError::Provider(_))))),_=>assert_eq!(result.unwrap().value(),&Value::Int(if two_reads {14} else {7}.into()))}}
            let measurements=provider.sink.measurements();assert_eq!(measurements.len(),if two_reads {2} else {1});assert_eq!(measurements[0].duration_ns,3_000_000.into());assert_eq!(measurements[0].outcome,match mode {"cancel"=>ToolExecutionOutcome::Cancelled,"error"=>ToolExecutionOutcome::Error,_=>ToolExecutionOutcome::Returned});assert!(control.signals.lock().unwrap().iter().all(|v|!*v));
        }
    });
}
