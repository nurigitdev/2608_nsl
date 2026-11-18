#![allow(dead_code)]

use nsl_core::{TypeKind, TypeRef};
use nsl_ir::{
    ApprovalPolicy, ApprovalStatement, Expression, ExpressionKind, LiteralValue, NamedExpression,
    ResourceLimits, ResultPolicy, Statement, StatementKind, WriteExpression,
};
use nsl_source::ast::{Ast, AstSkill};
use nsl_source::{ParseLimits, ParseMode, Parser, SourceFile};

pub fn encode(value: &str) -> String {
    if value.is_empty() {
        "-".into()
    } else {
        value.bytes().map(|byte| format!("{byte:02x}")).collect()
    }
}
pub fn decode(value: &str) -> String {
    if value == "-" {
        return String::new();
    }
    String::from_utf8(
        (0..value.len())
            .step_by(2)
            .map(|index| u8::from_str_radix(&value[index..index + 2], 16).unwrap())
            .collect(),
    )
    .unwrap()
}
pub fn expression(kind: ExpressionKind) -> Expression {
    Expression {
        node_id: "expr0001".into(),
        type_info: TypeRef::new(TypeKind::Primitive("Int".into())).unwrap(),
        kind,
    }
}
pub fn literal() -> Expression {
    expression(ExpressionKind::Literal(LiteralValue::Int(1.into())))
}
pub fn read(arguments: Vec<NamedExpression>) -> Expression {
    expression(ExpressionKind::Read {
        tool_ref: "tool0001".into(),
        arguments,
        result_policy: ResultPolicy::default(),
    })
}
fn take<T>(stack: &mut Vec<T>, count: usize) -> Vec<T> {
    stack.split_off(stack.len().checked_sub(count).unwrap())
}
fn named(values: Vec<Expression>) -> Vec<NamedExpression> {
    values
        .into_iter()
        .enumerate()
        .map(|(i, value)| NamedExpression {
            name: format!("a{i}"),
            value,
        })
        .collect()
}
pub fn program(text: &str) -> Vec<Statement> {
    let mut expressions = Vec::new();
    let mut statements = Vec::new();
    for line in text.lines() {
        let columns = line.split_whitespace().collect::<Vec<_>>();
        let kind = match columns[0] {
            "literal" => {
                expressions.push(literal());
                continue;
            }
            "symbol" => {
                expressions.push(expression(ExpressionKind::SymbolRef {
                    symbol_id: "s0001".into(),
                }));
                continue;
            }
            "read" | "write" | "call" => {
                let arguments = take(&mut expressions, columns[1].parse().unwrap());
                let kind = match columns[0] {
                    "read" => ExpressionKind::Read {
                        tool_ref: "tool0001".into(),
                        arguments: named(arguments),
                        result_policy: ResultPolicy::default(),
                    },
                    "write" => ExpressionKind::Write(WriteExpression {
                        tool_ref: "tool0001".into(),
                        arguments: named(arguments),
                        approval_ref: "A".into(),
                    }),
                    _ => ExpressionKind::Call {
                        function: "sum".into(),
                        arguments,
                    },
                };
                expressions.push(expression(kind));
                continue;
            }
            "field" | "projection" => {
                let source = Box::new(expressions.pop().unwrap());
                let kind = if columns[0] == "field" {
                    ExpressionKind::Field {
                        source,
                        field: "number".into(),
                    }
                } else {
                    ExpressionKind::Projection {
                        source,
                        field: "number".into(),
                    }
                };
                expressions.push(expression(kind));
                continue;
            }
            "binary" => {
                let right = Box::new(expressions.pop().unwrap());
                let left = Box::new(expressions.pop().unwrap());
                expressions.push(expression(ExpressionKind::Binary {
                    operator: "ADD".into(),
                    left,
                    right,
                }));
                continue;
            }
            "let" => StatementKind::Let {
                target_symbol_id: "s0001".into(),
                value: expressions.pop().unwrap(),
            },
            "check" => StatementKind::Check {
                check_id: "C".into(),
                condition: expressions.pop().unwrap(),
                severity: "WARN".into(),
                on_fail: "CONTINUE".into(),
                message: "note".into(),
                result_symbol_id: "s0001".into(),
            },
            "emit" => StatementKind::Emit {
                fields: named(take(&mut expressions, columns[2].parse().unwrap())),
            },
            "approval" => StatementKind::Approval(ApprovalStatement {
                approval_id: "A".into(),
                message: "go".into(),
                policy: ApprovalPolicy::default(),
            }),
            "foreach" => StatementKind::Foreach {
                iterator_symbol_id: "s0001".into(),
                collection: expressions.pop().unwrap(),
                max_iterations: columns[2].parse().unwrap(),
                body: take(&mut statements, columns[3].parse().unwrap()),
            },
            other => panic!("invalid fixture instruction: {other}"),
        };
        statements.push(Statement {
            node_id: columns[1].into(),
            kind,
        });
    }
    assert!(expressions.is_empty());
    statements
}
pub fn limits(tools: &str, loops: &str, emits: &str) -> ResourceLimits {
    ResourceLimits {
        tool_calls: tools.parse().unwrap(),
        loop_iterations: loops.parse().unwrap(),
        emitted_rows: emits.parse().unwrap(),
        collection_size: 1.into(),
        duration_ms: 1.into(),
    }
}
pub fn parse(source: &SourceFile) -> AstSkill {
    let Ast::RootSkill(skill) = Parser::parse(
        source,
        ParseMode::RootSkill,
        ParseLimits { max_nesting: 64 },
    )
    .unwrap() else {
        panic!("root skill")
    };
    *skill
}
pub fn source() -> SourceFile {
    SourceFile::from_text("bounds/skill.ns", "language NSL \"0.2\";\nskill TEST { version \"1\"; risk READ_ONLY;\nlimits { tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1; }\n}", None).unwrap()
}

// Test teardown must also avoid recursive destruction of deliberately deep IR.
pub fn drop_iteratively(mut statements: Vec<Statement>) {
    let mut expressions = Vec::new();
    while let Some(statement) = statements.pop() {
        match statement.kind {
            StatementKind::Let { value, .. } => expressions.push(value),
            StatementKind::Foreach {
                collection, body, ..
            } => {
                expressions.push(collection);
                statements.extend(body);
            }
            StatementKind::Check { condition, .. } => expressions.push(condition),
            StatementKind::Emit { fields } => {
                expressions.extend(fields.into_iter().map(|field| field.value))
            }
            StatementKind::Approval(_) => {}
        }
    }
    while let Some(expression) = expressions.pop() {
        match expression.kind {
            ExpressionKind::Read { arguments, .. } => {
                expressions.extend(arguments.into_iter().map(|argument| argument.value))
            }
            ExpressionKind::Write(write) => {
                expressions.extend(write.arguments.into_iter().map(|argument| argument.value))
            }
            ExpressionKind::Field { source, .. } | ExpressionKind::Projection { source, .. } => {
                expressions.push(*source)
            }
            ExpressionKind::Call { arguments, .. } => expressions.extend(arguments),
            ExpressionKind::Binary { left, right, .. } => {
                expressions.push(*left);
                expressions.push(*right);
            }
            _ => {}
        }
    }
}
