mod type_support;

use std::error::Error;

use nsl_compiler::includes::{
    IncludeOptions, IncludeResolveError, IncludeResolver, SourceBundleBuilder,
};
use nsl_compiler::symbols::resolve_symbols;
use nsl_compiler::types::{StaticTypeChecker, TypeCheckError, TypeDiagnosticKind};
use nsl_core::DataClassification;
use nsl_source::ast::StatementKind;
use nsl_source::{ParseLimits, SourceFile};
use type_support::*;

#[test]
fn optional_models_propagate_without_implicit_unwrapping_or_promotion() {
    let source = source(
        "input { x: Int; xs: List<Int>; }",
        "let same = x == x; foreach item in xs max 1 { let copy = item; }",
    );
    let mut skill = parse(&source, 2);
    skill.inputs[0].type_info.item = optional(primitive("Int"));
    skill.inputs[1].type_info.item = optional(primitive("Int"));
    let resolution = resolve_symbols(&skill, [&source]).unwrap();
    let result = StaticTypeChecker::new([&source])
        .check(&skill, &resolution, &catalog())
        .unwrap();
    for binding in resolution.bindings() {
        let value = result.symbol(binding.id).unwrap();
        let expected = match binding.name.as_str() {
            "xs" => list(optional(primitive("Int"))),
            "same" => primitive("Bool"),
            _ => optional(primitive("Int")),
        };
        assert_eq!(value.type_info, expected);
    }
    for (body, declarations, kind) in [
        (
            "let bad = x + x;",
            "input { x: Int; }",
            TypeDiagnosticKind::BinaryType,
        ),
        (
            "let bad = x.field;",
            "input { x: Int; }",
            TypeDiagnosticKind::UnknownField,
        ),
        (
            "emit { out: x; }",
            "input { x: Int; } output { out: Int; }",
            TypeDiagnosticKind::OutputType,
        ),
        (
            "let bad = read TEST.SCALAR(amount: x);",
            "requires { tool TEST.SCALAR version \"1.0.0\"; } input { x: Int; }",
            TypeDiagnosticKind::ToolArgumentType,
        ),
    ] {
        let source = type_support::source(declarations, body);
        let mut skill = parse(&source, 2);
        skill.inputs[0].type_info.item = optional(primitive("Int"));
        let resolution = resolve_symbols(&skill, [&source]).unwrap();
        let TypeCheckError::Diagnostic(error) = StaticTypeChecker::new([&source])
            .check(&skill, &resolution, &catalog())
            .unwrap_err()
        else {
            panic!("type diagnostic")
        };
        assert_eq!(error.kind, kind);
    }
    let source = type_support::source("input { x: Int; } output { out: Int; }", "emit { out: x; }");
    let mut skill = parse(&source, 1);
    skill.outputs[0].type_info.item = optional(primitive("Int"));
    let resolution = resolve_symbols(&skill, [&source]).unwrap();
    let TypeCheckError::Diagnostic(error) = StaticTypeChecker::new([&source])
        .check(&skill, &resolution, &catalog())
        .unwrap_err()
    else {
        panic!("type diagnostic")
    };
    assert_eq!(error.kind, TypeDiagnosticKind::OutputType);
}

#[test]
fn missing_source_context_keeps_the_full_span_and_typed_error_chain() {
    let source = source("", "let bad = true + false;");
    let skill = parse(&source, 0);
    let resolution = resolve_symbols(&skill, [&source]).unwrap();
    let error = StaticTypeChecker::new([])
        .check(&skill, &resolution, &catalog())
        .unwrap_err();
    let TypeCheckError::Diagnostic(ref diagnostic) = error else {
        panic!("type diagnostic")
    };
    let StatementKind::Let { value, .. } = skill.statement(skill.body[0]).unwrap().kind else {
        panic!("let")
    };
    assert_eq!(diagnostic.span, skill.expression(value).unwrap().span);
    assert_eq!(diagnostic.logical_path, None);
    assert_eq!(diagnostic.snippet, None);
    assert_eq!(diagnostic.position(), None);
    assert_eq!(
        error.to_string(),
        "NSL-E3001: operator + is not defined for Bool"
    );
    assert_eq!(error.source().unwrap().to_string(), error.to_string());
    assert!(diagnostic.source().is_none());
    assert_eq!(
        TypeCheckError::InvalidResolution.to_string(),
        "AST and symbol resolution are inconsistent"
    );
    assert!(TypeCheckError::InvalidResolution.source().is_none());
}

