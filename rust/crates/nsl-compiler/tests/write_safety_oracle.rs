#[path = "approval_support/mod.rs"]
mod source_support;
mod write_safety_support;

use std::collections::HashMap;

use nsl_compiler::approvals::{ApprovalAnalysisError, ApprovalBindingAnalyzer};
use nsl_compiler::write_safety::{WriteSafetyAnalysis, WriteSafetyAnalyzer, WriteSafetyError};
use nsl_source::{SourceFile, SourcePosition, SourceSpan};
use source_support::*;
use write_safety_support::build;

fn observation(value: &WriteSafetyAnalysis) -> String {
    format!(
        "ok\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
        value.write_node_count,
        value.approval_node_count,
        value.approval_write_mapping_count,
        value.unused_approval_count,
        value.max_write_calls,
        value.writes_inside_loop,
        value.all_writes_approved_exactly_once,
        value.all_write_operations_supported,
        value.all_write_tools_atomic,
        value.all_write_tools_idempotent,
        value.all_write_risk_metadata_present,
        value.requires_verified_package_for_write
    )
}

fn error(
    stage: &str,
    code: &str,
    message: &str,
    range: &SourceSpan,
    position: Option<SourcePosition>,
    path: &Option<String>,
    snippet: &Option<String>,
) -> String {
    let position = position.map_or_else(
        || "~".into(),
        |point| format!("{},{}", point.line(), point.column()),
    );
    let path = path
        .as_ref()
        .map_or_else(|| "~".into(), |path| encode(path));
    let snippet = snippet
        .as_ref()
        .map_or_else(|| "~".into(), |snippet| encode(snippet));
    format!(
        "error\t{stage}\t{code}\t{position}\t{path}\t{snippet}\t{}\t{}",
        encode(message),
        span(range)
    )
}

#[test]
fn frozen_oracle_strict_write_reports_and_error_order() {
    let rows = include_str!("fixtures/write-safety-source-oracle.tsv")
        .lines()
        .filter(|row| !row.starts_with('#'))
        .collect::<Vec<_>>();
    let (cases, remainder) = rows.as_chunks::<2>();
    assert!(remainder.is_empty());
    for pair in cases {
        let columns = pair[0].split('\t').collect::<Vec<_>>();
        let source =
            SourceFile::from_text("write-safety/skill.ns", &decode(columns[8]), None).unwrap();
        let skill = parse(&source, 64);
        let sources = if columns[6] == "present" {
            vec![&source]
        } else {
            vec![]
        };
        let actual = match ApprovalBindingAnalyzer::new(sources.clone()).analyze(&skill) {
            Err(ApprovalAnalysisError::Diagnostic(value)) => error(
                "approval",
                value.kind.code(),
                &value.message,
                &value.span,
                value.position(),
                &value.logical_path,
                &value.snippet,
            ),
            Err(error) => panic!("{error}"),
            Ok(approvals) => {
                let contract = build(columns[3], columns[4], columns[5], None).unwrap();
                let mut contracts = HashMap::new();
                if columns[7] == "declared" {
                    contracts.insert("TEST.WRITE", &contract);
                }
                let analyzer = WriteSafetyAnalyzer::new(sources);
                let result = analyzer.analyze(&approvals, &contracts);
                assert_eq!(result, analyzer.analyze(&approvals, &contracts));
                match result {
                    Ok(value) => observation(&value),
                    Err(WriteSafetyError::Diagnostic(value)) => error(
                        "safety",
                        value.kind.code(),
                        &value.message,
                        &value.span,
                        value.position(),
                        &value.logical_path,
                        &value.snippet,
                    ),
                    Err(error) => panic!("{error}"),
                }
            }
        };
        assert_eq!(actual, pair[1], "{}", columns[1]);
    }
    assert_eq!(cases.len(), 50);
}

#[test]
fn frozen_oracle_rejects_invalid_contracts_at_admission() {
    let mut count = 0;
    for row in include_str!("fixtures/write-safety-admission-oracle.tsv")
        .lines()
        .filter(|row| !row.starts_with('#'))
    {
        let columns = row.split('\t').collect::<Vec<_>>();
        let result = match columns[0] {
            "operation" => build("WRITE", columns[1], "R3", None),
            "risk" => build("WRITE", "CREATE_DRAFT", columns[1], None),
            name => build("WRITE", "CREATE_DRAFT", "R3", Some((name, columns[1]))),
        };
        match result {
            Ok(_) => assert_eq!(columns[2], "ok"),
            Err(error) => {
                assert_eq!(columns[2], "ValueError");
                assert_eq!(error.to_string(), decode(columns[3]), "{row}");
            }
        }
        count += 1;
    }
    assert_eq!(count, 17);
}
