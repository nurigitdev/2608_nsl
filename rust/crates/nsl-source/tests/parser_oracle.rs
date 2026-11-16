use std::{
    error::Error,
    fmt::{Arguments, Write},
};

use nsl_core::TypeKind;
use nsl_source::ast::*;
use nsl_source::{
    LexerError, ParseError, ParseLimits, ParseMode, Parser, SourceFile, SourceLanguageVersion,
    SourcePosition, SourceSpan,
};
use sha2::{Digest, Sha256};

const ORACLE: &str = include_str!("fixtures/parser-oracle.tsv");

struct Observation<'a> {
    text: String,
    source_id: &'a str,
}

impl Observation<'_> {
    fn line(&mut self, arguments: Arguments<'_>) {
        self.text.write_fmt(arguments).unwrap();
        self.text.push('\n');
    }

    fn value(&mut self, tag: &str, text: &str) {
        let payload = if text.len() > 256 {
            let digest = Sha256::digest(text.as_bytes());
            format!("sha256:{}", hex(digest.as_ref()))
        } else if text.is_empty() {
            "-".into()
        } else {
            hex(text.as_bytes())
        };
        self.line(format_args!("{tag}\t{payload}"));
    }

    fn string(&mut self, text: &str) {
        self.value("str", text);
    }

    fn span(&mut self, span: &SourceSpan) {
        assert_eq!(span.source_id().as_str(), self.source_id);
        self.line(format_args!(
            "span\t{}\t{}",
            position(span.start()),
            position(span.end())
        ));
    }

    fn node(&mut self, name: &str, span: &SourceSpan) {
        self.line(format_args!("node\t{name}"));
        self.span(span);
    }

    fn seq<T>(&mut self, values: &[T], mut visit: impl FnMut(&mut Self, &T)) {
        self.line(format_args!("seq\t{}", values.len()));
        for value in values {
            visit(self, value);
        }
    }

    fn ast(&mut self, ast: &Ast) {
        match ast {
            Ast::RootSkill(skill) => {
                self.node("AstSkill", &skill.span);
                for value in [
                    skill.language_version.as_str(),
                    &skill.skill_id,
                    &skill.skill_version,
                    &skill.risk,
                ] {
                    self.string(value);
                }
                self.seq(&skill.includes, Self::include);
                self.seq(&skill.requires, Self::tool);
                if let Some(limits) = &skill.limits {
                    self.limits(limits);
                } else {
                    self.line(format_args!("none"));
                }
                self.seq(&skill.inputs, Self::field_spec);
                self.seq(&skill.contexts, Self::field_spec);
                self.seq(&skill.outputs, Self::field_spec);
                self.seq(&skill.body, |out, &id| out.statement(skill, id));
                self.span(&skill.language_version_span);
                self.span(&skill.risk_span);
            }
            Ast::IncludeFragment(fragment) => {
                self.node("AstIncludeFragment", &fragment.span);
                self.seq(&fragment.includes, Self::include);
                self.seq(&fragment.requires, Self::tool);
                self.seq(&fragment.contexts, Self::field_spec);
                self.seq(&fragment.limits, Self::limits);
            }
        }
    }

    fn include(&mut self, include: &AstIncludeDeclaration) {
        self.node("AstIncludeDeclaration", &include.span);
        self.string(&include.path);
    }

    fn tool(&mut self, tool: &AstRequiredTool) {
        self.node("AstRequiredTool", &tool.span);
        self.string(&tool.tool_id);
        self.string(&tool.version);
    }

    fn limits(&mut self, limits: &AstLimits) {
        self.node("AstLimits", &limits.span);
        for value in [
            &limits.tool_calls,
            &limits.loop_iterations,
            &limits.emitted_rows,
            &limits.collection_size,
            &limits.duration_ms,
        ] {
            self.value("int", value.as_str());
        }
    }

    fn type_info(&mut self, ty: &AstType) {
        let (kind, name, values) = match ty.item.kind() {
            TypeKind::Primitive(name) => ("primitive", name.as_str(), &[][..]),
            TypeKind::Domain(name) => ("domain", name.as_str(), &[][..]),
            TypeKind::Money(currency) => ("money", currency.as_str(), &[][..]),
            TypeKind::Enum { name, values } => ("enum", name.as_str(), values.as_slice()),
            other => panic!("unexpected source type: {other:?}"),
        };
        self.line(format_args!(
            "type\t{}\t{kind}\t{name}{}",
            ty.list_depth,
            values
                .iter()
                .map(|value| format!("\t{value}"))
                .collect::<String>()
        ));
    }

    fn field_spec(&mut self, field: &AstFieldSpec) {
        self.node("AstFieldSpec", &field.span);
        self.string(&field.name);
        self.type_info(&field.type_info);
        self.string(field.classification.as_str());
        self.seq(&field.path, |out, part| out.string(part));
    }

    fn named_expressions(&mut self, skill: &AstSkill, fields: &[(String, ExpressionId)]) {
        self.seq(fields, |out, (name, id)| {
            out.line(format_args!("seq\t2"));
            out.string(name);
            out.expression(skill, *id);
        });
    }

    fn statement(&mut self, skill: &AstSkill, id: StatementId) {
        let AstStatement { kind, span } = skill.statement(id).unwrap();
        match kind {
            StatementKind::Let { name, value } => {
                self.node("AstLet", span);
                self.string(name);
                self.expression(skill, *value);
            }
            StatementKind::Foreach {
                iterator,
                collection,
                max_iterations,
                body,
            } => {
                self.node("AstForeach", span);
                self.string(iterator);
                self.expression(skill, *collection);
                self.value("int", max_iterations.as_str());
                self.seq(body, |out, &id| out.statement(skill, id));
            }
            StatementKind::Check {
                check_id,
                condition,
                severity,
                on_fail,
                message,
            } => {
                self.node("AstCheck", span);
                self.string(check_id);
                self.expression(skill, *condition);
                for value in [severity, on_fail, message] {
                    self.string(value);
                }
            }
            StatementKind::Emit { fields } => {
                self.node("AstEmit", span);
                self.named_expressions(skill, fields);
            }
            StatementKind::Approve {
                approval_id,
                message,
                approval_id_span,
            } => {
                self.node("AstApprove", span);
                self.string(approval_id);
                self.string(message);
                self.span(approval_id_span);
            }
        }
    }

    fn expression(&mut self, skill: &AstSkill, id: ExpressionId) {
        let AstExpression { kind, span } = skill.expression(id).unwrap();
        match kind {
            ExpressionKind::Literal(value) => {
                self.node("AstLiteral", span);
                let ty = match value {
                    AstLiteral::Int(value) => {
                        self.value("int", value.as_str());
                        "Int"
                    }
                    AstLiteral::Decimal(value) => {
                        self.value("decimal", &value.to_fixed_point(8192).unwrap());
                        "Decimal"
                    }
                    AstLiteral::String(value) => {
                        self.string(value);
                        "String"
                    }
                    AstLiteral::Bool(value) => {
                        self.line(format_args!("bool\t{value}"));
                        "Bool"
                    }
                };
                self.line(format_args!("type\t0\tprimitive\t{ty}"));
            }
            ExpressionKind::Path(parts) => {
                self.node("AstPath", span);
                self.seq(parts, |out, value| out.string(value));
            }
            ExpressionKind::Call { name, arguments } => {
                self.node("AstCall", span);
                self.string(name);
                self.seq(arguments, |out, &id| out.expression(skill, id));
            }
            ExpressionKind::Read { tool_id, arguments } => {
                self.node("AstRead", span);
                self.string(tool_id);
                self.named_expressions(skill, arguments);
            }
            ExpressionKind::Write {
                tool_id,
                arguments,
                approval_id,
                approval_span,
            } => {
                self.node("AstWrite", span);
                self.string(tool_id);
                self.named_expressions(skill, arguments);
                self.string(approval_id);
                self.span(approval_span);
            }
            ExpressionKind::Binary {
                operator,
                left,
                right,
            } => {
                self.node("AstBinary", span);
                self.string(operator.as_str());
                self.expression(skill, *left);
                self.expression(skill, *right);
            }
        }
    }

    fn failure(&mut self, error: &ParseError, source_id: &str) {
        let (code, actual_position, id) = match error {
            ParseError::Lexer(LexerError::Header(error)) => {
                (error.kind.code(), error.position, &error.source_id)
            }
            ParseError::Lexer(LexerError::Diagnostic(error)) => {
                (error.kind.code(), error.position, &error.source_id)
            }
            ParseError::Diagnostic(error) => (
                error.kind.code(),
                error.span.start(),
                error.span.source_id(),
            ),
            ParseError::Numeric(error) => {
                assert!(!error.to_string().contains("NSL-E"));
                assert_eq!(error.span.source_id().as_str(), source_id);
                self.line(format_args!(
                    "numeric\t{:?}\t{}\t{}\t{}",
                    error.kind,
                    error.span.source_id().as_str(),
                    position(error.span.start()),
                    position(error.span.end())
                ));
                return;
            }
            ParseError::Nesting(error) => {
                assert!(!error.to_string().contains("NSL-E"));
                assert_eq!(error.span.source_id().as_str(), source_id);
                self.line(format_args!("recursion"));
                return;
            }
            ParseError::Lexer(LexerError::Duration(error)) => {
                panic!("unexpected duration error: {error}")
            }
        };
        assert_eq!(id.as_str(), source_id);
        assert!(error.to_string().contains(code));
        self.line(format_args!(
            "diagnostic\t{code}\t{}",
            position(actual_position)
        ));
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn unhex(text: &str) -> String {
    if text == "-" {
        return String::new();
    }
    assert!(text.len().is_multiple_of(2));
    let bytes = (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
        .collect();
    String::from_utf8(bytes).unwrap()
}

fn position(position: SourcePosition) -> String {
    format!(
        "{},{},{}",
        position.offset(),
        position.line(),
        position.column()
    )
}

#[test]
fn semantic_ast_spans_and_failures_match_frozen_oracle() {
    let data = ORACLE
        .strip_prefix("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a\ncase\t")
        .unwrap();
    for case in data.split("\ncase\t") {
        let (metadata, expected) = case.split_once('\n').unwrap();
        let fields: Vec<_> = metadata.split('\t').collect();
        let [
            label,
            mode,
            version,
            prefix,
            unit,
            count,
            tail,
            cap,
            source_id,
        ] = fields.as_slice()
        else {
            panic!("invalid fixture metadata: {metadata}");
        };
        let language_version = match *version {
            "0.1" => SourceLanguageVersion::V0_1,
            "0.2" => SourceLanguageVersion::V0_2,
            _ => panic!("invalid fixture version: {version}"),
        };
        let mode = match *mode {
            "ROOT_SKILL" => ParseMode::RootSkill,
            "INCLUDE_FRAGMENT" => ParseMode::IncludeFragment { language_version },
            _ => panic!("invalid fixture mode: {mode}"),
        };
        let text = unhex(prefix) + &unhex(unit).repeat(count.parse().unwrap()) + &unhex(tail);
        let source = SourceFile::from_text("parser/fixture.ns", &text, None).unwrap();
        assert_eq!(source.source_id().as_str(), *source_id);
        let mut actual = Observation {
            text: String::new(),
            source_id,
        };
        match Parser::parse(
            &source,
            mode,
            ParseLimits {
                max_nesting: cap.parse().unwrap(),
            },
        ) {
            Ok(ast) => actual.ast(&ast),
            Err(error) => {
                assert_eq!(error.source().unwrap().to_string(), error.to_string());
                actual.failure(&error, source_id);
            }
        }
        let actual: Vec<_> = actual.text.lines().collect();
        let expected: Vec<_> = expected.lines().collect();
        for (index, (actual, expected)) in actual.iter().zip(&expected).enumerate() {
            assert_eq!(
                actual,
                expected,
                "{label} ({version}), observation {}",
                index + 1
            );
        }
        assert_eq!(actual.len(), expected.len(), "{label} ({version})");
    }
}
