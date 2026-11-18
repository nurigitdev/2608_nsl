use std::collections::{BTreeSet, HashMap};

use nsl_contract::{CatalogErrorKind, ToolContract, ToolContractCatalog};
use nsl_core::{DataClassification, ToolCapability, TypeKind, TypeRef};
use nsl_source::SourceLanguageVersion;
use nsl_source::ast::{
    AstLiteral, AstSkill, AstType, ExpressionId, ExpressionKind, StatementId, StatementKind,
};

use crate::symbols::{
    SymbolDeclaration, SymbolDiagnosticKind, SymbolId, SymbolReference, SymbolResolution,
    SymbolTable, collect_approvals,
};

use super::diagnostics::quoted_name;
use super::rules::{list, primitive};
use super::{
    ExpressionType, StaticTypeChecker, TypeCheckError, TypeDiagnosticKind::*, TypedSkill, ValueType,
};

impl StaticTypeChecker<'_> {
    /// Annotate a composed AST after successful symbol resolution.
    /// Approval linkage and resource/write safety are separate compiler stages.
    pub fn check(
        &self,
        skill: &AstSkill,
        resolution: &SymbolResolution,
        catalog: &ToolContractCatalog,
    ) -> Result<TypedSkill, TypeCheckError> {
        Ok(self.run(skill, Some(resolution), catalog)?.1)
    }

    pub(crate) fn analyze(
        &self,
        skill: &AstSkill,
        catalog: &ToolContractCatalog,
    ) -> Result<(SymbolResolution, TypedSkill), TypeCheckError> {
        let (symbols, types) = self.run(skill, None, catalog)?;
        Ok((symbols.expect("dynamic symbol analysis"), types))
    }

    pub(crate) fn required_contracts<'a>(
        &self,
        skill: &'a AstSkill,
        catalog: &'a ToolContractCatalog,
    ) -> Result<HashMap<&'a str, &'a ToolContract>, TypeCheckError> {
        let mut contracts = HashMap::new();
        for required in &skill.requires {
            if contracts.contains_key(required.tool_id.as_str()) {
                return Err(self.error(
                    DuplicateTool,
                    format!("duplicate required tool: {}", required.tool_id),
                    &required.span,
                ));
            }
            let contract = catalog
                .resolve(&required.tool_id, &required.version)
                .map_err(|error| {
                    let (kind, message) = if error.kind == CatalogErrorKind::IncompatibleToolVersion
                    {
                        (IncompatibleToolVersion, "incompatible tool version")
                    } else {
                        (UnknownToolContract, "unknown tool contract")
                    };
                    self.error(
                        kind,
                        format!("{message}: {}@{}", required.tool_id, required.version),
                        &required.span,
                    )
                })?;
            if skill.language_version == SourceLanguageVersion::V0_1
                && contract.capability().known() != Some(ToolCapability::Read)
            {
                return Err(self.error(
                    ReadCapability,
                    format!(
                        "{} tool capability is forbidden in v0.1: {}",
                        contract.capability(),
                        required.tool_id
                    ),
                    &required.span,
                ));
            }
            contracts.insert(required.tool_id.as_str(), contract);
        }
        Ok(contracts)
    }

    fn run(
        &self,
        skill: &AstSkill,
        resolution: Option<&SymbolResolution>,
        catalog: &ToolContractCatalog,
    ) -> Result<(Option<SymbolResolution>, TypedSkill), TypeCheckError> {
        let contracts = self.required_contracts(skill, catalog)?;
        let mut state = CheckState {
            checker: self,
            skill,
            contracts,
            result: TypedSkill::default(),
            table: resolution.is_none().then(SymbolTable::new),
            dynamic_references: Vec::new(),
            declarations: resolution
                .into_iter()
                .flat_map(|result| result.bindings())
                .map(|binding| (binding.declaration, binding.id))
                .collect(),
            references: resolution
                .into_iter()
                .flat_map(|result| result.references())
                .map(|reference| (reference.expression, reference.symbol))
                .collect(),
        };
        for (index, field) in skill.inputs.iter().enumerate() {
            state.bind(
                SymbolDeclaration::Input(index),
                ValueType {
                    type_info: ast_type(&field.type_info),
                    classification: field.classification,
                },
            )?;
        }
        for (index, field) in skill.contexts.iter().enumerate() {
            state.bind(
                SymbolDeclaration::Context(index),
                ValueType {
                    type_info: ast_type(&field.type_info),
                    classification: field.classification,
                },
            )?;
        }
        for field in &skill.outputs {
            if skill
                .outputs
                .iter()
                .filter(|other| other.name == field.name)
                .count()
                > 1
            {
                return Err(self.error(
                    EmitSchema,
                    format!("duplicate output field: {}", field.name),
                    &field.span,
                ));
            }
        }
        let mut pending = skill
            .body
            .iter()
            .rev()
            .copied()
            .map(StatementTask::Visit)
            .collect::<Vec<_>>();
        while let Some(task) = pending.pop() {
            let id = match task {
                StatementTask::Visit(id) => id,
                StatementTask::Leave => {
                    if let Some(table) = &mut state.table {
                        table.leave_foreach();
                    }
                    continue;
                }
            };
            let statement = skill
                .statement(id)
                .ok_or(TypeCheckError::InvalidResolution)?;
            match &statement.kind {
                StatementKind::Let { value, .. } => {
                    let value = state.expression(*value)?;
                    state.bind(SymbolDeclaration::Let(id), value)?;
                }
                StatementKind::Foreach {
                    collection, body, ..
                } => {
                    let value = state.expression(*collection)?;
                    let span = &skill
                        .expression(*collection)
                        .ok_or(TypeCheckError::InvalidResolution)?
                        .span;
                    let item = self.require_list(&value.type_info, span)?;
                    if let Some(table) = &mut state.table {
                        table.enter_foreach(id);
                    }
                    state.bind(
                        SymbolDeclaration::Iterator(id),
                        ValueType {
                            type_info: item,
                            classification: value.classification,
                        },
                    )?;
                    pending.push(StatementTask::Leave);
                    pending.extend(body.iter().rev().copied().map(StatementTask::Visit));
                }
                StatementKind::Check { condition, .. } => {
                    let value = state.expression(*condition)?;
                    self.require_bool(
                        &value.type_info,
                        &skill
                            .expression(*condition)
                            .ok_or(TypeCheckError::InvalidResolution)?
                            .span,
                    )?;
                    let status = TypeRef::new(TypeKind::Enum {
                        name: "CheckStatus".into(),
                        values: ["PASS", "FAIL", "UNKNOWN"].map(String::from).into(),
                    })
                    .expect("check status type");
                    let type_info = TypeRef::new(TypeKind::Record {
                        name: "CheckResult".into(),
                        fields: vec![("status".into(), status)],
                    })
                    .expect("check result type");
                    state.bind(
                        SymbolDeclaration::Check(id),
                        ValueType {
                            type_info,
                            classification: DataClassification::Internal,
                        },
                    )?;
                }
                StatementKind::Emit { fields } => {
                    let names = fields
                        .iter()
                        .map(|(name, _)| name.as_str())
                        .collect::<BTreeSet<_>>();
                    let expected = skill
                        .outputs
                        .iter()
                        .map(|field| field.name.as_str())
                        .collect::<BTreeSet<_>>();
                    if names != expected || names.len() != fields.len() {
                        return Err(self.error(
                            EmitSchema,
                            "EMIT fields must exactly match output schema",
                            &statement.span,
                        ));
                    }
                    for (name, expression) in fields {
                        let value = state.expression(*expression)?;
                        let output = skill
                            .outputs
                            .iter()
                            .find(|field| &field.name == name)
                            .ok_or(TypeCheckError::InvalidResolution)?;
                        let span = &skill
                            .expression(*expression)
                            .ok_or(TypeCheckError::InvalidResolution)?
                            .span;
                        if value.type_info != ast_type(&output.type_info) {
                            return Err(self.error(
                                OutputType,
                                format!("output type mismatch for {name}"),
                                span,
                            ));
                        }
                        if !output.classification.allows(value.classification) {
                            return Err(self.error(
                                OutputClassification,
                                format!("output classification for {name} is too weak"),
                                span,
                            ));
                        }
                    }
                }
                StatementKind::Approve { .. } => {}
            }
        }
        let symbols = if let Some(table) = state.table {
            Some(table.finish(
                state.dynamic_references,
                collect_approvals(skill, &self.diagnostics).map_err(TypeCheckError::Symbol)?,
            ))
        } else {
            None
        };
        Ok((symbols, state.result))
    }
}

