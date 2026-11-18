use super::{NsoCodecError, NsoLimit, NsoLimits, Result};
use crate::{Expression, ExpressionKind, LiteralValue, SkillObject, Statement, StatementKind};
use nsl_core::{MAX_JSON_INTEGER_DIGITS, TypeKind, TypeRef, ValueData};

enum Visit<'a> {
    Statement(&'a Statement, usize),
    Expression(&'a Expression, usize),
    Type(&'a TypeRef, usize),
}

/// Check recursive projection paths before projection or hash allocation.
/// Counts/bytes here are lower bounds; canonical() enforces exact JSON units.
pub(super) fn preflight(skill: &SkillObject, limits: NsoLimits) -> Result<()> {
    if limits.max_bytes == 0 {
        return Err(NsoCodecError::Limit(NsoLimit::Bytes));
    }
    if limits.max_nodes == 0 {
        return Err(NsoCodecError::Limit(NsoLimit::Nodes));
    }
    if limits.max_depth < 4 {
        return Err(NsoCodecError::Limit(NsoLimit::Depth));
    }
    let mut pending = Vec::new();
    pending.extend(skill.body.iter().map(|node| Visit::Statement(node, 2)));
    for item in &skill.symbols {
        pending.push(Visit::Type(&item.type_info, 3));
    }
    for item in &skill.inputs {
        pending.push(Visit::Type(&item.type_info, 3));
    }
    for item in &skill.contexts {
        pending.push(Visit::Type(&item.type_info, 3));
    }
    for item in &skill.outputs {
        pending.push(Visit::Type(&item.type_info, 3));
    }
    let mut nodes = 1usize;
    while let Some(task) = pending.pop() {
        nodes = nodes
            .checked_add(1)
            .ok_or(NsoCodecError::Limit(NsoLimit::Nodes))?;
        if nodes > limits.max_nodes {
            return Err(NsoCodecError::Limit(NsoLimit::Nodes));
        }
        let depth = match task {
            Visit::Statement(_, d) | Visit::Expression(_, d) | Visit::Type(_, d) => d,
        };
        if depth >= limits.max_depth {
            return Err(NsoCodecError::Limit(NsoLimit::Depth));
        }
        match task {
            Visit::Statement(node, d) => match &node.kind {
                StatementKind::Let { value, .. } => pending.push(Visit::Expression(value, d + 1)),
                StatementKind::Foreach {
                    collection, body, ..
                } => {
                    pending.push(Visit::Expression(collection, d + 1));
                    pending.extend(body.iter().map(|node| Visit::Statement(node, d + 2)));
                }
                StatementKind::Check { condition, .. } => {
                    pending.push(Visit::Expression(condition, d + 1))
                }
                StatementKind::Emit { fields } => pending.extend(
                    fields
                        .iter()
                        .map(|field| Visit::Expression(&field.value, d + 3)),
                ),
                StatementKind::Approval(_) => {}
            },
            Visit::Expression(node, d) => {
                pending.push(Visit::Type(&node.type_info, d + 1));
                match &node.kind {
                    ExpressionKind::Field { source, .. }
                    | ExpressionKind::Projection { source, .. } => {
                        pending.push(Visit::Expression(source, d + 1))
                    }
                    ExpressionKind::Binary { left, right, .. } => {
                        pending.push(Visit::Expression(left, d + 1));
                        pending.push(Visit::Expression(right, d + 1));
                    }
                    ExpressionKind::Call { arguments, .. } => {
                        pending.extend(arguments.iter().map(|node| Visit::Expression(node, d + 2)))
                    }
                    ExpressionKind::Read { arguments, .. } => pending.extend(
                        arguments
                            .iter()
                            .map(|arg| Visit::Expression(&arg.value, d + 3)),
                    ),
                    ExpressionKind::Write(write) => pending.extend(
                        write
                            .arguments
                            .iter()
                            .map(|arg| Visit::Expression(&arg.value, d + 3)),
                    ),
                    ExpressionKind::Literal(LiteralValue::String(text))
                        if text.len() > limits.max_bytes =>
                    {
                        return Err(NsoCodecError::Limit(NsoLimit::Bytes));
                    }
                    _ => {}
                }
            }
            Visit::Type(ty, d) => match ty.kind() {
                TypeKind::List(item) | TypeKind::Optional(item) => {
                    pending.push(Visit::Type(item, d + 1))
                }
                TypeKind::Record { fields, .. } => {
                    pending.extend(fields.iter().map(|(_, ty)| Visit::Type(ty, d + 3)))
                }
                _ => {}
            },
        }
        if pending.len() > limits.max_nodes.saturating_sub(nodes) {
            return Err(NsoCodecError::Limit(NsoLimit::Nodes));
        }
    }
    Ok(())
}

enum Task<'a> {
    Value(&'a ValueData, usize),
    Text(&'a str),
    Byte(u8),
}

pub(super) fn canonical(data: &ValueData, limits: NsoLimits) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    let mut pending = vec![Task::Value(data, 0)];
    let mut nodes = 0usize;
    while let Some(task) = pending.pop() {
        match task {
            Task::Byte(byte) => append(&mut output, &[byte], limits.max_bytes)?,
            Task::Text(text) => quote(&mut output, text, limits.max_bytes)?,
            Task::Value(value, depth) => {
                if depth > limits.max_depth {
                    return Err(NsoCodecError::Limit(NsoLimit::Depth));
                }
                if nodes >= limits.max_nodes {
                    return Err(NsoCodecError::Limit(NsoLimit::Nodes));
                }
                nodes += 1;
                match value {
                    ValueData::Null => append(&mut output, b"null", limits.max_bytes)?,
                    ValueData::Bool(value) => append(
                        &mut output,
                        if *value { b"true" } else { b"false" },
                        limits.max_bytes,
                    )?,
                    ValueData::Number(value) => {
                        if value.as_str().trim_start_matches('-').len() > MAX_JSON_INTEGER_DIGITS {
                            return Err(NsoCodecError::IntegerTooWide);
                        }
                        append(&mut output, value.as_str().as_bytes(), limits.max_bytes)?;
                    }
                    ValueData::String(text) => quote(&mut output, text, limits.max_bytes)?,
                    ValueData::Array(items) => {
                        if items.len() > limits.max_nodes - nodes {
                            return Err(NsoCodecError::Limit(NsoLimit::Nodes));
                        }
                        append(&mut output, b"[", limits.max_bytes)?;
                        pending.push(Task::Byte(b']'));
                        for (index, value) in items.iter().enumerate().rev() {
                            pending.push(Task::Value(value, depth + 1));
                            if index > 0 {
                                pending.push(Task::Byte(b','));
                            }
                        }
                    }
                    ValueData::Object(fields) => {
                        if fields.len() > limits.max_nodes - nodes {
                            return Err(NsoCodecError::Limit(NsoLimit::Nodes));
                        }
                        append(&mut output, b"{", limits.max_bytes)?;
                        pending.push(Task::Byte(b'}'));
                        for (index, (key, value)) in fields.iter().enumerate().rev() {
                            pending.push(Task::Value(value, depth + 1));
                            pending.push(Task::Byte(b':'));
                            pending.push(Task::Text(key));
                            if index > 0 {
                                pending.push(Task::Byte(b','));
                            }
                        }
                    }
                }
            }
        }
    }
    Ok(output)
}

fn append(output: &mut Vec<u8>, bytes: &[u8], limit: usize) -> Result<()> {
    if bytes.len() > limit.saturating_sub(output.len()) {
        return Err(NsoCodecError::Limit(NsoLimit::Bytes));
    }
    output.extend_from_slice(bytes);
    Ok(())
}

fn quote(output: &mut Vec<u8>, text: &str, limit: usize) -> Result<()> {
    append(output, b"\"", limit)?;
    for ch in text.chars() {
        let mut buffer = [0; 4];
        let escaped = match ch {
            '"' => "\\\"",
            '\\' => "\\\\",
            '\n' => "\\n",
            '\r' => "\\r",
            '\t' => "\\t",
            '\u{8}' => "\\b",
            '\u{c}' => "\\f",
            '\0'..='\u{1f}' => {
                let code = ch as u8;
                const HEX: &[u8] = b"0123456789abcdef";
                append(
                    output,
                    &[
                        b'\\',
                        b'u',
                        b'0',
                        b'0',
                        HEX[usize::from(code >> 4)],
                        HEX[usize::from(code & 15)],
                    ],
                    limit,
                )?;
                continue;
            }
            other => other.encode_utf8(&mut buffer),
        };
        append(output, escaped.as_bytes(), limit)?;
    }
    append(output, b"\"", limit)
}

#[cfg(test)]
#[path = "../../tests/bounded_support/mod.rs"]
mod tests;
