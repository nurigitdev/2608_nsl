//! Shared iterative Typed-IR traversal. The pure facade stops at service nodes;
//! the READ driver admits and resumes provider operations through the same machine.
mod builtins;
mod operations;
mod values;

use crate::{BindingError, BindingNamespace, ExecutionContext, LimitExceeded, RuntimeClock};
use nsl_core::{Completeness, DataClassification, Presence, TypeRef, Value, ValueEnvelope};
use nsl_ir::{Expression, ExpressionKind, LiteralValue, NamedExpression, ResultPolicy};
use std::fmt;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExpressionEvaluationError {
    pub node_id: String,
    pub detail_code: &'static str,
}
impl ExpressionEvaluationError {
    fn failed(node: &str) -> Self {
        Self {
            node_id: node.into(),
            detail_code: "EXPRESSION_EVALUATION_ERROR",
        }
    }
    fn mismatch(node: &str) -> Self {
        Self {
            node_id: node.into(),
            detail_code: "EXPRESSION_TYPE_MISMATCH",
        }
    }
}
impl fmt::Display for ExpressionEvaluationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = if self.detail_code == "EXPRESSION_TYPE_MISMATCH" {
            "expression type mismatch"
        } else {
            "expression evaluation failed"
        };
        write!(f, "{message} at {}", self.node_id)
    }
}
impl std::error::Error for ExpressionEvaluationError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExpressionRuntimeError {
    Evaluation(ExpressionEvaluationError),
    Contract(String),
    Money { kind: &'static str, message: String },
    Limit(LimitExceeded),
    Binding(BindingError),
    Unexpected { kind: &'static str, detail: String },
    Unsupported { node_id: String },
}
impl fmt::Display for ExpressionRuntimeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Evaluation(e) => e.fmt(f),
            Self::Contract(message) | Self::Money { message, .. } => f.write_str(message),
            Self::Limit(e) => e.fmt(f),
            Self::Binding(e) => e.fmt(f),
            Self::Unexpected { .. } => f.write_str("An unexpected runtime error occurred."),
            Self::Unsupported { .. } => f.write_str("expression requires an execution service"),
        }
    }
}
// Operand values and underlying conversion/lookup failures are never chained.
impl std::error::Error for ExpressionRuntimeError {}
impl From<LimitExceeded> for ExpressionRuntimeError {
    fn from(e: LimitExceeded) -> Self {
        Self::Limit(e)
    }
}
impl From<BindingError> for ExpressionRuntimeError {
    fn from(e: BindingError) -> Self {
        Self::Binding(e)
    }
}
pub(super) fn failed(node: &str) -> ExpressionRuntimeError {
    ExpressionRuntimeError::Evaluation(ExpressionEvaluationError::failed(node))
}
pub(super) fn mismatch(node: &str) -> ExpressionRuntimeError {
    ExpressionRuntimeError::Evaluation(ExpressionEvaluationError::mismatch(node))
}

