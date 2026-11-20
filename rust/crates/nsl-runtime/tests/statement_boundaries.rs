mod expression_support;
mod read_support;
mod support;
mod tool_support;
use nsl_contract::ToolContractCatalog;
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime::*;
use std::{
    collections::BTreeMap,
    error::Error,
    future::{Future, poll_fn},
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    task::Poll,
};

fn fields() -> Vec<&'static str> {
    vec![
        "1-0", "-", "Int", "seven", "PARTIAL", "valid", "read", "0", "100", "100", "0", "valid",
    ]
}
fn literal() -> Expression {
    expression_support::parse("L,seven,Int,literal")
}
fn read() -> Expression {
    expression_support::parse("L,seven,Int,z;L,one,Int,a;R,t0,z+a,5,Int,read")
}
fn emit(node: &str) -> Statement {
    Statement {
        node_id: node.into(),
        kind: StatementKind::Emit {
            fields: vec![NamedExpression {
                name: "value".into(),
                value: literal(),
            }],
        },
    }
}
fn foreach(node: &str, iterator: &str, body: Vec<Statement>) -> Statement {
    Statement {
        node_id: node.into(),
        kind: StatementKind::Foreach {
            iterator_symbol_id: iterator.into(),
            collection: expression_support::parse("S,source,ListInt,source"),
            max_iterations: 100.into(),
            body,
        },
    }
}
fn skill() -> SkillObject {
    let mut skill = (*read_support::skill(&fields())).clone();
    skill.inputs.clear();
    skill.contexts.clear();
    skill.symbols.clear();
    skill.outputs = vec![OutputField {
        name: "value".into(),
        type_info: expression_support::ty("Int"),
        classification: DataClassification::Restricted,
    }];
    skill.limits.loop_iterations = 100.into();
    skill.limits.emitted_rows = 100.into();
    skill
}
fn context(skill: SkillObject) -> ExecutionContext<support::Clock> {
    let mut ctx = ExecutionContext::with_clock(
        Arc::new(skill),
        read_support::request("read"),
        support::Clock::default(),
    );
    ctx.bind(
        BindingNamespace::Variable,
        "source".into(),
        ValueEnvelope::complete(
            Value::List(vec![Value::Int(1.into())]),
            expression_support::ty("ListInt"),
            DataClassification::Internal,
        ),
    )
    .unwrap();
    ctx
}

