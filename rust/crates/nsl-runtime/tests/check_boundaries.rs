mod expression_support;
mod read_support;
mod support;
mod tool_support;
use nsl_contract::ToolContractCatalog;
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime::*;
use std::{collections::BTreeMap, error::Error, sync::Arc};

fn rule(id: &str, program: &str) -> Statement {
    Statement {
        node_id: "check-node".into(),
        kind: StatementKind::Check {
            check_id: id.into(),
            condition: expression_support::parse(program),
            severity: "ERROR".into(),
            on_fail: "STOP".into(),
            message: "검증😀".into(),
            result_symbol_id: "check-result".into(),
        },
    }
}
fn fields(version: &str) -> Vec<&str> {
    vec![
        version, "-", "Bool", "true", "COMPLETE", "valid", "read", "0", "100", "10", "0", "valid",
    ]
}
fn check_type() -> TypeRef {
    TypeRef::new(TypeKind::Record {
        name: "CheckResult".into(),
        fields: vec![(
            "status".into(),
            TypeRef::new(TypeKind::Enum {
                name: "CheckStatus".into(),
                values: ["PASS", "FAIL", "UNKNOWN"].map(Into::into).to_vec(),
            })
            .unwrap(),
        )],
    })
    .unwrap()
}

#[test]
fn both_nso_versions_round_trip_checks_and_read_provenance_without_false_pass() {
    let rt = tool_support::runtime();
    let limits = NsoLimits {
        max_bytes: 100_000,
        max_depth: 64,
        max_nodes: 10_000,
    };
    for version in ["1-0", "1-1"] {
        for completeness in [
            Completeness::Complete,
            Completeness::Partial,
            Completeness::Unknown,
        ] {
            for truth in [false, true] {
                let f = fields(version);
                let mut skill = (*read_support::skill(&f)).clone();
                skill.inputs.clear();
                skill.contexts.clear();
                skill.outputs.clear();
                skill.body = vec![rule(
                    "rule",
                    "L,seven,Int,z;L,one,Int,a;R,t0,z+a,5,Bool,condition",
                )];
                skill.symbols = vec![SymbolSpec {
                    symbol_id: "check-result".into(),
                    name: "check_result".into(),
                    category: "CHECK".into(),
                    type_info: check_type(),
                    classification: DataClassification::Internal,
                }];
                skill.semantic_hash = skill.compute_semantic_sha256_with_limits(limits).unwrap();
                let encoded = NsoCodec::encode_with_limits(&skill, limits).unwrap();
                let decoded = NsoCodec::decode(&encoded, limits).unwrap();
                assert_eq!(
                    encoded,
                    NsoCodec::encode_with_limits(&decoded, limits).unwrap()
                );
                let catalog = read_support::catalog(&f);
                let handler: FixtureHandler = Arc::new(move |_| {
                    Ok(MockToolResult {
                        value: Value::Bool(truth),
                        completeness,
                    })
                });
                let provider = MockToolExecutor::new(
                    catalog.clone(),
                    BTreeMap::from([("TEST.READ".into(), handler)]),
                    100_000,
                );
                let auth = StaticReadAuthorizer::default();
                let mut ctx = ExecutionContext::with_clock(
                    Arc::new(decoded.clone()),
                    read_support::request("read"),
                    support::Clock::default(),
                );
                let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
                rt.block_on(evaluator.execute_check(&mut ctx, &decoded.body[0]))
                    .unwrap();
                let expected = if completeness != Completeness::Complete {
                    CheckStatus::Unknown
                } else if truth {
                    CheckStatus::Pass
                } else {
                    CheckStatus::Fail
                };
                assert_eq!(ctx.checks[0].status, expected);
                assert_eq!(ctx.checks[0].completeness, completeness);
                assert_eq!(ctx.data_completeness(), completeness);
                let bound = ctx.resolve("check-result").unwrap();
                assert_eq!(bound.type_ref(), &check_type());
                assert_eq!(bound.presence(), Presence::Present);
                assert_eq!(bound.completeness(), Completeness::Complete);
                assert_eq!(bound.classification(), DataClassification::Internal);
                assert_eq!(bound.provenance_refs(), &ctx.checks[0].provenance_refs);
                assert_eq!(bound.provenance_refs().len(), 1);
                assert!(bound.provenance_refs()[0].starts_with("prov:inv0001:"));
                // CHECK status itself is a known enum, even for UNKNOWN facts.
                let status_expression = Expression {
                    node_id: "status".into(),
                    type_info: check_type().field("status").unwrap().clone(),
                    kind: ExpressionKind::Field {
                        source: Box::new(Expression {
                            node_id: "result".into(),
                            type_info: check_type(),
                            kind: ExpressionKind::SymbolRef {
                                symbol_id: "check-result".into(),
                            },
                        }),
                        field: "status".into(),
                    },
                };
                let status = rt
                    .block_on(evaluator.evaluate(&mut ctx, &status_expression))
                    .unwrap();
                assert_eq!(status.value(), &Value::String(expected.as_str().into()));
                assert_eq!(status.completeness(), Completeness::Complete);
                assert_eq!(ctx.data_completeness(), completeness);
                assert_eq!(provider.call_count(), 1.into());
                assert_eq!(ctx.usage().tool_calls, 1.into());
                assert_eq!(ctx.invocation_counter, 1.into());
                assert!(ctx.outputs.is_empty() && ctx.writes.entries.is_empty());
            }
        }
    }
}

