use nsl_core::{Integer, Money, NslDecimal, TypeRef};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ResultPolicy {
    pub required: bool,
    pub accept_partial: bool,
    pub empty_is_valid: bool,
}

impl Default for ResultPolicy {
    fn default() -> Self {
        Self {
            required: true,
            accept_partial: false,
            empty_is_valid: true,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum LiteralValue {
    Bool(bool),
    Int(Integer),
    String(String),
    Decimal(NslDecimal),
    Money(Money),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NamedExpression {
    pub name: String,
    pub value: Expression,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Expression {
    pub node_id: String,
    pub type_info: TypeRef,
    pub kind: ExpressionKind,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExpressionKind {
    Literal(LiteralValue),
    SymbolRef {
        symbol_id: String,
    },
    Field {
        source: Box<Expression>,
        field: String,
    },
    Projection {
        source: Box<Expression>,
        field: String,
    },
    Call {
        function: String,
        arguments: Vec<Expression>,
    },
    Binary {
        operator: String,
        left: Box<Expression>,
        right: Box<Expression>,
    },
    Read {
        tool_ref: String,
        arguments: Vec<NamedExpression>,
        result_policy: ResultPolicy,
    },
    Write(WriteExpression),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteExpression {
    pub tool_ref: String,
    pub arguments: Vec<NamedExpression>,
    pub approval_ref: String,
}

/// An approval is always required. A missing TTL leaves it unspecified.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ApprovalPolicy {
    pub ttl_seconds: Option<Integer>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ApprovalStatement {
    pub approval_id: String,
    pub message: String,
    pub policy: ApprovalPolicy,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Statement {
    pub node_id: String,
    pub kind: StatementKind,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum StatementKind {
    Let {
        target_symbol_id: String,
        value: Expression,
    },
    Foreach {
        iterator_symbol_id: String,
        collection: Expression,
        max_iterations: Integer,
        body: Vec<Statement>,
    },
    /// The Oracle object projects the fixed CHECK data policy on output.
    Check {
        check_id: String,
        condition: Expression,
        severity: String,
        on_fail: String,
        message: String,
        result_symbol_id: String,
    },
    Emit {
        fields: Vec<NamedExpression>,
    },
    Approval(ApprovalStatement),
}
