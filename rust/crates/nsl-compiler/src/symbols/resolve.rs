use std::collections::{HashMap, HashSet, hash_map::Entry};

use nsl_source::ast::{AstSkill, ExpressionId, ExpressionKind, StatementId, StatementKind};
use nsl_source::{SourceFile, SourceLanguageVersion, SourceSpan};

use super::diagnostics::SourceDiagnosticContext;
use super::{
    ApprovalSymbol, ApprovalSymbolId, ScopeId, ScopeKind, SymbolBinding, SymbolDeclaration,
    SymbolDiagnosticKind, SymbolId, SymbolReference, SymbolResolution, SymbolResolutionError,
    SymbolScope,
};

/// Resolve a composed skill. Source files provide diagnostic locations and snippets.
pub fn resolve_symbols<'source>(
    skill: &AstSkill,
    sources: impl IntoIterator<Item = &'source SourceFile>,
) -> Result<SymbolResolution, SymbolResolutionError> {
    let diagnostics = SourceDiagnosticContext::new(sources);
    let approvals = collect_approvals(skill, &diagnostics)?;
    let mut resolver = Resolver {
        skill,
        diagnostics,
        table: SymbolTable::new(),
        references: Vec::new(),
    };
    for (index, input) in skill.inputs.iter().enumerate() {
        resolver.declare(&input.name, SymbolDeclaration::Input(index), &input.span)?;
    }
    for (index, context) in skill.contexts.iter().enumerate() {
        resolver.declare(
            &context.name,
            SymbolDeclaration::Context(index),
            &context.span,
        )?;
    }
    let body_result = resolver.walk_body();
    while resolver.table.frames.len() > 1 {
        resolver.table.leave_foreach();
    }
    body_result?;
    Ok(SymbolResolution {
        bindings: resolver.table.bindings,
        scopes: resolver.table.scopes,
        references: resolver.references,
        approvals,
    })
}

fn collect_approvals(
    skill: &AstSkill,
    diagnostics: &SourceDiagnosticContext<'_>,
) -> Result<Vec<ApprovalSymbol>, SymbolResolutionError> {
    let mut approvals = Vec::new();
    if skill.language_version == SourceLanguageVersion::V0_2 {
        let mut names = HashSet::new();
        for &id in &skill.body {
            let node = skill
                .statement(id)
                .ok_or(SymbolResolutionError::InvalidStatement(id))?;
            if let StatementKind::Approve {
                approval_id,
                approval_id_span,
                ..
            } = &node.kind
            {
                if !names.insert(approval_id) {
                    return Err(diagnostics.error(
                        SymbolDiagnosticKind::DuplicateApproval,
                        approval_id,
                        approval_id_span,
                    ));
                }
                approvals.push(ApprovalSymbol {
                    id: ApprovalSymbolId(approvals.len() + 1),
                    name: approval_id.clone(),
                    statement: id,
                    span: approval_id_span.clone(),
                });
            }
        }
    }
    Ok(approvals)
}

struct Frame<'ast> {
    scope: ScopeId,
    names: Vec<&'ast str>,
}

struct SymbolTable<'ast> {
    frames: Vec<Frame<'ast>>,
    active: HashMap<&'ast str, SymbolId>,
    scopes: Vec<SymbolScope>,
    bindings: Vec<SymbolBinding>,
}

impl<'ast> SymbolTable<'ast> {
    fn new() -> Self {
        let id = ScopeId(1);
        Self {
            frames: vec![Frame {
                scope: id,
                names: Vec::new(),
            }],
            active: HashMap::new(),
            scopes: vec![SymbolScope {
                id,
                parent_id: None,
                kind: ScopeKind::Skill,
            }],
            bindings: Vec::new(),
        }
    }

    fn enter_foreach(&mut self, statement: StatementId) {
        let id = ScopeId(self.scopes.len() + 1);
        let parent_id = Some(self.frames.last().expect("the root scope is active").scope);
        self.scopes.push(SymbolScope {
            id,
            parent_id,
            kind: ScopeKind::Foreach(statement),
        });
        self.frames.push(Frame {
            scope: id,
            names: Vec::new(),
        });
    }

    fn leave_foreach(&mut self) {
        let frame = self.frames.pop().expect("the foreach scope is active");
        for name in frame.names {
            self.active.remove(name);
        }
    }

