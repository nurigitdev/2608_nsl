#[path = "approval_support/mod.rs"]
mod source_support;
mod type_support;

use nsl_compiler::includes::{IncludeOptions, IncludeResolveError, IncludeResolver};
use nsl_compiler::{CompilationResult, CompilerOptions, NslCompiler};
use nsl_ir::{NsoCodec, NsoLimits};
use nsl_source::{ParseLimits, SourceFile};
use source_support::{decode, encode};
use std::{error::Error, fmt::Write};

struct Memory(Vec<SourceFile>);
impl IncludeResolver for Memory {
    fn resolve(&self, _: &SourceFile, path: &str) -> Result<SourceFile, IncludeResolveError> {
        self.0
            .iter()
            .find(|source| source.logical_path() == path)
            .cloned()
            .ok_or(IncludeResolveError)
    }
}
fn limits() -> NsoLimits {
    NsoLimits {
        max_bytes: 2_000_000,
        max_depth: 256,
        max_nodes: 100_000,
    }
}
fn render(result: &CompilationResult) -> String {
    let mut out = format!(
        "ok\t{}\t{}\t{}\n",
        result.semantic_hash.as_str(),
        result.source_bundle_hash.as_str(),
        result
            .nso_bytes
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    );
    let s = &result.write_safety;
    writeln!(
        out,
        "safety\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
        s.write_node_count,
        s.approval_node_count,
        s.approval_write_mapping_count,
        s.unused_approval_count,
        s.max_write_calls,
        s.writes_inside_loop,
        s.all_writes_approved_exactly_once,
        s.all_write_operations_supported,
        s.all_write_tools_atomic,
        s.all_write_tools_idempotent,
        s.all_write_risk_metadata_present,
        s.requires_verified_package_for_write
    )
    .unwrap();
    for item in &result.source_manifest {
        writeln!(
            out,
            "manifest\t{}\t{}\t{}\t{}\t{}",
            item.source_id.as_str(),
            encode(&item.logical_path),
            item.content_hash.as_str(),
            item.size_bytes.as_str(),
            u8::from(item.is_root)
        )
        .unwrap();
    }
    for edge in &result.include_edges {
        writeln!(
            out,
            "edge\t{}\t{}\t{}\t{}",
            edge.from_source.as_str(),
            edge.to_source.as_str(),
            edge.include_span.start().offset(),
            edge.include_span.end().offset()
        )
        .unwrap();
    }
    out
}

#[test]
fn frozen_oracle_full_compile_bytes_hashes_metadata_and_mixed_diagnostics() {
    let rows = include_str!("fixtures/compiler-oracle.tsv")
        .lines()
        .filter(|row| !row.starts_with('#'))
        .collect::<Vec<_>>();
    let catalog = type_support::catalog();
    let mut offset = 0;
    let mut count = 0;
    while offset < rows.len() {
        let header = rows[offset].split('\t').collect::<Vec<_>>();
        assert_eq!(header[0], "case");
        let source = SourceFile::from_text("compiler/root.ns", &decode(header[3]), None).unwrap();
        let resolver = Memory(if header[4] == "~" {
            vec![]
        } else {
            vec![SourceFile::from_text("compiler/part.ns", &decode(header[4]), None).unwrap()]
        });
        let compiler = NslCompiler::new(
            &catalog,
            CompilerOptions {
                parse_limits: ParseLimits { max_nesting: 64 },
                include_options: IncludeOptions::default(),
                nso_limits: limits(),
            },
        );
        let compiler = if header[5] == "resolve" {
            compiler.with_include_resolver(&resolver)
        } else {
            compiler
        };
        let result = compiler.compile(source.clone());
        assert_eq!(
            result,
            compiler.compile(source),
            "repeatability: {}",
            header[1]
        );
        let actual = match result {
            Ok(result) => {
                assert_eq!(
                    NsoCodec::decode(&result.nso_bytes, limits()).unwrap(),
                    result.skill
                );
                render(&result)
            }
            Err(error) => {
                assert!(error.source().is_some());
                assert!(!error.to_string().is_empty());
                assert!(error.output_limit().is_none());
                let diagnostic = error
                    .diagnostic()
                    .unwrap_or_else(|| panic!("{}: {error:?}", header[1]));
                let point = diagnostic.span.start();
                format!(
                    "error\t{}\t{}\t{},{}\t{}\t{}\t{}\n",
                    diagnostic.code,
                    diagnostic.phase,
                    point.line(),
                    point.column(),
                    encode(diagnostic.logical_path.as_ref().unwrap()),
                    encode(diagnostic.snippet.as_ref().unwrap()),
                    encode(&diagnostic.message)
                )
            }
        };
        let end = ((offset + 1)..rows.len())
            .find(|index| rows[*index].starts_with("case\t"))
            .unwrap_or(rows.len());
        let expected = rows[offset + 1..end].join("\n") + "\n";
        assert_eq!(actual, expected, "{} NSL {}", header[1], header[2]);
        offset = end;
        count += 1;
    }
    assert_eq!(count, 83);
}
