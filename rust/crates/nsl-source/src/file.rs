use std::{fmt, ops::Range, sync::Arc};

use sha2::{Digest, Sha256};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SourceError {
    MissingSourceId,
    InvalidLogicalPath,
    InvalidUtf8,
    InvalidPosition,
    InvalidByteOffset,
    ReversedSpan,
}

impl fmt::Display for SourceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::MissingSourceId => "source_id is required",
            Self::InvalidLogicalPath => "NSL source logical_path must end with .ns",
            Self::InvalidUtf8 => "NSL source bytes must be valid UTF-8",
            Self::InvalidPosition => "source line and column must be positive",
            Self::InvalidByteOffset => "source byte offset must be on a UTF-8 character boundary",
            Self::ReversedSpan => "source span end must not precede start",
        })
    }
}

impl std::error::Error for SourceError {}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct SourceId(Arc<str>);

impl SourceId {
    pub fn new(id: &str) -> Result<Self, SourceError> {
        if id.is_empty() {
            return Err(SourceError::MissingSourceId);
        }
        Ok(Self(id.into()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    fn derived(logical_path: &str, text: &str) -> Self {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let digest = Sha256::new()
            .chain_update(logical_path.as_bytes())
            .chain_update([0])
            .chain_update(text.as_bytes())
            .finalize();
        let mut id = String::with_capacity("source:".len() + 64);
        id.push_str("source:");
        for byte in digest {
            id.push(char::from(HEX[usize::from(byte >> 4)]));
            id.push(char::from(HEX[usize::from(byte & 15)]));
        }
        Self(id.into())
    }
}

/// Oracle coordinates. The offset counts Unicode scalar values from zero;
/// lines and columns begin at one.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SourcePosition {
    offset: usize,
    line: usize,
    column: usize,
}

impl SourcePosition {
    pub(crate) const START: Self = Self {
        offset: 0,
        line: 1,
        column: 1,
    };

    pub fn new(offset: usize, line: usize, column: usize) -> Result<Self, SourceError> {
        if line == 0 || column == 0 {
            return Err(SourceError::InvalidPosition);
        }
        Ok(Self {
            offset,
            line,
            column,
        })
    }

    pub const fn offset(self) -> usize {
        self.offset
    }

    pub const fn line(self) -> usize {
        self.line
    }

    pub const fn column(self) -> usize {
        self.column
    }

    pub(crate) fn advance(&mut self, character: char) {
        self.offset += 1;
        if character == '\n' {
            self.line += 1;
            self.column = 1;
        } else {
            self.column += 1;
        }
    }
}

/// A half-open span in a single source file.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceSpan {
    pub(crate) source_id: SourceId,
    pub(crate) start: SourcePosition,
    pub(crate) end: SourcePosition,
}

impl SourceSpan {
    pub fn new(
        source_id: SourceId,
        start: SourcePosition,
        end: SourcePosition,
    ) -> Result<Self, SourceError> {
        if end.offset < start.offset {
            return Err(SourceError::ReversedSpan);
        }
        Ok(Self {
            source_id,
            start,
            end,
        })
    }

    pub fn source_id(&self) -> &SourceId {
        &self.source_id
    }

    pub const fn start(&self) -> SourcePosition {
        self.start
    }

    pub const fn end(&self) -> SourcePosition {
        self.end
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceFile {
    source_id: SourceId,
    logical_path: String,
    text: String,
}

impl SourceFile {
    pub fn new(source_id: SourceId, logical_path: &str, text: &str) -> Result<Self, SourceError> {
        if !logical_path.ends_with(".ns") {
            return Err(SourceError::InvalidLogicalPath);
        }
        Ok(Self {
            source_id,
            logical_path: logical_path.into(),
            text: text.into(),
        })
    }

    pub fn from_text(
        logical_path: &str,
        text: &str,
        source_id: Option<&str>,
    ) -> Result<Self, SourceError> {
        let source_id = match source_id {
            Some(id) if !id.is_empty() => SourceId::new(id)?,
            _ => SourceId::derived(logical_path, text),
        };
        Self::new(source_id, logical_path, text)
    }

    pub fn from_bytes(
        logical_path: &str,
        bytes: &[u8],
        source_id: Option<&str>,
    ) -> Result<Self, SourceError> {
        let text = std::str::from_utf8(bytes).map_err(|_| SourceError::InvalidUtf8)?;
        Self::from_text(
            logical_path,
            text.strip_prefix('\u{feff}').unwrap_or(text),
            source_id,
        )
    }

    /// The Oracle's anonymous text entry point uses a fixed source identity.
    pub fn memory(text: &str) -> Self {
        Self {
            source_id: SourceId("memory:root".into()),
            logical_path: "memory/root.ns".into(),
            text: text.into(),
        }
    }

    pub fn source_id(&self) -> &SourceId {
        &self.source_id
    }

    pub fn logical_path(&self) -> &str {
        &self.logical_path
    }

    pub fn text(&self) -> &str {
        &self.text
    }

    pub const fn encoding(&self) -> &'static str {
        "utf-8"
    }

    /// Translate a byte offset in the decoded text to an Oracle position.
    pub fn position_at_byte(&self, offset: usize) -> Result<SourcePosition, SourceError> {
        let prefix = self
            .text
            .get(..offset)
            .ok_or(SourceError::InvalidByteOffset)?;
        let mut position = SourcePosition::START;
        for character in prefix.chars() {
            position.advance(character);
        }
        Ok(position)
    }

    pub fn span_at_bytes(&self, range: Range<usize>) -> Result<SourceSpan, SourceError> {
        if range.end < range.start {
            return Err(SourceError::ReversedSpan);
        }
        let start = self.position_at_byte(range.start)?;
        let text = self.text.get(range).ok_or(SourceError::InvalidByteOffset)?;
        let mut end = start;
        for character in text.chars() {
            end.advance(character);
        }
        SourceSpan::new(self.source_id.clone(), start, end)
    }
}
