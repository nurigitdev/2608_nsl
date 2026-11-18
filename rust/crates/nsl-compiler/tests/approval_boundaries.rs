mod approval_support;

use std::{error::Error, fmt::Write};

use approval_support::*;
use nsl_compiler::approvals::{
    ApprovalAnalysisError, ApprovalBindingAnalyzer, ApprovalDiagnosticKind,
};
use nsl_compiler::symbols::resolve_symbols;
use nsl_source::SourceLanguageVersion;
use nsl_source::ast::StatementKind;

#[test]
fn approval_namespace_allocation_matches_symbol_resolution_independently_of_values() {
    let source = source(
        "0.2",
        &format!(
            "{}let first = {}; {}let second = {};",
            approval("root"),
            write("root"),
            approval("A"),
            write("A")
        ),
    );
    let skill = parse(&source, 2);
    let resolution = resolve_symbols(&skill, [&source]).unwrap();
    let result = ApprovalBindingAnalyzer::new([&source])
        .analyze(&skill)
        .unwrap();
    assert_eq!(resolution.bindings()[0].name, "root");
    assert_eq!(resolution.bindings()[0].id.to_string(), "s0001");
    for (index, declaration) in result.approvals().iter().enumerate() {
        assert_eq!(&declaration.symbol, &resolution.approvals()[index]);
        assert_eq!(declaration.declaration_order, 2 * index + 1);
        assert_eq!(result.bindings()[index].write_order, 2 * index + 2);
        assert_eq!(result.bindings()[index].approval, declaration.symbol.id);
    }
    assert_eq!(result.approvals()[0].symbol.name, "root");
    assert_eq!(result.approvals()[0].symbol.id.to_string(), "s0001");
}

#[test]
fn missing_context_keeps_the_full_span_and_typed_error_chain() {
    let source = source("0.2", &format!("let x = {};", write("UNKNOWN")));
    let skill = parse(&source, 1);
    let error = ApprovalBindingAnalyzer::new([])
        .analyze(&skill)
        .unwrap_err();
    let ApprovalAnalysisError::Diagnostic(ref diagnostic) = error else {
        panic!("approval diagnostic")
    };
    assert_eq!(diagnostic.kind, ApprovalDiagnosticKind::UnknownApproval);
    assert_eq!(diagnostic.logical_path, None);
    assert_eq!(diagnostic.snippet, None);
    assert_eq!(diagnostic.position(), None);
    let StatementKind::Let { value, .. } = skill.statement(skill.body[0]).unwrap().kind else {
        panic!("let")
    };
    let nsl_source::ast::ExpressionKind::Write { approval_span, .. } =
        &skill.expression(value).unwrap().kind
    else {
        panic!("write")
    };
    assert_eq!(&diagnostic.span, approval_span);
    assert_eq!(error.to_string(), "NSL-E5301: unknown approval ID: UNKNOWN");
    assert_eq!(error.source().unwrap().to_string(), error.to_string());
    assert!(diagnostic.source().is_none());
}

#[test]
fn invalid_statement_references_are_typed_and_v01_skips_the_analysis() {
    let longer = source("0.2", "let a = 1; let b = 2;");
    let longer = parse(&longer, 1);
    let source = source("0.2", "");
    let mut skill = parse(&source, 1);
    skill.body.push(longer.body[1]);
    let analyzer = ApprovalBindingAnalyzer::new([&source]);
    let error = analyzer.analyze(&skill).unwrap_err();
    assert_eq!(
        error,
        ApprovalAnalysisError::InvalidStatement(longer.body[1])
    );
    assert_eq!(
        error.to_string(),
        "invalid statement reference in skill AST"
    );
    assert!(error.source().is_none());
    // Expression arena IDs are opaque and valid parser nodes cannot contain a
    // foreign expression. Keep the defensive error's public contract covered.
    let StatementKind::Let { value, .. } = longer.statement(longer.body[1]).unwrap().kind else {
        panic!("let")
    };
    let error = ApprovalAnalysisError::InvalidExpression(value);
    assert_eq!(
        error.to_string(),
        "invalid expression reference in skill AST"
    );
    assert!(error.source().is_none());
    skill.language_version = SourceLanguageVersion::V0_1;
    let result = analyzer.analyze(&skill).unwrap();
    assert!(result.approvals().is_empty());
    assert!(result.bindings().is_empty());
}