#[test]
fn both_nso_versions_round_trip_foreach_emit_order_metadata_and_output_schema() {
    let rt = tool_support::runtime();
    let limits = NsoLimits {
        max_bytes: 100_000,
        max_depth: 64,
        max_nodes: 10_000,
    };
    for version in ["1-0", "1-1"] {
        let mut f = fields();
        f[0] = version;
        let mut s = (*read_support::skill(&f)).clone();
        s.inputs.clear();
        s.contexts.clear();
        s.symbols = vec![
            SymbolSpec {
                symbol_id: "source".into(),
                name: "source".into(),
                category: "VARIABLE".into(),
                type_info: expression_support::ty("ListInt"),
                classification: DataClassification::Confidential,
            },
            SymbolSpec {
                symbol_id: "iterator".into(),
                name: "iterator".into(),
                category: "ITERATOR".into(),
                type_info: expression_support::ty("Int"),
                classification: DataClassification::Confidential,
            },
        ];
        s.outputs = vec![OutputField {
            name: "value".into(),
            type_info: expression_support::ty("Int"),
            classification: DataClassification::Restricted,
        }];
        s.body = vec![foreach(
            "foreach",
            "iterator",
            vec![Statement {
                node_id: "emit".into(),
                kind: StatementKind::Emit {
                    fields: vec![NamedExpression {
                        name: "value".into(),
                        value: expression_support::parse("S,iterator,Int,value"),
                    }],
                },
            }],
        )];
        s.limits.loop_iterations = 3.into();
        s.limits.emitted_rows = 3.into();
        s.limits.collection_size = 3.into();
        s.semantic_hash = s.compute_semantic_sha256_with_limits(limits).unwrap();
        let bytes = NsoCodec::encode_with_limits(&s, limits).unwrap();
        let decoded = NsoCodec::decode(&bytes, limits).unwrap();
        assert_eq!(
            bytes,
            NsoCodec::encode_with_limits(&decoded, limits).unwrap()
        );
        let catalog = ToolContractCatalog::new(vec![]).unwrap();
        let provider = MockToolExecutor::new(catalog.clone(), BTreeMap::new(), 100_000);
        let auth = StaticReadAuthorizer::default();
        let mut ctx = ExecutionContext::with_clock(
            Arc::new(decoded.clone()),
            read_support::request("read"),
            support::Clock::default(),
        );
        ctx.bind(
            BindingNamespace::Variable,
            "source".into(),
            ValueEnvelope::new(
                Value::List(vec![
                    Value::Int((-1).into()),
                    Value::Int(0.into()),
                    Value::Int(2.into()),
                ]),
                expression_support::ty("ListInt"),
                Presence::Present,
                Completeness::Partial,
                DataClassification::Confidential,
                vec!["prov:a".into(), "prov:a".into()],
            ),
        )
        .unwrap();
        rt.block_on(
            ReadEvaluator::new(&catalog, &provider, &auth, 100_000)
                .execute_block(&mut ctx, &decoded.body),
        )
        .unwrap();
        assert_eq!(
            ctx.outputs
                .iter()
                .map(|r| r.values()["value"].clone())
                .collect::<Vec<_>>(),
            vec![
                Value::Int((-1).into()),
                Value::Int(0.into()),
                Value::Int(2.into())
            ]
        );
        for record in &ctx.outputs {
            let value = &record.fields()[0].1;
            assert_eq!(value.presence(), Presence::Present);
            assert_eq!(value.completeness(), Completeness::Partial);
            assert_eq!(value.classification(), DataClassification::Confidential);
            assert_eq!(value.provenance_refs(), ["prov:a", "prov:a"]);
        }
        assert_eq!(ctx.usage().emitted_rows, 3.into());
        assert_eq!(ctx.usage().loop_iterations, 3.into());
        assert_eq!(ctx.frame_count(), 1);
        assert_eq!(ctx.foreach_depth, 0);
        assert!(ctx.current_node_id.is_none());
        assert!(ctx.resolve("iterator").is_err());
        assert_eq!(provider.call_count(), 0.into());
    }
}

#[test]
fn emit_stages_fields_and_preserves_completed_read_cost_before_recursive_rejection() {
    let rt = tool_support::runtime();
    let f = fields();
    let catalog = read_support::catalog(&f);
    let provider = read_support::Provider::new(&f);
    let auth = read_support::Auth::default();
    let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
    let mut s = skill();
    s.outputs = vec![
        OutputField {
            name: "z".into(),
            type_info: expression_support::ty("ListInt"),
            classification: DataClassification::Restricted,
        },
        OutputField {
            name: "a".into(),
            type_info: expression_support::ty("Int"),
            classification: DataClassification::Restricted,
        },
    ];
    let statement = Statement {
        node_id: "staged-emit".into(),
        kind: StatementKind::Emit {
            fields: vec![
                NamedExpression {
                    name: "a".into(),
                    value: read(),
                },
                NamedExpression {
                    name: "z".into(),
                    value: expression_support::parse("S,bad,ListInt,bad"),
                },
            ],
        },
    };
    let mut ctx = context(s);
    ctx.bind(
        BindingNamespace::Variable,
        "bad".into(),
        ValueEnvelope::complete(
            Value::Bool(true),
            expression_support::ty("ListInt"),
            DataClassification::Restricted,
        ),
    )
    .unwrap();
    let error = rt
        .block_on(evaluator.execute_emit(&mut ctx, &statement))
        .unwrap_err();
    assert_eq!(
        error,
        StatementExecutionError::Type(StatementTypeError {
            node_id: "staged-emit".into(),
            target: StatementTypeTarget::EmitValue("z".into())
        })
    );
    assert!(ctx.outputs.is_empty());
    assert_eq!(ctx.usage().emitted_rows, 0.into());
    assert_eq!(ctx.usage().tool_calls, 1.into());
    assert_eq!(ctx.invocation_counter, 1.into());
    assert_eq!(ctx.data_completeness(), Completeness::Partial);
    assert_eq!(provider.requests.lock().unwrap().len(), 1);
    // Declaration mismatch and exhausted row allowance must not invoke another READ.
    let mut s = skill();
    s.outputs[0].type_info = expression_support::ty("String");
    let mut ctx = context(s.clone());
    let declaration = Statement {
        node_id: "declaration".into(),
        kind: StatementKind::Emit {
            fields: vec![NamedExpression {
                name: "value".into(),
                value: read(),
            }],
        },
    };
    assert!(matches!(
        rt.block_on(evaluator.execute_emit(&mut ctx, &declaration)),
        Err(StatementExecutionError::Type(StatementTypeError {
            target: StatementTypeTarget::EmitDeclaration(_),
            ..
        }))
    ));
    s.limits.emitted_rows = 0.into();
    let mut ctx = context(s);
    assert_eq!(
        rt.block_on(evaluator.execute_emit(&mut ctx, &declaration)),
        Err(StatementExecutionError::Limit(LimitExceeded(
            ResourceLimit::EmittedRows
        )))
    );
    assert_eq!(provider.requests.lock().unwrap().len(), 1);
}

