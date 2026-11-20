mod engine_support;
mod expression_support;
mod read_support;
mod support;
mod tool_support;
use engine_support as h;
use expression_support::arr;
use nsl_runtime::*;

#[test]
fn full_read_and_pre_write_results_semantic_views_requests_measurements_and_order_match_oracle() {
    let rt = tool_support::runtime();
    let mut count = 0;
    for line in include_str!("fixtures/runtime-gate-oracle.tsv")
        .lines()
        .skip(1)
        .chain(
            include_str!("fixtures/runtime-summary-oracle.tsv")
                .lines()
                .skip(1),
        )
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let s = h::skill(&f);
        let req = h::request(&f, &s);
        let clock = h::Clock::new(f[10]);
        let catalog = h::catalog(&f);
        let provider = h::Provider::new(&f);
        let auth = read_support::Auth::default();
        let measurements = h::Measurements {
            fail: f[8] == "sink-error",
            ..Default::default()
        };
        let debug = h::DebugSink {
            fail: f[18] == "fail",
            ..Default::default()
        };
        let mut engine =
            RuntimeEngine::new(&catalog, &provider, &auth, &measurements, h::options(f[14]));
        if f[18] != "off" {
            engine = engine.with_protected_debug_sink(&debug);
        }
        let result = rt.block_on(async {
            if f[2] == "prefix" {
                engine.execute_pre_write_prefix(s, req, &clock).await
            } else {
                engine.execute_with_clock(s, req, &clock).await
            }
        });
        if let Err(error) = &result
            && matches!(error.kind, RuntimeTerminalKind::InvalidValidationSummary(_))
        {
            let count = match h::val(f[4].split(',').nth(2).unwrap()) {
                nsl_core::Value::List(v) => v.len(),
                _ => unreachable!(),
            };
            assert_eq!(error.evidence.checks.len(), count);
            assert_eq!(error.evidence.outputs.len(), count);
            assert!(error.evidence.checks.iter().all(|c| c.check_id == "rule"));
            assert_eq!(error.evidence.resources.tool_calls, 1.into());
            assert_eq!(
                error.evidence.resources.loop_iterations,
                count.to_string().parse().unwrap()
            );
            assert_eq!(
                error.evidence.resources.emitted_rows,
                count.to_string().parse().unwrap()
            );
            assert_eq!(measurements.records.lock().unwrap().len(), 1);
            assert_eq!(
                error.to_string(),
                "runtime validation summary could not be constructed"
            );
            assert!(std::error::Error::source(error).is_none());
            assert!(!format!("{error:?}").contains("검증😀"));
        }
        for debug in debug.records.lock().unwrap().iter() {
            if f[4].contains(",secret,") {
                assert!(!debug.detail.contains("CONFIDENTIAL-PROJECT"));
            }
        }
        assert!(
            !support::json(&arr(provider.base.requests.lock().unwrap().clone()))
                .contains("TEST.WRITE")
        );
        let observed = arr([
            h::outcome(result),
            arr(auth.calls.lock().unwrap().clone()),
            arr(provider.base.requests.lock().unwrap().clone()),
            arr(measurements
                .records
                .lock()
                .unwrap()
                .iter()
                .map(h::measurement)),
            expression_support::n(clock.calls()),
            expression_support::n(debug.records.lock().unwrap().len()),
        ]);
        assert_eq!(
            support::json(&observed),
            support::decode(f[20]),
            "case={} version={} mutation={} scopes={} limits={} clock={}",
            f[0],
            f[1],
            f[12],
            f[9],
            f[11],
            f[10]
        );
        count += 1;
    }
    assert_eq!(count, 258);
}