#[test]
fn inconsistent_ast_or_resolution_returns_a_typed_error() {
    let empty = source("", "");
    let empty_skill = parse(&empty, 0);
    let empty_resolution = resolve_symbols(&empty_skill, [&empty]).unwrap();
    for (declarations, body) in [
        ("input { x: Int; }", ""),
        ("context { ctx: Int from \"prior\"; }", ""),
        ("", "let x = 1;"),
        ("", "foreach x in read TEST.LIST() max 1 {}"),
    ] {
        let declarations =
            format!("requires {{ tool TEST.LIST version \"1.0.0\"; }} {declarations}");
        let source = source(&declarations, body);
        let skill = parse(&source, 2);
        assert_eq!(
            StaticTypeChecker::new([&source]).check(&skill, &empty_resolution, &catalog()),
            Err(TypeCheckError::InvalidResolution)
        );
    }
    let input = source("input { x: Int; }", "let y = x;");
    let mut skill = parse(&input, 0);
    let resolution = resolve_symbols(&skill, [&input]).unwrap();
    let StatementKind::Let { value, .. } = skill.statement(skill.body[0]).unwrap().kind else {
        panic!("let")
    };
    skill.body.clear();
    let without_references = resolve_symbols(&skill, [&input]).unwrap();
    let original = parse(&input, 0);
    assert_eq!(
        StaticTypeChecker::new([&input]).check(&original, &without_references, &catalog()),
        Err(TypeCheckError::InvalidResolution)
    );
    skill.inputs.clear();
    skill.body = original.body;
    assert_eq!(
        StaticTypeChecker::new([&input]).check(&skill, &resolution, &catalog()),
        Err(TypeCheckError::InvalidResolution)
    );
    // IDs are arena-local: references copied from a longer arena are rejected.
    let other = source("", "let a = 1; let b = 2; let c = 3;");
    let other_skill = parse(&other, 0);
    let mut broken = empty_skill;
    broken.body.push(other_skill.body[2]);
    assert_eq!(
        StaticTypeChecker::new([&empty]).check(&broken, &empty_resolution, &catalog()),
        Err(TypeCheckError::InvalidResolution)
    );
    let mut broken = parse(&empty, 0);
    broken.body.push(skill.body[0]);
    assert!(broken.expression(value).is_none());
}

struct Fragment(SourceFile);
impl IncludeResolver for Fragment {
    fn resolve(&self, _: &SourceFile, path: &str) -> Result<SourceFile, IncludeResolveError> {
        assert_eq!(path, self.0.logical_path());
        Ok(self.0.clone())
    }
}

#[test]
fn included_contract_error_preserves_fragment_location_and_snippet() {
    let root = source("include \"contracts.ns\";", "");
    let resolver = Fragment(
        SourceFile::from_text(
            "types/contracts.ns",
            "// 조각 😀\r\nrequires { tool TEST.MISSING version \"1.0.0\"; }",
            None,
        )
        .unwrap(),
    );
    let bundle = SourceBundleBuilder::new(
        &resolver,
        IncludeOptions::default(),
        ParseLimits { max_nesting: 8 },
    )
    .build(root)
    .unwrap();
    let skill = bundle.compose().unwrap();
    let resolution = resolve_symbols(&skill, bundle.sources()).unwrap();
    let TypeCheckError::Diagnostic(error) = StaticTypeChecker::new(bundle.sources())
        .check(&skill, &resolution, &catalog())
        .unwrap_err()
    else {
        panic!("type diagnostic")
    };
    assert_eq!(error.kind, TypeDiagnosticKind::UnknownToolContract);
    assert_eq!(error.logical_path.as_deref(), Some("types/contracts.ns"));
    assert_eq!(error.position().unwrap().line(), 2);
    assert_eq!(
        error.snippet.as_deref(),
        Some("requires { tool TEST.MISSING version \"1.0.0\"; }")
    );
    assert_eq!(error.span, skill.requires[0].span);
}

#[test]
fn long_binary_chain_and_nested_scopes_use_an_explicit_work_stack() {
    std::thread::Builder::new()
        .stack_size(512 * 1024)
        .spawn(|| {
            let expression = std::iter::repeat_n("1", 8192)
                .collect::<Vec<_>>()
                .join(" + ");
            let mut body = format!("let total = {expression};\n");
            for depth in 0..128 {
                body.push_str(&format!("foreach item{depth} in items max 1 {{\n"));
            }
            body.push_str("let copied = total;\n");
            body.push_str(&"}\n".repeat(128));
            let source = source(
                "input { items: List<Int> classification RESTRICTED; }",
                &body,
            );
            let skill = parse(&source, 128);
            let resolution = resolve_symbols(&skill, [&source]).unwrap();
            let result = StaticTypeChecker::new([&source])
                .check(&skill, &resolution, &catalog())
                .unwrap();
            assert_eq!(resolution.bindings().len(), 131);
            for binding in resolution
                .bindings()
                .iter()
                .filter(|binding| binding.name.starts_with("item") && binding.name != "items")
            {
                let value = result.symbol(binding.id).unwrap();
                assert_eq!(value.type_info, primitive("Int"));
                assert_eq!(value.classification, DataClassification::Restricted);
            }
        })
        .unwrap()
        .join()
        .unwrap();
}
