use super::*;
use nsl_core::{CommitState, ToolCapability};
impl RuntimeEngine<'_> {
    /// Consumes the non-Clone, claim-validated R7 handoff. Every later failure
    /// retains the real context/observation; automatic business retries are zero.
    pub async fn execute_approved<C: RuntimeClock + Clone + Send + Sync>(
        &self,
        resumed: ApprovalResumeOutcome<C>,
        services: ControlledWriteServices<'_>,
        options: ControlledWriteOptions<'_>,
    ) -> ControlledExecution<C> {
        let (context, validation, proof, consumed) = resumed.into_controlled_parts();
        let mut execution = ControlledExecution {
            context,
            result: None,
            persisted: None,
            verification: None,
            failure: None,
        };
        // Pin orchestration on the heap while retaining the original awaited
        // cleanup and evidence ownership; no task or retry is introduced.
        if let Err(error) = Box::pin(self.controlled_work(
            &mut execution,
            validation,
            &proof,
            consumed,
            services,
            options,
        ))
        .await
        {
            execution.failure = Some(error);
        }
        execution
    }
    async fn controlled_work<C: RuntimeClock + Clone + Send + Sync>(
        &self,
        execution: &mut ControlledExecution<C>,
        validated: ApprovalValidationResult,
        proof: &ApprovalEvidence,
        consumed: bool,
        services: ControlledWriteServices<'_>,
        o: ControlledWriteOptions<'_>,
    ) -> Result<(), ControlledExecutionError> {
        if !consumed || !validated.approval_gate_passed() {
            return Err(binding("$.approval.validation"));
        }
        let ctx = &mut execution.context;
        let skill = ctx.skill_arc();
        let plan = Plan::new(&skill)?;
        self.preflight(&skill, true).map_err(boundary)?;
        ctx.guard()
            .check_deadline()
            .map_err(StatementExecutionError::from)?;
        let intent = self.controlled_intent(ctx, &plan, o.limits).await?;
        let i = intent.metadata();
        let request_id = ctx
            .checkpoint
            .approval_request_ids_by_write_node
            .get(&i.node_id)
            .ok_or_else(|| binding("$.checkpoint.approval_request_id"))?
            .clone();
        let expected = ApprovalBindings::new(ApprovalBindingsSpec {
            tenant_id: ctx.tenant_id().into(),
            execution_id: ctx.identity().execution_id().into(),
            business_operation_id: ctx
                .identity()
                .business_operation_id()
                .ok_or_else(|| binding("$.business_operation_id"))?
                .into(),
            approval_id: i.approval_id.clone(),
            approval_request_id: request_id.clone(),
            skill_semantic_hash: skill.semantic_hash.to_string(),
            payload_hash: i.payload_hash.clone(),
            write_intent_hash: i.write_intent_hash.clone(),
        })
        .map_err(|_| binding("$.approval.bindings"))?;
        if ctx.approvals.records.get(&i.approval_id) != Some(&ApprovalStatus::Approved)
            || proof.check_bindings(&expected).is_err()
            || proof.is_expired_at(o.now)
        {
            return Err(binding("$.approval.bindings"));
        }
        let declared = skill
            .required_tools
            .iter()
            .find(|r| r.tool_ref == i.tool_ref)
            .ok_or_else(|| binding("$.skill.required_tools"))?;
        let contract = self
            .catalog
            .resolve(&declared.tool_id, &declared.version)
            .map_err(|_| binding("$.contract"))?;
        let semantics = contract
            .write_semantics()
            .ok_or_else(|| binding("$.contract.write_semantics"))?;
        let contract_evidence = WriteContractEvidence::new(WriteContractEvidenceSpec {
            tool_id: contract.tool_id().into(),
            contract_hash: declared.contract_hash.as_str().into(),
            capability: ToolCapability::Write,
            risk_level: contract
                .risk_level()
                .ok_or_else(|| binding("$.contract.risk_level"))?,
            preconditions_supported: semantics.preconditions_supported(),
        })?;
        let validation = ValidationSummary::from_checks(&ctx.checks).map_err(|e| {
            RuntimeTerminalError::capture(RuntimeTerminalKind::InvalidValidationSummary(e), ctx)
        })?;
        let mut evidence = WriteExecutionEvidence::new(validation.spec().status.as_str().parse()?);
        evidence.approval_status = ApprovalStatus::Approved;
        evidence.approval_evidence_valid = true;
        evidence.runtime_preflight_passed = true;
        evidence.intent_binding_valid = true;
        evidence.trust_valid = o.host.trust_valid;
        evidence.replay_live_write_allowed = o.host.replay_live_write_allowed;
        evidence.durability_ready = o.host.durability_ready;
        evidence.idempotency_ready = o.host.idempotency_ready;
        let p = &ctx.security_context().principal;
        let decision = WriteGuard::default().authorize(
            WriteGuardRequest {
                skill: &skill,
                intent: &intent,
                current_contract: contract,
                contract_evidence: &contract_evidence,
                execution_evidence: &evidence,
                context: WriteGuardContext {
                    principal: WritePrincipal {
                        tenant_id: &p.tenant_id,
                        subject_id: &p.subject_id,
                        actor_type: &p.actor_type,
                        roles: &p.roles,
                        scopes: &p.scopes,
                        auth_context_ref: &p.auth_context_ref,
                        verified: p.verification == PrincipalVerification::Verified,
                        on_behalf_of: p.on_behalf_of.as_deref(),
                    },
                    business_operation_id: ctx.identity().business_operation_id(),
                },
                policy_decision: None,
                business_preconditions: Some(o.business_preconditions),
            },
            services.policy,
            o.now,
        )?;
        require_write_allowed(&decision)?;
        let tenant = ctx.tenant_id().to_string();
        let business = ctx.identity().business_operation_id().map(str::to_string);
        let execution_id = ctx.identity().execution_id().to_string();
        let reservation = IdempotencyCoordinator::new(services.idempotency)
            .reserve(IdempotencyRequest {
                intent: &intent,
                context: IdempotencyContext {
                    tenant_id: &tenant,
                    business_operation_id: business.as_deref(),
                    keys_by_write_node: &mut ctx.idempotency.keys_by_write_node,
                    payload_hashes_by_write_node: &mut ctx.idempotency.payload_hashes_by_write_node,
                },
                contract,
                provider_evidence: o.provider_evidence,
                now: o.now,
                required_until: o.required_until,
            })
            .await?;
        let prepared = match WriteCallRequestBuilder.build(
            WriteCallBuildRequest {
                intent: &intent,
                context: WriteCallContext {
                    tenant_id: &tenant,
                    execution_id: &execution_id,
                    business_operation_id: business.as_deref(),
                },
                contract_evidence: &contract_evidence,
                reservation: &reservation,
                approval_request_id: &request_id,
                invocation_id: o.invocation_id,
                business_preconditions: o.business_preconditions,
                trace_refs: o.trace_refs,
            },
            o.limits,
        )? {
            WriteCallPreparation::Created(p) => p,
            WriteCallPreparation::Reused(r) | WriteCallPreparation::Pending(r) => {
                return Err(ControlledExecutionError::ExistingReservation(Box::new(r)));
            }
        };
        let durable = WriteRequestedCoordinator::new(services.requested)
            .persist(
                prepared,
                &decision,
                WriteEvidenceContext {
                    tenant_id: &tenant,
                    execution_id: &execution_id,
                    business_operation_id: business.as_deref(),
                    write_request_durable: &mut ctx.checkpoint.write_request_durable,
                },
                o.requested_at,
            )
            .await?;
        let invocation = WriteInvocationCoordinator::new(services.provider)
            .invoke_with_timeout(durable, i64::from(contract.timeout_ms()), o.cancellation)
            .await;
        execution.persisted = Some(
            WriteOutcomeCoordinator::new(services.outcomes)
                .persist(
                    invocation,
                    WriteOutcomeContext {
                        tenant_id: &tenant,
                        execution_id: &execution_id,
                        business_operation_id: business.as_deref(),
                        keys_by_write_node: &ctx.idempotency.keys_by_write_node,
                        payload_hashes_by_write_node: &ctx.idempotency.payload_hashes_by_write_node,
                        journal: &mut ctx.writes.entries,
                    },
                    o.recorded_at,
                )
                .await?,
        );
        let persisted = execution.persisted.as_ref().expect("stored outcome");
        let outcome = persisted.invocation().outcome();
        let status = match outcome {
            WriteExecutionOutcome::NotCommitted(_) => ExecutionStatus::Failed,
            WriteExecutionOutcome::Unknown(_) => ExecutionStatus::ReconciliationRequired,
            WriteExecutionOutcome::Committed(_) => {
                execution.verification = Some(
                    PostWriteVerifier
                        .verify(
                            persisted,
                            contract,
                            VerificationContext {
                                tenant_id: &tenant,
                                business_operation_id: business.as_deref(),
                                keys_by_write_node: &ctx.idempotency.keys_by_write_node,
                                payload_hashes_by_write_node: &ctx
                                    .idempotency
                                    .payload_hashes_by_write_node,
                            },
                            services.verification,
                        )
                        .await?,
                );
                match execution
                    .verification
                    .as_ref()
                    .expect("observed verification")
                    .metadata()
                    .status
                {
                    VerificationStatus::Verified | VerificationStatus::NotRequested => {
                        ExecutionStatus::Completed
                    }
                    VerificationStatus::Failed | VerificationStatus::Unknown => {
                        ExecutionStatus::ReconciliationRequired
                    }
                }
            }
        };
        let mut error = None;
        let mut final_status = status;
        if status == ExecutionStatus::Completed {
            let WriteExecutionOutcome::Committed(value) = outcome else {
                unreachable!()
            };
            let (symbol, expression) = plan.expression(&skill);
            let envelope = &value.metadata().value;
            if envelope.type_ref() != &expression.type_info
                || !expression.type_info.accepts(envelope.value())
            {
                return Err(binding("$.write.result.value"));
            }
            ctx.bind(BindingNamespace::Variable, symbol.into(), envelope.clone())
                .map_err(StatementExecutionError::from)?;
            ctx.observe_completeness(envelope.completeness());
            ctx.checkpoint.last_completed_node_id = Some(skill.body[plan.write].node_id.clone());
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
            if let Err(e) = evaluator
                .execute_block(ctx, &skill.body[plan.write + 1..])
                .await
            {
                let failure =
                    failure::project(e).map_err(|e| RuntimeTerminalError::capture(e, ctx))?;
                let sensitive = privacy::sensitive_values(ctx, self.options.max_decimal_bytes)
                    .map_err(|e| {
                        RuntimeTerminalError::capture(RuntimeTerminalKind::Encoding(e), ctx)
                    })?;
                let mut info = failure.error;
                info.message = privacy::redact(&info.message, &sensitive);
                info.detail_code = info.detail_code.map(|s| privacy::redact(&s, &sensitive));
                final_status = failure.status;
                error = Some(info);
            }
        }
        let validation = ValidationSummary::from_checks(&ctx.checks).map_err(|e| {
            RuntimeTerminalError::capture(RuntimeTerminalKind::InvalidValidationSummary(e), ctx)
        })?;
        let record = persisted.entry().record().metadata();
        let identity = record.identity.metadata();
        execution.result = Some(ExecutionResult {
            execution_id,
            skill_id: skill.skill_id.clone(),
            skill_version: skill.skill_version.clone(),
            semantic_hash: skill.semantic_hash.to_string(),
            status: final_status,
            completeness: ctx.result_completeness(final_status),
            checks: ctx.checks.clone(),
            outputs: ctx.outputs.clone(),
            resources: ctx.usage(),
            error,
            validation: Some(validation),
            pre_write_eligibility: Some(PreWriteEligibility::Allowed),
            approval: Some(
                ApprovalSummary::new(
                    ApprovalStatus::Approved,
                    i.approval_id.clone(),
                    Some(request_id),
                )
                .expect("validated approval identity"),
            ),
            write: Some(
                WriteSummary::new(WriteSummarySpec {
                    commit_state: record.commit_state,
                    tool_id: identity.tool_id.clone(),
                    node_id: identity.node_id.clone(),
                    idempotency_key: identity.idempotency_key.clone(),
                    payload_hash: identity.payload_hash.clone(),
                    provider_transaction_id: record.provider_transaction_id.clone(),
                    business_reference: record.business_reference.clone(),
                })
                .expect("persisted identity"),
            ),
            verification: execution.verification.as_ref().map(|v| {
                let m = v.metadata();
                VerificationSummary::new(m.status, m.reason_code.clone(), m.evidence_ref.clone())
                    .expect("validated verification")
            }),
            suspension: None,
        });
        debug_assert_eq!(outcome.commit_state(), record.commit_state);
        debug_assert!(matches!(
            record.commit_state,
            CommitState::Committed | CommitState::NotCommitted | CommitState::UnknownCommitState
        ));
        Ok(())
    }
}