#[test]
fn approved_recursive_admission_and_statement_exceptions_precede_effects() {
    let rt = tool_support::runtime();
    let mut count = 0;
    for line in include_str!("fixtures/runtime-admission-exceptions.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let skill = h::skill(&f);
        let req = h::request(&f, &skill);
        let clock = h::Clock::new(f[10]);
        let catalog = h::catalog(&f);
        let provider = h::Provider::new(&f);
        let auth = read_support::Auth::default();
        let measurements = h::Measurements::default();
        let engine = RuntimeEngine::new(
            &catalog,
            &provider,
            &auth,
            &measurements,
            h::options("production"),
        );
        let oracle = support::decode(f[20]);
        let result = rt.block_on(async {
            if f[2] == "prefix" {
                engine.execute_pre_write_prefix(skill, req, &clock).await
            } else {
                engine.execute_with_clock(skill, req, &clock).await
            }
        });
        if f[0] == "division-exception" {
            assert!(oracle.contains("\"status\":\"COMPLETED\""));
            let result = result.unwrap();
            assert_eq!(result.status, nsl_core::ExecutionStatus::Failed);
            let error = result.error.unwrap();
            assert_eq!(error.node_id.as_deref(), Some("division"));
            assert_eq!(
                error.detail_code.as_deref(),
                Some("EXPRESSION_TYPE_MISMATCH")
            );
        } else {
            let error = result.unwrap_err();
            match &error.kind {
                RuntimeTerminalKind::InputType(error) => {
                    assert!(oracle.contains("\"status\":\"COMPLETED\""));
                    assert_eq!(error.symbol_id, "source");
                    assert_eq!(
                        error.location,
                        if f[4].starts_with("c,") {
                            RuntimeBindingLocation::Context(vec!["nested".into(), "source".into()])
                        } else {
                            RuntimeBindingLocation::Input("source".into())
                        }
                    );
                    assert_eq!(clock.calls(), 2);
                }
                RuntimeTerminalKind::StatementType(error) => {
                    assert!(oracle.contains("\"code\":\"NSL-E8001\""));
                    assert_eq!(error.node_id, "emit");
                    assert_eq!(
                        error.target,
                        StatementTypeTarget::EmitDeclaration("value".into())
                    );
                }
                _ => panic!("{error:?}"),
            }
            assert_eq!(error.evidence.resources, ResourceUsage::default());
            assert!(error.evidence.checks.is_empty() && error.evidence.outputs.is_empty());
            assert!(std::error::Error::source(&error).is_none());
            assert!(!error.to_string().contains("NSL-E"));
        }
        assert!(provider.base.requests.lock().unwrap().is_empty());
        assert!(measurements.records.lock().unwrap().is_empty());
        assert_eq!(
            auth.calls.lock().unwrap().len(),
            1,
            "skill authorization only"
        );
        count += 1;
    }
    assert_eq!(count, 76);
}

#[test]
fn earlier_approved_actual_contract_authority_fix_remains_fail_closed() {
    let rt = tool_support::runtime();
    let mut count = 0;
    for line in include_str!("fixtures/runtime-security-exceptions.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let s = h::skill(&f);
        let req = h::request(&f, &s);
        let catalog = h::catalog(&f);
        let provider = h::Provider::new(&f);
        let auth = read_support::Auth::default();
        let m = h::Measurements::default();
        let clock = h::Clock::new("0");
        let engine = RuntimeEngine::new(&catalog, &provider, &auth, &m, h::options("production"));
        assert!(support::decode(f[20]).contains("\"status\":\"COMPLETED\""));
        let result = rt
            .block_on(async {
                if f[2] == "prefix" {
                    engine.execute_pre_write_prefix(s, req, &clock).await
                } else {
                    engine.execute_with_clock(s, req, &clock).await
                }
            })
            .unwrap();
        assert_eq!(result.status, nsl_core::ExecutionStatus::Failed);
        let error = result.error.unwrap();
        assert_eq!(error.code, "NSL-E8001");
        assert_eq!(error.message, "tool contract metadata mismatch: TEST.READ");
        assert!(provider.base.requests.lock().unwrap().is_empty());
        assert!(m.records.lock().unwrap().is_empty());
        assert_eq!(auth.calls.lock().unwrap().len(), 1);
        assert_eq!(result.resources, ResourceUsage::default());
        count += 1;
    }
    assert_eq!(count, 8);
}

fn fields(case: &str) -> Vec<&'static str> {
    include_str!("fixtures/runtime-gate-oracle.tsv")
        .lines()
        .skip(1)
        .find(|l| l.split('\t').next() == Some(case))
        .unwrap()
        .split('\t')
        .collect()
}
fn resign(s: &mut nsl_ir::SkillObject) {
    s.semantic_hash = s
        .compute_semantic_sha256_with_limits(h::options("production").nso_limits)
        .unwrap();
}
fn native(
    s: std::sync::Arc<nsl_ir::SkillObject>,
    req: ExecutionRequest,
    catalog: &nsl_contract::ToolContractCatalog,
    p: &dyn ToolExecutionPort,
    options: RuntimeOptions,
) -> Result<ExecutionResult, RuntimeTerminalError> {
    let auth = read_support::Auth::default();
    let m = h::Measurements::default();
    let clock = h::Clock::new("0");
    tool_support::runtime().block_on(
        RuntimeEngine::new(catalog, p, &auth, &m, options).execute_with_clock(s, req, &clock),
    )
}

