use std::fmt;

use crate::{SourceFile, SourceId, SourcePosition, SourceSpan};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SourceLanguageVersion {
    V0_1,
    V0_2,
}

impl SourceLanguageVersion {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::V0_1 => "0.1",
            Self::V0_2 => "0.2",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LanguageHeader {
    pub version: SourceLanguageVersion,
    pub span: SourceSpan,
    pub version_span: SourceSpan,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum HeaderErrorKind {
    Required,
    Malformed,
    UnsupportedVersion,
    NonLeading,
    Duplicate,
    Limit,
}

impl HeaderErrorKind {
    pub const fn code(self) -> &'static str {
        match self {
            Self::Required => "NSL-E1004",
            Self::Malformed => "NSL-E1005",
            Self::UnsupportedVersion => "NSL-E1006",
            Self::NonLeading => "NSL-E1007",
            Self::Duplicate => "NSL-E1008",
            Self::Limit => "NSL-E1009",
        }
    }

    const fn message(self) -> &'static str {
        match self {
            Self::Required => "language declaration is required",
            Self::Malformed => "malformed language declaration",
            Self::UnsupportedVersion => "unsupported NSL version",
            Self::NonLeading => "language declaration must be the first non-comment declaration",
            Self::Duplicate => "duplicate language declaration",
            Self::Limit => "language declaration exceeds the bounded header scan limit",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HeaderError {
    pub kind: HeaderErrorKind,
    pub source_id: SourceId,
    pub position: SourcePosition,
}

impl fmt::Display for HeaderError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            formatter,
            "{} at {}:{}: {}",
            self.kind.code(),
            self.position.line(),
            self.position.column(),
            self.kind.message()
        )
    }
}

impl std::error::Error for HeaderError {}

/// Scan the leading language declaration of a root source.
pub struct LanguageHeaderScanner;

impl LanguageHeaderScanner {
    pub const MAX_HEADER_BYTES: usize = 4096;

    pub fn scan(source: &SourceFile) -> Result<LanguageHeader, HeaderError> {
        let mut cursor = Cursor {
            source,
            byte_offset: 0,
            position: SourcePosition::START,
        };
        cursor.skip_trivia(true)?;
        let header_start = cursor.position;
        cursor.leading_keyword()?;

        cursor.skip_trivia(true)?;
        cursor.expect_word("NSL")?;
        cursor.skip_trivia(true)?;

        let version_start = cursor.position;
        let version_value = cursor.quoted_version()?;
        let version_end = cursor.position;
        cursor.skip_trivia(true)?;
        cursor.expect_char(';')?;
        let header_end = cursor.position;

        let version = match version_value {
            "0.1" => SourceLanguageVersion::V0_1,
            "0.2" => SourceLanguageVersion::V0_2,
            _ => return Err(cursor.error_at(HeaderErrorKind::UnsupportedVersion, version_start)),
        };

        cursor.skip_trivia(false)?;
        if matches_word(cursor.remaining(), "language") {
            return Err(cursor.error(HeaderErrorKind::Duplicate));
        }
        Ok(LanguageHeader {
            version,
            span: cursor.span(header_start, header_end),
            version_span: cursor.span(version_start, version_end),
        })
    }
}

struct Cursor<'a> {
    source: &'a SourceFile,
    byte_offset: usize,
    position: SourcePosition,
}

impl<'a> Cursor<'a> {
    fn remaining(&self) -> &'a str {
        &self.source.text()[self.byte_offset..]
    }

    fn error_at(&self, kind: HeaderErrorKind, position: SourcePosition) -> HeaderError {
        HeaderError {
            kind,
            source_id: self.source.source_id().clone(),
            position,
        }
    }

    fn error(&self, kind: HeaderErrorKind) -> HeaderError {
        self.error_at(kind, self.position)
    }

    fn span(&self, start: SourcePosition, end: SourcePosition) -> SourceSpan {
        SourceSpan {
            source_id: self.source.source_id().clone(),
            start,
            end,
        }
    }

    fn advance(&mut self, character: char, enforce_limit: bool) -> Result<(), HeaderError> {
        self.byte_offset += character.len_utf8();
        self.position.advance(character);
        if enforce_limit && self.byte_offset > LanguageHeaderScanner::MAX_HEADER_BYTES {
            return Err(self.error(HeaderErrorKind::Limit));
        }
        Ok(())
    }

    fn skip_trivia(&mut self, enforce_limit: bool) -> Result<(), HeaderError> {
        let mut in_comment = false;
        while let Some(character) = self.remaining().chars().next() {
            in_comment |= self.remaining().starts_with("//");
            if !in_comment && !matches!(character, ' ' | '\t' | '\r' | '\n') {
                break;
            }
            if !enforce_limit && self.byte_offset >= LanguageHeaderScanner::MAX_HEADER_BYTES {
                break;
            }
            self.advance(character, enforce_limit)?;
            in_comment &= character != '\n';
        }
        Ok(())
    }

    fn leading_keyword(&mut self) -> Result<(), HeaderError> {
        if matches_word(self.remaining(), "language") {
            return self.expect_word("language");
        }
        let text = self.source.text();
        for (relative, character) in self.remaining().char_indices() {
            let offset = self.byte_offset + relative;
            if offset + character.len_utf8() > LanguageHeaderScanner::MAX_HEADER_BYTES {
                break;
            }
            let starts_word = offset == 0 || !identifier_byte(text.as_bytes()[offset - 1]);
            if starts_word && matches_word(&text[offset..], "language") {
                return Err(self.error(HeaderErrorKind::NonLeading));
            }
        }
        Err(self.error(HeaderErrorKind::Required))
    }

    fn expect_word(&mut self, word: &str) -> Result<(), HeaderError> {
        if !matches_word(self.remaining(), word) {
            return Err(self.error(HeaderErrorKind::Malformed));
        }
        for character in word.chars() {
            self.advance(character, true)?;
        }
        Ok(())
    }

    fn expect_char(&mut self, expected: char) -> Result<(), HeaderError> {
        if !self.remaining().starts_with(expected) {
            return Err(self.error(HeaderErrorKind::Malformed));
        }
        self.advance(expected, true)
    }

    fn quoted_version(&mut self) -> Result<&'a str, HeaderError> {
        self.expect_char('"')?;
        let start = self.byte_offset;
        while let Some(character) = self.remaining().chars().next() {
            match character {
                '"' => {
                    let version = &self.source.text()[start..self.byte_offset];
                    self.advance(character, true)?;
                    return Ok(version);
                }
                '\n' | '\r' | '\\' => return Err(self.error(HeaderErrorKind::Malformed)),
                _ => self.advance(character, true)?,
            }
        }
        Err(self.error(HeaderErrorKind::Malformed))
    }
}

fn matches_word(text: &str, word: &str) -> bool {
    text.strip_prefix(word).is_some_and(|tail| {
        tail.as_bytes()
            .first()
            .is_none_or(|&byte| !identifier_byte(byte))
    })
}

fn identifier_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}
