use std::collections::HashMap;

use nsl_source::{SourceFile, SourceId, SourceSpan};

pub(crate) struct SourceDiagnosticContext<'source>(HashMap<&'source SourceId, &'source SourceFile>);

impl<'source> SourceDiagnosticContext<'source> {
    pub(crate) fn new(sources: impl IntoIterator<Item = &'source SourceFile>) -> Self {
        Self(
            sources
                .into_iter()
                .map(|source| (source.source_id(), source))
                .collect(),
        )
    }

    pub(crate) fn location(&self, span: &SourceSpan) -> (Option<String>, Option<String>) {
        let source = self.0.get(span.source_id());
        (
            source.map(|source| source.logical_path().to_owned()),
            source
                .and_then(|source| source_line(source.text(), span.start().line()))
                .map(str::to_owned),
        )
    }
}

// The Oracle indexes Python splitlines() for diagnostic snippets.
fn source_line(text: &str, mut line: usize) -> Option<&str> {
    let mut start = 0;
    for (offset, ch) in text.char_indices() {
        if offset < start
            || !matches!(
                ch,
                '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}'
                    ..='\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
            )
        {
            continue;
        }
        if line == 1 {
            return Some(&text[start..offset]);
        }
        start = offset + ch.len_utf8();
        if ch == '\r' && text.as_bytes().get(start) == Some(&b'\n') {
            start += 1;
        }
        line -= 1;
    }
    (line == 1 && start < text.len()).then(|| &text[start..])
}
