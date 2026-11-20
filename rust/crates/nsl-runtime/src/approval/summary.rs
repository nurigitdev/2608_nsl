use nsl_core::{Integer, ValueData, ensure_no_credential_material, redact_text};
use std::{collections::BTreeMap, fmt};

/// Raw JSON-shaped metadata. Number lexemes are validated without f32/f64,
/// Decimal substitution, rounding or string conversion. Not an NSL Value type.
#[derive(Debug, Eq, PartialEq)]
pub enum ApprovalSummaryInput {
    Null,
    Bool(bool),
    Number(String),
    Text(String),
    Array(Vec<Self>),
    Object(BTreeMap<String, Self>),
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ApprovalSummaryLimits {
    pub max_depth: usize,
    pub max_nodes: usize,
    /// Aggregate UTF-8 bytes of keys, text values and numeric lexemes.
    pub max_text_bytes: usize,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ApprovalSummaryErrorKind {
    MappingRequired,
    IntegerRequired,
    BlankKey,
    Depth,
    Nodes,
    TextBytes,
    Credential,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalSummaryError {
    pub kind: ApprovalSummaryErrorKind,
    pub path: String,
}
impl fmt::Display for ApprovalSummaryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{}: {}",
            self.path,
            match self.kind {
                ApprovalSummaryErrorKind::MappingRequired => "approval summary must be a mapping",
                ApprovalSummaryErrorKind::IntegerRequired =>
                    "approval summary numbers must be integers",
                ApprovalSummaryErrorKind::BlankKey => "summary keys must be non-empty strings",
                ApprovalSummaryErrorKind::Depth => "summary depth limit exceeded",
                ApprovalSummaryErrorKind::Nodes => "summary node limit exceeded",
                ApprovalSummaryErrorKind::TextBytes => "summary text byte limit exceeded",
                ApprovalSummaryErrorKind::Credential => "credential material is forbidden",
            }
        )
    }
}
impl std::error::Error for ApprovalSummaryError {}
fn error(kind: ApprovalSummaryErrorKind, path: &str) -> ApprovalSummaryError {
    ApprovalSummaryError {
        kind,
        path: redact_text(path),
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct SafeApprovalSummary(ValueData);
impl fmt::Debug for SafeApprovalSummary {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("SafeApprovalSummary { .. }")
    }
}
impl SafeApprovalSummary {
    pub fn new(
        input: &ApprovalSummaryInput,
        limits: ApprovalSummaryLimits,
    ) -> Result<Self, ApprovalSummaryError> {
        use ApprovalSummaryErrorKind as K;
        use ApprovalSummaryInput as I;
        let root = "display_safe_summary";
        if !matches!(input, I::Object(_)) {
            return Err(error(K::MappingRequired, root));
        }
        let mut pending = vec![(input, root.to_string(), 0)];
        let mut nodes = 0usize;
        let mut bytes = 0usize;
        while let Some((v, path, depth)) = pending.pop() {
            if depth > limits.max_depth {
                return Err(error(K::Depth, &path));
            }
            nodes = nodes.saturating_add(1);
            if nodes > limits.max_nodes {
                return Err(error(K::Nodes, &path));
            }
            let mut add_bytes = |n: usize| -> Result<(), ApprovalSummaryError> {
                bytes = bytes.saturating_add(n);
                if bytes > limits.max_text_bytes {
                    Err(error(K::TextBytes, &path))
                } else {
                    Ok(())
                }
            };
            match v {
                I::Number(n) => add_bytes(n.len())?,
                I::Text(s) => {
                    add_bytes(s.len())?;
                    ensure_no_credential_material(&ValueData::String(s.clone()))
                        .map_err(|_| error(K::Credential, &path))?;
                }
                I::Array(items) => pending.extend(
                    items
                        .iter()
                        .enumerate()
                        .rev()
                        .map(|(i, v)| (v, format!("{path}[{i}]"), depth + 1)),
                ),
                I::Object(fields) => {
                    // Credential screening precedes JSON-type freezing, as in
                    // the Oracle, including its null/empty credential exception.
                    let screen = ValueData::Object(
                        fields
                            .iter()
                            .map(|(k, v)| {
                                (
                                    k.clone(),
                                    if matches!(v, I::Null)
                                        || matches!(v,I::Text(s) if s.is_empty())
                                    {
                                        ValueData::Null
                                    } else {
                                        ValueData::Bool(true)
                                    },
                                )
                            })
                            .collect(),
                    );
                    ensure_no_credential_material(&screen)
                        .map_err(|_| error(K::Credential, &path))?;
                    for k in fields.keys() {
                        add_bytes(k.len())?;
                        if crate::data::blank(k) {
                            return Err(error(K::BlankKey, &path));
                        }
                    }
                    pending.extend(
                        fields
                            .iter()
                            .rev()
                            .map(|(k, v)| (v, format!("{path}.{k}"), depth + 1)),
                    );
                }
                _ => {}
            }
        }
        // Explicit postorder stack freezes the entire owned snapshot without
        // recursing through untrusted nested metadata.
        enum Work<'a> {
            Visit(&'a ApprovalSummaryInput, String),
            Array(usize),
            Object(Vec<String>),
        }
        let mut work = vec![Work::Visit(input, root.into())];
        let mut out = vec![];
        while let Some(w) = work.pop() {
            match w {
                Work::Array(n) => {
                    let items = out.split_off(out.len() - n);
                    out.push(ValueData::Array(items));
                }
                Work::Object(keys) => {
                    let items = out.split_off(out.len() - keys.len());
                    out.push(ValueData::Object(keys.into_iter().zip(items).collect()));
                }
                Work::Visit(v, path) => match v {
                    I::Null => out.push(ValueData::Null),
                    I::Bool(b) => out.push(ValueData::Bool(*b)),
                    I::Text(s) => out.push(ValueData::String(s.clone())),
                    I::Number(n) => out.push(ValueData::Number(if n == "-0" {
                        Integer::from(0)
                    } else {
                        n.parse().map_err(|_| error(K::IntegerRequired, &path))?
                    })),
                    I::Array(items) => {
                        work.push(Work::Array(items.len()));
                        work.extend(
                            items
                                .iter()
                                .enumerate()
                                .rev()
                                .map(|(i, v)| Work::Visit(v, format!("{path}[{i}]"))),
                        );
                    }
                    I::Object(fields) => {
                        work.push(Work::Object(fields.keys().cloned().collect()));
                        work.extend(
                            fields
                                .iter()
                                .rev()
                                .map(|(k, v)| Work::Visit(v, format!("{path}.{k}"))),
                        );
                    }
                },
            }
        }
        Ok(Self(out.pop().expect("the root produces one JSON value")))
    }
    pub fn data(&self) -> &ValueData {
        &self.0
    }
}