#[test]
fn explicit_encoder_budgets_and_typed_provider_failures_retain_evidence() {
    use nsl_core::*;
    use std::{collections::BTreeMap, sync::Arc};
    let f = fields("read-results");
    let s = h::skill(&f);
    let catalog = h::catalog(&f);
    let p = h::Provider::new(&f);
    for limit in 0..3 {
        let mut options = h::options("production");
        match limit {
            0 => options.nso_limits.max_bytes = 0,
            1 => options.nso_limits.max_depth = 0,
            _ => options.nso_limits.max_nodes = 0,
        }
        let e = native(s.clone(), h::request(&f, &s), &catalog, &p, options).unwrap_err();
        assert!(matches!(e.kind, RuntimeTerminalKind::Nso(_)));
        assert_eq!(e.evidence.resources, ResourceUsage::default());
        assert!(e.to_string().contains("encoding"));
    }
    // Exact Decimal output budgets: 1.2300 preserves its six fixed-point bytes.
    for budget in [0, 1, 5, 6, 7] {
        let mut options = h::options("production");
        options.max_decimal_bytes = budget;
        let req = h::request(&f, &s);
        let req = ExecutionRequest::new(
            "exec-a".into(),
            BTreeMap::from([("unused".into(), Value::Decimal("1.2300".parse().unwrap()))]),
            BTreeMap::new(),
            req.security_context().clone(),
            None,
        )
        .unwrap();
        let result = native(s.clone(), req, &catalog, &p, options);
        if budget < 6 {
            let error = result.unwrap_err();
            assert!(matches!(error.kind, RuntimeTerminalKind::Encoding(_)));
            assert!(error.to_string().contains("encoding"));
        } else {
            assert_eq!(result.unwrap().status, ExecutionStatus::Completed);
        }
    }
    let catalog_optional = nsl_contract::ToolContractCatalog::new(vec![tool_support::contract(
        tool_support::ty("Optional"),
    )])
    .unwrap();
    let mut optional = (*s).clone();
    optional.required_tools[0].tool_id = "TEST.ECHO".into();
    resign(&mut optional);
    let optional = Arc::new(optional);
    let e = native(
        optional.clone(),
        h::request(&f, &optional),
        &catalog_optional,
        &p,
        h::options("production"),
    )
    .unwrap_err();
    assert!(matches!(e.kind, RuntimeTerminalKind::ContractEncoding(_)));
    assert!(e.to_string().contains("encoding"));
    for mode in ["cancelled", "encoding", "contract-mismatch"] {
        let mut provider = h::Provider::new(&f);
        provider.mode = mode.into();
        let result = native(
            s.clone(),
            h::request(&f, &s),
            &catalog,
            &provider,
            h::options("production"),
        );
        if mode == "contract-mismatch" {
            assert_eq!(result.unwrap().error.unwrap().code, "NSL-E4001");
        } else {
            let error = result.unwrap_err();
            assert!(matches!(
                (&error.kind, mode),
                (RuntimeTerminalKind::Cancelled, "cancelled")
                    | (RuntimeTerminalKind::Encoding(_), "encoding")
            ));
            assert_eq!(error.evidence.resources.tool_calls, 1.into());
            assert_eq!(error.evidence.current_node_id.as_deref(), Some("emit"));
            assert!(std::error::Error::source(&error).is_none());
            assert!(!format!("{error:?}").contains("CONFIDENTIAL-PROJECT"));
            assert!(!error.to_string().is_empty());
        }
        assert_eq!(provider.base.requests.lock().unwrap().len(), 1);
    }
    // Public system-clock entrypoint returns the same empty, effect-free result.
    let mut empty = (*s).clone();
    empty.body.clear();
    empty.outputs.clear();
    resign(&mut empty);
    let empty = Arc::new(empty);
    let req = h::request(&f, &empty);
    let auth = read_support::Auth::default();
    let m = h::Measurements::default();
    let result = tool_support::runtime()
        .block_on(
            RuntimeEngine::new(&catalog, &p, &auth, &m, h::options("production"))
                .execute(empty, req),
        )
        .unwrap();
    assert_eq!(result.status, ExecutionStatus::Completed);
}

