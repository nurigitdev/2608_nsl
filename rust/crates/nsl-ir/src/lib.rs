#![forbid(unsafe_code)]

mod codec;
mod model;
mod nodes;
mod schema;

pub use codec::{NsoCodec, NsoCodecError, NsoLimit, NsoLimits};
pub use model::{
    BuildMetadata, BuildSource, ContextSpec, InputSpec, IrVersion, OutputField, RequiredTool,
    RequiredToolKind, ResourceLimits, SkillObject, StaticAnalysis, SymbolSpec,
    UnsupportedIrVersion,
};
pub use nodes::{
    ApprovalPolicy, ApprovalStatement, Expression, ExpressionKind, LiteralValue, NamedExpression,
    ResultPolicy, Statement, StatementKind, WriteExpression,
};
pub use schema::{NsoSchemaError, NsoValidationError, validate_nso_document};
