use crate::data::{bigint, integer};
use crate::expression::{EvaluationProgress, ExpressionMachine};
use crate::{
    BindingNamespace, CancellationSignal, ExecutionContext, ExpressionRuntimeError, LimitExceeded,
    ReadAuthorizationError, ReadAuthorizationPort, ResourceLimit, ResultEncodingError,
    RuntimeClock, ToolCallError, ToolCallRequest, ToolContractValidator, ToolExecutionError,
    ToolExecutionPort, execute_with_timeout, tool_result_hash,
};
use nsl_contract::{CatalogError, ToolContract, ToolContractCatalog};
use nsl_core::{Presence, ToolCapability, Value, ValueEnvelope};
use nsl_ir::{Expression, NamedExpression, RequiredTool, ResultPolicy};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ReadExecutionError {
    Expression(ExpressionRuntimeError),
    Tool(ToolCallError),
    Authorization(ReadAuthorizationError),
    Catalog(CatalogError),
    Encoding(ResultEncodingError),
}
impl fmt::Display for ReadExecutionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Expression(e) => e.fmt(f),
            Self::Tool(e) => e.fmt(f),
            Self::Authorization(e) => e.fmt(f),
            Self::Catalog(e) => e.fmt(f),
            Self::Encoding(e) => e.fmt(f),
        }
    }
}
impl std::error::Error for ReadExecutionError {}
impl From<ExpressionRuntimeError> for ReadExecutionError {
    fn from(e: ExpressionRuntimeError) -> Self {
        Self::Expression(e)
    }
}
impl From<ToolCallError> for ReadExecutionError {
    fn from(e: ToolCallError) -> Self {
        Self::Tool(e)
    }
}
impl From<ToolExecutionError> for ReadExecutionError {
    fn from(e: ToolExecutionError) -> Self {
        Self::Tool(e.into())
    }
}
impl From<ReadAuthorizationError> for ReadExecutionError {
    fn from(e: ReadAuthorizationError) -> Self {
        Self::Authorization(e)
    }
}
impl From<CatalogError> for ReadExecutionError {
    fn from(e: CatalogError) -> Self {
        Self::Catalog(e)
    }
}
impl From<ResultEncodingError> for ReadExecutionError {
    fn from(e: ResultEncodingError) -> Self {
        Self::Encoding(e)
    }
}
impl From<LimitExceeded> for ReadExecutionError {
    fn from(e: LimitExceeded) -> Self {
        Self::Expression(e.into())
    }
}

struct PreparedRead<'a> {
    required: RequiredTool,
    contract: &'a ToolContract,
    decision_ref: String,
    policy: ResultPolicy,
}

