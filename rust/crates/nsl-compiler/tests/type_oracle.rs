mod type_support;

use std::fmt::Write;

use nsl_compiler::symbols::resolve_symbols;
use nsl_compiler::types::{StaticTypeChecker, TypeCheckError, TypedSkill};
use nsl_source::SourceFile;
use nsl_source::ast::{AstSkill, BinaryOperator, ExpressionKind, StatementKind};
use type_support::*;

fn rule_result(result: Result<String, TypeCheckError>) -> String {
    match result {
        Ok(value) => format!("ok\t{value}"),
        Err(TypeCheckError::Diagnostic(error)) => {
            assert_eq!(error.position(), None);
            assert_eq!(error.logical_path, None);
            assert_eq!(error.snippet, None);
            format!("{}\t{}", error.kind.code(), encode(&error.message))
        }
        Err(error) => panic!("{error}"),
    }
}

#[test]
fn frozen_oracle_type_rule_matrix() {
    use BinaryOperator::*;
    let types = types();
    let source = source("", "");
    let skill = parse(&source, 8);
    let checker = StaticTypeChecker::new([]);
    let mut count = 0;
    for row in include_str!("fixtures/type-rules-oracle.tsv")
        .lines()
        .filter(|row| !row.starts_with('#'))
    {
        let columns = row.split('\t').collect::<Vec<_>>();
        if columns[0] == "type" {
            assert_eq!(
                describe(&types[columns[1].parse::<usize>().unwrap()]),
                columns[2]
            );
            continue;
        }
        let (prefix, actual) = match columns[0] {
            "binary" => {
                let operator = [Eq, Ne, Lt, Le, Gt, Ge, Add, Sub, Mul, Div]
                    .into_iter()
                    .find(|operator| operator.as_str() == columns[1])
                    .unwrap();
                let left = &types[columns[2].parse::<usize>().unwrap()];
                let right = &types[columns[3].parse::<usize>().unwrap()];
                (
                    4,
                    checker
                        .binary_result(operator, left, right, &skill.span)
                        .map(|ty| describe(&ty)),
                )
            }
            "bool" => (
                2,
                checker
                    .require_bool(&types[columns[1].parse::<usize>().unwrap()], &skill.span)
                    .map(|()| "-".into()),
            ),
            "list" => (
                2,
                checker
                    .require_list(&types[columns[1].parse::<usize>().unwrap()], &skill.span)
                    .map(|ty| describe(&ty)),
            ),
            "field" => (
                3,
                checker
                    .field_result(
                        &types[columns[1].parse::<usize>().unwrap()],
                        columns[2],
                        &skill.span,
                    )
                    .map(|field| {
                        format!(
                            "{}:{}",
                            describe(&field.type_info),
                            u8::from(field.projected)
                        )
                    }),
            ),
            "call" => {
                let arguments = vec![
                    types[columns[2].parse::<usize>().unwrap()].clone();
                    columns[3].parse().unwrap()
                ];
                (
                    4,
                    checker
                        .builtin_result(columns[1], &arguments, &skill.span)
                        .map(|ty| describe(&ty)),
                )
            }
            other => panic!("unknown rule {other}"),
        };
        assert_eq!(rule_result(actual), columns[prefix..].join("\t"), "{row}");
        count += 1;
    }
    assert_eq!(count, 6925);
}

fn expressions(
    skill: &AstSkill,
    typed: &TypedSkill,
) -> Vec<(usize, usize, String, String, String)> {
    let mut statements = skill.body.clone();
    let mut pending = Vec::new();
    while let Some(id) = statements.pop() {
        match &skill.statement(id).unwrap().kind {
            StatementKind::Let { value, .. } => pending.push(*value),
            StatementKind::Foreach {
                collection, body, ..
            } => {
                pending.push(*collection);
                statements.extend(body);
            }
            StatementKind::Check { condition, .. } => pending.push(*condition),
            StatementKind::Emit { fields } => pending.extend(fields.iter().map(|(_, id)| *id)),
            StatementKind::Approve { .. } => {}
        }
    }
    let mut observations = Vec::new();
    while let Some(id) = pending.pop() {
        let expression = skill.expression(id).unwrap();
        match &expression.kind {
            ExpressionKind::Binary { left, right, .. } => pending.extend([*left, *right]),
            ExpressionKind::Call { arguments, .. } => pending.extend(arguments),
            ExpressionKind::Read { arguments, .. } | ExpressionKind::Write { arguments, .. } => {
                pending.extend(arguments.iter().map(|(_, id)| *id))
            }
            _ => {}
        }
        let annotation = typed.expression(id).unwrap();
        let steps = annotation
            .fields
            .iter()
            .map(|field| {
                format!(
                    "{}:{}",
                    describe(&field.type_info),
                    u8::from(field.projected)
                )
            })
            .collect::<Vec<_>>();
        observations.push((
            expression.span.start().offset(),
            expression.span.end().offset(),
            describe(&annotation.value.type_info),
            annotation.value.classification.to_string(),
            if steps.is_empty() {
                "-".into()
            } else {
                steps.join(";")
            },
        ));
    }
    observations.sort();
    observations
}

#[test]
fn frozen_oracle_typed_skills_and_semantic_diagnostics() {
    let catalog = catalog();
    let rows = include_str!("fixtures/type-skills-oracle.tsv")
        .lines()
        .filter(|row| !row.starts_with('#'))
        .collect::<Vec<_>>();
    let mut offset = 0;
    let mut count = 0;
    while offset < rows.len() {
        let header = rows[offset].split('\t').collect::<Vec<_>>();
        assert_eq!(header[0], "case");
        let source = SourceFile::from_text("types/skill.ns", &decode(header[3]), None).unwrap();
        let skill = parse(&source, 64);
        let resolution = resolve_symbols(&skill, [&source]).unwrap();
        let checker = StaticTypeChecker::new([&source]);
        let result = checker.check(&skill, &resolution, &catalog);
        assert_eq!(
            result,
            checker.check(&skill, &resolution, &catalog),
            "repeatability: {}",
            header[1]
        );
        let mut actual = String::new();
        match result {
            Err(TypeCheckError::Diagnostic(error)) => {
                let position = error.position().unwrap();
                writeln!(
                    actual,
                    "error\t{}\t{},{}\t{}\t{}\t{}",
                    error.kind.code(),
                    position.line(),
                    position.column(),
                    encode(error.logical_path.as_ref().unwrap()),
                    encode(error.snippet.as_ref().unwrap()),
                    encode(&error.message)
                )
                .unwrap();
            }
            Err(error) => panic!("{}: {error}", header[1]),
            Ok(typed) => {
                for binding in resolution.bindings() {
                    let value = typed.symbol(binding.id).unwrap();
                    writeln!(
                        actual,
                        "binding\t{}\t{}\t{}\t{}",
                        binding.id,
                        binding.name,
                        describe(&value.type_info),
                        value.classification
                    )
                    .unwrap();
                }
                for (start, end, ty, classification, fields) in expressions(&skill, &typed) {
                    writeln!(
                        actual,
                        "expression\t{start}\t{end}\t{ty}\t{classification}\t{fields}"
                    )
                    .unwrap();
                }
            }
        }
        let end = ((offset + 1)..rows.len())
            .find(|index| rows[*index].starts_with("case\t"))
            .unwrap_or(rows.len());
        let expected = if end == offset + 1 {
            String::new()
        } else {
            rows[(offset + 1)..end].join("\n") + "\n"
        };
        assert_eq!(actual, expected, "{} NSL {}", header[1], header[2]);
        offset = end;
        count += 1;
    }
    assert_eq!(count, 95);
}
