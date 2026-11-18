mod expression_support;
mod support;
use expression_support::*;
use nsl_core::*;
use nsl_ir::*;
use nsl_runtime::*;
use std::{error::Error, sync::Arc};

fn context() -> ExecutionContext<support::Clock> {
    ExecutionContext::with_clock(
        support::skill(),
        support::request(),
        support::Clock::default(),
    )
}
fn bind(ctx: &mut ExecutionContext<support::Clock>, symbol: &str, value: Value, t: TypeRef) {
    ctx.bind(
        BindingNamespace::Variable,
        symbol.into(),
        ValueEnvelope::complete(value, t, DataClassification::Internal),
    )
    .unwrap();
}
fn evaluation_error(e: ExpressionRuntimeError, node: &str) {
    assert!(e.source().is_none());
    let ExpressionRuntimeError::Evaluation(e) = e else {
        panic!()
    };
    assert_eq!(e.node_id, node);
    assert_eq!(e.detail_code, "EXPRESSION_EVALUATION_ERROR");
    assert!(e.source().is_none());
}

#[test]
fn admitted_ir_1_0_and_1_1_with_bad_int_division_is_rejected_at_runtime() {
    let limits = NsoLimits {
        max_bytes: 100_000,
        max_depth: 64,
        max_nodes: 10_000,
    };
    for original in [
        include_bytes!("../../../../compatibility/v0_2_1/nso/ir-1-0-canonical/expected.nso")
            .as_slice(),
        include_bytes!("../../../../compatibility/v0_2_1/nso/ir-1-1-canonical/expected.nso")
            .as_slice(),
    ] {
        let mut skill = NsoCodec::decode(original, limits).unwrap();
        let expression = parse("L,one,Int,left;L,two,Int,right;B,DIV,Int,divide");
        skill.body = vec![Statement {
            node_id: "stmt-divide".into(),
            kind: StatementKind::Let {
                target_symbol_id: "s-approved-division".into(),
                value: expression,
            },
        }];
        skill.symbols.push(SymbolSpec {
            symbol_id: "s-approved-division".into(),
            name: "quotient".into(),
            category: "VARIABLE".into(),
            type_info: ty("Int"),
            classification: DataClassification::Public,
        });
        skill.semantic_hash = skill.compute_semantic_sha256_with_limits(limits).unwrap();
        let bytes = NsoCodec::encode_with_limits(&skill, limits).unwrap();
        let decoded = NsoCodec::decode(&bytes, limits).unwrap();
        let StatementKind::Let {
            target_symbol_id,
            value,
        } = &decoded.body[0].kind
        else {
            panic!()
        };
        let mut ctx = ExecutionContext::with_clock(
            Arc::new(decoded.clone()),
            support::request(),
            support::Clock::default(),
        );
        let error = ExpressionEvaluator
            .execute_let(&mut ctx, target_symbol_id, value)
            .unwrap_err();
        let ExpressionRuntimeError::Evaluation(error) = error else {
            panic!()
        };
        assert_eq!(error.node_id, "divide");
        assert_eq!(error.detail_code, "EXPRESSION_TYPE_MISMATCH");
        assert!(ctx.resolve(target_symbol_id).is_err());
    }
}

fn drop_expression(mut stack: Vec<Expression>) {
    // Test teardown does not use the derived recursive Box destructor for a
    // deliberately deeper-than-wire tree. The evaluator only borrows the IR.
    while let Some(expression) = stack.pop() {
        if let ExpressionKind::Binary { left, right, .. } = expression.kind {
            stack.push(*left);
            stack.push(*right);
        }
    }
}
#[test]
fn iterative_expression_traversal_works_on_a_small_stack() {
    for depth in [0, 1, 31, 32, 255, 256, 1024, 4096] {
        let mut expression = parse("L,one,Int,e0");
        for _ in 0..depth {
            expression = Expression {
                node_id: "add".into(),
                type_info: ty("Int"),
                kind: ExpressionKind::Binary {
                    operator: "ADD".into(),
                    left: Box::new(expression),
                    right: Box::new(parse("L,zero,Int,zero")),
                },
            };
        }
        let expression = std::thread::Builder::new()
            .stack_size(128 * 1024)
            .spawn(move || {
                let mut ctx = context();
                let before = ctx.usage();
                let result = ExpressionEvaluator.evaluate(&mut ctx, &expression).unwrap();
                assert_eq!(result.value(), &Value::Int(1.into()));
                assert_eq!(ctx.usage(), before);
                expression
            })
            .unwrap()
            .join()
            .unwrap();
        drop_expression(vec![expression]);
    }
}

