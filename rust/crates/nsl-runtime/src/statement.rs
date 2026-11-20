use crate::data::{bigint, integer};
use crate::{
    BindingError, BindingNamespace, CheckExecutionError, EmitRecord, ExecutionContext,
    LimitExceeded, MAX_FOREACH_NESTING_DEPTH, ModelError, ReadEvaluator, ReadExecutionError,
    RuntimeClock,
};
use nsl_core::{Integer, Presence, TypeKind, Value, ValueEnvelope};
use nsl_ir::{Statement, StatementKind};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StatementTypeTarget {
    ForeachCollection,
    EmitDeclaration(String),
    EmitValue(String),
}

/// An approved strict-type boundary. It has a node identity, no diagnostic code,
/// and never stores the rejected value in an error or its source chain.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StatementTypeError {
    pub node_id: String,
    pub target: StatementTypeTarget,
}
impl fmt::Display for StatementTypeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.target {
            StatementTypeTarget::ForeachCollection => {
                f.write_str("FOREACH collection type mismatch")
            }
            StatementTypeTarget::EmitDeclaration(name) => {
                write!(f, "EMIT type does not match output schema: {name}")
            }
            StatementTypeTarget::EmitValue(name) => {
                write!(f, "runtime type mismatch for output {name}")
            }
        }
    }
}
impl std::error::Error for StatementTypeError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StatementLimitKind {
    ForeachDepth,
    ForeachIterations(Integer),
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StatementLimitError {
    pub node_id: String,
    pub kind: StatementLimitKind,
}
impl fmt::Display for StatementLimitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.kind {
            StatementLimitKind::ForeachDepth => write!(
                f,
                "foreach nesting depth exceeds {MAX_FOREACH_NESTING_DEPTH}"
            ),
            StatementLimitKind::ForeachIterations(max) => {
                write!(f, "foreach {} exceeds max {}", self.node_id, max.as_str())
            }
        }
    }
}
impl std::error::Error for StatementLimitError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StatementExecutionError {
    Type(StatementTypeError),
    StatementLimit(StatementLimitError),
    Limit(LimitExceeded),
    Read(ReadExecutionError),
    Check(CheckExecutionError),
    Binding(BindingError),
    Contract(String),
    Model(ModelError),
    Unsupported { node_id: String },
}
impl fmt::Display for StatementExecutionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Type(e) => e.fmt(f),
            Self::StatementLimit(e) => e.fmt(f),
            Self::Limit(e) => e.fmt(f),
            Self::Read(e) => e.fmt(f),
            Self::Check(e) => e.fmt(f),
            Self::Binding(e) => e.fmt(f),
            Self::Contract(message) => f.write_str(message),
            Self::Model(e) => e.fmt(f),
            Self::Unsupported { .. } => {
                f.write_str("statement requires a controlled execution service")
            }
        }
    }
}
impl std::error::Error for StatementExecutionError {}
macro_rules! from_error {
    ($ty:ty, $variant:ident) => {
        impl From<$ty> for StatementExecutionError {
            fn from(e: $ty) -> Self {
                Self::$variant(e)
            }
        }
    };
}
from_error!(StatementTypeError, Type);
from_error!(StatementLimitError, StatementLimit);
from_error!(LimitExceeded, Limit);
from_error!(ReadExecutionError, Read);
from_error!(CheckExecutionError, Check);
from_error!(BindingError, Binding);
from_error!(ModelError, Model);

// Also runs when an awaiting caller drops this future. Preserve caller frames,
// completed results and charged work; never leak an iterator/check/local frame.
struct RestoreScopes<'a, C: RuntimeClock> {
    ctx: &'a mut ExecutionContext<C>,
    frames: usize,
    depth: usize,
}
impl<C: RuntimeClock> Drop for RestoreScopes<'_, C> {
    fn drop(&mut self) {
        while self.ctx.frame_count() > self.frames {
            let _ = self.ctx.pop_frame();
        }
        self.ctx.foreach_depth = self.depth;
    }
}