#[test]
fn controlled_plan_guards_stop_before_authority_or_provider_and_do_not_execute_write() {
    use nsl_ir::*;
    use std::sync::Arc;
    let f = include_str!("fixtures/runtime-gate-oracle.tsv")
        .lines()
        .skip(1)
        .find(|l| l.starts_with("ordinary-controlled\t"))
        .unwrap()
        .split('\t')
        .collect::<Vec<_>>();
    let source = h::skill(&f);
    let catalog = h::catalog(&f);
    let p = h::Provider::new(&f);
    let rt = tool_support::runtime();
    for mutation in [
        "duplicate-approval",
        "no-write",
        "write-before-approval",
        "wrong-version",
    ] {
        let mut s = (*source).clone();
        let approval = s
            .body
            .iter()
            .position(|n| matches!(n.kind, StatementKind::Approval(_)))
            .unwrap();
        let write = approval + 1;
        match mutation {
            "duplicate-approval" => s.body.push(s.body[approval].clone()),
            "no-write" => {
                s.body.remove(write);
            }
            "write-before-approval" => s.body.swap(approval, write),
            _ => s.ir_version = IrVersion::V1_0,
        }
        let s = Arc::new(s);
        let req = h::request(&f, &s);
        let auth = read_support::Auth::default();
        let m = h::Measurements::default();
        let clock = h::Clock::new("0");
        let engine = RuntimeEngine::new(&catalog, &p, &auth, &m, h::options("production"));
        let e = rt
            .block_on(engine.execute_pre_write_prefix(s, req, &clock))
            .unwrap_err();
        assert!(matches!(e.kind, RuntimeTerminalKind::Contract(_)));
        assert_eq!(e.to_string(), "invalid controlled execution request");
        assert_eq!(e.evidence.resources, ResourceUsage::default());
        assert!(auth.calls.lock().unwrap().is_empty());
        assert_eq!(clock.calls(), 0);
    }
    // Ordinary READ APIs reject controlled content anywhere in a direct host model.
    for shape in [
        "nested-approval",
        "field",
        "projection",
        "binary",
        "call",
        "read",
    ] {
        let mut s = (*source).clone();
        let approval = s
            .body
            .iter()
            .find(|n| matches!(n.kind, StatementKind::Approval(_)))
            .unwrap()
            .clone();
        let write = s
            .body
            .iter()
            .find_map(|n| {
                if let StatementKind::Let { value, .. } = &n.kind {
                    if matches!(value.kind, ExpressionKind::Write(_)) {
                        Some(value.clone())
                    } else {
                        None
                    }
                } else {
                    None
                }
            })
            .unwrap();
        let expr = match shape {
            "field" => ExpressionKind::Field {
                source: Box::new(write.clone()),
                field: "a".into(),
            },
            "projection" => ExpressionKind::Projection {
                source: Box::new(write.clone()),
                field: "a".into(),
            },
            "binary" => ExpressionKind::Binary {
                operator: "ADD".into(),
                left: Box::new(write.clone()),
                right: Box::new(write.clone()),
            },
            "call" => ExpressionKind::Call {
                function: "sum".into(),
                arguments: vec![write.clone()],
            },
            _ => ExpressionKind::Read {
                tool_ref: "t0".into(),
                arguments: vec![NamedExpression {
                    name: "z".into(),
                    value: write.clone(),
                }],
                result_policy: ResultPolicy::default(),
            },
        };
        s.body = if shape == "nested-approval" {
            vec![Statement {
                node_id: "loop".into(),
                kind: StatementKind::Foreach {
                    iterator_symbol_id: "iterator".into(),
                    collection: expression_support::parse("S,source,ListInt,list"),
                    max_iterations: 1.into(),
                    body: vec![approval],
                },
            }]
        } else {
            vec![Statement {
                node_id: "let".into(),
                kind: StatementKind::Let {
                    target_symbol_id: "unused".into(),
                    value: Expression {
                        kind: expr,
                        ..write
                    },
                },
            }]
        };
        resign(&mut s);
        let s = Arc::new(s);
        let result = native(
            s.clone(),
            h::request(&f, &s),
            &catalog,
            &p,
            h::options("production"),
        )
        .unwrap();
        assert_eq!(
            result.error.unwrap().message,
            "Approval and WRITE require the controlled v0.2 execution pipeline"
        );
    }
    assert!(p.base.requests.lock().unwrap().is_empty());
}