enum Step<'a> {
    Enter(&'a Expression),
    Finish(&'a Expression, usize),
    FinishRead(&'a Expression, usize),
}

pub(crate) enum EvaluationProgress<'a> {
    Complete(ValueEnvelope),
    ReadStart {
        expression: &'a Expression,
        tool_ref: &'a str,
        arguments: &'a [NamedExpression],
        policy: ResultPolicy,
    },
    ReadReady {
        expression: &'a Expression,
        arguments: Vec<ValueEnvelope>,
    },
}
pub(crate) struct ExpressionMachine<'a> {
    steps: Vec<Step<'a>>,
    values: Vec<ValueEnvelope>,
}
impl<'a> ExpressionMachine<'a> {
    pub(crate) fn new(expression: &'a Expression) -> Self {
        Self {
            steps: vec![Step::Enter(expression)],
            values: vec![],
        }
    }
    pub(crate) fn begin_read(
        &mut self,
        expression: &'a Expression,
        arguments: &'a [NamedExpression],
    ) {
        self.steps
            .push(Step::FinishRead(expression, self.values.len()));
        self.steps
            .extend(arguments.iter().rev().map(|a| Step::Enter(&a.value)));
    }
    pub(crate) fn accept_read<C: RuntimeClock>(
        &mut self,
        ctx: &mut ExecutionContext<C>,
        expression: &Expression,
        value: ValueEnvelope,
    ) -> Result<(), ExpressionRuntimeError> {
        finish(ctx, expression, &value)?;
        self.values.push(value);
        Ok(())
    }
    pub(crate) fn advance<C: RuntimeClock>(
        &mut self,
        ctx: &mut ExecutionContext<C>,
    ) -> Result<EvaluationProgress<'a>, ExpressionRuntimeError> {
        while let Some(step) = self.steps.pop() {
            match step {
                Step::Enter(expression) => {
                    ctx.guard().check_deadline()?;
                    match &expression.kind {
                        ExpressionKind::Literal(value) => {
                            let value = match value {
                                LiteralValue::Bool(v) => Value::Bool(*v),
                                LiteralValue::Int(v) => Value::Int(v.clone()),
                                LiteralValue::String(v) => Value::String(v.clone()),
                                LiteralValue::Decimal(v) => Value::Decimal(v.clone()),
                                LiteralValue::Money(v) => Value::Money(v.clone()),
                            };
                            let result = ValueEnvelope::complete(
                                value,
                                expression.type_info.clone(),
                                DataClassification::Public,
                            );
                            finish(ctx, expression, &result)?;
                            self.values.push(result);
                        }
                        ExpressionKind::SymbolRef { symbol_id } => {
                            let result = ctx
                                .resolve(symbol_id)
                                .map_err(|e| ExpressionRuntimeError::Unexpected {
                                    kind: "RuntimeError",
                                    detail: e.to_string(),
                                })?
                                .clone();
                            finish(ctx, expression, &result)?;
                            self.values.push(result);
                        }
                        ExpressionKind::Field { source, .. }
                        | ExpressionKind::Projection { source, .. } => {
                            self.steps.push(Step::Finish(expression, self.values.len()));
                            self.steps.push(Step::Enter(source));
                        }
                        ExpressionKind::Binary { left, right, .. } => {
                            self.steps.push(Step::Finish(expression, self.values.len()));
                            self.steps.push(Step::Enter(right));
                            self.steps.push(Step::Enter(left));
                        }
                        ExpressionKind::Call { arguments, .. } => {
                            self.steps.push(Step::Finish(expression, self.values.len()));
                            self.steps.extend(arguments.iter().rev().map(Step::Enter));
                        }
                        ExpressionKind::Read {
                            tool_ref,
                            arguments,
                            result_policy,
                        } => {
                            return Ok(EvaluationProgress::ReadStart {
                                expression,
                                tool_ref,
                                arguments,
                                policy: *result_policy,
                            });
                        }
                        ExpressionKind::Write(_) => {
                            return Err(ExpressionRuntimeError::Unsupported {
                                node_id: expression.node_id.clone(),
                            });
                        }
                    }
                }
                Step::FinishRead(expression, start) => {
                    return Ok(EvaluationProgress::ReadReady {
                        expression,
                        arguments: self.values.split_off(start),
                    });
                }
                Step::Finish(expression, start) => {
                    let arguments = self.values.split_off(start);
                    let result = calculate(expression, &arguments)?;
                    finish(ctx, expression, &result)?;
                    self.values.push(result);
                }
            }
        }
        Ok(EvaluationProgress::Complete(
            self.values
                .pop()
                .expect("one result for a completed expression"),
        ))
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ExpressionEvaluator;
impl ExpressionEvaluator {
    pub fn evaluate<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        expression: &Expression,
    ) -> Result<ValueEnvelope, ExpressionRuntimeError> {
        let mut machine = ExpressionMachine::new(expression);
        match machine.advance(ctx)? {
            EvaluationProgress::Complete(value) => Ok(value),
            EvaluationProgress::ReadStart { expression, .. }
            | EvaluationProgress::ReadReady { expression, .. } => {
                Err(ExpressionRuntimeError::Unsupported {
                    node_id: expression.node_id.clone(),
                })
            }
        }
    }

    /// Evaluate before binding, exactly as _execute_let. Block statement-node
    /// bookkeeping belongs to the surrounding execution driver.
    pub fn execute_let<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        target_symbol_id: &str,
        expression: &Expression,
    ) -> Result<(), ExpressionRuntimeError> {
        let value = self.evaluate(ctx, expression)?;
        ctx.bind(BindingNamespace::Variable, target_symbol_id.into(), value)?;
        Ok(())
    }
}

