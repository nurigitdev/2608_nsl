//! Resolve the names in a composed AST in the frozen Oracle's allocation order.

mod approvals;
mod diagnostics;
mod resolve;
mod table;
pub(crate) use resolve::collect_approvals;
pub(crate) use table::SymbolTable;

use std::fmt;

use nsl_source::SourceSpan;
use nsl_source::ast::{ExpressionId, StatementId};

pub(crate) use approvals::ApprovalTable;
pub use diagnostics::{SymbolDiagnostic, SymbolDiagnosticKind, SymbolResolutionError};
pub use resolve::resolve_symbols;

macro_rules! sequential_id {
    ($name:ident, $prefix:literal) => {
        #[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
        pub struct $name(usize);

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, concat!($prefix, "{:04}"), self.0)
            }
        }
    };
}

sequential_id!(SymbolId, "s");
sequential_id!(ApprovalSymbolId, "s");
sequential_id!(ScopeId, "scope");

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ScopeKind {
    Skill,
    Foreach(StatementId),
}

impl ScopeKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Skill => "SKILL",
            Self::Foreach(_) => "FOREACH",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SymbolScope {
    pub id: ScopeId,
    pub parent_id: Option<ScopeId>,
    pub kind: ScopeKind,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum SymbolDeclaration {
    Input(usize),
    Context(usize),
    Let(StatementId),
    Iterator(StatementId),
    Check(StatementId),
}

impl SymbolDeclaration {
    pub const fn category(self) -> &'static str {
        match self {
            Self::Input(_) => "INPUT",
            Self::Context(_) => "CONTEXT",
            Self::Let(_) => "VARIABLE",
            Self::Iterator(_) => "ITERATOR",
            Self::Check(_) => "CHECK",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SymbolBinding {
    pub id: SymbolId,
    pub name: String,
    pub scope_id: ScopeId,
    pub declaration: SymbolDeclaration,
    pub span: SourceSpan,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SymbolReference {
    pub expression: ExpressionId,
    pub symbol: SymbolId,
    pub span: SourceSpan,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalSymbol {
    pub id: ApprovalSymbolId,
    pub name: String,
    pub statement: StatementId,
    pub span: SourceSpan,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SymbolResolution {
    bindings: Vec<SymbolBinding>,
    scopes: Vec<SymbolScope>,
    references: Vec<SymbolReference>,
    approvals: Vec<ApprovalSymbol>,
}

impl SymbolResolution {
    pub fn bindings(&self) -> &[SymbolBinding] {
        &self.bindings
    }

    pub fn binding(&self, id: SymbolId) -> Option<&SymbolBinding> {
        self.bindings.get(id.0 - 1)
    }

    pub fn scopes(&self) -> &[SymbolScope] {
        &self.scopes
    }

    pub fn references(&self) -> &[SymbolReference] {
        &self.references
    }

    pub fn approvals(&self) -> &[ApprovalSymbol] {
        &self.approvals
    }

    pub fn approval(&self, name: &str) -> Option<&ApprovalSymbol> {
        self.approvals.iter().find(|approval| approval.name == name)
    }
}
