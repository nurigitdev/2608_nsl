mod models;
mod prewrite;
mod principal;
use crate::WriteIntent;
pub use models::*;
use nsl_contract::ToolContract;
use nsl_core::{PreWriteEligibility, ToolCapability, UtcInstant};
use nsl_ir::SkillObject;
pub use prewrite::*;
pub use principal::*;
use std::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WriteGuardErrorKind {
    Model,
    Principal,
    ContractEncoding,
    PolicyPort,
    NotAllowed,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteGuardError {
    pub kind: WriteGuardErrorKind,
    pub path: String,
    pub message: &'static str,
}
impl fmt::Display for WriteGuardError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.path, self.message)
    }
}
impl std::error::Error for WriteGuardError {}
fn error(kind: WriteGuardErrorKind, path: &str, message: &'static str) -> WriteGuardError {
    WriteGuardError {
        kind,
        path: nsl_core::redact_text(path),
        message,
    }
}
fn required(value: &str, path: &str) -> Result<(), WriteGuardError> {
    crate::required(value, path).map_err(|_| {
        error(
            WriteGuardErrorKind::Model,
            path,
            "expected nonempty credential-free evidence text",
        )
    })
}
fn hash(value: &str, path: &str) -> Result<(), WriteGuardError> {
    required(value, path)?;
    crate::hash(value, path).map(|_| ()).map_err(|_| {
        error(
            WriteGuardErrorKind::Model,
            path,
            "expected lowercase SHA-256 evidence",
        )
    })
}

/// Opaque adapter failure. Protected policy-service details stay with the host.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RuntimePolicyError;
/// Trusted current-authority adapter. A caller-created ALLOW/reference alone is
/// not authenticated authority. The host supplies the current decision issuer.
pub trait RuntimePolicyPort: Send + Sync {
    fn authorize_write(
        &self,
        request: &WritePolicyRequest,
    ) -> Result<PolicyDecision, RuntimePolicyError>;
}

