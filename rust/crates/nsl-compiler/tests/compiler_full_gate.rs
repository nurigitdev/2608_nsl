mod compiler_gate_support;
#[path = "approval_support/mod.rs"]
mod source_support;
mod type_support;

use nsl_compiler::includes::{IncludeError, IncludeOptions, IncludeResolveError, IncludeResolver};
use nsl_compiler::{CompileError, CompilerOptions, NslCompiler};
use nsl_ir::{NsoCodec, NsoLimits, artifact_sha256};
use nsl_source::{LexerError, ParseError, ParseLimits, SourceFile, SourceSpan};
use source_support::{decode, encode, position, span};
use std::collections::HashMap;

const ORACLE: &str = include_str!("fixtures/compiler-gate-oracle.tsv");
const LIMITS: NsoLimits = NsoLimits {
    max_bytes: 2_000_000,
    max_depth: 256,
    max_nodes: 100_000,
};
struct Memory(Vec<SourceFile>);
impl IncludeResolver for Memory {
    fn resolve(&self, _: &SourceFile, path: &str) -> Result<SourceFile, IncludeResolveError> {
        self.0
            .iter()
            .find(|s| s.logical_path() == path)
            .cloned()
            .ok_or(IncludeResolveError)
    }
}
fn span_data(value: Option<&SourceSpan>) -> String {
    value.map_or_else(|| "none\t~\t~\t~".into(), |s| format!("span\t{}", span(s)))
}
fn parse_data(error: &ParseError) -> Option<String> {
    match error {
        ParseError::Diagnostic(e) => Some(format!(
            "{}\tPARSER\t{}",
            e.kind.code(),
            span_data(Some(&e.span))
        )),
        ParseError::Lexer(LexerError::Diagnostic(e)) => Some(format!(
            "{}\tLEXER\tpoint\t{}\t{}\t{}",
            e.kind.code(),
            e.source_id.as_str(),
            position(e.position),
            position(e.position)
        )),
        ParseError::Lexer(LexerError::Header(e)) => Some(format!(
            "{}\tLEXER\tpoint\t{}\t{}\t{}",
            e.kind.code(),
            e.source_id.as_str(),
            position(e.position),
            position(e.position)
        )),
        _ => None,
    }
}
fn error_data(error: &CompileError) -> (String, bool) {
    let prefix = match error {
        CompileError::Parse(e) => parse_data(e),
        CompileError::Include(IncludeError::Parse { error, .. }) => parse_data(error),
        CompileError::Include(IncludeError::Diagnostic(e)) => Some(format!(
            "{}\tINCLUDE\t{}",
            e.kind.code(),
            span_data(e.span.as_ref())
        )),
        _ => None,
    };
    if let Some(prefix) = prefix {
        return (format!("error\t{prefix}"), false);
    }
    let diagnostic = error
        .diagnostic()
        .unwrap_or_else(|| panic!("expected NSL diagnostic: {error:?}"));
    (
        format!(
            "error\t{}\t{}\t{}\t{}\t{}\t{}",
            diagnostic.code,
            diagnostic.phase,
            span_data(Some(&diagnostic.span)),
            encode(diagnostic.logical_path.as_deref().unwrap()),
            encode(diagnostic.snippet.as_deref().unwrap()),
            encode(&diagnostic.message)
        ),
        true,
    )
}