struct PendingProvider {
    entered: AtomicUsize,
    dropped: Arc<AtomicUsize>,
}
struct Dropped(Arc<AtomicUsize>);
impl Drop for Dropped {
    fn drop(&mut self) {
        self.0.fetch_add(1, Ordering::SeqCst);
    }
}
impl ToolExecutionPort for PendingProvider {
    fn execute<'a>(
        &'a self,
        _request: &'a ToolCallRequest,
        _cancel: CancellationSignal,
    ) -> ToolExecutionFuture<'a> {
        Box::pin(async move {
            self.entered.fetch_add(1, Ordering::SeqCst);
            let _guard = Dropped(self.dropped.clone());
            std::future::pending().await
        })
    }
}

#[test]
fn dropping_a_pending_nested_execution_restores_caller_scopes_and_keeps_prior_work() {
    let rt = tool_support::runtime();
    let catalog = read_support::catalog(&fields());
    let provider = PendingProvider {
        entered: AtomicUsize::new(0),
        dropped: Arc::new(AtomicUsize::new(0)),
    };
    let auth = StaticReadAuthorizer::default();
    let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
    let mut ctx = context(skill());
    ctx.push_frame();
    ctx.foreach_depth = 2;
    ctx.current_node_id = Some("caller".into());
    let statements = vec![
        Statement {
            node_id: "root-let".into(),
            kind: StatementKind::Let {
                target_symbol_id: "target".into(),
                value: literal(),
            },
        },
        emit("prior-emit"),
        foreach(
            "outer-loop",
            "iterator",
            vec![foreach(
                "inner-loop",
                "inner",
                vec![Statement {
                    node_id: "waiting-let".into(),
                    kind: StatementKind::Let {
                        target_symbol_id: "local".into(),
                        value: read(),
                    },
                }],
            )],
        ),
    ];
    rt.block_on(async {
        let mut execution = Box::pin(evaluator.execute_block(&mut ctx, &statements));
        poll_fn(|cx| {
            assert!(execution.as_mut().poll(cx).is_pending());
            Poll::Ready(())
        })
        .await;
        drop(execution);
    });
    assert_eq!(provider.entered.load(Ordering::SeqCst), 1);
    assert_eq!(provider.dropped.load(Ordering::SeqCst), 1);
    assert_eq!(ctx.frame_count(), 2);
    assert_eq!(ctx.foreach_depth, 2);
    assert_eq!(ctx.current_node_id.as_deref(), Some("waiting-let"));
    assert!(
        ctx.resolve("iterator").is_err()
            && ctx.resolve("inner").is_err()
            && ctx.resolve("local").is_err()
    );
    assert_eq!(
        ctx.resolve("target").unwrap().value(),
        &Value::Int(7.into())
    );
    assert_eq!(ctx.outputs.len(), 1);
    assert_eq!(ctx.usage().emitted_rows, 1.into());
    assert_eq!(ctx.usage().loop_iterations, 2.into());
    assert_eq!(ctx.usage().tool_calls, 1.into());
    assert_eq!(ctx.invocation_counter, 1.into());
    rt.block_on(evaluator.execute_block(&mut ctx, &[emit("after-cancel")]))
        .unwrap();
    assert_eq!(ctx.outputs.len(), 2);
    assert_eq!(ctx.frame_count(), 2);
    assert_eq!(ctx.foreach_depth, 2);
}

