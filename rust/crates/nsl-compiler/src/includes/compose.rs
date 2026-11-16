use std::collections::HashMap;

use nsl_source::ast::AstSkill;

use super::{IncludeDiagnosticKind::*, IncludeError, SourceBundle, diagnostic};

impl SourceBundle {
    /// Merge fragment declarations in discovery order and retain their source spans.
    pub fn compose(&self) -> Result<AstSkill, IncludeError> {
        let root = self.root.ast.as_ref();
        let (mut requires_by_id, mut requires) = index_root(&root.requires, |entry| &entry.tool_id);
        let (mut contexts_by_name, mut contexts) = index_root(&root.contexts, |entry| &entry.name);
        let mut limits = root.limits.as_ref();

        for unit in &self.fragments {
            let fragment = &unit.ast;
            for item in &fragment.requires {
                let name = item.tool_id.as_str();
                if let Some(&index) = requires_by_id.get(name) {
                    if requires[index].version != item.version {
                        return Err(diagnostic(
                            ToolVersionConflict,
                            &unit.source,
                            Some(&item.span),
                        ));
                    }
                } else {
                    requires_by_id.insert(name, requires.len());
                    requires.push(item);
                }
            }
            for item in &fragment.contexts {
                if contexts_by_name
                    .insert(item.name.as_str(), contexts.len())
                    .is_some()
                {
                    return Err(diagnostic(DuplicateContext, &unit.source, Some(&item.span)));
                }
                contexts.push(item);
            }
            if let Some(first) = fragment.limits.first() {
                if limits.is_some() || fragment.limits.len() > 1 {
                    return Err(diagnostic(
                        DuplicateLimits,
                        &unit.source,
                        Some(&fragment.span),
                    ));
                }
                limits = Some(first);
            }
        }
        let limits = limits.ok_or_else(|| {
            diagnostic(RequiredLimitsMissing, &self.root.source, Some(&root.span))
        })?;
        let mut composed = root.clone();
        composed.includes.clear();
        composed.requires = requires.into_iter().cloned().collect();
        composed.contexts = contexts.into_iter().cloned().collect();
        composed.limits = Some(limits.clone());
        Ok(composed)
    }
}

/// Root duplicates keep the first slot with the last value, matching Oracle dict order.
fn index_root<T>(items: &[T], key: impl Fn(&T) -> &str) -> (HashMap<&str, usize>, Vec<&T>) {
    let mut indexed = HashMap::new();
    let mut ordered = Vec::new();
    for item in items {
        let slot = indexed.entry(key(item)).or_insert(ordered.len());
        if *slot == ordered.len() {
            ordered.push(item);
        } else {
            ordered[*slot] = item;
        }
    }
    (indexed, ordered)
}
