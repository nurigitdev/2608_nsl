use nsl_ir::{Expression, ExpressionKind, Statement, StatementKind, StaticAnalysis};
use num_bigint::BigInt;

use super::{UnboundedStructureError, integer};

#[derive(Default)]
struct Counts {
    tools: BigInt,
    loops: BigInt,
    emits: BigInt,
}

struct Frame<'ir> {
    statements: &'ir [Statement],
    next: usize,
    counts: Counts,
    multiplier: BigInt,
}

impl<'ir> Frame<'ir> {
    fn new(statements: &'ir [Statement], multiplier: BigInt) -> Self {
        Self {
            statements,
            next: 0,
            counts: Counts::default(),
            multiplier,
        }
    }
}

pub(super) fn analyze(statements: &[Statement]) -> Result<StaticAnalysis, UnboundedStructureError> {
    let mut pending = vec![Frame::new(statements, 1.into())];
    loop {
        let frame = pending.last_mut().expect("root frame");
        let Some(statement) = frame.statements.get(frame.next) else {
            let frame = pending.pop().expect("completed frame");
            if let Some(parent) = pending.last_mut() {
                parent.counts.tools += &frame.multiplier * frame.counts.tools;
                parent.counts.loops += &frame.multiplier * (1 + frame.counts.loops);
                parent.counts.emits += frame.multiplier * frame.counts.emits;
                continue;
            }
            return Ok(StaticAnalysis {
                max_tool_calls: frame
                    .counts
                    .tools
                    .to_string()
                    .parse()
                    .expect("exact integer count"),
                max_loop_iterations: frame
                    .counts
                    .loops
                    .to_string()
                    .parse()
                    .expect("exact integer count"),
                max_emit_records: frame
                    .counts
                    .emits
                    .to_string()
                    .parse()
                    .expect("exact integer count"),
                bounded: true,
            });
        };
        frame.next += 1;
        match &statement.kind {
            StatementKind::Let { value, .. } => frame.counts.tools += expression_calls(value),
            StatementKind::Foreach {
                collection,
                max_iterations,
                body,
                ..
            } => {
                let maximum = integer(max_iterations);
                if maximum <= BigInt::from(0) {
                    return Err(UnboundedStructureError {
                        node_id: statement.node_id.clone(),
                    });
                }
                frame.counts.tools += expression_calls(collection);
                pending.push(Frame::new(body, maximum));
            }
            StatementKind::Check { condition, .. } => {
                frame.counts.tools += expression_calls(condition)
            }
            StatementKind::Emit { fields } => {
                frame.counts.emits += 1;
                for field in fields {
                    frame.counts.tools += expression_calls(&field.value);
                }
            }
            StatementKind::Approval(_) => {}
        }
    }
}

fn expression_calls(expression: &Expression) -> BigInt {
    let mut calls = BigInt::from(0);
    let mut pending = vec![expression];
    while let Some(expression) = pending.pop() {
        match &expression.kind {
            ExpressionKind::Read { arguments, .. } => {
                calls += 1;
                pending.extend(arguments.iter().rev().map(|argument| &argument.value));
            }
            ExpressionKind::Write(write) => {
                calls += 1;
                pending.extend(write.arguments.iter().rev().map(|argument| &argument.value));
            }
            ExpressionKind::Field { source, .. } | ExpressionKind::Projection { source, .. } => {
                pending.push(source)
            }
            ExpressionKind::Call { arguments, .. } => pending.extend(arguments.iter().rev()),
            ExpressionKind::Binary { left, right, .. } => {
                pending.push(right);
                pending.push(left);
            }
            ExpressionKind::Literal(_) | ExpressionKind::SymbolRef { .. } => {}
        }
    }
    calls
}