#[test]
fn invalid_statement_and_errors_remain_typed_safe_and_without_service_effects() {
    let f = fields("1-0");
    let catalog = read_support::catalog(&f);
    let provider = read_support::Provider::new(&f);
    let auth = read_support::Auth::default();
    let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
    let mut ctx = ExecutionContext::with_clock(
        read_support::skill(&f),
        read_support::request("read"),
        support::Clock::default(),
    );
    let not_check = Statement {
        node_id: "let".into(),
        kind: StatementKind::Let {
            target_symbol_id: "x".into(),
            value: expression_support::parse("R,t1,-,5,Int,provider"),
        },
    };
    let predicate = ValueEnvelope::complete(
        Value::Bool(true),
        expression_support::ty("Bool"),
        DataClassification::Public,
    );
    assert_eq!(
        StrictRuleEvaluator.evaluate(&not_check, &predicate),
        Err(CheckEvaluationError::NotCheckStatement)
    );
    let error = tool_support::runtime()
        .block_on(evaluator.execute_check(&mut ctx, &not_check))
        .unwrap_err();
    assert_eq!(
        error,
        CheckExecutionError::Evaluation(CheckEvaluationError::NotCheckStatement)
    );
    assert_eq!(error.to_string(), "statement is not CHECK");
    assert!(ctx.checks.is_empty() && ctx.resolve("check-result").is_err());
    assert!(provider.requests.lock().unwrap().is_empty() && auth.calls.lock().unwrap().is_empty());
    for error in [
        CheckExecutionError::from(CheckEvaluationError::PredicateNotBool),
        CheckExecutionError::from(BindingError::Immutable("check-result".into())),
        CheckExecutionError::from(ReadExecutionError::Expression(
            ExpressionRuntimeError::Unexpected {
                kind: "ProviderError",
                detail: "PRIVATE-PROVIDER-DETAIL".into(),
            },
        )),
    ] {
        assert!(error.source().is_none());
        assert!(!error.to_string().contains("PRIVATE-PROVIDER-DETAIL"));
    }
}

