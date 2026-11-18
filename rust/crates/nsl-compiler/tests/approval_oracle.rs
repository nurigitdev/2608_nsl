mod approval_support;

use std::fmt::Write;

use approval_support::*;
use nsl_compiler::approvals::{ApprovalAnalysisError, ApprovalBindingAnalyzer};
use nsl_source::ast::{ExpressionKind, StatementKind};
use nsl_source::{SourceFile, SourceLanguageVersion};

#[test]
fn frozen_oracle_approval_bindings_and_diagnostic_priority() {
    let rows = include_str!("fixtures/approval-oracle.tsv")
        .lines()
        .filter(|row| !row.starts_with('#'))
        .collect::<Vec<_>>();
    let mut offset = 0;
    let mut count = 0;
    while offset < rows.len() {
        let header = rows[offset].split('\t').collect::<Vec<_>>();
        assert_eq!(header[0], "case");
        let source = SourceFile::from_text(&decode(header[4]), &decode(header[5]), None).unwrap();
        let mut skill = parse(&source, 64);
        if header[1] == "force_v01" {
            skill.language_version = SourceLanguageVersion::V0_1;
        }
        let analyzer = ApprovalBindingAnalyzer::new(if header[6] == "missing" {
            vec![]
        } else {
            vec![&source]
        });
        let result = analyzer.analyze(&skill);
        assert_eq!(
            result,
            analyzer.analyze(&skill),
            "determinism: {}",
            header[3]
        );
        let mut actual = String::new();
        match result {
            Ok(result) => {
                for declaration in result.approvals() {
                    let symbol = &declaration.symbol;
                    assert!(
                        matches!(&skill.statement(symbol.statement).unwrap().kind, StatementKind::Approve { approval_id, approval_id_span, .. } if approval_id == &symbol.name && approval_id_span == &symbol.span)
                    );
                    writeln!(
                        actual,
                        "approval\t{}\t{}\t{}\t{}",
                        symbol.id,
                        symbol.name,
                        declaration.declaration_order,
                        span(&symbol.span)
                    )
                    .unwrap();
                }
                for binding in result.bindings() {
                    let write = skill.expression(binding.write).unwrap();
                    let ExpressionKind::Write {
                        approval_id,
                        approval_span,
                        ..
                    } = &write.kind
                    else {
                        panic!("WRITE")
                    };
                    let declaration = result
                        .approvals()
                        .iter()
                        .find(|item| item.symbol.id == binding.approval)
                        .unwrap();
                    assert_eq!(&declaration.symbol.name, approval_id);
                    assert!(declaration.declaration_order < binding.write_order);
                    writeln!(
                        actual,
                        "binding\t{}\t{}\t{}\t{}\t{}",
                        binding.approval,
                        binding.write_order,
                        u8::from(binding.inside_foreach),
                        span(&write.span),
                        span(approval_span)
                    )
                    .unwrap();
                }
            }
            Err(ApprovalAnalysisError::Diagnostic(error)) => {
                let coords = error.position().map_or_else(
                    || "~".into(),
                    |position| format!("{},{}", position.line(), position.column()),
                );
                let path = error
                    .logical_path
                    .as_ref()
                    .map_or_else(|| "~".into(), |path| encode(path));
                let snippet = error
                    .snippet
                    .as_ref()
                    .map_or_else(|| "~".into(), |snippet| encode(snippet));
                writeln!(
                    actual,
                    "error\t{}\t{coords}\t{path}\t{snippet}\t{}\t{}",
                    error.kind.code(),
                    encode(&error.message),
                    span(&error.span)
                )
                .unwrap();
            }
            Err(error) => panic!("{}: {error}", header[3]),
        }
        let end = ((offset + 1)..rows.len())
            .find(|index| rows[*index].starts_with("case\t"))
            .unwrap_or(rows.len());
        let expected = if end == offset + 1 {
            String::new()
        } else {
            rows[(offset + 1)..end].join("\n") + "\n"
        };
        assert_eq!(actual, expected, "{} NSL {}", header[3], header[2]);
        offset = end;
        count += 1;
    }
    assert_eq!(count, 385);
}
