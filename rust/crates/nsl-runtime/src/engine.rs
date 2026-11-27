mod admission;
mod controlled;
pub use controlled::*;
mod failure;
mod privacy;
pub use failure::*;

use crate::*;
use nsl_contract::ToolContractCatalog;
use nsl_core::{Completeness, ExecutionStatus, Value};
use nsl_ir::{IrVersion, NsoLimits, SkillObject};
use std::{collections::BTreeSet, sync::Arc};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RuntimeEnvironment {
    Production,
    Development,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RuntimeOptions {
    pub nso_limits: NsoLimits,
    pub max_decimal_bytes: usize,
    pub environment: RuntimeEnvironment,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProtectedRuntimeDebugRecord {
    pub execution_id: String,
    pub node_id: Option<String>,
    pub detail: String,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RuntimeDebugSinkError;
/// Explicit opt-in protected diagnostics. No default logging or backtrace output.
pub trait ProtectedRuntimeDebugSink: Send + Sync {
    fn record(&self, record: ProtectedRuntimeDebugRecord) -> Result<(), RuntimeDebugSinkError>;
}

/// Runtime orchestration with injected READ and controlled WRITE services.
/// Wire callers must use the existing strict NSO decoder. No transport is owned here.
pub struct RuntimeEngine<'a> {
    catalog: &'a ToolContractCatalog,
    tools: &'a dyn ToolExecutionPort,
    authorizer: &'a dyn ReadAuthorizationPort,
    measurements: &'a dyn ToolMeasurementSink,
    options: RuntimeOptions,
    debug: Option<&'a dyn ProtectedRuntimeDebugSink>,
}
impl<'a> RuntimeEngine<'a> {
    pub fn new(
        catalog: &'a ToolContractCatalog,
        tools: &'a dyn ToolExecutionPort,
        authorizer: &'a dyn ReadAuthorizationPort,
        measurements: &'a dyn ToolMeasurementSink,
        options: RuntimeOptions,
    ) -> Self {
        Self {
            catalog,
            tools,
            authorizer,
            measurements,
            options,
            debug: None,
        }
    }
    pub fn with_protected_debug_sink(mut self, sink: &'a dyn ProtectedRuntimeDebugSink) -> Self {
        self.debug = Some(sink);
        self
    }
    pub async fn execute(
        &self,
        skill: Arc<SkillObject>,
        request: ExecutionRequest,
    ) -> Result<ExecutionResult, RuntimeTerminalError> {
        self.execute_with_clock(skill, request, &SystemRuntimeClock::default())
            .await
    }
    pub async fn execute_with_clock<C: RuntimeClock + Send + Sync>(
        &self,
        skill: Arc<SkillObject>,
        request: ExecutionRequest,
        clock: &C,
    ) -> Result<ExecutionResult, RuntimeTerminalError> {
        self.run(skill, request, clock, None).await
    }
    pub async fn execute_pre_write_prefix<C: RuntimeClock + Send + Sync>(
        &self,
        skill: Arc<SkillObject>,
        request: ExecutionRequest,
        clock: &C,
    ) -> Result<ExecutionResult, RuntimeTerminalError> {
        if request.identity().business_operation_id().is_none() {
            return Err(RuntimeTerminalError::empty(RuntimeTerminalKind::Contract(
                "controlled execution requires a business operation identity".into(),
            )));
        }
        let end = admission::controlled_prefix_end(&skill).map_err(|message| {
            RuntimeTerminalError::empty(RuntimeTerminalKind::Contract(message.into()))
        })?;
        self.run(skill, request, clock, Some(end)).await
    }
    async fn run<C: RuntimeClock + Send + Sync>(
        &self,
        skill: Arc<SkillObject>,
        request: ExecutionRequest,
        clock: &C,
        prefix_end: Option<usize>,
    ) -> Result<ExecutionResult, RuntimeTerminalError> {
        self.run_with_context(skill, request, clock, prefix_end)
            .await
            .map(|(result, _)| result)
    }
    async fn run_with_context<'c, C: RuntimeClock + Send + Sync>(
        &self,
        skill: Arc<SkillObject>,
        request: ExecutionRequest,
        clock: &'c C,
        prefix_end: Option<usize>,
    ) -> Result<(ExecutionResult, Option<ExecutionContext<&'c C>>), RuntimeTerminalError> {
        let authorize = request
            .validate_for_execution(self.options.environment == RuntimeEnvironment::Production)
            .map_err(|e| e.to_string())
            .and_then(|()| {
                self.authorizer
                    .authorize(
                        &request.security_context().principal,
                        &format!("skill:{}:execute", skill.skill_id),
                        &BTreeSet::from(["nsl:skill:execute".into()]),
                    )
                    .map(|_| ())
                    .map_err(|e| e.message)
            });
        if let Err(message) = authorize {
            return Ok((
                ExecutionResult {
                    execution_id: request.identity().execution_id().into(),
                    skill_id: skill.skill_id.clone(),
                    skill_version: skill.skill_version.clone(),
                    semantic_hash: skill.semantic_hash.to_string(),
                    status: ExecutionStatus::Failed,
                    completeness: Completeness::Unknown,
                    checks: vec![],
                    outputs: vec![],
                    resources: ResourceUsage::default(),
                    error: Some(RuntimeErrorInfo {
                        code: "NSL-E5201".into(),
                        category: "AUTHORIZATION".into(),
                        message: nsl_core::redact_text(&message),
                        node_id: None,
                        detail_code: None,
                    }),
                    validation: None,
                    pre_write_eligibility: None,
                    approval: None,
                    write: None,
                    verification: None,
                    suspension: None,
                },
                None,
            ));
        }
        let mut ctx = ExecutionContext::with_clock(skill.clone(), request, clock);
        let work: Result<(), BoundaryError> = async {
            ctx.guard()
                .check_deadline()
                .map_err(StatementExecutionError::from)?;
            self.preflight(&skill, prefix_end.is_some())?;
            // Oracle evaluates these canonical hashes even with a null audit sink.
            for values in [ctx.request().inputs(), ctx.request().runtime_context()] {
                tool_result_hash(
                    &Value::Record(values.clone()),
                    self.options.max_decimal_bytes,
                )
                .map_err(RuntimeTerminalKind::Encoding)?;
            }
            admission::bind(&mut ctx)?;
            let measured = MeasuringToolExecutor {
                delegate: self.tools,
                clock,
                sink: self.measurements,
            };
            let evaluator = ReadEvaluator::new(
                self.catalog,
                &measured,
                self.authorizer,
                self.options.max_decimal_bytes,
            );
            evaluator
                .execute_block(
                    &mut ctx,
                    &skill.body[..prefix_end.unwrap_or(skill.body.len())],
                )
                .await?;
            ctx.guard()
                .check_deadline()
                .map_err(StatementExecutionError::from)?;
            Ok(())
        }
        .await;
        let (status, error) = match work {
            Ok(()) => (ExecutionStatus::Completed, None),
            Err(BoundaryError::Terminal(kind)) => {
                return Err(RuntimeTerminalError::capture(kind, &ctx));
            }
            Err(BoundaryError::Statement(error)) => {
                let protected = format!("{error:?}");
                let mut failure = failure::project(error)
                    .map_err(|kind| RuntimeTerminalError::capture(kind, &ctx))?;
                let sensitive = privacy::sensitive_values(&ctx, self.options.max_decimal_bytes)
                    .map_err(|e| {
                        RuntimeTerminalError::capture(RuntimeTerminalKind::Encoding(e), &ctx)
                    })?;
                if failure.unexpected
                    && let Some(sink) = self.debug
                {
                    let _ = sink.record(ProtectedRuntimeDebugRecord {
                        execution_id: ctx.identity().execution_id().into(),
                        node_id: ctx.current_node_id.clone(),
                        detail: privacy::redact(&protected, &sensitive),
                    });
                }
                failure.error.message = privacy::redact(&failure.error.message, &sensitive);
                failure.error.detail_code = failure
                    .error
                    .detail_code
                    .map(|s| privacy::redact(&s, &sensitive));
                (failure.status, Some(failure.error))
            }
        };
        let validation =
            if (skill.ir_version, skill.language_version.as_str()) == (IrVersion::V1_1, "0.2") {
                Some(ValidationSummary::from_checks(&ctx.checks).map_err(|e| {
                    RuntimeTerminalError::capture(
                        RuntimeTerminalKind::InvalidValidationSummary(e),
                        &ctx,
                    )
                })?)
            } else {
                None
            };
        let result = ExecutionResult {
            execution_id: ctx.identity().execution_id().into(),
            skill_id: skill.skill_id.clone(),
            skill_version: skill.skill_version.clone(),
            semantic_hash: skill.semantic_hash.to_string(),
            status,
            completeness: ctx.result_completeness(status),
            checks: ctx.checks.clone(),
            outputs: ctx.outputs.clone(),
            resources: ctx.usage(),
            error,
            validation,
            pre_write_eligibility: None,
            approval: None,
            write: None,
            verification: None,
            suspension: None,
        };
        Ok((result, Some(ctx)))
    }
}

enum BoundaryError {
    Statement(StatementExecutionError),
    Terminal(RuntimeTerminalKind),
}
impl From<StatementExecutionError> for BoundaryError {
    fn from(e: StatementExecutionError) -> Self {
        Self::Statement(e)
    }
}
impl From<RuntimeTerminalKind> for BoundaryError {
    fn from(e: RuntimeTerminalKind) -> Self {
        Self::Terminal(e)
    }
}
