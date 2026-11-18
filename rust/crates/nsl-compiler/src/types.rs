//! Static type rules and type annotations for a symbol-resolved, composed skill.

mod check;
mod diagnostics;
mod rules;

use std::{collections::HashMap, fmt};

use nsl_core::{DataClassification, TypeRef};
use nsl_source::ast::ExpressionId;
use nsl_source::{SourceFile, SourcePosition, SourceSpan};

use crate::{diagnostics::SourceDiagnosticContext, symbols::SymbolId};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TypeDiagnosticKind {
    UnknownField,
    UnsupportedBuiltin,
    BinaryType,
    ForeachCollection,
    CheckCondition,
    SumArgument,
    ToolArgumentType,
    UndeclaredTool,
    DuplicateTool,
    UnknownToolContract,
    IncompatibleToolVersion,
    ToolArguments,
    ReadCapability,
    EmitSchema,
    OutputType,
    OutputClassification,
}

impl TypeDiagnosticKind {
    pub const fn code(self) -> &'static str {
        use TypeDiagnosticKind::*;
        match self {
            UnknownField => "NSL-E2005",
            UnsupportedBuiltin => "NSL-E2006",
            BinaryType => "NSL-E3001",
            ForeachCollection => "NSL-E3002",
            CheckCondition => "NSL-E3003",
            SumArgument => "NSL-E3004",
            ToolArgumentType => "NSL-E3005",
            UndeclaredTool => "NSL-E2201",
            DuplicateTool => "NSL-E2202",
            UnknownToolContract => "NSL-E4001",
            IncompatibleToolVersion => "NSL-E4002",
            ToolArguments => "NSL-E4201",
            ReadCapability => "NSL-E5001",
            EmitSchema => "NSL-E7001",
            OutputType => "NSL-E7002",
            OutputClassification => "NSL-E7003",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TypeDiagnostic {
    pub kind: TypeDiagnosticKind,
    pub message: String,
    pub span: SourceSpan,
    pub logical_path: Option<String>,
    pub snippet: Option<String>,
}

impl TypeDiagnostic {
    pub fn position(&self) -> Option<SourcePosition> {
        self.logical_path.as_ref().map(|_| self.span.start())
    }
}

impl fmt::Display for TypeDiagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.kind.code(), self.message)
    }
}

impl std::error::Error for TypeDiagnostic {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum TypeCheckError {
    Diagnostic(Box<TypeDiagnostic>),
    InvalidResolution,
}

impl fmt::Display for TypeCheckError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Diagnostic(error) => error.fmt(f),
            Self::InvalidResolution => f.write_str("AST and symbol resolution are inconsistent"),
        }
    }
}

impl std::error::Error for TypeCheckError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Diagnostic(error) => Some(error.as_ref()),
            Self::InvalidResolution => None,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ValueType {
    pub type_info: TypeRef,
    pub classification: DataClassification,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FieldResolution {
    pub type_info: TypeRef,
    pub projected: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExpressionType {
    pub value: ValueType,
    pub fields: Vec<FieldResolution>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct TypedSkill {
    symbols: HashMap<SymbolId, ValueType>,
    expressions: HashMap<ExpressionId, ExpressionType>,
}

impl TypedSkill {
    pub fn symbol(&self, id: SymbolId) -> Option<&ValueType> {
        self.symbols.get(&id)
    }

    pub fn expression(&self, id: ExpressionId) -> Option<&ExpressionType> {
        self.expressions.get(&id)
    }
}

pub struct StaticTypeChecker<'source> {
    diagnostics: SourceDiagnosticContext<'source>,
}

impl<'source> StaticTypeChecker<'source> {
    pub fn new(sources: impl IntoIterator<Item = &'source SourceFile>) -> Self {
        Self {
            diagnostics: SourceDiagnosticContext::new(sources),
        }
    }

    fn error(
        &self,
        kind: TypeDiagnosticKind,
        message: impl Into<String>,
        span: &SourceSpan,
    ) -> TypeCheckError {
        let (logical_path, snippet) = self.diagnostics.location(span);
        TypeCheckError::Diagnostic(Box::new(TypeDiagnostic {
            kind,
            message: message.into(),
            span: span.clone(),
            logical_path,
            snippet,
        }))
    }
}