/// Borrowed Runtime projection; no WRITE -> Runtime dependency or mutable state.
#[derive(Clone, Copy)]
pub struct WriteGuardContext<'a> {
    pub principal: WritePrincipal<'a>,
    pub business_operation_id: Option<&'a str>,
}
pub struct WriteGuardRequest<'a> {
    /// The immutable, schema/integrity-admitted Skill from the current Runtime
    /// context. This borrowed model is not itself a package/authentication proof.
    pub skill: &'a SkillObject,
    pub intent: &'a WriteIntent,
    pub context: WriteGuardContext<'a>,
    pub current_contract: &'a ToolContract,
    pub contract_evidence: &'a WriteContractEvidence,
    pub execution_evidence: &'a WriteExecutionEvidence,
    pub policy_decision: Option<&'a PolicyDecision>,
    pub business_preconditions: Option<&'a BusinessPreconditionEvidence>,
}
pub fn write_policy_request(
    intent: &WriteIntent,
    context: WriteGuardContext<'_>,
    contract: &WriteContractEvidence,
) -> Result<WritePolicyRequest, WriteGuardError> {
    WritePolicyRequest::new(WritePolicyRequestSpec {
        tenant_id: context.principal.tenant_id.into(),
        subject_id: context.principal.subject_id.into(),
        action: "WRITE".into(),
        tool_id: intent.metadata().tool_id.clone(),
        risk_level: contract.metadata().risk_level,
        business_operation_id: intent.business_operation_id().to_string(),
        write_node_id: intent.metadata().node_id.clone(),
        write_intent_hash: intent.write_intent_hash().to_string(),
    })
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteGuard {
    risk_policy: RiskAcceptancePolicy,
}
impl Default for WriteGuard {
    fn default() -> Self {
        Self::new(RiskAcceptancePolicy::initial_v0_2())
    }
}
impl WriteGuard {
    pub fn new(risk_policy: RiskAcceptancePolicy) -> Self {
        Self { risk_policy }
    }
    /// Snapshot evaluation matches the Oracle after the explicit native admission
    /// checks. The supplied decision must originate from the trusted host issuer.
    pub fn evaluate(
        &self,
        request: WriteGuardRequest<'_>,
        now: UtcInstant,
    ) -> Result<WriteGuardDecision, WriteGuardError> {
        if let Some(blocked) = self.admit(&request)? {
            return Ok(blocked);
        }
        let expected =
            write_policy_request(request.intent, request.context, request.contract_evidence)?;
        Ok(Self::evaluate_policy(
            &request,
            request.policy_decision,
            &expected,
            now,
        ))
    }
    /// Admission happens before any policy-service call. Always obtains a fresh
    /// decision from the injected port; ignores a cached decision on the request.
    /// No provider call, reservation, persistence or mutation occurs here.
    pub fn authorize(
        &self,
        request: WriteGuardRequest<'_>,
        port: &dyn RuntimePolicyPort,
        now: UtcInstant,
    ) -> Result<WriteGuardDecision, WriteGuardError> {
        if let Some(blocked) = self.admit(&request)? {
            return Ok(blocked);
        }
        let expected =
            write_policy_request(request.intent, request.context, request.contract_evidence)?;
        let policy = port.authorize_write(&expected).map_err(|_| {
            error(
                WriteGuardErrorKind::PolicyPort,
                "$.policy_decision",
                "current policy authorization failed",
            )
        })?;
        Ok(Self::evaluate_policy(
            &request,
            Some(&policy),
            &expected,
            now,
        ))
    }
    fn admit(
        &self,
        r: &WriteGuardRequest<'_>,
    ) -> Result<Option<WriteGuardDecision>, WriteGuardError> {
        r.context.principal.validate()?;
        let reject = |reason| Ok(Some(WriteGuardDecision::blocked(reason)));
        let i = r.intent.metadata();
        let e = r.contract_evidence.metadata();
        let c = r.current_contract;
        if r.context.principal.tenant_id != i.tenant_id {
            return reject("WRITE_TENANT_BINDING_MISMATCH");
        }
        if r.context.business_operation_id != Some(i.business_operation_id.as_str()) {
            return reject("WRITE_BUSINESS_OPERATION_MISMATCH");
        }
        if e.tool_id != i.tool_id
            || e.capability != ToolCapability::Write
            || !e.preconditions_supported
            || c.tool_id() != i.tool_id
            || c.capability().known() != Some(ToolCapability::Write)
            || c.risk_level() != Some(e.risk_level)
        {
            return reject("WRITE_CONTRACT_INVALID");
        }
        // A validated WRITE ToolContract already requires atomic semantics and
        // preconditions_supported=true. Do not re-admit an impossible raw model.
        let current_hash = c.contract_hash().map_err(|_| {
            error(
                WriteGuardErrorKind::ContractEncoding,
                "$.current_contract",
                "current Tool Contract cannot be canonically encoded",
            )
        })?;
        if current_hash.as_str() != e.contract_hash {
            return reject("WRITE_CONTRACT_INVALID");
        }
        // Empty/credential-bearing requirements cannot become vacuous permission.
        if required(c.required_scope(), "$.current_contract.required_scope").is_err()
            || !r.context.principal.scopes.contains(c.required_scope())
        {
            return reject("WRITE_SCOPE_DENIED");
        }
        if !self.risk_policy.accepts(e.risk_level) {
            return reject("RISK_NOT_ACCEPTED");
        }
        if r.skill.semantic_hash.as_str() != i.skill_semantic_hash {
            return reject("WRITE_SKILL_BINDING_MISMATCH");
        }
        let mut declarations = r
            .skill
            .required_tools
            .iter()
            .filter(|t| t.tool_ref == i.tool_ref);
        let Some(declared) = declarations.next() else {
            return reject("WRITE_DECLARED_CONTRACT_MISMATCH");
        };
        if declarations.next().is_some()
            || declared.tool_id != i.tool_id
            || declared.version != c.version()
            || declared.contract_hash != current_hash
            || declared.kind.capability() != ToolCapability::Write
            || declared.kind.risk_level() != c.risk_level()
            || declared.required_scope != c.required_scope()
        {
            return reject("WRITE_DECLARED_CONTRACT_MISMATCH");
        }
        Ok(None)
    }
    fn evaluate_policy(
        r: &WriteGuardRequest<'_>,
        policy: Option<&PolicyDecision>,
        expected: &WritePolicyRequest,
        now: UtcInstant,
    ) -> WriteGuardDecision {
        let Some(policy) = policy else {
            return WriteGuardDecision::blocked("POLICY_EVIDENCE_MISSING");
        };
        if !policy.matches(expected) {
            return WriteGuardDecision::blocked("POLICY_BINDING_MISMATCH");
        }
        let p = policy.metadata();
        if p.evaluated_at > now || now >= p.expires_at {
            return WriteGuardDecision::blocked("POLICY_NOT_CURRENT");
        }
        if p.effect != PolicyEffect::Allow {
            return WriteGuardDecision::blocked("POLICY_DENIED");
        }
        let Some(pre) = r.business_preconditions else {
            return WriteGuardDecision::blocked("BUSINESS_PRECONDITION_MISSING");
        };
        let b = pre.metadata();
        let i = r.intent.metadata();
        let c = r.contract_evidence.metadata();
        if b.tenant_id != r.context.principal.tenant_id
            || b.business_operation_id != i.business_operation_id
            || b.write_node_id != i.node_id
            || b.tool_id != i.tool_id
            || b.contract_hash != c.contract_hash
            || b.payload_hash != i.payload_hash
            || b.write_intent_hash != i.write_intent_hash
        {
            return WriteGuardDecision::blocked("BUSINESS_PRECONDITION_BINDING_MISMATCH");
        }
        // Admission established context tenant == intent tenant and both tools
        // equal the current contract; the comparisons above cover all bindings.
        if b.observed_at > now || now >= b.expires_at {
            return WriteGuardDecision::blocked("BUSINESS_PRECONDITION_NOT_CURRENT");
        }
        if !b.provider_atomic_enforcement {
            return WriteGuardDecision::blocked("PROVIDER_PRECONDITION_UNAVAILABLE");
        }
        let mut current = r.execution_evidence.clone();
        current.current_policy_allowed = true;
        current.tool_preconditions_passed = true;
        current.business_preconditions_passed = true;
        PreWriteEvaluator.evaluate(&current)
    }
}
/// Later execution boundaries must require ALLOWED, never inspect a reason alone.
pub fn require_write_allowed(decision: &WriteGuardDecision) -> Result<(), WriteGuardError> {
    if decision.eligibility() != PreWriteEligibility::Allowed {
        return Err(error(
            WriteGuardErrorKind::NotAllowed,
            "$.decision",
            "WRITE eligibility is not ALLOWED",
        ));
    }
    Ok(())
}
