use super::CompileError;
use crate::symbols::{SymbolDeclaration, SymbolResolution};
use crate::types::TypedSkill;
use nsl_contract::ToolContract;
use nsl_ir::{
    ApprovalPolicy, ApprovalStatement, Expression, ExpressionKind, LiteralValue, NamedExpression,
    ResultPolicy, Statement, StatementKind, WriteExpression,
};
use nsl_source::ast::{
    AstLiteral, AstSkill, ExpressionId, ExpressionKind as E, StatementId, StatementKind as S,
};
use std::collections::HashMap;

enum Task {
    Statement(StatementId),
    FinishStatement(StatementId),
    Expression(ExpressionId),
    FinishExpression(ExpressionId),
}

pub(super) fn lower(
    skill: &AstSkill,
    symbols: &SymbolResolution,
    types: &TypedSkill,
    contracts: &HashMap<&str, &ToolContract>,
) -> Result<Vec<Statement>, CompileError> {
    let mut pending = skill
        .body
        .iter()
        .rev()
        .copied()
        .map(Task::Statement)
        .collect::<Vec<_>>();
    let mut expressions = HashMap::<ExpressionId, Expression>::new();
    let mut statements = HashMap::<StatementId, Statement>::new();
    let mut counters = HashMap::<&str, usize>::new();
    let mut next = |kind| {
        let counter = counters.entry(kind).or_default();
        *counter += 1;
        format!("{kind}{counter:04}")
    };
    let declarations = symbols
        .bindings()
        .iter()
        .map(|binding| (binding.declaration, binding.id.to_string()))
        .collect::<HashMap<_, _>>();
    let references = symbols
        .references()
        .iter()
        .map(|reference| (reference.expression, reference.symbol))
        .collect::<HashMap<_, _>>();
    let tools = skill
        .requires
        .iter()
        .enumerate()
        .map(|(i, tool)| (tool.tool_id.as_str(), format!("tool{:04}", i + 1)))
        .collect::<HashMap<_, _>>();
    while let Some(task) = pending.pop() {
        match task {
            Task::Statement(id) => {
                pending.push(Task::FinishStatement(id));
                match &skill
                    .statement(id)
                    .ok_or(CompileError::InvalidModel("missing AST statement"))?
                    .kind
                {
                    S::Let { value, .. } => pending.push(Task::Expression(*value)),
                    S::Foreach {
                        collection, body, ..
                    } => {
                        pending.extend(body.iter().rev().copied().map(Task::Statement));
                        pending.push(Task::Expression(*collection));
                    }
                    S::Check { condition, .. } => pending.push(Task::Expression(*condition)),
                    S::Emit { fields } => {
                        pending.extend(fields.iter().rev().map(|(_, id)| Task::Expression(*id)))
                    }
                    S::Approve { .. } => {}
                }
            }
            Task::FinishStatement(id) => {
                let (tag, kind) = match &skill.statement(id).expect("AST statement").kind {
                    S::Let { value, .. } => (
                        "stmt",
                        StatementKind::Let {
                            target_symbol_id: declarations[&SymbolDeclaration::Let(id)].clone(),
                            value: expressions.remove(value).expect("lowered expression"),
                        },
                    ),
                    S::Foreach {
                        collection,
                        max_iterations,
                        body,
                        ..
                    } => (
                        "stmt",
                        StatementKind::Foreach {
                            iterator_symbol_id: declarations[&SymbolDeclaration::Iterator(id)]
                                .clone(),
                            collection: expressions.remove(collection).expect("lowered collection"),
                            max_iterations: max_iterations.clone(),
                            body: body
                                .iter()
                                .map(|id| statements.remove(id).expect("lowered statement"))
                                .collect(),
                        },
                    ),
                    S::Check {
                        check_id,
                        condition,
                        severity,
                        on_fail,
                        message,
                    } => (
                        "check",
                        StatementKind::Check {
                            check_id: check_id.clone(),
                            condition: expressions.remove(condition).expect("lowered condition"),
                            severity: severity.clone(),
                            on_fail: on_fail.clone(),
                            message: message.clone(),
                            result_symbol_id: declarations[&SymbolDeclaration::Check(id)].clone(),
                        },
                    ),
                    S::Emit { fields } => (
                        "emit",
                        StatementKind::Emit {
                            fields: fields
                                .iter()
                                .map(|(name, id)| NamedExpression {
                                    name: name.clone(),
                                    value: expressions.remove(id).expect("lowered field"),
                                })
                                .collect(),
                        },
                    ),
                    S::Approve {
                        approval_id,
                        message,
                        ..
                    } => (
                        "approval",
                        StatementKind::Approval(ApprovalStatement {
                            approval_id: approval_id.clone(),
                            message: message.clone(),
                            policy: ApprovalPolicy::default(),
                        }),
                    ),
                };
                statements.insert(
                    id,
                    Statement {
                        node_id: next(tag),
                        kind,
                    },
                );
            }
            Task::Expression(id) => {
                pending.push(Task::FinishExpression(id));
                match &skill
                    .expression(id)
                    .ok_or(CompileError::InvalidModel("missing AST expression"))?
                    .kind
                {
                    E::Binary { left, right, .. } => {
                        pending.push(Task::Expression(*right));
                        pending.push(Task::Expression(*left));
                    }
                    E::Call { arguments, .. } => {
                        pending.extend(arguments.iter().rev().copied().map(Task::Expression))
                    }
                    E::Read { arguments, .. } | E::Write { arguments, .. } => {
                        pending.extend(arguments.iter().rev().map(|(_, id)| Task::Expression(*id)))
                    }
                    _ => {}
                }
            }
            Task::FinishExpression(id) => {
                let ast = skill.expression(id).expect("AST expression");
                let annotation = types
                    .expression(id)
                    .ok_or(CompileError::InvalidModel("untyped expression"))?;
                if let E::Path(parts) = &ast.kind {
                    let symbol = references[&id];
                    let mut value = Expression {
                        node_id: next("expr"),
                        type_info: types
                            .symbol(symbol)
                            .expect("typed reference")
                            .type_info
                            .clone(),
                        kind: ExpressionKind::SymbolRef {
                            symbol_id: symbol.to_string(),
                        },
                    };
                    for (field, resolved) in parts.iter().skip(1).zip(&annotation.fields) {
                        let kind = if resolved.projected {
                            ExpressionKind::Projection {
                                source: Box::new(value),
                                field: field.clone(),
                            }
                        } else {
                            ExpressionKind::Field {
                                source: Box::new(value),
                                field: field.clone(),
                            }
                        };
                        value = Expression {
                            node_id: next("expr"),
                            type_info: resolved.type_info.clone(),
                            kind,
                        };
                    }
                    expressions.insert(id, value);
                    continue;
                }
                let (tag, kind) = match &ast.kind {
                    E::Literal(value) => (
                        "expr",
                        ExpressionKind::Literal(match value {
                            AstLiteral::Int(v) => LiteralValue::Int(v.clone()),
                            AstLiteral::Decimal(v) => LiteralValue::Decimal(v.clone()),
                            AstLiteral::Bool(v) => LiteralValue::Bool(*v),
                            AstLiteral::String(v) => LiteralValue::String(v.clone()),
                        }),
                    ),
                    E::Binary {
                        operator,
                        left,
                        right,
                    } => (
                        "expr",
                        ExpressionKind::Binary {
                            operator: match operator.as_str() {
                                "+" => "ADD",
                                "-" => "SUB",
                                "*" => "MUL",
                                "/" => "DIV",
                                "<" => "LT",
                                "<=" => "LE",
                                ">" => "GT",
                                ">=" => "GE",
                                "==" => "EQ",
                                "!=" => "NE",
                                _ => unreachable!("closed operators"),
                            }
                            .into(),
                            left: Box::new(expressions.remove(left).expect("left")),
                            right: Box::new(expressions.remove(right).expect("right")),
                        },
                    ),
                    E::Call { name, arguments } => (
                        "expr",
                        ExpressionKind::Call {
                            function: name.clone(),
                            arguments: arguments
                                .iter()
                                .map(|id| expressions.remove(id).expect("argument"))
                                .collect(),
                        },
                    ),
                    E::Read { tool_id, arguments } => (
                        "read",
                        ExpressionKind::Read {
                            tool_ref: tools[tool_id.as_str()].clone(),
                            arguments: named(arguments, &mut expressions),
                            result_policy: ResultPolicy {
                                empty_is_valid: contracts[tool_id.as_str()].empty_is_valid(),
                                ..ResultPolicy::default()
                            },
                        },
                    ),
                    E::Write {
                        tool_id,
                        arguments,
                        approval_id,
                        ..
                    } => (
                        "write",
                        ExpressionKind::Write(WriteExpression {
                            tool_ref: tools[tool_id.as_str()].clone(),
                            arguments: named(arguments, &mut expressions),
                            approval_ref: approval_id.clone(),
                        }),
                    ),
                    E::Path(_) => unreachable!("path handled"),
                };
                expressions.insert(
                    id,
                    Expression {
                        node_id: next(tag),
                        type_info: annotation.value.type_info.clone(),
                        kind,
                    },
                );
            }
        }
    }
    Ok(skill
        .body
        .iter()
        .map(|id| statements.remove(id).expect("root statement"))
        .collect())
}
fn named(
    arguments: &[(String, ExpressionId)],
    expressions: &mut HashMap<ExpressionId, Expression>,
) -> Vec<NamedExpression> {
    arguments
        .iter()
        .map(|(name, id)| NamedExpression {
            name: name.clone(),
            value: expressions.remove(id).expect("named argument"),
        })
        .collect()
}
