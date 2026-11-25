use crate::{SnapshotMetadataLimits, WriteIntentError, WriteIntentErrorKind, error};
use nsl_core::{TypeKind, TypeRef, ValueEnvelope};
use std::collections::BTreeMap;

struct Budget {
    limits: SnapshotMetadataLimits,
    nodes: usize,
    text: usize,
}
impl Budget {
    fn nodes(&mut self, n: usize, path: &str) -> Result<(), WriteIntentError> {
        self.nodes = self.nodes.saturating_add(n);
        if self.nodes > self.limits.max_nodes {
            return Err(rejected(path, "snapshot metadata node limit exceeded"));
        }
        Ok(())
    }
    fn depth(&self, depth: usize, path: &str) -> Result<(), WriteIntentError> {
        if depth > self.limits.max_depth {
            return Err(rejected(path, "snapshot metadata depth limit exceeded"));
        }
        Ok(())
    }
    fn text(&mut self, s: &str, path: &str) -> Result<(), WriteIntentError> {
        self.text = self.text.saturating_add(s.len());
        if self.text > self.limits.max_text_bytes {
            return Err(rejected(path, "snapshot metadata text byte limit exceeded"));
        }
        Ok(())
    }
}
fn rejected(path: &str, message: &'static str) -> WriteIntentError {
    error(WriteIntentErrorKind::Type, path, message)
}
/// Iterative borrowed traversal; does not clone/encode types, project Optional,
/// check value/type conformance, or let metadata affect canonical identity.
pub(crate) fn check(
    arguments: &BTreeMap<String, ValueEnvelope>,
    limits: SnapshotMetadataLimits,
) -> Result<(), WriteIntentError> {
    const ROOT: &str = "$.canonical_arguments";
    let mut budget = Budget {
        limits,
        nodes: 0,
        text: 0,
    };
    budget.nodes(1, ROOT)?; // metadata map at depth zero
    let mut work: Vec<(&TypeRef, usize, String)> = Vec::new();
    for (name, envelope) in arguments {
        // Envelope plus type, three scalar statuses and provenance-list nodes.
        budget.nodes(6, ROOT)?;
        budget.text(name, ROOT)?;
        let path = format!("{ROOT}[{name:?}]");
        budget.depth(1, &path)?;
        budget.depth(2, &path)?;
        let provenance = format!("{path}.provenance_refs");
        budget.nodes(envelope.provenance_refs().len(), &provenance)?;
        for (i, value) in envelope.provenance_refs().iter().enumerate() {
            let path = format!("{provenance}[{i}]");
            budget.depth(3, &path)?;
            budget.text(value, &path)?;
        }
        work.push((envelope.type_ref(), 2, format!("{path}.type_ref")));
    }
    while let Some((ty, depth, path)) = work.pop() {
        budget.depth(depth, &path)?;
        match ty.kind() {
            TypeKind::Primitive(name) | TypeKind::Domain(name) => budget.text(name, &path)?,
            TypeKind::Money(currency) => budget.text(currency.as_str(), &path)?,
            TypeKind::List(item) | TypeKind::Optional(item) => {
                budget.nodes(1, &path)?;
                work.push((item, depth.saturating_add(1), format!("{path}.item")));
            }
            TypeKind::Record { name, fields } => {
                budget.text(name, &path)?;
                budget.nodes(fields.len(), &path)?;
                for (name, ty) in fields.iter().rev() {
                    budget.text(name, &path)?;
                    work.push((
                        ty,
                        depth.saturating_add(1),
                        format!("{path}.fields[{name:?}]"),
                    ));
                }
            }
            TypeKind::Enum { name, values } => {
                budget.text(name, &path)?;
                budget.nodes(values.len(), &path)?;
                for (i, value) in values.iter().enumerate() {
                    let path = format!("{path}.values[{i}]");
                    budget.depth(depth.saturating_add(1), &path)?;
                    budget.text(value, &path)?;
                }
            }
        }
    }
    Ok(())
}