#[test]
fn depth_bound_and_arbitrary_precision_limits_fit_a_small_stack() {
    std::thread::Builder::new()
        .stack_size(256 * 1024)
        .spawn(|| {
            let rt = tool_support::runtime();
            let catalog = ToolContractCatalog::new(vec![]).unwrap();
            let provider = MockToolExecutor::new(catalog.clone(), BTreeMap::new(), 100_000);
            let auth = StaticReadAuthorizer::default();
            let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
            for depth in [
                MAX_FOREACH_NESTING_DEPTH - 1,
                MAX_FOREACH_NESTING_DEPTH,
                MAX_FOREACH_NESTING_DEPTH + 1,
            ] {
                let mut body = vec![emit("emit")];
                for index in (0..depth).rev() {
                    body = vec![foreach(
                        &format!("loop-{index}"),
                        &format!("iterator-{index}"),
                        body,
                    )];
                }
                let mut s = skill();
                s.limits.loop_iterations = format!("1{}", "0".repeat(100)).parse().unwrap();
                let mut ctx = context(s);
                let result = rt.block_on(evaluator.execute_block(&mut ctx, &body));
                if depth <= MAX_FOREACH_NESTING_DEPTH {
                    result.unwrap();
                    assert_eq!(ctx.outputs.len(), 1);
                } else {
                    assert!(matches!(
                        result,
                        Err(StatementExecutionError::StatementLimit(
                            StatementLimitError {
                                kind: StatementLimitKind::ForeachDepth,
                                ..
                            }
                        ))
                    ));
                    assert!(ctx.outputs.is_empty());
                }
                assert_eq!(ctx.frame_count(), 1);
                assert_eq!(ctx.foreach_depth, 0);
                assert_eq!(
                    ctx.usage().loop_iterations.as_str(),
                    depth.min(MAX_FOREACH_NESTING_DEPTH).to_string()
                );
            }
            // A three-way branch at every level would require 3^16 leaves.
            // The global budget must stop it after exactly 100 charged entries.
            let mut body = vec![emit("leaf")];
            for index in (0..MAX_FOREACH_NESTING_DEPTH).rev() {
                body = vec![foreach(
                    &format!("branch-{index}"),
                    &format!("item-{index}"),
                    body,
                )];
            }
            let mut ctx = ExecutionContext::with_clock(
                Arc::new(skill()),
                read_support::request("read"),
                support::Clock::default(),
            );
            ctx.bind(
                BindingNamespace::Variable,
                "source".into(),
                ValueEnvelope::complete(
                    Value::List(vec![Value::Int(1.into()); 3]),
                    expression_support::ty("ListInt"),
                    DataClassification::Internal,
                ),
            )
            .unwrap();
            assert_eq!(
                rt.block_on(evaluator.execute_block(&mut ctx, &body)),
                Err(StatementExecutionError::Limit(LimitExceeded(
                    ResourceLimit::LoopIterations
                )))
            );
            assert_eq!(ctx.usage().loop_iterations, 100.into());
            assert_eq!(ctx.frame_count(), 1);
            assert_eq!(ctx.foreach_depth, 0);
            assert!(ctx.outputs.len() <= 100);
        })
        .unwrap()
        .join()
        .unwrap();
}

