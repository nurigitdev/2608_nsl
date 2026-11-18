mod bound_support;

use std::error::Error;

use bound_support::*;
use nsl_compiler::bounds::{
    BoundDiagnosticKind, BoundError, BoundIntegerConversionError, StaticBoundAnalyzer,
};

#[test]
fn missing_diagnostic_context_preserves_spans_and_typed_error_chain() {
    let source = source();
    let skill = parse(&source);
    let limits_span = &skill.limits.as_ref().unwrap().span;
    let body = program("read 0\nread 1\nlet stmt0001");
    let analyzer = StaticBoundAnalyzer::new([]);
    let error = analyzer
        .check(&body, &limits("1", "1", "1"), &skill.span, limits_span)
        .unwrap_err();
    let BoundError::Diagnostic(ref diagnostic) = error else {
        panic!("diagnostic")
    };
    assert_eq!(diagnostic.kind, BoundDiagnosticKind::ToolCalls);
    assert_eq!(diagnostic.logical_path, None);
    assert_eq!(diagnostic.snippet, None);
    assert_eq!(diagnostic.position(), None);
    assert_eq!(&diagnostic.span, limits_span);
    assert_eq!(
        error.to_string(),
        "NSL-E6101: static tool call bound 2 exceeds declared limit 1"
    );
    assert_eq!(error.source().unwrap().to_string(), error.to_string());
    assert!(diagnostic.source().is_none());
    let invalid = program("symbol\nforeach stmt0001 0 0");
    assert!(analyzer.analyze(&invalid).unwrap_err().source().is_none());
}

#[test]
fn wide_counts_remain_exact_and_conversion_limit_applies_only_to_diagnostics() {
    let source = source();
    let skill = parse(&source);
    let span = &skill.limits.as_ref().unwrap().span;
    let analyzer = StaticBoundAnalyzer::new([&source]);
    for digits in [4299, 4300, 4301] {
        let wide = format!("1{}", "0".repeat(digits - 1));
        let body = program(&format!(
            "symbol\nread 0\nemit emit0001 1\nforeach stmt0001 {wide} 1"
        ));
        let analysis = analyzer.analyze(&body).unwrap();
        assert_eq!(analysis.max_tool_calls.as_str(), wide);
        assert_eq!(analysis.max_loop_iterations.as_str(), wide);
        assert_eq!(analysis.max_emit_records.as_str(), wide);
        assert!(analysis.bounded);
        assert_eq!(
            analyzer.check(&body, &limits(&wide, &wide, &wide), &skill.span, span),
            Ok(analysis)
        );
        for (tools, loops, emits, kind) in [
            (
                "1",
                wide.as_str(),
                wide.as_str(),
                BoundDiagnosticKind::ToolCalls,
            ),
            (
                wide.as_str(),
                "1",
                wide.as_str(),
                BoundDiagnosticKind::LoopIterations,
            ),
            (
                wide.as_str(),
                wide.as_str(),
                "1",
                BoundDiagnosticKind::EmitRecords,
            ),
        ] {
            let error = analyzer
                .check(&body, &limits(tools, loops, emits), &skill.span, span)
                .unwrap_err();
            if digits <= 4300 {
                let BoundError::Diagnostic(diagnostic) = error else {
                    panic!("diagnostic")
                };
                assert_eq!(diagnostic.kind, kind);
                assert_eq!(&diagnostic.span, span);
            } else {
                let BoundError::IntegerConversion(ref abort) = error else {
                    panic!("typed conversion abort")
                };
                assert_eq!(&abort.span, span);
                assert_eq!(error.to_string(), abort.to_string());
                assert!(
                    error
                        .source()
                        .unwrap()
                        .downcast_ref::<BoundIntegerConversionError>()
                        .is_some()
                );
                assert!(abort.source().is_none());
            }
        }
    }
}

#[test]
fn diagnostic_digit_limit_excludes_the_sign_of_a_declared_integer() {
    let source = source();
    let skill = parse(&source);
    let span = &skill.limits.as_ref().unwrap().span;
    let analyzer = StaticBoundAnalyzer::new([&source]);
    // Out-of-admission model values still obey Oracle formatting, rather than
    // panicking. Source and NSO admission separately require positive limits.
    for digits in [4300, 4301] {
        let negative = format!("-1{}", "0".repeat(digits - 1));
        let result = analyzer.check(&[], &limits(&negative, "1", "1"), &skill.span, span);
        if digits == 4300 {
            assert!(matches!(result, Err(BoundError::Diagnostic(_))));
        } else {
            assert!(matches!(result, Err(BoundError::IntegerConversion(_))));
        }
    }
}

#[test]
fn collection_and_duration_limits_are_preserved_for_runtime_enforcement() {
    let source = source();
    let skill = parse(&source);
    let span = &skill.limits.as_ref().unwrap().span;
    let body = program("symbol\nforeach stmt0001 100 0");
    let analyzer = StaticBoundAnalyzer::new([&source]);
    let expected = analyzer.analyze(&body).unwrap();
    for maximum in [1, 99, 100, 101] {
        let mut declaration = limits("1", "100", "1");
        declaration.collection_size = maximum.into();
        declaration.duration_ms = maximum.into();
        let original = declaration.clone();
        assert_eq!(
            analyzer.check(&body, &declaration, &skill.span, span),
            Ok(expected.clone())
        );
        assert_eq!(declaration, original);
    }
}

#[test]
fn deep_ir_and_long_expressions_do_not_require_recursive_analysis() {
    std::thread::Builder::new()
        .stack_size(512 * 1024)
        .spawn(|| {
            let mut instructions = String::from("read 0\n");
            for _ in 1..8192 {
                instructions.push_str("read 0\nbinary\n");
            }
            instructions.push_str("let stmt0001\n");
            instructions.push_str(&"symbol\n".repeat(2048));
            instructions.push_str("read 0\nemit emit0001 1\n");
            instructions.push_str(&"foreach stmt0002 1 1\n".repeat(2048));
            let body = program(&instructions);
            let analysis = StaticBoundAnalyzer::new([]).analyze(&body).unwrap();
            assert_eq!(analysis.max_tool_calls.as_str(), "8193");
            assert_eq!(analysis.max_loop_iterations.as_str(), "2048");
            assert_eq!(analysis.max_emit_records.as_str(), "1");
            drop_iteratively(body);
        })
        .unwrap()
        .join()
        .unwrap();
}