#[test]
fn check_namespace_is_immutable_scoped_and_isolated_between_executions() {
    let f = fields("1-0");
    let catalog = ToolContractCatalog::new(vec![]).unwrap();
    let provider = MockToolExecutor::new(catalog.clone(), BTreeMap::new(), 100_000);
    let auth = StaticReadAuthorizer::default();
    let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
    let rt = tool_support::runtime();
    let mut first = ExecutionContext::with_clock(
        read_support::skill(&f),
        read_support::request("read"),
        support::Clock::default(),
    );
    let mut second = ExecutionContext::with_clock(
        read_support::skill(&f),
        read_support::request("read"),
        support::Clock::default(),
    );
    first.push_frame();
    rt.block_on(evaluator.execute_check(&mut first, &rule("a", "L,true,Bool,condition")))
        .unwrap();
    assert!(second.checks.is_empty() && second.resolve("check-result").is_err());
    rt.block_on(evaluator.execute_check(&mut second, &rule("b", "L,false,Bool,condition")))
        .unwrap();
    assert_eq!(first.checks[0].status, CheckStatus::Pass);
    assert_eq!(second.checks[0].status, CheckStatus::Fail);
    let previous = first.resolve("check-result").unwrap().clone();
    let error = rt
        .block_on(evaluator.execute_check(&mut first, &rule("duplicate", "L,false,Bool,condition")))
        .unwrap_err();
    assert_eq!(
        error,
        CheckExecutionError::Binding(BindingError::Immutable("check-result".into()))
    );
    assert_eq!(first.resolve("check-result").unwrap(), &previous);
    assert_eq!(first.checks.len(), 2); // Oracle appends before the binding failure.
    first.pop_frame().unwrap();
    assert!(first.resolve("check-result").is_err());
    first.push_frame();
    rt.block_on(evaluator.execute_check(&mut first, &rule("sibling", "L,true,Bool,condition")))
        .unwrap();
    first.pop_frame().unwrap();
    assert_eq!(first.checks.len(), 3);
    assert_eq!(second.checks.len(), 1);
    assert_eq!(provider.call_count(), 0.into());
}

#[test]
fn required_check_summary_keeps_fail_and_unknown_separate_and_never_all_pass() {
    let f = fields("1-0");
    let catalog = ToolContractCatalog::new(vec![]).unwrap();
    let provider = MockToolExecutor::new(catalog.clone(), BTreeMap::new(), 100_000);
    let auth = StaticReadAuthorizer::default();
    let evaluator = ReadEvaluator::new(&catalog, &provider, &auth, 100_000);
    let rt = tool_support::runtime();
    for statuses in [
        vec![],
        vec![CheckStatus::Pass],
        vec![CheckStatus::Fail],
        vec![CheckStatus::Unknown],
        vec![CheckStatus::Unknown, CheckStatus::Pass, CheckStatus::Fail],
    ] {
        let mut ctx = ExecutionContext::with_clock(
            read_support::skill(&f),
            read_support::request("read"),
            support::Clock::default(),
        );
        for (index, status) in statuses.iter().enumerate() {
            ctx.push_frame();
            let predicate = ValueEnvelope::new(
                Value::Bool(*status != CheckStatus::Fail),
                expression_support::ty("Bool"),
                Presence::Present,
                if *status == CheckStatus::Unknown {
                    Completeness::Partial
                } else {
                    Completeness::Complete
                },
                DataClassification::Restricted,
                vec![format!("prov:{index}")],
            );
            ctx.bind(BindingNamespace::Variable, "predicate".into(), predicate)
                .unwrap();
            rt.block_on(evaluator.execute_check(
                &mut ctx,
                &rule(&format!("check-{index}"), "S,predicate,Bool,condition"),
            ))
            .unwrap();
            ctx.pop_frame().unwrap();
        }
        let summary = ValidationSummary::from_checks(&ctx.checks).unwrap();
        let expected = if statuses.is_empty() {
            ValidationSummaryStatus::NoCheck
        } else if statuses.contains(&CheckStatus::Fail) && statuses.contains(&CheckStatus::Unknown)
        {
            ValidationSummaryStatus::HasFailAndUnknown
        } else if statuses.contains(&CheckStatus::Fail) {
            ValidationSummaryStatus::HasFail
        } else if statuses.contains(&CheckStatus::Unknown) {
            ValidationSummaryStatus::HasUnknown
        } else {
            ValidationSummaryStatus::AllPass
        };
        assert_eq!(summary.spec().status, expected);
        assert_eq!(
            ctx.checks.iter().map(|c| c.status).collect::<Vec<_>>(),
            statuses
        );
        assert_eq!(ctx.usage().tool_calls, 0.into());
        assert_eq!(provider.call_count(), 0.into());
    }
}