#[test]
fn malformed_last_binding_and_non_record_context_stop_before_read_or_output() {
    use nsl_core::*;
    use nsl_ir::*;
    use std::{collections::BTreeMap, sync::Arc};
    let f = fields("read-results");
    let original = h::skill(&f);
    let catalog = h::catalog(&f);
    let provider = h::Provider::new(&f);
    for context in [false, true] {
        let mut s = (*original).clone();
        s.inputs.push(InputSpec {
            symbol_id: "first".into(),
            name: "first".into(),
            type_info: expression_support::ty("Int"),
            required: true,
            classification: DataClassification::Internal,
        });
        if context {
            s.contexts.push(ContextSpec {
                symbol_id: "last".into(),
                name: "last".into(),
                path: vec!["nested".into(), "last".into()],
                type_info: expression_support::ty("Nested"),
                classification: DataClassification::Internal,
            });
        } else {
            s.inputs.push(InputSpec {
                symbol_id: "last".into(),
                name: "last".into(),
                type_info: expression_support::ty("Nested"),
                required: true,
                classification: DataClassification::Internal,
            });
        }
        resign(&mut s);
        let s = Arc::new(s);
        let base = h::request(&f, &s);
        let mut inputs = BTreeMap::from([("first".into(), Value::Int(1.into()))]);
        let mut ctx = BTreeMap::new();
        let malformed = Value::Record(BTreeMap::from([(
            "a".into(),
            Value::List(vec![Value::Int(1.into()), Value::Bool(true)]),
        )]));
        if context {
            ctx.insert(
                "nested".into(),
                Value::Record(BTreeMap::from([("last".into(), malformed)])),
            );
        } else {
            inputs.insert("last".into(), malformed);
        }
        let req = ExecutionRequest::new(
            "exec-a".into(),
            inputs.clone(),
            ctx,
            base.security_context().clone(),
            None,
        )
        .unwrap();
        let e = native(
            s.clone(),
            req,
            &catalog,
            &provider,
            h::options("production"),
        )
        .unwrap_err();
        assert!(
            matches!(e.kind,RuntimeTerminalKind::InputType(RuntimeInputTypeError{ref symbol_id,..}) if symbol_id=="last")
        );
        assert_eq!(e.evidence.resources, ResourceUsage::default());
        if context {
            let req = ExecutionRequest::new(
                "exec-a".into(),
                inputs,
                BTreeMap::from([("nested".into(), Value::Int(1.into()))]),
                base.security_context().clone(),
                None,
            )
            .unwrap();
            let r = native(s, req, &catalog, &provider, h::options("production")).unwrap();
            assert_eq!(
                r.error.unwrap().message,
                "missing runtime context path: nested.last"
            );
        }
    }
    assert!(provider.base.requests.lock().unwrap().is_empty());
}

#[test]
fn full_engine_worst_case_is_bounded_and_contexts_are_independent() {
    std::thread::Builder::new()
        .stack_size(256 * 1024)
        .spawn(|| {
            use nsl_ir::*;
            use std::sync::Arc;
            let f = include_str!("fixtures/runtime-gate-oracle.tsv")
                .lines()
                .find(|l| l.starts_with("request-binding\t1-0") && l.contains(",ints,ListInt,"))
                .unwrap()
                .split('\t')
                .collect::<Vec<_>>();
            let mut s = (*h::skill(&f)).clone();
            let mut body = vec![Statement {
                node_id: "emit".into(),
                kind: StatementKind::Emit {
                    fields: vec![NamedExpression {
                        name: "value".into(),
                        value: expression_support::parse("L,seven,Int,value"),
                    }],
                },
            }];
            for level in (0..16).rev() {
                body = vec![Statement {
                    node_id: format!("loop-{level}"),
                    kind: StatementKind::Foreach {
                        iterator_symbol_id: format!("it-{level}"),
                        collection: expression_support::parse("S,source,ListInt,list"),
                        max_iterations: 3.into(),
                        body,
                    },
                }];
            }
            s.body = body;
            s.outputs[0].type_info = expression_support::ty("Int");
            s.limits.loop_iterations = 100.into();
            s.limits.emitted_rows = 100.into();
            resign(&mut s);
            let s = Arc::new(s);
            let catalog = h::catalog(&f);
            let p = h::Provider::new(&f);
            for _ in 0..2 {
                let result = native(
                    s.clone(),
                    h::request(&f, &s),
                    &catalog,
                    &p,
                    h::options("production"),
                )
                .unwrap();
                assert_eq!(result.status, nsl_core::ExecutionStatus::LimitExceeded);
                assert_eq!(result.resources.loop_iterations, 100.into());
                assert_eq!(result.resources.tool_calls, 0.into());
                assert_eq!(
                    result.error.unwrap().message,
                    "loop iteration limit exceeded"
                );
                assert!(result.outputs.len() < 100);
            }
            assert!(p.base.requests.lock().unwrap().is_empty());
        })
        .unwrap()
        .join()
        .unwrap();
}