/// READ-capable Typed IR driver. Services are injected; no enterprise transport,
/// direct I/O, WRITE execution, snapshot persistence or audit recorder is owned.
pub struct ReadEvaluator<'a> {
    catalog: &'a ToolContractCatalog,
    tools: &'a dyn ToolExecutionPort,
    authorizer: &'a dyn ReadAuthorizationPort,
    max_decimal_bytes: usize,
}
impl<'a> ReadEvaluator<'a> {
    pub fn new(
        catalog: &'a ToolContractCatalog,
        tools: &'a dyn ToolExecutionPort,
        authorizer: &'a dyn ReadAuthorizationPort,
        max_decimal_bytes: usize,
    ) -> Self {
        Self {
            catalog,
            tools,
            authorizer,
            max_decimal_bytes,
        }
    }
    pub async fn evaluate<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        expression: &Expression,
    ) -> Result<ValueEnvelope, ReadExecutionError> {
        let mut machine = ExpressionMachine::new(expression);
        let mut prepared = Vec::new();
        loop {
            match machine.advance(ctx)? {
                EvaluationProgress::Complete(value) => return Ok(value),
                EvaluationProgress::ReadStart {
                    expression,
                    tool_ref,
                    arguments,
                    policy,
                } => {
                    // Admission/authorization precedes *all* argument evaluation,
                    // including nested READs with their own provider effects.
                    prepared.push(self.prepare(ctx, tool_ref, arguments, policy)?);
                    machine.begin_read(expression, arguments);
                }
                EvaluationProgress::ReadReady {
                    expression,
                    arguments,
                } => {
                    let read = prepared
                        .pop()
                        .expect("each READ finish has a prepared header");
                    let value = self.invoke(ctx, expression, &read, arguments).await?;
                    machine.accept_read(ctx, expression, value)?;
                }
            }
        }
    }
    pub async fn execute_let<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        target_symbol_id: &str,
        expression: &Expression,
    ) -> Result<(), ReadExecutionError> {
        let value = self.evaluate(ctx, expression).await?;
        ctx.bind(BindingNamespace::Variable, target_symbol_id.into(), value)
            .map_err(ExpressionRuntimeError::from)?;
        Ok(())
    }
    fn prepare<C: RuntimeClock>(
        &self,
        ctx: &ExecutionContext<C>,
        tool_ref: &str,
        arguments: &[NamedExpression],
        policy: ResultPolicy,
    ) -> Result<PreparedRead<'a>, ReadExecutionError> {
        let required = ctx
            .skill()
            .required_tools
            .iter()
            .find(|t| t.tool_ref == tool_ref)
            .ok_or_else(|| {
                ExpressionRuntimeError::Contract(format!(
                    "read references unregistered tool: {tool_ref}"
                ))
            })?
            .clone();
        let contract = self.catalog.resolve(&required.tool_id, &required.version)?;
        // Approved security exception to frozen Oracle 28a949e: declared READ
        // metadata must not route a WRITE or substitute a weaker scope.
        if required.kind.capability() != ToolCapability::Read
            || contract.capability().known() != Some(ToolCapability::Read)
            || required.required_scope != contract.required_scope()
        {
            return Err(ToolExecutionError::new(
                "TOOL_CONTRACT_MISMATCH",
                format!("read contract metadata mismatch: {}", required.tool_id),
            )
            .into());
        }
        let names = arguments
            .iter()
            .map(|a| a.name.as_str())
            .collect::<BTreeSet<_>>();
        let expected = contract
            .input_types()
            .iter()
            .map(|(name, _)| name.as_str())
            .collect::<BTreeSet<_>>();
        if names != expected || names.len() != arguments.len() {
            return Err(ToolExecutionError::new(
                "TOOL_ARGUMENT_MISMATCH",
                format!("argument names do not match contract: {}", required.tool_id),
            )
            .into());
        }
        let decision_ref = self.authorizer.authorize(
            &ctx.security_context().principal,
            &format!("tool:{}:execute", required.tool_id),
            &BTreeSet::from([contract.required_scope().into()]),
        )?;
        Ok(PreparedRead {
            required,
            contract,
            decision_ref,
            policy,
        })
    }
    async fn invoke<C: RuntimeClock>(
        &self,
        ctx: &mut ExecutionContext<C>,
        expression: &Expression,
        read: &PreparedRead<'_>,
        values: Vec<ValueEnvelope>,
    ) -> Result<ValueEnvelope, ReadExecutionError> {
        let nsl_ir::ExpressionKind::Read { arguments, .. } = &expression.kind else {
            unreachable!("READ finish is scheduled only for a READ")
        };
        let arguments = arguments
            .iter()
            .zip(values)
            .map(|(arg, value)| (arg.name.clone(), value))
            .collect::<BTreeMap<_, _>>();
        ctx.guard_mut().before_tool_call()?;
        ctx.invocation_counter = integer(bigint(&ctx.invocation_counter) + 1);
        let raw = ctx.invocation_counter.as_str();
        let (sign, digits) = raw
            .strip_prefix('-')
            .map_or(("", raw), |digits| ("-", digits));
        let invocation_id = format!(
            "inv{sign}{}{digits}",
            "0".repeat(4_usize.saturating_sub(raw.len()))
        );
        // Oracle hashes arguments even without an audit sink. Preserve encoding
        // failure order after resource/invocation accounting and before dispatch.
        let raw_arguments = arguments
            .iter()
            .map(|(k, v)| (k.clone(), v.value().clone()))
            .collect();
        let _argument_hash =
            tool_result_hash(&Value::Record(raw_arguments), self.max_decimal_bytes)?;
        let request = ToolCallRequest {
            execution_id: ctx.identity().execution_id().into(),
            invocation_id,
            node_id: expression.node_id.clone(),
            tool_id: read.required.tool_id.clone(),
            tool_version: read.required.version.clone(),
            contract_hash: read.required.contract_hash.to_string(),
            arguments,
            principal: ctx.security_context().principal.clone(),
            authorization_decision_ref: read.decision_ref.clone(),
        };
        let remaining = ctx.guard().remaining_duration_ms()?;
        let duration_limited = bigint(&remaining) <= read.contract.timeout_ms().into();
        let timeout_ms = if duration_limited {
            remaining
                .as_str()
                .parse::<i64>()
                .expect("positive remaining time is bounded by contract timeout")
        } else {
            i64::from(read.contract.timeout_ms())
        };
        ToolContractValidator.validate_request(&request, read.contract)?;
        // A timeout requests cancellation for this invocation only. A late
        // successful cleanup must not cancel a later sibling/nested READ.
        let result = execute_with_timeout(
            self.tools,
            &request,
            timeout_ms,
            CancellationSignal::default(),
        )
        .await
        .map_err(|e| {
            if matches!(&e,ToolCallError::Execution(e) if e.code=="TOOL_TIMEOUT")
                && duration_limited
            {
                ReadExecutionError::from(LimitExceeded(ResourceLimit::Duration))
            } else {
                e.into()
            }
        })?;
        ToolContractValidator.validate_result_structure(Some(&result), &request)?;
        ToolContractValidator.validate_result(&result, read.contract)?;
        ToolContractValidator.validate_result_hash(&result, self.max_decimal_bytes)?;
        if read.policy.required && result.presence == Presence::Empty && !read.policy.empty_is_valid
        {
            return Err(ToolExecutionError::new(
                "REQUIRED_TOOL_RESULT_MISSING",
                format!("required tool result is missing: {}", read.required.tool_id),
            )
            .into());
        }
        ctx.guard().check_deadline()?;
        // accept_partial=false preserves Partial/Unknown, as in the Oracle;
        // CHECK's later strict evaluator decides whether a rule can pass.
        if let Value::List(items) = &result.value {
            ctx.guard_mut().observe_collection(integer(items.len()))?;
        }
        let provenance = format!(
            "prov:{}:{}",
            request.invocation_id,
            &result.result_hash[7..19]
        );
        Ok(result.to_value(provenance))
    }
}
