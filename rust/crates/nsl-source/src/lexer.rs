mod digits;

use std::{borrow::Cow, fmt};

use crate::{
    HeaderError, LanguageHeaderScanner, SourceFile, SourceId, SourceLanguageVersion,
    SourcePosition, SourceSpan,
};

use self::digits::{decimal_digit, is_digit};

macro_rules! token_kinds {
    ($($kind:ident => $name:literal),+ $(,)?) => {
        #[derive(Clone, Copy, Debug, Eq, PartialEq)]
        pub enum TokenKind { $($kind),+ }

        impl TokenKind {
            pub const fn as_str(self) -> &'static str {
                match self { $(Self::$kind => $name),+ }
            }
        }
    };
}

token_kinds!(
    Keyword => "KEYWORD", Identifier => "IDENTIFIER", String => "STRING", Integer => "INTEGER",
    Decimal => "DECIMAL", Boolean => "BOOLEAN", Duration => "DURATION", LBrace => "LBRACE",
    RBrace => "RBRACE", LParen => "LPAREN", RParen => "RPAREN", Colon => "COLON",
    Semicolon => "SEMICOLON", Comma => "COMMA", Dot => "DOT", Assign => "ASSIGN", Plus => "PLUS",
    Minus => "MINUS", Star => "STAR", Slash => "SLASH", Eq => "EQ", Ne => "NE", Lt => "LT",
    Le => "LE", Gt => "GT", Ge => "GE", Eof => "EOF",
);

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum TokenValue<'a> {
    Text(Cow<'a, str>),
    Boolean(bool),
    /// Canonical base-10 milliseconds, with no machine integer limit.
    DurationMilliseconds(String),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Token<'a> {
    pub kind: TokenKind,
    pub value: TokenValue<'a>,
    pub lexeme: &'a str,
    pub span: SourceSpan,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LexDiagnosticKind {
    UnterminatedEscape,
    UnterminatedString,
    UnexpectedCharacter,
}

impl LexDiagnosticKind {
    pub const fn code(self) -> &'static str {
        match self {
            Self::UnterminatedEscape => "NSL-E1001",
            Self::UnterminatedString => "NSL-E1002",
            Self::UnexpectedCharacter => "NSL-E1003",
        }
    }

    const fn message(self) -> &'static str {
        match self {
            Self::UnterminatedEscape => "unterminated string escape",
            Self::UnterminatedString => "unterminated string",
            Self::UnexpectedCharacter => "unexpected character",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LexDiagnostic {
    pub kind: LexDiagnosticKind,
    pub source_id: SourceId,
    pub position: SourcePosition,
}

impl fmt::Display for LexDiagnostic {
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

impl std::error::Error for LexDiagnostic {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DurationErrorKind {
    NonDecimalDigit,
    TooManyDigits,
}

/// A fatal duration conversion error. It has no NSL diagnostic code.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DurationError {
    pub kind: DurationErrorKind,
    pub span: SourceSpan,
}

impl fmt::Display for DurationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self.kind {
            DurationErrorKind::NonDecimalDigit => "duration contains a non-decimal digit",
            DurationErrorKind::TooManyDigits => "duration exceeds 4300 decimal digits",
        })
    }
}

impl std::error::Error for DurationError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum LexerError {
    Header(HeaderError),
    Diagnostic(LexDiagnostic),
    Duration(DurationError),
}

impl fmt::Display for LexerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Header(error) => error.fmt(formatter),
            Self::Diagnostic(error) => error.fmt(formatter),
            Self::Duration(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for LexerError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Header(error) => Some(error),
            Self::Diagnostic(error) => Some(error),
            Self::Duration(error) => Some(error),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LexResult<'a> {
    pub tokens: Vec<Token<'a>>,
    pub diagnostics: Vec<LexDiagnostic>,
}

pub struct Lexer;

impl Lexer {
    /// Scan with an explicit version and collect ordinary lexical diagnostics.
    pub fn scan(
        source: &SourceFile,
        version: SourceLanguageVersion,
    ) -> Result<LexResult<'_>, DurationError> {
        Scanner::new(source).scan(version)
    }

    pub fn tokenize(
        source: &SourceFile,
        version: SourceLanguageVersion,
    ) -> Result<Vec<Token<'_>>, LexerError> {
        let result = Self::scan(source, version).map_err(LexerError::Duration)?;
        if let Some(error) = result.diagnostics.into_iter().next() {
            return Err(LexerError::Diagnostic(error));
        }
        Ok(result.tokens)
    }

    pub fn tokenize_root(source: &SourceFile) -> Result<Vec<Token<'_>>, LexerError> {
        let header = LanguageHeaderScanner::scan(source).map_err(LexerError::Header)?;
        Self::tokenize(source, header.version)
    }
}

