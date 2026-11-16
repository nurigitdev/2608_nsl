#![forbid(unsafe_code)]

pub mod ast;
mod file;
mod header;
mod lexer;
mod parser;

pub use file::{SourceError, SourceFile, SourceId, SourcePosition, SourceSpan};
pub use header::{
    HeaderError, HeaderErrorKind, LanguageHeader, LanguageHeaderScanner, SourceLanguageVersion,
};
pub use lexer::{
    DurationError, DurationErrorKind, LexDiagnostic, LexDiagnosticKind, LexResult, Lexer,
    LexerError, Token, TokenKind, TokenValue,
};
pub use parser::{
    NestingError, NumericError, NumericErrorKind, ParseDiagnostic, ParseDiagnosticKind, ParseError,
    ParseLimits, ParseMode, Parser,
};
