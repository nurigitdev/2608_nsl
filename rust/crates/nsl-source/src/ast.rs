//! Source-oriented AST. References to child nodes are indices into their owning skill.

use nsl_core::{DataClassification, Integer, NslDecimal, TypeRef};

use crate::{SourceLanguageVersion, SourceSpan};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Ast {
    RootSkill(Box<AstSkill>),
    IncludeFragment(AstIncludeFragment),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AstSkill {
    pub language_version: SourceLanguageVersion,
    pub language_version_span: SourceSpan,
    pub skill_id: String,
    pub skill_version: String,
    pub risk: String,
    pub risk_span: SourceSpan,
    pub includes: Vec<AstIncludeDeclaration>,
    pub requires: Vec<AstRequiredTool>,
    pub limits: Option<AstLimits>,
    pub inputs: Vec<AstFieldSpec>,
    pub contexts: Vec<AstFieldSpec>,
    pub outputs: Vec<AstFieldSpec>,
    pub body: Vec<StatementId>,
    pub span: SourceSpan,
    pub(crate) statements: Vec<AstStatement>,
    pub(crate) expressions: Vec<AstExpression>,
}

impl AstSkill {
    pub fn statement(&self, id: StatementId) -> Option<&AstStatement> {
        self.statements.get(id.0)
    }

    pub fn expression(&self, id: ExpressionId) -> Option<&AstExpression> {
        self.expressions.get(id.0)
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct StatementId(pub(crate) usize);

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub struct ExpressionId(pub(crate) usize);

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AstIncludeFragment {
    pub includes: Vec<AstIncludeDeclaration>,
    pub requires: Vec<AstRequiredTool>,
    pub contexts: Vec<AstFieldSpec>,
    pub limits: Vec<AstLimits>,
    pub span: SourceSpan,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AstIncludeDeclaration {
    pub path: String,
    pub span: SourceSpan,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AstRequiredTool {
    pub tool_id: String,
    pub version: String,
    pub span: SourceSpan,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AstFieldSpec {
    pub name: String,
    pub type_info: AstType,
    pub classification: DataClassification,
    pub path: Vec<String>,
    pub span: SourceSpan,
}

/// Zero or more List wrappers around a scalar, domain, enum or Money type.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AstType {
    pub list_depth: usize,
    pub item: TypeRef,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AstLimits {
    pub tool_calls: Integer,
    pub loop_iterations: Integer,
    pub emitted_rows: Integer,
    pub collection_size: Integer,
    pub duration_ms: Integer,
    pub span: SourceSpan,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AstStatement {
    pub kind: StatementKind,
    pub span: SourceSpan,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StatementKind {
    Let {
        name: String,
        value: ExpressionId,
    },
    Foreach {
        iterator: String,
        collection: ExpressionId,
        max_iterations: Integer,
        body: Vec<StatementId>,
    },
    Check {
        check_id: String,
        condition: ExpressionId,
        severity: String,
        on_fail: String,
        message: String,
    },
    Emit {
        fields: Vec<(String, ExpressionId)>,
    },
    Approve {
        approval_id: String,
        approval_id_span: SourceSpan,
        message: String,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AstExpression {
    pub kind: ExpressionKind,
    pub span: SourceSpan,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExpressionKind {
    Literal(AstLiteral),
    Path(Vec<String>),
    Call {
        name: String,
        arguments: Vec<ExpressionId>,
    },
    Read {
        tool_id: String,
        arguments: Vec<(String, ExpressionId)>,
    },
    Write {
        tool_id: String,
        arguments: Vec<(String, ExpressionId)>,
        approval_id: String,
        approval_span: SourceSpan,
    },
    Binary {
        operator: BinaryOperator,
        left: ExpressionId,
        right: ExpressionId,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AstLiteral {
    Int(Integer),
    Decimal(NslDecimal),
    String(String),
    Bool(bool),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BinaryOperator {
    Eq,
    Ne,
    Lt,
    Le,
    Gt,
    Ge,
    Add,
    Sub,
    Mul,
    Div,
}

impl BinaryOperator {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Eq => "==",
            Self::Ne => "!=",
            Self::Lt => "<",
            Self::Le => "<=",
            Self::Gt => ">",
            Self::Ge => ">=",
            Self::Add => "+",
            Self::Sub => "-",
            Self::Mul => "*",
            Self::Div => "/",
        }
    }
}