#[derive(Clone, Copy)]
struct Mark {
    byte: usize,
    position: SourcePosition,
}

struct Scanner<'a> {
    source: &'a SourceFile,
    at: Mark,
}

impl<'a> Scanner<'a> {
    fn new(source: &'a SourceFile) -> Self {
        Self {
            source,
            at: Mark {
                byte: 0,
                position: SourcePosition::START,
            },
        }
    }

    fn remaining(&self) -> &'a str {
        &self.source.text()[self.at.byte..]
    }

    fn bump(&mut self, character: char) {
        self.at.byte += character.len_utf8();
        self.at.position.advance(character);
    }

    fn advance_while(&mut self, predicate: impl Fn(char) -> bool) {
        while let Some(character) = self.remaining().chars().next().filter(|&c| predicate(c)) {
            self.bump(character);
        }
    }

    fn span(&self, start: Mark) -> SourceSpan {
        SourceSpan {
            source_id: self.source.source_id().clone(),
            start: start.position,
            end: self.at.position,
        }
    }

    fn diagnostic(&self, kind: LexDiagnosticKind, position: SourcePosition) -> LexDiagnostic {
        LexDiagnostic {
            kind,
            source_id: self.source.source_id().clone(),
            position,
        }
    }

    fn lexeme(&self, start: Mark) -> &'a str {
        &self.source.text()[start.byte..self.at.byte]
    }

    fn token(&self, start: Mark, kind: TokenKind, value: TokenValue<'a>) -> Token<'a> {
        Token {
            kind,
            value,
            lexeme: self.lexeme(start),
            span: self.span(start),
        }
    }

    fn text_token(&self, start: Mark, kind: TokenKind) -> Token<'a> {
        self.token(start, kind, TokenValue::Text(self.lexeme(start).into()))
    }

    fn scan(mut self, version: SourceLanguageVersion) -> Result<LexResult<'a>, DurationError> {
        let mut tokens = Vec::new();
        let mut diagnostics = Vec::new();
        while let Some(character) = self.remaining().chars().next() {
            let start = self.at;
            let token =
                match character {
                    ' ' | '\t' | '\r' | '\n' => {
                        self.bump(character);
                        continue;
                    }
                    '/' if self.remaining().starts_with("//") => {
                        self.advance_while(|c| c != '\n');
                        continue;
                    }
                    '"' => match self.string(start) {
                        Ok(value) => {
                            self.token(start, TokenKind::String, TokenValue::Text(value.into()))
                        }
                        Err(error) => {
                            diagnostics.push(error);
                            break;
                        }
                    },
                    c if is_digit(c) => self.number(start)?,
                    c if c.is_ascii_alphabetic() || c == '_' => self.word(start, version),
                    _ => match operator(self.remaining()) {
                        Some((kind, spelling)) => {
                            for c in spelling.chars() {
                                self.bump(c);
                            }
                            self.text_token(start, kind)
                        }
                        None => {
                            diagnostics.push(self.diagnostic(
                                LexDiagnosticKind::UnexpectedCharacter,
                                self.at.position,
                            ));
                            self.bump(character);
                            continue;
                        }
                    },
                };
            tokens.push(token);
        }
        tokens.push(self.token(self.at, TokenKind::Eof, TokenValue::Text("<eof>".into())));
        Ok(LexResult {
            tokens,
            diagnostics,
        })
    }

    fn string(&mut self, start: Mark) -> Result<String, LexDiagnostic> {
        self.bump('"');
        let mut value = String::new();
        while let Some(character) = self.remaining().chars().next() {
            self.bump(character);
            match character {
                '"' => return Ok(value),
                '\\' => {
                    let Some(escaped) = self.remaining().chars().next() else {
                        return Err(self
                            .diagnostic(LexDiagnosticKind::UnterminatedEscape, self.at.position));
                    };
                    self.bump(escaped);
                    value.push(match escaped {
                        'n' => '\n',
                        'r' => '\r',
                        't' => '\t',
                        other => other,
                    });
                }
                other => value.push(other),
            }
        }
        Err(self.diagnostic(LexDiagnosticKind::UnterminatedString, start.position))
    }

    fn number(&mut self, start: Mark) -> Result<Token<'a>, DurationError> {
        self.advance_while(is_digit);
        if self
            .remaining()
            .strip_prefix('.')
            .and_then(|tail| tail.chars().next())
            .is_some_and(is_digit)
        {
            self.bump('.');
            self.advance_while(is_digit);
            return Ok(self.text_token(start, TokenKind::Decimal));
        }
        let Some((suffix, multiplier)) = duration_unit(self.remaining()) else {
            return Ok(self.text_token(start, TokenKind::Integer));
        };
        let digits = self.lexeme(start);
        for c in suffix.chars() {
            self.bump(c);
        }
        let value = duration_milliseconds(digits, multiplier).map_err(|kind| DurationError {
            kind,
            span: self.span(start),
        })?;
        Ok(self.token(
            start,
            TokenKind::Duration,
            TokenValue::DurationMilliseconds(value),
        ))
    }

    fn word(&mut self, start: Mark, version: SourceLanguageVersion) -> Token<'a> {
        self.advance_while(identifier_char);
        let text = self.lexeme(start);
        match text {
            "true" | "false" => self.token(
                start,
                TokenKind::Boolean,
                TokenValue::Boolean(text == "true"),
            ),
            _ if keyword(text, version) => self.text_token(start, TokenKind::Keyword),
            _ => self.text_token(start, TokenKind::Identifier),
        }
    }
}

