#![forbid(unsafe_code)]

mod file;
mod header;
mod lexer;

pub use file::{SourceError, SourceFile, SourceId, SourcePosition, SourceSpan};
pub use header::{
    HeaderError, HeaderErrorKind, LanguageHeader, LanguageHeaderScanner, SourceLanguageVersion,
};
pub use lexer::{
    DurationError, DurationErrorKind, LexDiagnostic, LexDiagnosticKind, LexResult, Lexer,
    LexerError, Token, TokenKind, TokenValue,
};
