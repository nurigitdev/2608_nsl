#[path = "approval_support/mod.rs"]
mod source_support;
mod write_safety_support;

use std::{collections::HashMap, error::Error};

use nsl_compiler::approvals::{ApprovalBindingAnalysis, ApprovalBindingAnalyzer};
use nsl_compiler::write_safety::{
    WriteSafetyAnalyzer, WriteSafetyDiagnosticKind, WriteSafetyError,
};
use nsl_source::ast::{Ast, StatementKind};
use nsl_source::{ParseLimits, ParseMode, Parser};
use source_support::*;
use write_safety_support::build;

#[test]
fn unbound_default_analysis_cannot_bypass_ast_validation() {
    let result =
        WriteSafetyAnalyzer::new([]).analyze(&ApprovalBindingAnalysis::default(), &HashMap::new());
    assert_eq!(result, Err(WriteSafetyError::InvalidApprovalAnalysis));
    let error = result.unwrap_err();
    assert_eq!(
        error.to_string(),
        "approval analysis has no originating AST"
    );
    assert!(error.source().is_none());
}

#[test]
fn missing_context_keeps_whole_write_span_and_typed_error_chain() {
    let source = source(
        "0.2",
        &format!(
            "{}foreach row in items max 1 {{ let x = {}; }}",
            approval("A"),
            write("A")
        ),
    );
    let skill = parse(&source, 2);
    let approvals = ApprovalBindingAnalyzer::new([&source])
        .analyze(&skill)
        .unwrap();
    let error = WriteSafetyAnalyzer::new([])
        .analyze(&approvals, &HashMap::new())
        .unwrap_err();
    let WriteSafetyError::Diagnostic(ref diagnostic) = error else {
        panic!("safety diagnostic")
    };
    assert_eq!(diagnostic.kind, WriteSafetyDiagnosticKind::WriteInForeach);
    assert_eq!(diagnostic.logical_path, None);
    assert_eq!(diagnostic.snippet, None);
    assert_eq!(diagnostic.position(), None);
    assert_eq!(
        diagnostic.span,
        skill
            .expression(approvals.bindings()[0].write)
            .unwrap()
            .span
    );
    assert_eq!(error.source().unwrap().to_string(), error.to_string());
    assert!(diagnostic.source().is_none());
    // Opaque, origin-bound results cannot contain a foreign expression through
    // the public API; retain the defensive error's formatting/source contract.
    let error = WriteSafetyError::InvalidWriteExpression(approvals.bindings()[0].write);
    assert_eq!(
        error.to_string(),
        "invalid WRITE expression in approval analysis"
    );
    assert!(error.source().is_none());
}

#[test]
fn cloned_approval_reports_keep_their_own_skill_and_analysis_state() {
    let first = source(
        "0.2",
        &format!(
            "{}let x = {};",
            approval("A"),
            write("A").replace("TEST.CREATE", "TEST.WRITE")
        ),
    );
    let other = source("0.2", "let x = root;");
    let first_skill = parse(&first, 1);
    let other_skill = parse(&other, 1);
    let approve = ApprovalBindingAnalyzer::new([&first, &other]);
    let checked = approve.analyze(&first_skill).unwrap().clone();
    let empty = approve.analyze(&other_skill).unwrap();
    let tool = build("WRITE", "UPDATE", "R5", None).unwrap();
    let contracts = HashMap::from([("TEST.WRITE", &tool)]);
    let analyzer = WriteSafetyAnalyzer::new([&first, &other]);
    assert!(
        analyzer
            .analyze(&checked, &contracts)
            .unwrap()
            .requires_verified_package_for_write
    );
    assert!(
        !analyzer
            .analyze(&empty, &HashMap::new())
            .unwrap()
            .requires_verified_package_for_write
    );
    assert_eq!(
        analyzer
            .analyze(&checked, &contracts)
            .unwrap()
            .write_node_count,
        1
    );
}

#[test]
fn grammar_requires_static_tool_names_and_explicit_approval_ids() {
    for body in [
        "let x = write TEST.CREATE(amount = root);",
        "let x = write TEST.CREATE(amount = root) approved_by true;",
        "let x = write tools[root](amount = root) approved_by A;",
        "let x = write (root)(amount = root) approved_by A;",
    ] {
        let source = source("0.2", body);
        assert!(
            Parser::parse(
                &source,
                ParseMode::RootSkill,
                ParseLimits { max_nesting: 8 }
            )
            .is_err()
        );
    }
    let source = source(
        "0.2",
        &format!(
            "let selected = \"TEST.CREATE\"; {}let x = write selected(amount = root) approved_by A;",
            approval("A")
        ),
    );
    let Ast::RootSkill(skill) = Parser::parse(
        &source,
        ParseMode::RootSkill,
        ParseLimits { max_nesting: 8 },
    )
    .unwrap() else {
        panic!("skill")
    };
    let StatementKind::Let { value, .. } = skill.statement(skill.body[2]).unwrap().kind else {
        panic!("let")
    };
    let checked = ApprovalBindingAnalyzer::new([&source])
        .analyze(&skill)
        .unwrap();
    let error = WriteSafetyAnalyzer::new([&source])
        .analyze(&checked, &HashMap::new())
        .unwrap_err();
    let WriteSafetyError::Diagnostic(error) = error else {
        panic!("safety diagnostic")
    };
    assert_eq!(error.kind, WriteSafetyDiagnosticKind::UndeclaredTool);
    assert_eq!(error.span, skill.expression(value).unwrap().span);
}
