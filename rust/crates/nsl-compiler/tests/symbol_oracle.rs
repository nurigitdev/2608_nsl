use std::fmt::Write;

use nsl_compiler::includes::{
    IncludeOptions, IncludeResolveError, IncludeResolver, SourceBundleBuilder,
};
use nsl_compiler::symbols::{ScopeKind, SymbolResolution, SymbolResolutionError, resolve_symbols};
use nsl_source::ast::{Ast, AstSkill, ExpressionKind, StatementKind};
use nsl_source::{ParseLimits, ParseMode, Parser, SourceFile, SourcePosition, SourceSpan};

const ORACLE: &str = include_str!("fixtures/symbol-oracle.tsv");
const LIMITS: ParseLimits = ParseLimits { max_nesting: 64 };
const ROOT: &str = "symbols/skill.ns";
const FRAGMENT: &str = "symbols/fields.ns";

struct Include(SourceFile);

impl IncludeResolver for Include {
    fn resolve(&self, _: &SourceFile, path: &str) -> Result<SourceFile, IncludeResolveError> {
        assert_eq!(path, FRAGMENT);
        Ok(self.0.clone())
    }
}

fn decode(data: &str) -> String {
    if data == "-" {
        return String::new();
    }
    assert!(data.len().is_multiple_of(2));
    let bytes = (0..data.len())
        .step_by(2)
        .map(|index| u8::from_str_radix(&data[index..index + 2], 16).unwrap())
        .collect();
    String::from_utf8(bytes).unwrap()
}

fn encode(data: &str) -> String {
    if data.is_empty() {
        return "-".into();
    }
    data.bytes().map(|byte| format!("{byte:02x}")).collect()
}

fn position(point: SourcePosition) -> String {
    format!("{},{},{}", point.offset(), point.line(), point.column())
}

fn span(range: &SourceSpan) -> String {
    format!(
        "{}\t{}\t{}",
        range.source_id().as_str(),
        position(range.start()),
        position(range.end())
    )
}

fn observe_success(out: &mut String, skill: &AstSkill, resolution: &SymbolResolution, mode: &str) {
    for approval in resolution.approvals() {
        assert_eq!(resolution.approval(&approval.name), Some(approval));
        assert!(matches!(
            &skill.statement(approval.statement).unwrap().kind,
            StatementKind::Approve { approval_id, .. } if approval_id == &approval.name
        ));
        writeln!(
            out,
            "approval\t{}\t{}\t{}",
            approval.id,
            approval.name,
            span(&approval.span)
        )
        .unwrap();
    }
    assert_eq!(resolution.approval("UNDECLARED"), None);
    if mode == "approval" {
        return;
    }
    for scope in resolution.scopes() {
        let parent = scope
            .parent_id
            .map_or_else(|| "-".into(), |id| id.to_string());
        if let ScopeKind::Foreach(statement) = scope.kind {
            assert!(matches!(
                skill.statement(statement).unwrap().kind,
                StatementKind::Foreach { .. }
            ));
        }
        writeln!(
            out,
            "scope\t{}\t{parent}\t{}",
            scope.id,
            scope.kind.as_str()
        )
        .unwrap();
    }
    for binding in resolution.bindings() {
        assert_eq!(resolution.binding(binding.id), Some(binding));
        writeln!(
            out,
            "binding\t{}\t{}\t{}\t{}\t{}",
            binding.id,
            binding.name,
            binding.declaration.category(),
            binding.scope_id,
            span(&binding.span)
        )
        .unwrap();
    }
    for reference in resolution.references() {
        let binding = resolution.binding(reference.symbol).unwrap();
        let node = skill.expression(reference.expression).unwrap();
        assert!(matches!(&node.kind, ExpressionKind::Path(parts) if parts[0] == binding.name));
        assert_eq!(node.span, reference.span);
        writeln!(
            out,
            "reference\t{}\t{}\t{}",
            reference.symbol,
            binding.name,
            span(&reference.span)
        )
        .unwrap();
    }
}

fn observe_result(
    out: &mut String,
    skill: &AstSkill,
    result: Result<SymbolResolution, SymbolResolutionError>,
    mode: &str,
) {
    match result {
        Ok(resolution) => observe_success(out, skill, &resolution, mode),
        Err(SymbolResolutionError::Diagnostic(error)) => {
            let point = error.position().unwrap();
            let code = error.kind.code();
            let display = error.to_string();
            let message = display.strip_prefix(&format!("{code}: ")).unwrap();
            let snippet = error.snippet.as_deref().map_or_else(|| "~".into(), encode);
            writeln!(
                out,
                "error\t{code}\t{},{}\t{}\t{snippet}\t{}",
                point.line(),
                point.column(),
                encode(error.logical_path.as_deref().unwrap()),
                encode(message)
            )
            .unwrap();
        }
        Err(other) => panic!("unexpected symbol error: {other}"),
    }
}

#[test]
fn symbols_scopes_references_and_diagnostics_match_frozen_oracle() {
    let cases = ORACLE
        .strip_prefix("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a\ncase\t")
        .unwrap();
    for case in cases.split("\ncase\t") {
        let (input, expected) = case.split_once('\n').unwrap();
        let header: Vec<_> = input.split('\t').collect();
        let [mode, version, label, text, fragment] = header.as_slice() else {
            panic!("invalid case row: {header:?}");
        };
        let source = SourceFile::from_text(ROOT, &decode(text), None).unwrap();
        let mut actual = String::new();
        if *fragment == "~" {
            let Ast::RootSkill(skill) =
                Parser::parse(&source, ParseMode::RootSkill, LIMITS).unwrap()
            else {
                panic!("expected skill");
            };
            let result = resolve_symbols(&skill, [&source]);
            assert_eq!(
                result,
                resolve_symbols(&skill, [&source]),
                "{version}: {label}"
            );
            observe_result(&mut actual, &skill, result, mode);
        } else {
            let resolver =
                Include(SourceFile::from_text(FRAGMENT, &decode(fragment), None).unwrap());
            let bundle = SourceBundleBuilder::new(&resolver, IncludeOptions::default(), LIMITS)
                .build(source)
                .unwrap();
            let skill = bundle.compose().unwrap();
            let result = resolve_symbols(&skill, bundle.sources());
            observe_result(&mut actual, &skill, result, mode);
        }
        for (index, (actual, expected)) in actual.lines().zip(expected.lines()).enumerate() {
            assert_eq!(
                actual,
                expected,
                "{version}: {label}, observation {}",
                index + 1
            );
        }
        assert_eq!(
            actual.lines().count(),
            expected.lines().count(),
            "{version}: {label}"
        );
    }
}
