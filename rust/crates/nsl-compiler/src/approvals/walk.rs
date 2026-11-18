use nsl_source::SourceSpan;
use nsl_source::ast::{AstSkill, ExpressionId, ExpressionKind, StatementId, StatementKind};

use super::ApprovalAnalysisError;

#[derive(Clone, Copy)]
pub(super) enum EventKind<'ast> {
    Approval {
        statement: StatementId,
        name: &'ast str,
        span: &'ast SourceSpan,
    },
    Write {
        expression: ExpressionId,
        approval_name: &'ast str,
        approval_span: &'ast SourceSpan,
    },
}

pub(super) struct Event<'ast> {
    pub kind: EventKind<'ast>,
    pub inside_foreach: bool,
}

enum Visit {
    Statement(StatementId, bool),
    Expression(ExpressionId, bool),
}

pub(super) fn events(skill: &AstSkill) -> Result<Vec<Event<'_>>, ApprovalAnalysisError> {
    let mut pending = skill
        .body
        .iter()
        .rev()
        .map(|id| Visit::Statement(*id, false))
        .collect::<Vec<_>>();
    let mut events = Vec::new();
    while let Some(visit) = pending.pop() {
        match visit {
            Visit::Statement(id, inside) => {
                let statement = skill
                    .statement(id)
                    .ok_or(ApprovalAnalysisError::InvalidStatement(id))?;
                match &statement.kind {
                    StatementKind::Let { value, .. } => {
                        pending.push(Visit::Expression(*value, inside))
                    }
                    StatementKind::Foreach {
                        collection, body, ..
                    } => {
                        pending.extend(body.iter().rev().map(|id| Visit::Statement(*id, true)));
                        pending.push(Visit::Expression(*collection, inside));
                    }
                    StatementKind::Check { condition, .. } => {
                        pending.push(Visit::Expression(*condition, inside))
                    }
                    StatementKind::Emit { fields } => pending.extend(
                        fields
                            .iter()
                            .rev()
                            .map(|(_, id)| Visit::Expression(*id, inside)),
                    ),
                    StatementKind::Approve {
                        approval_id,
                        approval_id_span,
                        ..
                    } => events.push(Event {
                        kind: EventKind::Approval {
                            statement: id,
                            name: approval_id,
                            span: approval_id_span,
                        },
                        inside_foreach: inside,
                    }),
                }
            }
            Visit::Expression(id, inside) => {
                let expression = skill
                    .expression(id)
                    .ok_or(ApprovalAnalysisError::InvalidExpression(id))?;
                match &expression.kind {
                    ExpressionKind::Literal(_) | ExpressionKind::Path(_) => {}
                    ExpressionKind::Binary { left, right, .. } => {
                        pending.push(Visit::Expression(*right, inside));
                        pending.push(Visit::Expression(*left, inside));
                    }
                    ExpressionKind::Call { arguments, .. } => pending.extend(
                        arguments
                            .iter()
                            .rev()
                            .map(|id| Visit::Expression(*id, inside)),
                    ),
                    ExpressionKind::Read { arguments, .. } => pending.extend(
                        arguments
                            .iter()
                            .rev()
                            .map(|(_, id)| Visit::Expression(*id, inside)),
                    ),
                    ExpressionKind::Write {
                        approval_id,
                        approval_span,
                        arguments,
                        ..
                    } => {
                        events.push(Event {
                            kind: EventKind::Write {
                                expression: id,
                                approval_name: approval_id,
                                approval_span,
                            },
                            inside_foreach: inside,
                        });
                        pending.extend(
                            arguments
                                .iter()
                                .rev()
                                .map(|(_, id)| Visit::Expression(*id, inside)),
                        );
                    }
                }
            }
        }
    }
    Ok(events)
}
