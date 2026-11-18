mod bound_support;

use bound_support::*;
use nsl_compiler::bounds::{BoundError, StaticBoundAnalyzer};
use nsl_compiler::includes::{
    IncludeOptions, IncludeResolveError, IncludeResolver, SourceBundleBuilder,
};
use nsl_ir::{ResourceLimits, StaticAnalysis};
use nsl_source::{ParseLimits, SourceFile};

fn counts(value: &StaticAnalysis) -> String {
    format!(
        "{}\t{}\t{}\t{}",
        value.max_tool_calls.as_str(),
        value.max_loop_iterations.as_str(),
        value.max_emit_records.as_str(),
        value.bounded
    )
}

#[test]
fn frozen_oracle_resource_counts_for_every_ir_shape() {
    let rows = include_str!("fixtures/bounds-model-oracle.tsv")
        .lines()
        .filter(|row| !row.starts_with('#'))
        .collect::<Vec<_>>();
    let analyzer = StaticBoundAnalyzer::new([]);
    let (pairs, remainder) = rows.as_chunks::<2>();
    assert!(remainder.is_empty());
    for pair in pairs {
        let header = pair[0].split('\t').collect::<Vec<_>>();
        assert_eq!(header[0], "case");
        let body = program(&decode(header[2]));
        let result = analyzer.analyze(&body);
        assert_eq!(result, analyzer.analyze(&body));
        let actual = match result {
            Ok(value) => format!("analysis\t{}", counts(&value)),
            Err(error) => format!("unbounded\t{}", encode(&error.to_string())),
        };
        assert_eq!(actual, pair[1], "{}", header[1]);
    }
    assert_eq!(rows.len(), 86 * 2);
}

struct Fragment(SourceFile);
impl IncludeResolver for Fragment {
    fn resolve(&self, _: &SourceFile, path: &str) -> Result<SourceFile, IncludeResolveError> {
        assert_eq!(path, "bounds/limits.ns");
        Ok(self.0.clone())
    }
}

#[test]
fn frozen_oracle_limit_validation_and_conversion_aborts() {
    let rows = include_str!("fixtures/bounds-source-oracle.tsv")
        .lines()
        .filter(|row| !row.starts_with('#'))
        .collect::<Vec<_>>();
    let (pairs, remainder) = rows.as_chunks::<2>();
    assert!(remainder.is_empty());
    for pair in pairs {
        let header = pair[0].split('\t').collect::<Vec<_>>();
        assert_eq!(header[0], "case");
        let root = SourceFile::from_text("bounds/skill.ns", &decode(header[3]), None).unwrap();
        let (skill, sources) = if header[4] == "~" {
            (parse(&root), vec![root])
        } else {
            let resolver = Fragment(
                SourceFile::from_text("bounds/limits.ns", &decode(header[4]), None).unwrap(),
            );
            let bundle = SourceBundleBuilder::new(
                &resolver,
                IncludeOptions::default(),
                ParseLimits { max_nesting: 64 },
            )
            .build(root)
            .unwrap();
            (
                bundle.compose().unwrap(),
                bundle.sources().cloned().collect(),
            )
        };
        let declaration = skill.limits.as_ref().unwrap();
        let limits = ResourceLimits {
            tool_calls: declaration.tool_calls.clone(),
            loop_iterations: declaration.loop_iterations.clone(),
            emitted_rows: declaration.emitted_rows.clone(),
            collection_size: declaration.collection_size.clone(),
            duration_ms: declaration.duration_ms.clone(),
        };
        let body = program(&decode(header[5]));
        let analyzer = StaticBoundAnalyzer::new(&sources);
        let result = analyzer.check(&body, &limits, &skill.span, &declaration.span);
        assert_eq!(
            result,
            analyzer.check(&body, &limits, &skill.span, &declaration.span)
        );
        let actual = match result {
            Ok(value) => format!("ok\t{}", counts(&value)),
            Err(BoundError::Diagnostic(error)) => {
                let position = error.position().unwrap();
                let span = if error.kind.code() == "NSL-E6104" {
                    &skill.span
                } else {
                    &declaration.span
                };
                assert_eq!(&error.span, span);
                format!(
                    "error\t{}\t{},{}\t{}\t{}\t{}",
                    error.kind.code(),
                    position.line(),
                    position.column(),
                    encode(error.logical_path.as_ref().unwrap()),
                    encode(error.snippet.as_ref().unwrap()),
                    encode(&error.message)
                )
            }
            Err(BoundError::IntegerConversion(error)) => {
                assert_eq!(error.span, declaration.span);
                assert!(pair[1].starts_with("abort\tValueError\t"));
                assert!(decode(pair[1].split('\t').nth(2).unwrap()).contains("4300 digits"));
                assert_eq!(
                    error.to_string(),
                    "static bound diagnostic integer exceeds the 4300-digit conversion limit"
                );
                pair[1].into()
            }
        };
        assert_eq!(actual, pair[1], "{} NSL {}", header[1], header[2]);
    }
    assert_eq!(rows.len(), 61 * 2);
}
