use std::error::Error;

use nsl_source::ast::{Ast, ExpressionKind, StatementKind};
use nsl_source::{LexerError, ParseError, ParseLimits, ParseMode, Parser, SourceFile};

const ROOT: &str =
    "language NSL \"0.2\"; skill TEST { version \"1\"; risk READ_ONLY; include \"base.ns\"; ";

fn root(body: &str) -> SourceFile {
    SourceFile::memory(&format!("{ROOT}{body}}}"))
}

fn parse(source: &SourceFile, max_nesting: usize) -> Result<Ast, ParseError> {
    Parser::parse(source, ParseMode::RootSkill, ParseLimits { max_nesting })
}

fn assert_limit(source: &SourceFile, max_nesting: usize, start: usize, width: usize) {
    let error = parse(source, max_nesting).unwrap_err();
    let ParseError::Nesting(nesting) = &error else {
        panic!("expected nesting error, got {error:?}");
    };
    assert_eq!(nesting.max_nesting, max_nesting);
    assert_eq!(
        nesting.span,
        source.span_at_bytes(start..start + width).unwrap()
    );
    assert!(!error.to_string().contains("NSL-E"));
    assert_eq!(error.source().unwrap().to_string(), error.to_string());
}

#[test]
fn zero_limit_allows_flat_syntax_and_each_opening_has_a_span() {
    let flat = root("input { cost: Money<USD>; } let x = value + 1 * 2;");
    assert!(parse(&flat, 0).is_ok());
    for (body, opening) in [
        ("let x = (1);", "("),
        ("let x = foo();", "("),
        ("let x = read TOOL();", "("),
        ("let x = write TOOL() approved_by OK;", "("),
        ("input { x: List<Int>; }", "List"),
        ("foreach x in xs max 1 {}", "foreach"),
    ] {
        let source = root(body);
        assert_limit(
            &source,
            0,
            ROOT.len() + body.find(opening).unwrap(),
            opening.len(),
        );
        assert!(parse(&source, 1).is_ok(), "{body}");
    }
}

#[test]
fn nesting_budget_counts_simultaneous_openings_and_reuses_closed_levels() {
    let body = "foreach x in list(xs) max 1 { let v = wrap((x)); }";
    let source = root(body);
    assert_limit(&source, 2, ROOT.len() + body.find("(x)").unwrap(), 1);
    assert!(parse(&source, 3).is_ok());
    assert!(parse(&source, 4).is_ok());

    let siblings = root("let x = f(g(1), h(2)); let y = t((x));");
    assert_limit(&siblings, 1, ROOT.len() + "let x = f(g".len(), 1);
    assert!(parse(&siblings, 2).is_ok());

    let source = root("input { x: List<List<Int>>; }");
    assert_limit(
        &source,
        1,
        ROOT.len() + "input { x: List<".len(),
        "List".len(),
    );
    assert!(parse(&source, 2).is_ok());

    let source = root("foreach x in xs max 1 { foreach y in ys max 1 {} }");
    assert_limit(
        &source,
        1,
        ROOT.len() + "foreach x in xs max 1 { ".len(),
        "foreach".len(),
    );
    assert!(parse(&source, 2).is_ok());
}

#[test]
fn deep_input_and_flat_operator_chains_do_not_need_a_deep_thread_stack() {
    std::thread::Builder::new()
        .stack_size(128 * 1024)
        .spawn(|| {
            let depth = 4096;
            let body = format!(
                "input {{ x: {}Int{}; }} let group = {}1{}; let call = {}1{}; {}{}let sum = 1{};",
                "List<".repeat(depth),
                ">".repeat(depth),
                "(".repeat(depth),
                ")".repeat(depth),
                "f(".repeat(depth),
                ")".repeat(depth),
                "foreach x in xs max 1 {".repeat(depth),
                "}".repeat(depth),
                " + 1".repeat(depth),
            );
            let Ast::RootSkill(skill) = parse(&root(&body), depth).unwrap() else {
                panic!("expected root");
            };
            assert_eq!(skill.inputs[0].type_info.list_depth, depth);
            let StatementKind::Let { value, .. } = skill.statement(skill.body[3]).unwrap().kind
            else {
                panic!("expected let");
            };
            assert!(matches!(
                skill.expression(value).unwrap().kind,
                ExpressionKind::Binary { .. }
            ));
            let cloned = skill.clone();
            assert_eq!(cloned, skill);
        })
        .unwrap()
        .join()
        .unwrap();
}

#[test]
fn duration_conversion_is_still_a_lexer_failure_at_the_parser_entry_point() {
    let source = root("let x = ²s;");
    let error = parse(&source, 0).unwrap_err();
    assert!(matches!(
        error,
        ParseError::Lexer(LexerError::Duration(ref duration))
            if duration.span == source.span_at_bytes(ROOT.len() + 8..ROOT.len() + 11).unwrap()
    ));
    assert!(!error.to_string().contains("NSL-E"));
}
