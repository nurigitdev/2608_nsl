use super::*;
/// Prefix state may be retained for an idempotent suspension retry. It cannot
/// bypass the R7 claim/proof boundary or be passed directly to the WRITE runner.
pub struct ControlledPrefix<C: RuntimeClock> {
    context: Option<ExecutionContext<C>>,
    result: ExecutionResult,
    checkpoint: Option<ExecutionCheckpoint>,
    intent: Option<WriteIntent>,
}
impl<C: RuntimeClock> ControlledPrefix<C> {
    pub fn context(&self) -> Option<&ExecutionContext<C>> {
        self.context.as_ref()
    }
    pub fn result(&self) -> &ExecutionResult {
        &self.result
    }
    pub fn checkpoint(&self) -> Option<&ExecutionCheckpoint> {
        self.checkpoint.as_ref()
    }
    pub fn intent(&self) -> Option<&WriteIntent> {
        self.intent.as_ref()
    }
    pub async fn suspend(
        &mut self,
        coordinator: &ApprovalSuspensionCoordinator,
        request: &ApprovalRequest,
        checkpoints: &dyn CheckpointPort,
        approvals: &dyn ApprovalPort,
        evidence: &PreWriteEvidence,
    ) -> Result<ApprovalSuspension, ControlledExecutionError> {
        let ctx = self
            .context
            .as_mut()
            .ok_or_else(|| binding("$.prefix.context"))?;
        let intent = self
            .intent
            .as_ref()
            .ok_or_else(|| binding("$.prefix.intent"))?;
        let i = intent.metadata();
        let r = request.metadata();
        if r.write_node_id != i.node_id
            || r.tool_id != i.tool_id
            || request.bindings().approval_id() != i.approval_id
        {
            return Err(binding("$.approval_request.write_node_id"));
        }
        let b = request.bindings();
        if b.payload_hash() != intent.payload_hash() || b.write_intent_hash() != i.write_intent_hash
        {
            return Err(binding("$.approval_request.bindings"));
        }
        Ok(coordinator
            .suspend(ctx, request, checkpoints, approvals, Some(evidence))
            .await?)
    }
}
impl RuntimeEngine<'_> {
    pub async fn prepare_controlled<'c, C: RuntimeClock + Send + Sync>(
        &self,
        skill: Arc<SkillObject>,
        request: ExecutionRequest,
        clock: &'c C,
        checkpoints: &CheckpointCoordinator,
        limits: WriteSnapshotLimits,
    ) -> Result<ControlledPrefix<&'c C>, ControlledExecutionError> {
        let plan = Plan::new(&skill)?;
        if request.identity().business_operation_id().is_none() {
            return Err(binding("$.request.business_operation_id"));
        }
        let (result, mut context) = self
            .run_with_context(skill.clone(), request, clock, Some(plan.approval))
            .await?;
        let ready = result.status == ExecutionStatus::Completed;
        let (checkpoint, intent) = if ready {
            let ctx = context.as_mut().expect("completed prefix context");
            let checkpoint = checkpoints.capture(ctx)?;
            let intent = self.controlled_intent(ctx, &plan, limits).await?;
            (Some(checkpoint), Some(intent))
        } else {
            (None, None)
        };
        Ok(ControlledPrefix {
            context,
            result,
            checkpoint,
            intent,
        })
    }
    pub(super) async fn controlled_intent<C: RuntimeClock + Clone + Send + Sync>(
        &self,
        ctx: &mut ExecutionContext<C>,
        plan: &Plan,
        limits: WriteSnapshotLimits,
    ) -> Result<WriteIntent, ControlledExecutionError> {
        let skill = ctx.skill_arc();
        let (_, expression) = plan.expression(&skill);
        let nsl_ir::ExpressionKind::Write(w) = &expression.kind else {
            unreachable!()
        };
        let clock = ctx.guard().clock().clone();
        let measured = MeasuringToolExecutor {
            delegate: self.tools,
            clock: &clock,
            sink: self.measurements,
        };
        let evaluator = ReadEvaluator::new(
            self.catalog,
            &measured,
            self.authorizer,
            self.options.max_decimal_bytes,
        );
        let mut args = BTreeMap::new();
        for arg in &w.arguments {
            let value = evaluator
                .evaluate(ctx, &arg.value)
                .await
                .map_err(StatementExecutionError::from)?;
            args.insert(arg.name.clone(), value);
        }
        Ok(WriteIntentBuilder.build(
            expression,
            &args,
            WriteIntentContext {
                tenant_id: ctx.tenant_id(),
                business_operation_id: ctx.identity().business_operation_id(),
                skill: &skill,
            },
            limits,
        )?)
    }
}