#[test]
fn immutable_let_is_atomic_across_failures_and_sibling_frames() {
    let mut ctx = context();
    let expression = parse("L,seven,Int,literal");
    ctx.push_frame();
    ExpressionEvaluator
        .execute_let(&mut ctx, "local", &expression)
        .unwrap();
    assert_eq!(ctx.resolve("local").unwrap().value(), &Value::Int(7.into()));
    let old = ctx.resolve("local").unwrap().clone();
    let error = ExpressionEvaluator
        .execute_let(&mut ctx, "local", &parse("L,one,Int,other"))
        .unwrap_err();
    assert!(matches!(
        error,
        ExpressionRuntimeError::Binding(BindingError::Immutable(_))
    ));
    assert!(error.source().is_none());
    assert!(error.to_string().contains("immutable symbol already bound"));
    assert_eq!(ctx.resolve("local").unwrap(), &old);
    let failing = parse("L,one,Int,left;L,zero,Int,right;B,DIV,Decimal,division");
    evaluation_error(
        ExpressionEvaluator
            .execute_let(&mut ctx, "failed", &failing)
            .unwrap_err(),
        "division",
    );
    assert!(ctx.resolve("failed").is_err());
    ctx.pop_frame().unwrap();
    ctx.push_frame();
    ExpressionEvaluator
        .execute_let(&mut ctx, "local", &parse("L,two,Int,sibling"))
        .unwrap();
    assert_eq!(ctx.resolve("local").unwrap().value(), &Value::Int(2.into()));
    assert!(context().resolve("local").is_err());
}

#[test]
fn unsupported_provider_expressions_do_not_change_state_or_invoke_tools() {
    for kind in [
        ExpressionKind::Read {
            tool_ref: "t1".into(),
            arguments: vec![],
            result_policy: ResultPolicy::default(),
        },
        ExpressionKind::Write(WriteExpression {
            tool_ref: "t1".into(),
            arguments: vec![],
            approval_ref: "A".into(),
        }),
    ] {
        let mut ctx = context();
        let before = ctx.usage();
        let expr = Expression {
            node_id: "service-node".into(),
            type_info: ty("Int"),
            kind,
        };
        let error = ExpressionEvaluator
            .execute_let(&mut ctx, "target", &expr)
            .unwrap_err();
        assert_eq!(
            error,
            ExpressionRuntimeError::Unsupported {
                node_id: "service-node".into()
            }
        );
        assert_eq!(
            error.to_string(),
            "expression requires an execution service"
        );
        assert!(error.source().is_none());
        assert_eq!(ctx.usage(), before);
        assert_eq!(ctx.invocation_counter, 0.into());
        assert!(ctx.resolve("target").is_err());
    }
    let mut ctx = context();
    let e = ExpressionEvaluator
        .evaluate(&mut ctx, &parse("S,password=synthetic,Int,missing"))
        .unwrap_err();
    assert!(matches!(
        e,
        ExpressionRuntimeError::Unexpected {
            kind: "RuntimeError",
            ..
        }
    ));
    assert_eq!(e.to_string(), "An unexpected runtime error occurred.");
    assert!(e.source().is_none());
}

