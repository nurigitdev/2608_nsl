use super::{
    ApprovalSymbol, ScopeId, ScopeKind, SymbolBinding, SymbolDeclaration, SymbolId,
    SymbolReference, SymbolResolution, SymbolScope,
};
use nsl_source::SourceSpan;
use nsl_source::ast::StatementId;
use std::collections::{HashMap, hash_map::Entry};

pub(crate) struct Frame<'ast> {
    pub(crate) scope: ScopeId,
    pub(crate) names: Vec<&'ast str>,
}

pub(crate) struct SymbolTable<'ast> {
    pub(crate) frames: Vec<Frame<'ast>>,
    pub(crate) active: HashMap<&'ast str, SymbolId>,
    pub(crate) scopes: Vec<SymbolScope>,
    pub(crate) bindings: Vec<SymbolBinding>,
}

impl<'ast> SymbolTable<'ast> {
    pub(crate) fn new() -> Self {
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

    pub(crate) fn enter_foreach(&mut self, statement: StatementId) {
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

    pub(crate) fn leave_foreach(&mut self) {
        let frame = self.frames.pop().expect("the foreach scope is active");
        for name in frame.names {
            self.active.remove(name);
        }
    }

    pub(crate) fn declare(
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

    pub(crate) fn resolve(&self, name: &str) -> Option<SymbolId> {
        self.active.get(name).copied()
    }
}

impl SymbolTable<'_> {
    pub(crate) fn finish(
        self,
        references: Vec<SymbolReference>,
        approvals: Vec<ApprovalSymbol>,
    ) -> SymbolResolution {
        SymbolResolution {
            bindings: self.bindings,
            scopes: self.scopes,
            references,
            approvals,
        }
    }
}