fn identifier_char(character: char) -> bool {
    character.is_ascii_alphanumeric() || character == '_'
}

fn keyword(text: &str, version: SourceLanguageVersion) -> bool {
    match text {
        "language" | "skill" | "version" | "description" | "risk" | "include" | "requires"
        | "limits" | "input" | "context" | "output" | "tool" | "let" | "read" | "foreach"
        | "in" | "max" | "check" | "assert" | "severity" | "on_fail" | "message" | "emit" => true,
        "write" | "approve" | "approved_by" => version == SourceLanguageVersion::V0_2,
        _ => false,
    }
}

fn operator(text: &str) -> Option<(TokenKind, &'static str)> {
    use TokenKind::*;
    for (spelling, kind) in [("<=", Le), (">=", Ge), ("==", Eq), ("!=", Ne)] {
        if text.starts_with(spelling) {
            return Some((kind, spelling));
        }
    }
    Some(match text.as_bytes().first()? {
        b'{' => (LBrace, "{"),
        b'}' => (RBrace, "}"),
        b'(' => (LParen, "("),
        b')' => (RParen, ")"),
        b':' => (Colon, ":"),
        b';' => (Semicolon, ";"),
        b',' => (Comma, ","),
        b'.' => (Dot, "."),
        b'=' => (Assign, "="),
        b'+' => (Plus, "+"),
        b'-' => (Minus, "-"),
        b'*' => (Star, "*"),
        b'/' => (Slash, "/"),
        b'<' => (Lt, "<"),
        b'>' => (Gt, ">"),
        _ => return None,
    })
}

fn duration_unit(text: &str) -> Option<(&'static str, u32)> {
    [("ms", 1), ("s", 1_000), ("m", 60_000)]
        .into_iter()
        .find(|(suffix, _)| {
            text.strip_prefix(suffix)
                .is_some_and(|tail| tail.chars().next().is_none_or(|c| !identifier_char(c)))
        })
}

fn duration_milliseconds(text: &str, multiplier: u32) -> Result<String, DurationErrorKind> {
    const MAX_DIGITS: usize = 4_300;
    let mut digits = Vec::new();
    for character in text.chars().rev() {
        let digit = decimal_digit(character).ok_or(DurationErrorKind::NonDecimalDigit)?;
        if digits.len() <= MAX_DIGITS {
            digits.push(digit);
        }
    }
    if digits.len() > MAX_DIGITS {
        return Err(DurationErrorKind::TooManyDigits);
    }
    // Each step multiplies one digit and adds the bounded carry.
    let mut carry = 0;
    for digit in &mut digits {
        let product = u32::from(*digit) * multiplier + carry;
        *digit = (product % 10) as u8;
        carry = product / 10;
    }
    while carry != 0 {
        digits.push((carry % 10) as u8);
        carry /= 10;
    }
    while digits.len() > 1 && digits.last() == Some(&0) {
        digits.pop();
    }
    Ok(digits
        .into_iter()
        .rev()
        .map(|digit| char::from(b'0' + digit))
        .collect())
}