struct Iteration<'a> {
    body: &'a [Statement],
    iterator: &'a str,
    collection: ValueEnvelope,
    index: usize,
}
enum Step<'a> {
    Block(&'a [Statement], usize),
    Execute(&'a Statement),
    Finish(Option<String>),
    Iterate(Box<Iteration<'a>>),
    PopFrame,
    LeaveForeach,
}

impl ReadEvaluator<'_> {
    pub async fn execute_block<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        statements: &[Statement],
    ) -> Result<(), StatementExecutionError> {
        self.run_statements(ctx, vec![Step::Block(statements, 0)])
            .await
    }

    /// The component entry omits the enclosing block's deadline/node event,
    /// matching the Oracle's direct _execute_foreach entry.
    pub async fn execute_foreach<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        statement: &Statement,
    ) -> Result<(), StatementExecutionError> {
        if !matches!(statement.kind, StatementKind::Foreach { .. }) {
            return Err(StatementExecutionError::Unsupported {
                node_id: statement.node_id.clone(),
            });
        }
        self.run_statements(ctx, vec![Step::Execute(statement)])
            .await
    }

    async fn run_statements<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        mut steps: Vec<Step<'_>>,
    ) -> Result<(), StatementExecutionError> {
        let scopes = RestoreScopes {
            frames: ctx.frame_count(),
            depth: ctx.foreach_depth,
            ctx,
        };
        let ctx = &mut *scopes.ctx;
        while let Some(step) = steps.pop() {
            match step {
                Step::Block(statements, index) => {
                    if let Some(statement) = statements.get(index) {
                        ctx.guard().check_deadline()?;
                        let previous = ctx.current_node_id.replace(statement.node_id.clone());
                        steps.push(Step::Block(statements, index + 1));
                        steps.push(Step::Finish(previous));
                        steps.push(Step::Execute(statement));
                    }
                }
                Step::Finish(previous) => ctx.current_node_id = previous,
                Step::Execute(statement) => match &statement.kind {
                    StatementKind::Let {
                        target_symbol_id,
                        value,
                    } => self.execute_let(ctx, target_symbol_id, value).await?,
                    StatementKind::Check { .. } => self.execute_check(ctx, statement).await?,
                    StatementKind::Emit { .. } => self.execute_emit(ctx, statement).await?,
                    StatementKind::Approval(_) => {
                        return Err(StatementExecutionError::Unsupported {
                            node_id: statement.node_id.clone(),
                        });
                    }
                    StatementKind::Foreach {
                        iterator_symbol_id,
                        collection,
                        max_iterations,
                        body,
                    } => {
                        if ctx.foreach_depth >= MAX_FOREACH_NESTING_DEPTH {
                            return Err(StatementLimitError {
                                node_id: statement.node_id.clone(),
                                kind: StatementLimitKind::ForeachDepth,
                            }
                            .into());
                        }
                        ctx.foreach_depth += 1;
                        let collection = self.evaluate(ctx, collection).await?;
                        let mismatch = || StatementTypeError {
                            node_id: statement.node_id.clone(),
                            target: StatementTypeTarget::ForeachCollection,
                        };
                        let (TypeKind::List(_), Value::List(values)) =
                            (collection.type_ref().kind(), collection.value())
                        else {
                            return Err(mismatch().into());
                        };
                        if bigint(&integer(values.len())) > bigint(max_iterations) {
                            return Err(StatementLimitError {
                                node_id: statement.node_id.clone(),
                                kind: StatementLimitKind::ForeachIterations(max_iterations.clone()),
                            }
                            .into());
                        }
                        if !collection.type_ref().accepts(collection.value()) {
                            return Err(mismatch().into());
                        }
                        steps.push(Step::LeaveForeach);
                        steps.push(Step::Iterate(Box::new(Iteration {
                            body,
                            iterator: iterator_symbol_id,
                            collection,
                            index: 0,
                        })));
                    }
                },
                Step::Iterate(mut iteration) => {
                    let (TypeKind::List(item_type), Value::List(values)) = (
                        iteration.collection.type_ref().kind(),
                        iteration.collection.value(),
                    ) else {
                        unreachable!(
                            "private iteration is constructed only after full List validation"
                        )
                    };
                    if let Some(value) = values.get(iteration.index) {
                        ctx.guard_mut().before_loop_iteration()?;
                        ctx.push_frame();
                        ctx.bind(
                            BindingNamespace::Variable,
                            iteration.iterator.into(),
                            ValueEnvelope::new(
                                value.clone(),
                                (**item_type).clone(),
                                Presence::Present,
                                iteration.collection.completeness(),
                                iteration.collection.classification(),
                                iteration.collection.provenance_refs().to_vec(),
                            ),
                        )?;
                        iteration.index += 1;
                        let body = iteration.body;
                        steps.push(Step::Iterate(iteration));
                        steps.push(Step::PopFrame);
                        steps.push(Step::Block(body, 0));
                    }
                }
                Step::PopFrame => ctx.pop_frame()?,
                Step::LeaveForeach => ctx.foreach_depth -= 1,
            }
        }
        Ok(())
    }

    pub async fn execute_emit<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        statement: &Statement,
    ) -> Result<(), StatementExecutionError> {
        let StatementKind::Emit { fields } = &statement.kind else {
            return Err(StatementExecutionError::Unsupported {
                node_id: statement.node_id.clone(),
            });
        };
        ctx.guard().before_emit()?;
        let outputs = ctx.skill().outputs.clone();
        let declared = outputs
            .iter()
            .map(|o| (o.name.as_str(), o))
            .collect::<BTreeMap<_, _>>();
        let names = fields
            .iter()
            .map(|f| f.name.as_str())
            .collect::<BTreeSet<_>>();
        if declared.len() != outputs.len()
            || fields.len() != declared.len()
            || fields.len() != names.len()
            || names != declared.keys().copied().collect()
        {
            return Err(StatementExecutionError::Contract(
                "EMIT fields do not match output schema".into(),
            ));
        }
        let mut evaluated = BTreeMap::new();
        for field in fields {
            let output = declared[field.name.as_str()];
            if field.value.type_info != output.type_info {
                return Err(StatementTypeError {
                    node_id: statement.node_id.clone(),
                    target: StatementTypeTarget::EmitDeclaration(field.name.clone()),
                }
                .into());
            }
            let value = self.evaluate(ctx, &field.value).await?;
            if !output.type_info.accepts(value.value()) {
                return Err(StatementTypeError {
                    node_id: statement.node_id.clone(),
                    target: StatementTypeTarget::EmitValue(field.name.clone()),
                }
                .into());
            }
            if !output.classification.allows(value.classification()) {
                return Err(StatementExecutionError::Contract(format!(
                    "EMIT classification exceeds output schema: {}",
                    field.name
                )));
            }
            evaluated.insert(field.name.clone(), value);
        }
        let fields = outputs
            .into_iter()
            .map(|o| {
                (
                    o.name.clone(),
                    evaluated.remove(&o.name).expect("validated field name set"),
                )
            })
            .collect();
        ctx.outputs.push(EmitRecord::new(fields)?);
        ctx.guard_mut().record_emit();
        Ok(())
    }
}
