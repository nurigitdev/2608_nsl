#![forbid(unsafe_code)]

mod file;
mod header;

pub use file::{SourceError, SourceFile, SourceId, SourcePosition, SourceSpan};
pub use header::{
    HeaderError, HeaderErrorKind, LanguageHeader, LanguageHeaderScanner, SourceLanguageVersion,
};