pub(crate) fn ast_type(ast: &AstType) -> TypeRef {
    (0..ast.list_depth).fold(ast.item.clone(), |item, _| list(item))
}

enum StatementTask {
    Visit(StatementId),
    Leave,
}

struct CheckState<'a, 'source> {
    table: Option<SymbolTable<'a>>,
    dynamic_references: Vec<SymbolReference>,
    checker: &'a StaticTypeChecker<'source>,
    skill: &'a AstSkill,
    contracts: HashMap<&'a str, &'a ToolContract>,
    declarations: HashMap<SymbolDeclaration, SymbolId>,
    references: HashMap<ExpressionId, SymbolId>,
    result: TypedSkill,
}

enum ExpressionTask<'a> {
    Visit(ExpressionId),
    Finish(ExpressionId),
    Argument {
        id: ExpressionId,
        tool: &'a str,
        name: &'a str,
        expected: &'a TypeRef,
    },
}

impl CheckState<'_, '_> {
    fn bind(
        &mut self,
        declaration: SymbolDeclaration,
        value: ValueType,
    ) -> Result<(), TypeCheckError> {
        if let Some(table) = &mut self.table {
            let (name, span) = match declaration {
                SymbolDeclaration::Input(index) => {
                    let field = self
                        .skill
                        .inputs
                        .get(index)
                        .ok_or(TypeCheckError::InvalidResolution)?;
                    (field.name.as_str(), &field.span)
                }
                SymbolDeclaration::Context(index) => {
                    let field = self
                        .skill
                        .contexts
                        .get(index)
                        .ok_or(TypeCheckError::InvalidResolution)?;
                    (field.name.as_str(), &field.span)
                }
                SymbolDeclaration::Let(id)
                | SymbolDeclaration::Iterator(id)
                | SymbolDeclaration::Check(id) => {
                    let node = self
                        .skill
                        .statement(id)
                        .ok_or(TypeCheckError::InvalidResolution)?;
                    let name = match &node.kind {
                        StatementKind::Let { name, .. } => name,
                        StatementKind::Foreach { iterator, .. } => iterator,
                        StatementKind::Check { check_id, .. } => check_id,
                        _ => return Err(TypeCheckError::InvalidResolution),
                    };
                    (name.as_str(), &node.span)
                }
            };
            if !table.declare(name, declaration, span) {
                return Err(TypeCheckError::Symbol(self.checker.diagnostics.error(
                    SymbolDiagnosticKind::DuplicateSymbol,
                    name,
                    span,
                )));
            }
            self.declarations.insert(
                declaration,
                table.bindings.last().expect("declared symbol").id,
            );
        }
        let id = self
            .declarations
            .get(&declaration)
            .ok_or(TypeCheckError::InvalidResolution)?;
        self.result.symbols.insert(*id, value);
        Ok(())
    }

