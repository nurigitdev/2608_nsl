use super::{
    BoundaryError, RuntimeBindingLocation, RuntimeEngine, RuntimeInputTypeError,
    RuntimeTerminalKind,
};
use crate::*;
use nsl_contract::CatalogErrorKind;
use nsl_core::{ToolCapability, Value, ValueEnvelope};
use nsl_ir::{ExpressionKind, IrVersion, SkillObject, StatementKind};

pub(super) fn controlled_prefix_end(skill: &SkillObject) -> Result<usize, &'static str> {
    if (skill.ir_version, skill.language_version.as_str()) != (IrVersion::V1_1, "0.2") {
        return Err("controlled execution requires NSL 0.2 and IR 1.1");
    }
    let approvals = skill
        .body
        .iter()
        .enumerate()
        .filter_map(|(i, s)| {
            if let StatementKind::Approval(a) = &s.kind {
                Some((i, a))
            } else {
                None
            }
        })
        .collect::<Vec<_>>();
    let writes = skill
        .body
        .iter()
        .enumerate()
        .filter_map(|(i, s)| {
            if let StatementKind::Let { value, .. } = &s.kind
                && let ExpressionKind::Write(w) = &value.kind
            {
                return Some((i, w));
            }
            None
        })
        .collect::<Vec<_>>();
    if approvals.len() != 1 || writes.len() != 1 {
        return Err("controlled execution requires exactly one top-level Approval and WRITE");
    }
    if approvals[0].0 >= writes[0].0 || approvals[0].1.approval_id != writes[0].1.approval_ref {
        return Err("controlled execution Approval/WRITE binding is invalid");
    }
    Ok(approvals[0].0)
}
fn controlled(skill: &SkillObject) -> bool {
    let mut statements = skill.body.iter().collect::<Vec<_>>();
    let mut expressions = vec![];
    while let Some(s) = statements.pop() {
        match &s.kind {
            StatementKind::Approval(_) => return true,
            StatementKind::Let { value, .. } => expressions.push(value),
            StatementKind::Foreach {
                collection, body, ..
            } => {
                expressions.push(collection);
                statements.extend(body);
            }
            StatementKind::Check { condition, .. } => expressions.push(condition),
            StatementKind::Emit { fields } => expressions.extend(fields.iter().map(|f| &f.value)),
        }
    }
    while let Some(e) = expressions.pop() {
        match &e.kind {
            ExpressionKind::Write(_) => return true,
            ExpressionKind::Field { source, .. } | ExpressionKind::Projection { source, .. } => {
                expressions.push(source)
            }
            ExpressionKind::Binary { left, right, .. } => {
                expressions.extend([left.as_ref(), right.as_ref()])
            }
            ExpressionKind::Call { arguments, .. } => expressions.extend(arguments),
            ExpressionKind::Read { arguments, .. } => {
                expressions.extend(arguments.iter().map(|f| &f.value))
            }
            _ => {}
        }
    }
    false
}
fn contract(message: impl Into<String>) -> BoundaryError {
    StatementExecutionError::Contract(message.into()).into()
}
impl RuntimeEngine<'_> {
    pub(super) fn preflight(&self, skill: &SkillObject, prefix: bool) -> Result<(), BoundaryError> {
        let profile = match (skill.ir_version, skill.language_version.as_str()) {
            (IrVersion::V1_0, "0.1") => "NSL-0.1-STRICT",
            (IrVersion::V1_1, "0.2") => "NSL-0.2-STRICT",
            _ => return Err(contract("unsupported IR or language version")),
        };
        if skill.semantics_profile != profile {
            return Err(contract("unsupported semantics profile"));
        }
        if skill
            .compute_semantic_sha256_with_limits(self.options.nso_limits)
            .map_err(RuntimeTerminalKind::Nso)?
            != skill.semantic_hash
        {
            return Err(contract("semantic hash mismatch"));
        }
        if !skill.analysis.bounded {
            return Err(contract("unbounded skill"));
        }
        if controlled(skill) && !prefix {
            return Err(contract(
                "Approval and WRITE require the controlled v0.2 execution pipeline",
            ));
        }
        for required in &skill.required_tools {
            let actual = self
                .catalog
                .resolve(&required.tool_id, &required.version)
                .map_err(|e| {
                    contract(format!(
                        "'{}: {}@{}'",
                        if e.kind == CatalogErrorKind::IncompatibleToolVersion {
                            "incompatible tool version"
                        } else {
                            "unknown tool contract"
                        },
                        e.tool_id,
                        e.version
                    ))
                })?;
            if actual
                .contract_hash()
                .map_err(RuntimeTerminalKind::ContractEncoding)?
                != required.contract_hash
            {
                return Err(contract(format!(
                    "tool contract mismatch: {}",
                    required.tool_id
                )));
            }
            if actual.capability().as_str() != required.kind.capability().as_str()
                || actual.required_scope() != required.required_scope
            {
                return Err(contract(format!(
                    "tool contract metadata mismatch: {}",
                    required.tool_id
                )));
            }
            if required.kind.capability() != ToolCapability::Read
                && !(prefix && required.kind.capability() == ToolCapability::Write)
            {
                return Err(contract("WRITE capability is forbidden"));
            }
        }
        Ok(())
    }
}
pub(super) fn bind<C: RuntimeClock>(ctx: &mut ExecutionContext<C>) -> Result<(), BoundaryError> {
    let mut bindings = vec![];
    for spec in ctx.skill().inputs.clone() {
        let Some(value) = ctx.request().inputs().get(&spec.name).cloned() else {
            return Err(if spec.required {
                contract(format!("missing required input: {}", spec.name))
            } else {
                StatementExecutionError::Read(ReadExecutionError::Expression(
                    ExpressionRuntimeError::Unexpected {
                        kind: "KeyError",
                        detail: "missing optional input".into(),
                    },
                ))
                .into()
            });
        };
        if !spec.type_info.accepts(&value) {
            return Err(RuntimeTerminalKind::InputType(RuntimeInputTypeError {
                symbol_id: spec.symbol_id,
                location: RuntimeBindingLocation::Input(spec.name),
            })
            .into());
        }
        if let Value::List(values) = &value {
            ctx.guard_mut()
                .observe_collection(crate::data::integer(values.len()))
                .map_err(StatementExecutionError::from)?;
        }
        bindings.push((
            BindingNamespace::Input,
            spec.symbol_id,
            ValueEnvelope::complete(value, spec.type_info, spec.classification),
        ));
    }
    let root = Value::Record(ctx.request().runtime_context().clone());
    for spec in ctx.skill().contexts.clone() {
        let mut value = &root;
        for part in &spec.path {
            let Some(next) = (match value {
                Value::Record(fields) => fields.get(part),
                _ => None,
            }) else {
                return Err(contract(format!(
                    "missing runtime context path: {}",
                    spec.path.join(".")
                )));
            };
            value = next;
        }
        if !spec.type_info.accepts(value) {
            return Err(RuntimeTerminalKind::InputType(RuntimeInputTypeError {
                symbol_id: spec.symbol_id,
                location: RuntimeBindingLocation::Context(spec.path),
            })
            .into());
        }
        if let Value::List(values) = value {
            ctx.guard_mut()
                .observe_collection(crate::data::integer(values.len()))
                .map_err(StatementExecutionError::from)?;
        }
        bindings.push((
            BindingNamespace::Context,
            spec.symbol_id,
            ValueEnvelope::complete(value.clone(), spec.type_info, spec.classification),
        ));
    }
    for (namespace, symbol, value) in bindings {
        ctx.bind(namespace, symbol, value)
            .map_err(StatementExecutionError::from)?;
    }
    Ok(())
}