#[test]
fn declared_non_read_tools_and_duplicate_admission_symbols_are_rejected_before_provider() {
    use nsl_core::*;
    use nsl_ir::*;
    use std::{collections::BTreeMap, sync::Arc};
    let f = fields("read-results");
    let base = h::skill(&f);
    let p = h::Provider::new(&f);
    // A matching WRITE declaration without controlled nodes still cannot enter
    // the ordinary READ driver. A matching transaction is forbidden in prefix.
    for prefix in [false, true] {
        let source = if prefix {
            include_str!("fixtures/runtime-gate-oracle.tsv")
                .lines()
                .find(|l| l.starts_with("ordinary-controlled\t"))
                .unwrap()
                .split('\t')
                .collect::<Vec<_>>()
        } else {
            f.clone()
        };
        let mut s = (*h::skill(&source)).clone();
        let c = if prefix {
            read_support::contract("Int", "transaction", false)
        } else {
            h::write_contract()
        };
        s.required_tools[0].tool_id = c.tool_id().into();
        s.required_tools[0].contract_hash = c.contract_hash().unwrap();
        s.required_tools[0].required_scope = c.required_scope().into();
        s.required_tools[0].kind = RequiredToolKind::V1_1 {
            capability: if prefix {
                ToolCapability::Transaction
            } else {
                ToolCapability::Write
            },
            risk_level: RiskLevel::R2,
        };
        if !prefix {
            s.ir_version = IrVersion::V1_1;
            s.language_version = "0.2".into();
            s.semantics_profile = "NSL-0.2-STRICT".into();
        }
        resign(&mut s);
        let s = Arc::new(s);
        let catalog = nsl_contract::ToolContractCatalog::new(vec![c]).unwrap();
        let req = h::request(&source, &s);
        let clock = h::Clock::new("0");
        let auth = read_support::Auth::default();
        let m = h::Measurements::default();
        let engine = RuntimeEngine::new(&catalog, &p, &auth, &m, h::options("production"));
        let result = tool_support::runtime()
            .block_on(async {
                if prefix {
                    engine.execute_pre_write_prefix(s, req, &clock).await
                } else {
                    engine.execute_with_clock(s, req, &clock).await
                }
            })
            .unwrap();
        assert_eq!(
            result.error.unwrap().message,
            "WRITE capability is forbidden"
        );
        assert_eq!(result.resources, ResourceUsage::default());
    }
    let catalog = h::catalog(&f);
    let mut s = (*base).clone();
    for name in ["a", "b"] {
        s.inputs.push(InputSpec {
            symbol_id: "same".into(),
            name: name.into(),
            type_info: expression_support::ty("Int"),
            required: true,
            classification: DataClassification::Internal,
        });
    }
    resign(&mut s);
    let s = Arc::new(s);
    let req = h::request(&f, &s);
    let req = ExecutionRequest::new(
        "exec-a".into(),
        BTreeMap::from([
            ("a".into(), Value::Int(1.into())),
            ("b".into(), Value::Int(2.into())),
        ]),
        BTreeMap::new(),
        req.security_context().clone(),
        None,
    )
    .unwrap();
    let result = native(s, req, &catalog, &p, h::options("production")).unwrap();
    assert_eq!(
        result.error.unwrap().detail_code.as_deref(),
        Some("IMMUTABLE_BINDING_ERROR")
    );
    assert!(p.base.requests.lock().unwrap().is_empty());
}