    fn value(&self, id: ExpressionId) -> Result<&ValueType, TypeCheckError> {
        self.result
            .expression(id)
            .map(|expression| &expression.value)
            .ok_or(TypeCheckError::InvalidResolution)
    }

    fn expression(&mut self, root: ExpressionId) -> Result<ValueType, TypeCheckError> {
        use ExpressionTask::*;
        let mut pending = vec![Visit(root)];
        while let Some(task) = pending.pop() {
            let id = match task {
                Visit(id) | Finish(id) | Argument { id, .. } => id,
            };
            let expression = self
                .skill
                .expression(id)
                .ok_or(TypeCheckError::InvalidResolution)?;
            let mut fields = Vec::new();
            let value = match task {
                Argument {
                    tool,
                    name,
                    expected,
                    ..
                } => {
                    if &self.value(id)?.type_info != expected {
                        return Err(self.checker.error(
                            ToolArgumentType,
                            format!("tool argument type mismatch: {tool}.{name}"),
                            &expression.span,
                        ));
                    }
                    continue;
                }
                Visit(_) => match &expression.kind {
                    ExpressionKind::Literal(literal) => ValueType {
                        type_info: primitive(match literal {
                            AstLiteral::Int(_) => "Int",
                            AstLiteral::Decimal(_) => "Decimal",
                            AstLiteral::String(_) => "String",
                            AstLiteral::Bool(_) => "Bool",
                        }),
                        classification: DataClassification::Public,
                    },
                    ExpressionKind::Path(parts) => {
                        if let Some(table) = &self.table {
                            let name = parts.first().ok_or(TypeCheckError::InvalidResolution)?;
                            let symbol = table.resolve(name).ok_or_else(|| {
                                TypeCheckError::Symbol(self.checker.diagnostics.error(
                                    SymbolDiagnosticKind::UnknownIdentifier,
                                    name,
                                    &expression.span,
                                ))
                            })?;
                            self.references.insert(id, symbol);
                            self.dynamic_references.push(SymbolReference {
                                expression: id,
                                symbol,
                                span: expression.span.clone(),
                            });
                        }
                        let symbol = self
                            .references
                            .get(&id)
                            .ok_or(TypeCheckError::InvalidResolution)?;
                        let mut value = self
                            .result
                            .symbol(*symbol)
                            .ok_or(TypeCheckError::InvalidResolution)?
                            .clone();
                        for field in parts.iter().skip(1) {
                            let resolved = self.checker.field_result(
                                &value.type_info,
                                field,
                                &expression.span,
                            )?;
                            value.type_info = resolved.type_info.clone();
                            fields.push(resolved);
                        }
                        value
                    }
                    ExpressionKind::Binary { left, right, .. } => {
                        pending.extend([Finish(id), Visit(*right), Visit(*left)]);
                        continue;
                    }
                    ExpressionKind::Call { arguments, .. } => {
                        pending.push(Finish(id));
                        pending.extend(arguments.iter().rev().copied().map(Visit));
                        continue;
                    }
                    ExpressionKind::Read { tool_id, arguments }
                    | ExpressionKind::Write {
                        tool_id, arguments, ..
                    } => {
                        let contract = self.contracts.get(tool_id.as_str()).ok_or_else(|| {
                            self.checker.error(
                                UndeclaredTool,
                                format!("tool not declared in requires: {tool_id}"),
                                &expression.span,
                            )
                        })?;
                        if matches!(expression.kind, ExpressionKind::Read { .. })
                            && contract.capability().known() != Some(ToolCapability::Read)
                        {
                            return Err(self.checker.error(
                                ReadCapability,
                                format!("READ expression requires a READ tool contract: {tool_id}"),
                                &expression.span,
                            ));
                        }
                        let expected = contract
                            .input_types()
                            .iter()
                            .map(|(name, _)| name.as_str())
                            .collect::<BTreeSet<_>>();
                        let actual = arguments
                            .iter()
                            .map(|(name, _)| name.as_str())
                            .collect::<BTreeSet<_>>();
                        if expected != actual || actual.len() != arguments.len() {
                            let names = expected
                                .iter()
                                .map(|name| quoted_name(name))
                                .collect::<Vec<_>>()
                                .join(", ");
                            return Err(self.checker.error(
                                ToolArguments,
                                format!("tool arguments for {tool_id} must be [{names}]"),
                                &expression.span,
                            ));
                        }
                        pending.push(Finish(id));
                        for (name, child) in arguments.iter().rev() {
                            let expected = contract
                                .input_type(name)
                                .ok_or(TypeCheckError::InvalidResolution)?;
                            pending.push(Argument {
                                id: *child,
                                tool: tool_id,
                                name,
                                expected,
                            });
                            pending.push(Visit(*child));
                        }
                        continue;
                    }
                },
                Finish(_) => match &expression.kind {
                    ExpressionKind::Binary {
                        operator,
                        left,
                        right,
                    } => {
                        let left = self.value(*left)?;
                        let right = self.value(*right)?;
                        ValueType {
                            type_info: self.checker.binary_result(
                                *operator,
                                &left.type_info,
                                &right.type_info,
                                &expression.span,
                            )?,
                            classification: DataClassification::highest([
                                left.classification,
                                right.classification,
                            ])
                            .expect("two classifications"),
                        }
                    }
                    ExpressionKind::Call { name, arguments } => {
                        let types = arguments
                            .iter()
                            .map(|id| self.value(*id).map(|value| value.type_info.clone()))
                            .collect::<Result<Vec<_>, _>>()?;
                        let type_info =
                            self.checker
                                .builtin_result(name, &types, &expression.span)?;
                        ValueType {
                            type_info,
                            classification: self.value(arguments[0])?.classification,
                        }
                    }
                    ExpressionKind::Read { tool_id, .. }
                    | ExpressionKind::Write { tool_id, .. } => {
                        let contract = self.contracts[tool_id.as_str()];
                        ValueType {
                            type_info: contract.output_type().clone(),
                            classification: contract.output_classification(),
                        }
                    }
                    _ => return Err(TypeCheckError::InvalidResolution),
                },
            };
            self.result
                .expressions
                .insert(id, ExpressionType { value, fields });
        }
        self.value(root).cloned()
    }
}