#[test]
fn malformed_datetime_text_is_rejected_without_panics_or_value_disclosure() {
    let invalid = [
        "",
        "no-date",
        "2026T01:02:03",
        "2026-11T01:02:03",
        "-1-11-18T01:02:03",
        "0000-01-01T00:00:00",
        "10000-01-01T00:00:00",
        "2026-00-01T00:00:00",
        "2026-13-01T00:00:00",
        "2026-11-00T00:00:00",
        "2026-11-31T00:00:00",
        "1900-02-29T00:00:00",
        "2026-11-18-1T00:00:00",
        "2026-11-18T",
        "2026-11-18T12",
        "2026-11-18T12:60:00",
        "2026-11-18T24:00:00",
        "2026-11-18T00:00:60",
        "2026-11-18T00:00:00:1",
        "2026-11-18T00:00:00.1234567",
        "2026-11-18T00:00:00.a",
        "2026-11-18T00:00:00+24:00",
        "2026-11-18T00:00:00+",
        "999999999999999999999999999999999999999999-01-01T00:00:00",
        "2026-11-18T00:00:00+00:00:00.a",
    ];
    for raw in invalid {
        let mut ctx = context();
        bind(&mut ctx, "s0", Value::DateTime(raw.into()), ty("DateTime"));
        bind(&mut ctx, "s1", value("utc"), ty("DateTime"));
        let expression = parse("S,s0,DateTime,left;S,s1,DateTime,right;B,EQ,Bool,equality");
        let error = ExpressionEvaluator
            .evaluate(&mut ctx, &expression)
            .unwrap_err();
        assert!(!error.to_string().contains(raw) || raw.is_empty());
        evaluation_error(error, "equality");
    }
    for text in ["2026-11-18T01:02:03Z", "2026-11-18T01:02:03.000000+00:00"] {
        let mut ctx = context();
        bind(&mut ctx, "s0", Value::DateTime(text.into()), ty("DateTime"));
        bind(&mut ctx, "s1", value("utc"), ty("DateTime"));
        assert_eq!(
            ExpressionEvaluator
                .evaluate(
                    &mut ctx,
                    &parse("S,s0,DateTime,left;S,s1,DateTime,right;B,EQ,Bool,result")
                )
                .unwrap()
                .value(),
            &Value::Bool(true)
        );
    }
}

#[test]
fn arbitrary_precision_int_division_and_invalid_operator_errors_are_preserved() {
    let mut ctx = context();
    let wide: Integer = format!("1{}", "0".repeat(5000)).parse().unwrap();
    bind(&mut ctx, "s0", Value::Int(wide), ty("Int"));
    let result = ExpressionEvaluator
        .evaluate(
            &mut ctx,
            &parse("S,s0,Int,left;L,two,Int,right;B,DIV,Decimal,division"),
        )
        .unwrap();
    assert_eq!(result.value(), &Value::Decimal("5e4999".parse().unwrap()));
    for program in [
        "L,usd,USD,left;L,usd,USD,right;B,MUL,USD,bad",
        "L,dp,Decimal,left;L,dp,Decimal,right;B,UNKNOWN,Decimal,bad",
    ] {
        evaluation_error(
            ExpressionEvaluator
                .evaluate(&mut ctx, &parse(program))
                .unwrap_err(),
            "bad",
        );
    }
    // Scalar literals retain the frozen model's declared envelope metadata;
    // value admission is distinct from envelope-type consistency checks.
    let expression = parse("L,one,Bool,declared");
    let result = ExpressionEvaluator.evaluate(&mut ctx, &expression).unwrap();
    assert_eq!(result.value(), &Value::Int(1.into()));
    assert_eq!(result.type_ref(), &ty("Bool"));
}

