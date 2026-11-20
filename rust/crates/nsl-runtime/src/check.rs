use crate::{
    BindingError, BindingNamespace, CheckResult, ExecutionContext, ReadEvaluator,
    ReadExecutionError, RuntimeClock,
};
use nsl_core::{
    CheckStatus, Completeness, DataClassification, Presence, TypeKind, TypeRef, Value,
    ValueEnvelope,
};
use nsl_ir::{Statement, StatementKind};
use std::{collections::BTreeMap, fmt};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CheckEvaluationError {
    PredicateNotBool,
    NotCheckStatement,
}
impl fmt::Display for CheckEvaluationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::PredicateNotBool => "CHECK predicate is not Bool",
            Self::NotCheckStatement => "statement is not CHECK",
        })
    }
}
impl std::error::Error for CheckEvaluationError {}

/// Deterministic rule evaluation for both strict profiles. Neither presence,
/// severity nor on_fail can promote an incomplete predicate to PASS.
#[derive(Clone, Copy, Debug, Default)]
pub struct StrictRuleEvaluator;
impl StrictRuleEvaluator {
    // Retain the Oracle evaluator's legacy name, also used for NSL 0.2.
    pub const SEMANTICS_PROFILE: &'static str = "NSL-0.1-STRICT";

    pub fn evaluate(
        &self,
        statement: &Statement,
        predicate: &ValueEnvelope,
    ) -> Result<CheckResult, CheckEvaluationError> {
        let StatementKind::Check {
            check_id,
            condition,
            severity,
            message,
            ..
        } = &statement.kind
        else {
            return Err(CheckEvaluationError::NotCheckStatement);
        };
        if !matches!(predicate.type_ref().kind(), TypeKind::Primitive(name) if name == "Bool") {
            return Err(CheckEvaluationError::PredicateNotBool);
        }
        let Value::Bool(truth) = predicate.value() else {
            return Err(CheckEvaluationError::PredicateNotBool);
        };
        let (status, reason_code) = match predicate.completeness() {
            Completeness::Complete => (
                if *truth {
                    CheckStatus::Pass
                } else {
                    CheckStatus::Fail
                },
                None,
            ),
            Completeness::Partial => (CheckStatus::Unknown, Some("PARTIAL_INPUT")),
            Completeness::Unknown => (CheckStatus::Unknown, Some("UNKNOWN_COMPLETENESS")),
        };
        Ok(CheckResult {
            check_id: check_id.clone(),
            status,
            severity: severity.clone(),
            message: message.clone(),
            condition_node_id: condition.node_id.clone(),
            presence: predicate.presence(),
            completeness: predicate.completeness(),
            provenance_refs: predicate.provenance_refs().to_vec(),
            reason_code: reason_code.map(Into::into),
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CheckExecutionError {
    Evaluation(CheckEvaluationError),
    Read(ReadExecutionError),
    Binding(BindingError),
}
impl fmt::Display for CheckExecutionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Evaluation(e) => e.fmt(f),
            Self::Read(e) => e.fmt(f),
            Self::Binding(e) => e.fmt(f),
        }
    }
}
// Preserve the existing safe READ error boundary, without provider source chains.
impl std::error::Error for CheckExecutionError {}
impl From<CheckEvaluationError> for CheckExecutionError {
    fn from(e: CheckEvaluationError) -> Self {
        Self::Evaluation(e)
    }
}
impl From<ReadExecutionError> for CheckExecutionError {
    fn from(e: ReadExecutionError) -> Self {
        Self::Read(e)
    }
}
impl From<BindingError> for CheckExecutionError {
    fn from(e: BindingError) -> Self {
        Self::Binding(e)
    }
}

impl ReadEvaluator<'_> {
    /// Evaluate one CHECK; statement dispatch, audit and WRITE eligibility are
    /// later integration boundaries. FAIL/UNKNOWN are recorded outcomes.
    pub async fn execute_check<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        statement: &Statement,
    ) -> Result<(), CheckExecutionError> {
        let StatementKind::Check {
            condition,
            result_symbol_id,
            ..
        } = &statement.kind
        else {
            return Err(CheckEvaluationError::NotCheckStatement.into());
        };
        let predicate = self.evaluate(ctx, condition).await?;
        let check = StrictRuleEvaluator.evaluate(statement, &predicate)?;
        let status = check.status.as_str().into();
        // The Oracle appends before binding, including an immutable-name error.
        ctx.checks.push(check);
        let value = ValueEnvelope::new(
            Value::Record(BTreeMap::from([("status".into(), Value::String(status))])),
            check_result_type(),
            Presence::Present,
            Completeness::Complete,
            DataClassification::Internal,
            predicate.provenance_refs().to_vec(),
        );
        ctx.bind(BindingNamespace::Check, result_symbol_id.clone(), value)?;
        Ok(())
    }
}

fn check_result_type() -> TypeRef {
    let status = TypeRef::new(TypeKind::Enum {
        name: "CheckStatus".into(),
        values: ["PASS", "FAIL", "UNKNOWN"].map(Into::into).to_vec(),
    })
    .expect("fixed CHECK status schema");
    TypeRef::new(TypeKind::Record {
        name: "CheckResult".into(),
        fields: vec![("status".into(), status)],
    })
    .expect("fixed CHECK result schema")
}