#[test]
fn full_compiler_gate_matches_success_failure_spans_ir_bytes_and_hashes() {
    let rows = ORACLE
        .lines()
        .filter(|r| !r.starts_with('#'))
        .collect::<Vec<_>>();
    assert_eq!(rows.len() % 2, 0);
    let mut catalogs = HashMap::new();
    let mut counts = [0usize; 3];
    let mut mismatches = Vec::new();
    for pair in rows.as_chunks::<2>().0 {
        let input = pair[0].split('\t').collect::<Vec<_>>();
        let expected = pair[1].split('\t').collect::<Vec<_>>();
        assert_eq!(input[0], "case");
        let source = SourceFile::from_text(&decode(input[2]), &decode(input[3]), None).unwrap();
        let memory = Memory(if input[4] == "~" {
            vec![]
        } else {
            input[4]
                .split(';')
                .map(|s| {
                    let (p, t) = s.split_once(':').unwrap();
                    SourceFile::from_text(&decode(p), &decode(t), None).unwrap()
                })
                .collect()
        });
        let catalog = catalogs
            .entry(input[6])
            .or_insert_with(|| compiler_gate_support::catalog(input[6]));
        let compiler = NslCompiler::new(
            catalog,
            CompilerOptions {
                parse_limits: ParseLimits { max_nesting: 64 },
                include_options: IncludeOptions {
                    max_include_depth: input[7].parse().unwrap(),
                    max_include_files: input[8].parse().unwrap(),
                    max_total_source_bytes: input[9].parse().unwrap(),
                    ..IncludeOptions::default()
                },
                nso_limits: LIMITS,
            },
        );
        let compiler = if input[5] == "1" {
            compiler.with_include_resolver(&memory)
        } else {
            compiler
        };
        let actual = compiler.compile(source.clone());
        assert_eq!(
            actual,
            compiler.compile(source),
            "{} repeatability",
            input[1]
        );
        match (expected[0], actual) {
            ("ok", Ok(result)) => {
                let bytes = (0..expected[1].len())
                    .step_by(2)
                    .map(|i| u8::from_str_radix(&expected[1][i..i + 2], 16).unwrap())
                    .collect::<Vec<_>>();
                assert_eq!(
                    result.skill,
                    NsoCodec::decode(&bytes, LIMITS).unwrap(),
                    "{} typed IR",
                    input[1]
                );
                assert_eq!(result.nso_bytes, bytes, "{} NSO bytes", input[1]);
                assert_eq!(
                    result.semantic_hash.as_str(),
                    expected[2],
                    "{} semantic hash",
                    input[1]
                );
                assert_eq!(
                    result.source_bundle_hash.as_str(),
                    expected[3],
                    "{} bundle hash",
                    input[1]
                );
                assert_eq!(
                    artifact_sha256(&result.nso_bytes).as_str(),
                    format!("sha256:{}", expected[4]),
                    "{} artifact hash",
                    input[1]
                );
                counts[0] += 1;
            }
            ("error", Err(error)) => {
                assert!(!error.to_string().is_empty(), "{} error display", input[1]);
                use std::error::Error;
                let cause = error.source().expect("typed error chain");
                let _ = cause.source();
                let (actual, complete) = error_data(&error);
                let expected = if complete {
                    pair[1].to_owned()
                } else {
                    expected[..7].join("\t")
                };
                if actual != expected {
                    mismatches.push(format!(
                        "{}\nactual: {actual}\nexpected: {expected}",
                        input[1]
                    ));
                }
                counts[1] += 1;
            }
            ("abort", Err(error)) => {
                assert!(error.diagnostic().is_none(), "{}: {error:?}", input[1]);
                let kind = match error {
                    CompileError::Codec(_) => "codec",
                    CompileError::Parse(
                        ParseError::Numeric(_) | ParseError::Lexer(LexerError::Duration(_)),
                    )
                    | CompileError::Bounds(_) => "numeric",
                    _ => panic!("{} unexpected abort: {error:?}", input[1]),
                };
                assert_eq!(kind, expected[1], "{} typed abort", input[1]);
                counts[2] += 1;
            }
            (expected, actual) => panic!("{} expected {expected}; got {actual:?}", input[1]),
        }
    }
    eprintln!(
        "R5 compiler gate: success={} diagnostic={} typed_abort={}",
        counts[0], counts[1], counts[2]
    );
    assert_eq!(counts, [144, 651, 2]);
    assert!(
        mismatches.is_empty(),
        "{} mismatches:\n{}",
        mismatches.len(),
        mismatches.join("\n")
    );
}