#[test]
fn error_displays_are_safe_and_futures_can_be_sent() {
    let f = fields();
    let catalog = read_support::catalog(&f);
    let provider = read_support::Provider::new(&f);
    let auth = StaticReadAuthorizer::default();
    let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
    let errors = [
        StatementExecutionError::from(StatementTypeError {
            node_id: "node".into(),
            target: StatementTypeTarget::ForeachCollection,
        }),
        StatementExecutionError::from(StatementLimitError {
            node_id: "node".into(),
            kind: StatementLimitKind::ForeachDepth,
        }),
        StatementExecutionError::from(LimitExceeded(ResourceLimit::CollectionSize)),
        StatementExecutionError::from(ReadExecutionError::Expression(
            ExpressionRuntimeError::Unexpected {
                kind: "ProviderError",
                detail: "PRIVATE-DETAIL".into(),
            },
        )),
        StatementExecutionError::from(CheckExecutionError::Evaluation(
            CheckEvaluationError::PredicateNotBool,
        )),
        StatementExecutionError::from(BindingError::Immutable("symbol".into())),
        StatementExecutionError::Contract("safe contract error".into()),
        StatementExecutionError::from(ModelError::Invalid("safe model error")),
        StatementExecutionError::Unsupported {
            node_id: "node".into(),
        },
    ];
    for error in errors {
        assert!(error.source().is_none());
        assert!(!error.to_string().contains("PRIVATE-DETAIL"));
    }
    assert!(provider.requests.lock().unwrap().is_empty());
    fn send<T: Send>(_: T) {}
    let mut ctx = ExecutionContext::new(Arc::new(skill()), read_support::request("read"));
    let statements = [emit("emit")];
    send(evaluator.execute_block(&mut ctx, &statements));
}

#[test]
fn bounded_generated_inputs_never_exceed_counters_or_leak_iteration_bindings() {
    let rt = tool_support::runtime();
    let catalog = ToolContractCatalog::new(vec![]).unwrap();
    let provider = MockToolExecutor::new(catalog.clone(), BTreeMap::new(), 100_000);
    let auth = StaticReadAuthorizer::default();
    let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
    let mut seed = 17_u64;
    for _ in 0..128 {
        seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
        let size = (seed % 5) as usize;
        let loops = ((seed >> 5) % 5) as i64;
        let rows = ((seed >> 9) % 5) as i64;
        let collection = ((seed >> 13) % 5) as i64;
        let invalid = seed & 32 != 0;
        let mut values = vec![Value::Int(1.into()); size];
        if invalid && !values.is_empty() {
            *values.last_mut().unwrap() = Value::Bool(true);
        }
        let mut s = skill();
        s.limits.loop_iterations = loops.into();
        s.limits.emitted_rows = rows.into();
        s.limits.collection_size = collection.into();
        let mut ctx = ExecutionContext::with_clock(
            Arc::new(s),
            read_support::request("read"),
            support::Clock::default(),
        );
        let source = ValueEnvelope::complete(
            Value::List(values),
            expression_support::ty("ListInt"),
            DataClassification::Internal,
        );
        ctx.bind(BindingNamespace::Variable, "source".into(), source.clone())
            .unwrap();
        let result = rt.block_on(evaluator.execute_block(
            &mut ctx,
            &[foreach("foreach", "iterator", vec![emit("emit")])],
        ));
        let used_loops = ctx.usage().loop_iterations.as_str().parse::<i64>().unwrap();
        let used_rows = ctx.usage().emitted_rows.as_str().parse::<i64>().unwrap();
        assert!(used_loops <= loops && used_rows <= rows && used_rows <= used_loops);
        assert_eq!(ctx.outputs.len(), used_rows as usize);
        assert_eq!(ctx.frame_count(), 1);
        assert_eq!(ctx.foreach_depth, 0);
        assert_eq!(ctx.resolve("source").unwrap(), &source);
        assert!(ctx.resolve("iterator").is_err());
        if invalid && size > 0 {
            assert!(result.is_err());
            assert_eq!(used_loops, 0);
            assert_eq!(used_rows, 0);
        }
    }
    assert_eq!(provider.call_count(), 0.into());
}