#[test]
fn analyzer_calls_do_not_share_names_orders_or_foreach_state_between_skills() {
    let first = source(
        "0.2",
        &format!(
            "{}foreach row in items max 1 {{ let x = {}; }}",
            approval("A"),
            write("A")
        ),
    );
    let second = source("0.2", &format!("let x = {};", write("A")));
    let third = source("0.2", &format!("{}let x = {};", approval("B"), write("B")));
    let analyzer = ApprovalBindingAnalyzer::new([&first, &second, &third]);
    let first_skill = parse(&first, 2);
    let valid = analyzer.analyze(&first_skill).unwrap();
    assert!(valid.bindings()[0].inside_foreach);
    let error = analyzer.analyze(&parse(&second, 1)).unwrap_err();
    assert!(
        matches!(error, ApprovalAnalysisError::Diagnostic(error) if error.kind == ApprovalDiagnosticKind::UnknownApproval)
    );
    let third_skill = parse(&third, 1);
    let last = analyzer.analyze(&third_skill).unwrap();
    assert!(!last.bindings()[0].inside_foreach);
    assert_eq!(last.approvals()[0].symbol.id.to_string(), "s0001");
    assert_eq!(last.approvals()[0].declaration_order, 1);
    assert_eq!(last.bindings()[0].write_order, 2);
    assert_eq!(valid, analyzer.analyze(&first_skill).unwrap());
}

#[test]
fn deep_scopes_calls_and_binary_expressions_use_an_explicit_work_stack() {
    std::thread::Builder::new()
        .stack_size(512 * 1024)
        .spawn(|| {
            let depth = 4096;
            let body = format!(
                "{}{}let x = {}; {}",
                approval("A"),
                "foreach row in items max 1 {".repeat(depth),
                write("A"),
                "}".repeat(depth)
            );
            let source = source("0.2", &body);
            let skill = parse(&source, depth + 1);
            let result = ApprovalBindingAnalyzer::new([&source])
                .analyze(&skill)
                .unwrap();
            assert!(result.bindings()[0].inside_foreach);
            let body = format!(
                "{}let x = {}{}{};",
                approval("A"),
                "count(".repeat(depth),
                write("A"),
                ")".repeat(depth)
            );
            let source = approval_support::source("0.2", &body);
            let skill = parse(&source, depth + 1);
            let result = ApprovalBindingAnalyzer::new([&source])
                .analyze(&skill)
                .unwrap();
            assert!(!result.bindings()[0].inside_foreach);
            assert_eq!(result.bindings()[0].write_order, 2);
            let body = format!(
                "{}let x = {}{};",
                approval("A"),
                "1 + ".repeat(8192),
                write("A")
            );
            let source = approval_support::source("0.2", &body);
            let skill = parse(&source, 1);
            let result = ApprovalBindingAnalyzer::new([&source])
                .analyze(&skill)
                .unwrap();
            assert_eq!(result.bindings().len(), 1);
            assert_eq!(result.bindings()[0].write_order, 2);
        })
        .unwrap()
        .join()
        .unwrap();
}

#[test]
fn approval_ids_and_event_orders_remain_exact_across_four_digit_ids() {
    for count in [0, 1, 2, 9999, 10000, 10001] {
        let mut body = String::new();
        for index in 0..count {
            writeln!(
                body,
                "{}let x{index} = {};",
                approval(&format!("A{index}")),
                write(&format!("A{index}"))
            )
            .unwrap();
        }
        let source = source("0.2", &body);
        let skill = parse(&source, 1);
        let result = ApprovalBindingAnalyzer::new([&source])
            .analyze(&skill)
            .unwrap();
        assert_eq!(result.approvals().len(), count);
        assert_eq!(result.bindings().len(), count);
        if let Some(last) = result.approvals().last() {
            assert_eq!(last.symbol.id.to_string(), format!("s{count:04}"));
            assert_eq!(last.declaration_order, 2 * count - 1);
            assert_eq!(result.bindings().last().unwrap().write_order, 2 * count);
        }
    }
}
