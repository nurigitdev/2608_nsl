#![forbid(unsafe_code)]

pub mod approvals;
pub mod bounds;
mod compiler;
mod diagnostics;
pub mod includes;
pub mod symbols;
pub mod types;
pub mod write_safety;
pub use compiler::{
    CompilationResult, CompileError, CompilerDiagnostic, CompilerOptions, CompilerOutputLimit,
    NslCompiler, SourceManifestEntry,
};