fn finish<C: RuntimeClock>(
    ctx: &mut ExecutionContext<C>,
    expression: &Expression,
    value: &ValueEnvelope,
) -> Result<(), ExpressionRuntimeError> {
    if value.type_ref() != &expression.type_info {
        return Err(mismatch(&expression.node_id));
    }
    // Observation deliberately precedes post-evaluation deadline/collection checks.
    ctx.observe_completeness(value.completeness());
    ctx.guard().check_deadline()?;
    if let Value::List(items) = value.value() {
        ctx.guard()
            .check_collection(&crate::data::integer(items.len()))?;
    }
    Ok(())
}

fn calculate(
    expression: &Expression,
    arguments: &[ValueEnvelope],
) -> Result<ValueEnvelope, ExpressionRuntimeError> {
    let first = || &arguments[0];
    let (value, presence, completeness, classification, provenance) = match &expression.kind {
        ExpressionKind::Field { field, .. } => {
            let Value::Record(fields) = first().value() else {
                return Err(failed(&expression.node_id));
            };
            let value = fields
                .get(field)
                .ok_or_else(|| failed(&expression.node_id))?
                .clone();
            (
                value,
                Presence::Present,
                first().completeness(),
                first().classification(),
                first().provenance_refs().to_vec(),
            )
        }
        ExpressionKind::Projection { field, .. } => {
            let Value::List(items) = first().value() else {
                return Err(failed(&expression.node_id));
            };
            let projected = items
                .iter()
                .map(|item| {
                    let Value::Record(fields) = item else {
                        return Err(failed(&expression.node_id));
                    };
                    fields
                        .get(field)
                        .cloned()
                        .ok_or_else(|| failed(&expression.node_id))
                })
                .collect::<Result<Vec<_>, _>>()?;
            let presence = if projected.is_empty() {
                Presence::Empty
            } else {
                Presence::Present
            };
            (
                Value::List(projected),
                presence,
                first().completeness(),
                first().classification(),
                first().provenance_refs().to_vec(),
            )
        }
        ExpressionKind::Call { function, .. } => {
            let value = builtins::evaluate(
                function,
                arguments,
                &expression.type_info,
                &expression.node_id,
            )?;
            (
                value,
                Presence::Present,
                first().completeness(),
                first().classification(),
                first().provenance_refs().to_vec(),
            )
        }
        ExpressionKind::Binary { operator, .. } => {
            let left = &arguments[0];
            let right = &arguments[1];
            if operator == "DIV"
                && primitive(left.type_ref(), "Int")
                && primitive(right.type_ref(), "Int")
                && !primitive(&expression.type_info, "Decimal")
            {
                return Err(mismatch(&expression.node_id));
            }
            let value = operations::binary(
                operator,
                left.value(),
                right.value(),
                &expression.type_info,
                &expression.node_id,
            )?;
            let completeness = combine(left.completeness(), right.completeness());
            let classification = if left.classification().rank() >= right.classification().rank() {
                left.classification()
            } else {
                right.classification()
            };
            let mut provenance = Vec::new();
            let mut seen = std::collections::BTreeSet::new();
            for reference in left.provenance_refs().iter().chain(right.provenance_refs()) {
                if seen.insert(reference) {
                    provenance.push(reference.clone());
                }
            }
            (
                value,
                Presence::Present,
                completeness,
                classification,
                provenance,
            )
        }
        _ => unreachable!("only composite pure expressions schedule a finish"),
    };
    Ok(ValueEnvelope::new(
        value,
        expression.type_info.clone(),
        presence,
        completeness,
        classification,
        provenance,
    ))
}
fn combine(left: Completeness, right: Completeness) -> Completeness {
    if [left, right].contains(&Completeness::Unknown) {
        Completeness::Unknown
    } else if [left, right].contains(&Completeness::Partial) {
        Completeness::Partial
    } else {
        Completeness::Complete
    }
}

pub(super) fn primitive(ty: &TypeRef, name: &str) -> bool {
    matches!(ty.kind(),nsl_core::TypeKind::Primitive(n) if n==name)
}
