use std::collections::{HashMap, hash_map::Entry};

use nsl_source::SourceSpan;
use nsl_source::ast::StatementId;

use super::{ApprovalSymbol, ApprovalSymbolId};

/// The independent approval namespace used by name and linkage analysis.
#[derive(Default)]
pub(crate) struct ApprovalTable<'ast> {
    names: HashMap<&'ast str, usize>,
    symbols: Vec<ApprovalSymbol>,
}

impl<'ast> ApprovalTable<'ast> {
    pub(crate) fn declare(
        &mut self,
        name: &'ast str,
        statement: StatementId,
        span: &SourceSpan,
    ) -> Option<usize> {
        let Entry::Vacant(slot) = self.names.entry(name) else {
            return None;
        };
        let index = self.symbols.len();
        slot.insert(index);
        self.symbols.push(ApprovalSymbol {
            id: ApprovalSymbolId(index + 1),
            name: name.into(),
            statement,
            span: span.clone(),
        });
        Some(index)
    }

    pub(crate) fn get(&self, name: &str) -> Option<(usize, &ApprovalSymbol)> {
        self.names
            .get(name)
            .map(|index| (*index, &self.symbols[*index]))
    }

    pub(crate) fn into_symbols(self) -> Vec<ApprovalSymbol> {
        self.symbols
    }
}
