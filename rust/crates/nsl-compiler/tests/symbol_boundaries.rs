use std::{error::Error, fmt::Write};

use nsl_compiler::symbols::{SymbolDiagnosticKind, SymbolResolutionError, resolve_symbols};
use nsl_source::ast::{Ast, AstSkill};
use nsl_source::{ParseLimits, ParseMode, Parser, SourceFile};

fn parsed(version: &str, body: &str, max_nesting: usize) -> (SourceFile, Box<AstSkill>) {
    let source = SourceFile::memory(&format!(
        "language NSL \"{version}\"; skill TEST {{ version \"1\"; risk READ_ONLY; \
         input {{ root: Int; items: List<Int>; }} \
         limits {{ tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1; }}\n{body}\n}}"
    ));
    let Ast::RootSkill(skill) =
        Parser::parse(&source, ParseMode::RootSkill, ParseLimits { max_nesting }).unwrap()
    else {
        panic!("expected skill");
    };
    (source, skill)
}

#[test]
fn approval_namespace_tracks_declarations_with_link_checks_reserved_for_r5_s06() {
    let (source, skill) = parsed(
        "0.2",
        r#"
        let result = write TOOL.CREATE(amount = root) approved_by LATE;
        approve LATE { message "later"; }
        foreach row in items max 1 { approve INNER { message "scope"; } }
        approve UNUSED { message "orphan"; }
    "#,
        4,
    );
    let result = resolve_symbols(&skill, [&source]).unwrap();
    assert_eq!(
        result
            .approvals()
            .iter()
            .map(|item| item.name.as_str())
            .collect::<Vec<_>>(),
        ["LATE", "UNUSED"]
    );
    assert_eq!(result.approval("LATE").unwrap().id.to_string(), "s0001");
    assert_eq!(result.approval("UNUSED").unwrap().id.to_string(), "s0002");
    assert_eq!(result.bindings()[0].id.to_string(), "s0001");
    assert_eq!(result.references().len(), 2);
}

#[test]
fn missing_source_context_keeps_the_original_span() {
    let (_, skill) = parsed("0.1", "let a = unknown;", 1);
    let error = resolve_symbols(&skill, []).unwrap_err();
    let SymbolResolutionError::Diagnostic(ref diagnostic) = error else {
        panic!("expected a symbol diagnostic");
    };
    assert_eq!(diagnostic.kind, SymbolDiagnosticKind::UnknownIdentifier);
    assert_eq!(diagnostic.span.source_id().as_str(), "memory:root");
    assert_eq!(diagnostic.logical_path, None);
    assert_eq!(diagnostic.position(), None);
    assert_eq!(diagnostic.snippet, None);
    assert_eq!(error.source().unwrap().to_string(), error.to_string());
}

#[test]
fn out_of_range_statement_reference_fails_without_a_partial_resolution() {
    let (_, other) = parsed("0.1", "let a = 1; let b = 2;", 1);
    for version in ["0.1", "0.2"] {
        let (source, mut skill) = parsed(version, "let a = 1;", 1);
        let foreign = other.body[1];
        skill.body.push(foreign);
        let error = resolve_symbols(&skill, [&source]).unwrap_err();
        assert_eq!(error, SymbolResolutionError::InvalidStatement(foreign));
        assert!(error.source().is_none());
        assert_eq!(
            error.to_string(),
            "invalid statement reference in skill AST"
        );
    }
}

#[test]
fn deep_scope_walk_and_error_unwind_fit_a_small_stack() {
    std::thread::Builder::new()
        .stack_size(128 * 1024)
        .spawn(|| {
            let depth = 4096;
            let mut body = String::new();
            for index in 0..depth {
                writeln!(body, "foreach row{index} in items max 1 {{").unwrap();
            }
            let closing = "}".repeat(depth);
            let valid = format!(
                "{body}let value = row{}; {closing} let after = root;",
                depth - 1
            );
            let (source, skill) = parsed("0.1", &valid, depth);
            let result = resolve_symbols(&skill, [&source]).unwrap();
            assert_eq!(result.scopes().len(), depth + 1);
            assert_eq!(result.bindings().len(), depth + 4);
            assert_eq!(result.references().len(), depth + 2);
            assert_eq!(
                result.bindings().last().unwrap().scope_id,
                result.scopes()[0].id
            );

            let invalid = format!("{body}let value = missing; {closing}");
            let (source, skill) = parsed("0.1", &invalid, depth);
            let error = resolve_symbols(&skill, [&source]).unwrap_err();
            assert!(matches!(error, SymbolResolutionError::Diagnostic(error)
            if error.kind == SymbolDiagnosticKind::UnknownIdentifier && error.name == "missing"));
        })
        .unwrap()
        .join()
        .unwrap();
}
