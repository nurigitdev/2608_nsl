use std::{cell::RefCell, collections::HashMap, error::Error, fmt::Write};

use nsl_compiler::includes::{
    IncludeError, IncludeOptions, IncludeResolveError, IncludeResolver, SourceBundle,
    SourceBundleBuilder,
};
use nsl_core::TypeKind;
use nsl_source::{LexerError, ParseError, ParseLimits, SourceFile, SourcePosition, SourceSpan};

const ORACLE: &str = include_str!("fixtures/include-oracle.tsv");

#[derive(Default)]
struct Resolver {
    responses: HashMap<String, SourceFile>,
    calls: RefCell<Vec<(String, String)>>,
}

impl IncludeResolver for Resolver {
    fn resolve(
        &self,
        including: &SourceFile,
        target: &str,
    ) -> Result<SourceFile, IncludeResolveError> {
        self.calls
            .borrow_mut()
            .push((including.logical_path().into(), target.into()));
        self.responses
            .get(target)
            .cloned()
            .ok_or(IncludeResolveError)
    }
}

fn encode(text: &str) -> String {
    if text.is_empty() {
        return "-".into();
    }
    text.bytes().map(|byte| format!("{byte:02x}")).collect()
}

fn decode(hex: &str) -> String {
    if hex == "-" {
        return String::new();
    }
    assert!(hex.len().is_multiple_of(2));
    let bytes = (0..hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
        .collect();
    String::from_utf8(bytes).unwrap()
}

fn coords(position: SourcePosition) -> String {
    format!(
        "{},{},{}",
        position.offset(),
        position.line(),
        position.column()
    )
}

fn span_fields(span: &SourceSpan) -> String {
    format!(
        "{}\t{}\t{}",
        span.source_id().as_str(),
        coords(span.start()),
        coords(span.end())
    )
}

fn observe_error(out: &mut String, stage: &str, error: &IncludeError) {
    let (code, path, position) = match error {
        IncludeError::Diagnostic(error) => {
            if let Some(span) = &error.span {
                assert_eq!(span.source_id(), &error.source_id);
            }
            (
                error.kind.code(),
                error.logical_path.as_str(),
                error.span.as_ref().map(SourceSpan::start),
            )
        }
        IncludeError::Parse {
            logical_path,
            error,
        } => {
            let (code, position) = match error {
                ParseError::Diagnostic(error) => (error.kind.code(), error.span.start()),
                ParseError::Lexer(LexerError::Header(error)) => (error.kind.code(), error.position),
                ParseError::Lexer(LexerError::Diagnostic(error)) => {
                    (error.kind.code(), error.position)
                }
                other => panic!("unexpected parser error: {other:?}"),
            };
            (code, logical_path.as_str(), Some(position))
        }
        other => panic!("unexpected include error: {other:?}"),
    };
    assert!(error.to_string().contains(code));
    assert!(error.source().is_some());
    let location = position.map_or_else(|| "-".into(), |p| format!("{},{}", p.line(), p.column()));
    writeln!(out, "error\t{stage}\t{code}\t{}\t{location}", encode(path)).unwrap();
}

fn observe_bundle(out: &mut String, bundle: &SourceBundle) {
    let build = bundle.build_metadata();
    assert_eq!(
        build.compute_source_bundle_sha256().unwrap(),
        build.source_bundle_sha256
    );
    writeln!(
        out,
        "bundle\t{}\t{}",
        encode(&build.root_source),
        build.source_bundle_sha256
    )
    .unwrap();
    for (source_id, entry) in bundle.manifest() {
        writeln!(
            out,
            "manifest\t{}\t{}\t{}\t{}\t{}",
            source_id.as_str(),
            encode(&entry.logical_path),
            entry.content_hash,
            entry.size_bytes.as_str(),
            entry.is_root
        )
        .unwrap();
    }
    for edge in bundle.edges() {
        assert_eq!(&edge.from_source, edge.include_span.source_id());
        writeln!(
            out,
            "edge\t{}\t{}\t{}",
            edge.from_source.as_str(),
            edge.to_source.as_str(),
            span_fields(&edge.include_span)
        )
        .unwrap();
    }
    let skill = match bundle.compose() {
        Ok(skill) => skill,
        Err(error) => {
            observe_error(out, "compose", &error);
            return;
        }
    };
    let mut root = bundle.root().ast.as_ref().clone();
    root.includes.clear();
    root.requires = skill.requires.clone();
    root.contexts = skill.contexts.clone();
    root.limits = skill.limits.clone();
    assert_eq!(root, skill);
    writeln!(
        out,
        "skill\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
        skill.language_version.as_str(),
        encode(&skill.skill_id),
        skill.includes.len(),
        skill.inputs.len(),
        skill.outputs.len(),
        skill.body.len(),
        span_fields(&skill.span)
    )
    .unwrap();
    for tool in skill.requires {
        writeln!(
            out,
            "tool\t{}\t{}\t{}",
            encode(&tool.tool_id),
            encode(&tool.version),
            span_fields(&tool.span)
        )
        .unwrap();
    }
    for context in skill.contexts {
        let (kind, name) = match context.type_info.item.kind() {
            TypeKind::Primitive(name) => ("primitive", name.as_str()),
            TypeKind::Money(currency) => ("money", currency.as_str()),
            other => panic!("unexpected fixture context type: {other:?}"),
        };
        write!(
            out,
            "context\t{}\t{}\t{}\t{kind}\t{}\t{}",
            encode(&context.name),
            context.classification,
            context.type_info.list_depth,
            encode(name),
            span_fields(&context.span)
        )
        .unwrap();
        for part in context.path {
            write!(out, "\t{}", encode(&part)).unwrap();
        }
        out.push('\n');
    }
    let block = skill.limits.unwrap();
    writeln!(
        out,
        "limits\t{}\t{}\t{}\t{}\t{}\t{}",
        block.tool_calls.as_str(),
        block.loop_iterations.as_str(),
        block.emitted_rows.as_str(),
        block.collection_size.as_str(),
        block.duration_ms.as_str(),
        span_fields(&block.span)
    )
    .unwrap();
}

#[test]
fn canonical_includes_composition_and_limits_match_frozen_oracle() {
    let cases = ORACLE
        .strip_prefix("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a\ncase\t")
        .unwrap();
    for case in cases.split("\ncase\t") {
        let (input, expected) = case.split_once("\nexpected\n").unwrap();
        let mut rows = input
            .lines()
            .map(|line| line.split('\t').collect::<Vec<_>>());
        let settings = rows.next().unwrap();
        let [label, scope, depth, files, size] = settings.as_slice() else {
            panic!("invalid fixture settings: {settings:?}");
        };
        let options = IncludeOptions {
            include_root: decode(scope),
            max_include_depth: depth.parse().unwrap(),
            max_include_files: files.parse().unwrap(),
            max_total_source_bytes: size.parse().unwrap(),
        };
        let root_row = rows.next().unwrap();
        let ["root", path, text] = root_row.as_slice() else {
            panic!("invalid fixture root: {root_row:?}");
        };
        let root = SourceFile::from_text(&decode(path), &decode(text), None).unwrap();
        let mut resolver = Resolver::default();
        for row in rows {
            let ["source", key, path, text, id] = row.as_slice() else {
                panic!("invalid fixture source: {row:?}");
            };
            let id = decode(id);
            let id = (!id.is_empty()).then_some(id.as_str());
            let source = SourceFile::from_text(&decode(path), &decode(text), id).unwrap();
            assert!(resolver.responses.insert(decode(key), source).is_none());
        }
        let mut actual = String::new();
        let builder =
            SourceBundleBuilder::new(&resolver, options, ParseLimits { max_nesting: 128 });
        match builder.build(root) {
            Ok(ref bundle) => observe_bundle(&mut actual, bundle),
            Err(ref error) => observe_error(&mut actual, "build", error),
        }
        for (parent, target) in resolver.calls.into_inner() {
            writeln!(actual, "call\t{}\t{}", encode(&parent), encode(&target)).unwrap();
        }
        for (index, (actual, expected)) in actual.lines().zip(expected.lines()).enumerate() {
            assert_eq!(actual, expected, "{label}, observation {}", index + 1);
        }
        assert_eq!(actual.lines().count(), expected.lines().count(), "{label}");
    }
}
