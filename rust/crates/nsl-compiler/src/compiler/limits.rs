use super::{CompileError, CompilerOutputLimit};
use nsl_core::DecimalError;
use nsl_ir::{NsoCodecError, NsoLimit, NsoLimits};
use nsl_source::SourceSpan;
use nsl_source::ast::{
    AstLiteral, AstSkill, ExpressionId, ExpressionKind, StatementId, StatementKind,
};

pub(super) fn exceeded(limit: NsoLimit, span: &SourceSpan) -> CompileError {
    CompileError::OutputLimit(CompilerOutputLimit {
        limit,
        span: span.clone(),
    })
}
pub(super) fn codec(error: NsoCodecError, span: &SourceSpan) -> CompileError {
    match error {
        NsoCodecError::Limit(limit) => exceeded(limit, span),
        NsoCodecError::Decimal(DecimalError::EncodingLimit) => exceeded(NsoLimit::Bytes, span),
        error => CompileError::Codec(error),
    }
}
enum Task {
    Statement(StatementId, usize),
    Expression(ExpressionId, usize),
}
pub(super) fn ast(skill: &AstSkill, limits: NsoLimits) -> Result<(), CompileError> {
    if limits.max_bytes == 0 {
        return Err(exceeded(NsoLimit::Bytes, &skill.span));
    }
    if limits.max_nodes == 0 {
        return Err(exceeded(NsoLimit::Nodes, &skill.span));
    }
    if limits.max_depth < 4 {
        return Err(exceeded(NsoLimit::Depth, &skill.span));
    }
    let mut pending = skill
        .body
        .iter()
        .map(|id| Task::Statement(*id, 2))
        .collect::<Vec<_>>();
    let mut nodes = 1usize
        .saturating_add(skill.inputs.len().saturating_mul(2))
        .saturating_add(skill.contexts.len().saturating_mul(2))
        .saturating_add(skill.outputs.len())
        .saturating_add(skill.requires.len());
    if nodes > limits.max_nodes {
        return Err(exceeded(NsoLimit::Nodes, &skill.span));
    }
    let mut bytes = skill
        .skill_id
        .len()
        .saturating_add(skill.skill_version.len())
        .saturating_add(skill.risk.len());
    for field in skill
        .inputs
        .iter()
        .chain(&skill.contexts)
        .chain(&skill.outputs)
    {
        bytes = bytes.saturating_add(field.name.len());
        if field.type_info.list_depth.saturating_add(4) > limits.max_depth {
            return Err(exceeded(NsoLimit::Depth, &field.span));
        }
    }
    if bytes > limits.max_bytes {
        return Err(exceeded(NsoLimit::Bytes, &skill.span));
    }
    while let Some(task) = pending.pop() {
        let (span, depth) = match task {
            Task::Statement(id, d) => (
                &skill
                    .statement(id)
                    .ok_or(CompileError::InvalidModel("invalid AST statement"))?
                    .span,
                d,
            ),
            Task::Expression(id, d) => (
                &skill
                    .expression(id)
                    .ok_or(CompileError::InvalidModel("invalid AST expression"))?
                    .span,
                d,
            ),
        };
        if nodes >= limits.max_nodes {
            return Err(exceeded(NsoLimit::Nodes, span));
        }
        nodes += 1;
        if depth >= limits.max_depth {
            return Err(exceeded(NsoLimit::Depth, span));
        }
        match task {
            Task::Statement(id, d) => match &skill.statement(id).expect("AST statement").kind {
                StatementKind::Let { value, .. } => pending.push(Task::Expression(*value, d + 1)),
                StatementKind::Foreach {
                    collection, body, ..
                } => {
                    pending.push(Task::Expression(*collection, d + 1));
                    pending.extend(body.iter().map(|id| Task::Statement(*id, d + 2)));
                }
                StatementKind::Check { condition, .. } => {
                    pending.push(Task::Expression(*condition, d + 1))
                }
                StatementKind::Emit { fields } => {
                    pending.extend(fields.iter().map(|(_, id)| Task::Expression(*id, d + 3)))
                }
                StatementKind::Approve { message, .. } => {
                    bytes = bytes.saturating_add(message.len())
                }
            },
            Task::Expression(id, d) => match &skill.expression(id).expect("AST expression").kind {
                ExpressionKind::Binary { left, right, .. } => {
                    pending.push(Task::Expression(*left, d + 1));
                    pending.push(Task::Expression(*right, d + 1));
                }
                ExpressionKind::Call { arguments, .. } => {
                    pending.extend(arguments.iter().map(|id| Task::Expression(*id, d + 2)))
                }
                ExpressionKind::Read { arguments, .. }
                | ExpressionKind::Write { arguments, .. } => {
                    pending.extend(arguments.iter().map(|(_, id)| Task::Expression(*id, d + 3)))
                }
                ExpressionKind::Literal(AstLiteral::String(text)) => {
                    bytes = bytes.saturating_add(text.len())
                }
                ExpressionKind::Literal(AstLiteral::Decimal(decimal)) => {
                    let text = decimal
                        .to_fixed_point(limits.max_bytes.saturating_sub(bytes))
                        .map_err(|_| exceeded(NsoLimit::Bytes, span))?;
                    bytes = bytes.saturating_add(text.len());
                }
                ExpressionKind::Path(parts) if parts.len() > limits.max_depth - depth => {
                    return Err(exceeded(NsoLimit::Depth, span));
                }
                _ => {}
            },
        }
        if bytes > limits.max_bytes {
            return Err(exceeded(NsoLimit::Bytes, span));
        }
        if pending.len() > limits.max_nodes.saturating_sub(nodes) {
            return Err(exceeded(NsoLimit::Nodes, span));
        }
    }
    Ok(())
}