#[test]
fn collection_limits_preserve_input_and_failed_observation_order() {
    for length in [0, 1, 2, 3, 4] {
        let mut skill = (*support::skill()).clone();
        skill.limits.collection_size = 3.into();
        let mut ctx = ExecutionContext::with_clock(
            Arc::new(skill),
            support::request(),
            support::Clock::default(),
        );
        let envelope = ValueEnvelope::new(
            Value::List(vec![Value::Int(1.into()); length]),
            ty("ListInt"),
            Presence::Present,
            Completeness::Partial,
            DataClassification::Restricted,
            vec!["p1".into()],
        );
        ctx.bind(BindingNamespace::Input, "s0".into(), envelope.clone())
            .unwrap();
        let result =
            ExpressionEvaluator.execute_let(&mut ctx, "target", &parse("S,s0,ListInt,result"));
        assert_eq!(result.is_ok(), length <= 3);
        assert_eq!(ctx.input_values()["s0"], envelope);
        assert_eq!(ctx.data_completeness(), Completeness::Partial);
        assert_eq!(ctx.usage().max_collection_size_seen, 0.into());
        if length == 4 {
            assert!(ctx.resolve("target").is_err());
            assert_eq!(
                result.unwrap_err(),
                ExpressionRuntimeError::Limit(LimitExceeded(ResourceLimit::CollectionSize))
            );
        }
    }
    // The output limit is checked too when concatenation creates a larger list.
    let mut skill = (*support::skill()).clone();
    skill.limits.collection_size = 3.into();
    let mut ctx = ExecutionContext::with_clock(
        Arc::new(skill),
        support::request(),
        support::Clock::default(),
    );
    bind(&mut ctx, "s0", value("ints"), ty("ListInt"));
    let error = ExpressionEvaluator
        .execute_let(
            &mut ctx,
            "target",
            &parse("S,s0,ListInt,left;S,s0,ListInt,right;B,ADD,ListInt,concat"),
        )
        .unwrap_err();
    assert_eq!(
        error,
        ExpressionRuntimeError::Limit(LimitExceeded(ResourceLimit::CollectionSize))
    );
    assert!(ctx.resolve("target").is_err());
}

#[test]
fn core_money_aggregation_has_typed_zero_ordered_failures_and_safe_errors() {
    let usd: CurrencyCode = "USD".parse().unwrap();
    assert_eq!(
        sum_money(&[], &usd).unwrap(),
        Money::new("0".parse().unwrap(), usd.clone())
    );
    assert_eq!(
        sum_money(&[value("usd")], &usd).unwrap(),
        Money::new("12.500".parse().unwrap(), usd.clone())
    );
    let mixed = sum_money(&[value("eur"), Value::Null], &usd).unwrap_err();
    assert!(matches!(mixed, MoneySumError::CurrencyMismatch { .. }));
    assert!(mixed.source().is_none());
    assert_eq!(mixed.to_string(), "money currencies must match");
    let invalid = sum_money(&[Value::Null, value("eur")], &usd).unwrap_err();
    assert_eq!(invalid, MoneySumError::InvalidValue);
    assert_eq!(invalid.to_string(), "Money sum requires Money values");
    assert!(invalid.source().is_none());
    let overflow = sum_money(&[value("money_high"), value("money_high")], &usd).unwrap_err();
    assert_eq!(overflow, MoneySumError::Decimal(DecimalError::Overflow));
    assert!(overflow.source().is_some());
    assert_eq!(overflow.to_string(), DecimalError::Overflow.to_string());
    // Static Int declarations must reject a non-Decimal quotient even when a
    // host supplies an inconsistent value under an Int envelope.
    let mut ctx = context();
    bind(&mut ctx, "s0", value("dp"), ty("Int"));
    let error = ExpressionEvaluator
        .evaluate(
            &mut ctx,
            &parse("S,s0,Int,left;L,two,Int,right;B,DIV,Int,divide"),
        )
        .unwrap_err();
    let ExpressionRuntimeError::Evaluation(error) = error else {
        panic!()
    };
    assert_eq!(error.detail_code, "EXPRESSION_TYPE_MISMATCH");
    assert_eq!(error.node_id, "divide");
}