    fn declare(
        &mut self,
        name: &'ast str,
        declaration: SymbolDeclaration,
        span: &SourceSpan,
    ) -> bool {
        let Entry::Vacant(slot) = self.active.entry(name) else {
            return false;
        };
        let frame = self.frames.last_mut().expect("the root scope is active");
        let id = SymbolId(self.bindings.len() + 1);
        slot.insert(id);
        frame.names.push(name);
        self.bindings.push(SymbolBinding {
            id,
            name: name.into(),
            scope_id: frame.scope,
            declaration,
            span: span.clone(),
        });
        true
    }

    fn resolve(&self, name: &str) -> Option<SymbolId> {
        self.active.get(name).copied()
    }
}

enum Visit {
    Statement(StatementId),
    LeaveForeach,
}

struct Resolver<'ast, 'source> {
    skill: &'ast AstSkill,
    diagnostics: SourceDiagnosticContext<'source>,
    table: SymbolTable<'ast>,
    references: Vec<SymbolReference>,
}

impl<'ast> Resolver<'ast, '_> {
    fn declare(
        &mut self,
        name: &'ast str,
        declaration: SymbolDeclaration,
        span: &SourceSpan,
    ) -> Result<(), SymbolResolutionError> {
        if self.table.declare(name, declaration, span) {
            return Ok(());
        }
        Err(self
            .diagnostics
            .error(SymbolDiagnosticKind::DuplicateSymbol, name, span))
    }

    fn walk_body(&mut self) -> Result<(), SymbolResolutionError> {
        let mut pending: Vec<_> = self
            .skill
            .body
            .iter()
            .rev()
            .copied()
            .map(Visit::Statement)
            .collect();
        while let Some(visit) = pending.pop() {
            let id = match visit {
                Visit::Statement(id) => id,
                Visit::LeaveForeach => {
                    self.table.leave_foreach();
                    continue;
                }
            };
            let node = self
                .skill
                .statement(id)
                .ok_or(SymbolResolutionError::InvalidStatement(id))?;
            match &node.kind {
                StatementKind::Let { name, value } => {
                    self.walk_expression(*value)?;
                    self.declare(name, SymbolDeclaration::Let(id), &node.span)?;
                }
                StatementKind::Foreach {
                    iterator,
                    collection,
                    body,
                    ..
                } => {
                    self.walk_expression(*collection)?;
                    self.table.enter_foreach(id);
                    self.declare(iterator, SymbolDeclaration::Iterator(id), &node.span)?;
                    pending.push(Visit::LeaveForeach);
                    pending.extend(body.iter().rev().copied().map(Visit::Statement));
                }
                StatementKind::Check {
                    check_id,
                    condition,
                    ..
                } => {
                    self.walk_expression(*condition)?;
                    self.declare(check_id, SymbolDeclaration::Check(id), &node.span)?;
                }
                StatementKind::Emit { fields } => {
                    for (_, value) in fields {
                        self.walk_expression(*value)?;
                    }
                }
                StatementKind::Approve { .. } => {}
            }
        }
        Ok(())
    }

    fn walk_expression(&mut self, root: ExpressionId) -> Result<(), SymbolResolutionError> {
        let mut pending = vec![root];
        while let Some(id) = pending.pop() {
            let node = self
                .skill
                .expression(id)
                .ok_or(SymbolResolutionError::InvalidExpression(id))?;
            match &node.kind {
                ExpressionKind::Literal(_) => {}
                ExpressionKind::Path(parts) => {
                    let name = parts
                        .first()
                        .ok_or(SymbolResolutionError::InvalidExpression(id))?;
                    let symbol = self.table.resolve(name).ok_or_else(|| {
                        self.diagnostics.error(
                            SymbolDiagnosticKind::UnknownIdentifier,
                            name,
                            &node.span,
                        )
                    })?;
                    self.references.push(SymbolReference {
                        expression: id,
                        symbol,
                        span: node.span.clone(),
                    });
                }
                ExpressionKind::Call { arguments, .. } => {
                    pending.extend(arguments.iter().rev().copied());
                }
                ExpressionKind::Read { arguments, .. }
                | ExpressionKind::Write { arguments, .. } => {
                    pending.extend(arguments.iter().rev().map(|(_, value)| *value));
                }
                ExpressionKind::Binary { left, right, .. } => {
                    pending.extend([*right, *left]);
                }
            }
        }
        Ok(())
    }
}
