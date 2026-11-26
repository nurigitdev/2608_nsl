use crate::requested::{hash, key, required};
use crate::{WriteCallRequest, WriteExecutionError};
use nsl_core::{CommitState, UtcInstant, ValueEnvelope};
use std::fmt;

#[derive(Clone, Eq, PartialEq)]
pub struct WriteOutcomeIdentitySpec {
    pub tenant_id: String,
    pub business_operation_id: String,
    pub invocation_id: String,
    pub node_id: String,
    pub tool_id: String,
    pub idempotency_key: String,
    pub payload_hash: String,
    pub approval_request_id: String,
    pub write_intent_hash: String,
}
#[derive(Clone, Eq, PartialEq)]
pub struct WriteOutcomeIdentity(WriteOutcomeIdentitySpec);
impl WriteOutcomeIdentity {
    pub fn new(s: WriteOutcomeIdentitySpec) -> Result<Self, WriteExecutionError> {
        for (name, value) in [
            ("tenant_id", &s.tenant_id),
            ("business_operation_id", &s.business_operation_id),
            ("invocation_id", &s.invocation_id),
            ("node_id", &s.node_id),
            ("tool_id", &s.tool_id),
            ("approval_request_id", &s.approval_request_id),
        ] {
            required(value, &format!("$.identity.{name}"))?;
        }
        required(&s.idempotency_key, "$.identity.idempotency_key")?;
        key(&s.idempotency_key, "$.identity.idempotency_key").map_err(|mut error| {
            error.kind = crate::WriteExecutionErrorKind::Outcome;
            error
        })?;
        hash(&s.payload_hash, "$.identity.payload_hash")?;
        hash(&s.write_intent_hash, "$.identity.write_intent_hash")?;
        Ok(Self(s))
    }
    pub fn from_request(request: &WriteCallRequest) -> Self {
        let s = request.metadata();
        Self(WriteOutcomeIdentitySpec {
            tenant_id: s.tenant_id.clone(),
            business_operation_id: s.business_operation_id.clone(),
            invocation_id: s.invocation_id.clone(),
            node_id: s.node_id.clone(),
            tool_id: s.tool_id.clone(),
            idempotency_key: s.idempotency_key.clone(),
            payload_hash: s.payload_hash.clone(),
            approval_request_id: s.approval_request_id.clone(),
            write_intent_hash: s.write_intent_hash.clone(),
        })
    }
    pub fn metadata(&self) -> &WriteOutcomeIdentitySpec {
        &self.0
    }
}
fn optional(value: &Option<String>, path: &str) -> Result<(), WriteExecutionError> {
    if let Some(value) = value {
        required(value, path)?;
    }
    Ok(())
}
fn evidence(refs: &[String]) -> Result<(), WriteExecutionError> {
    for (i, value) in refs.iter().enumerate() {
        required(value, &format!("$.evidence_refs[{i}]"))?;
    }
    Ok(())
}
/// Owned envelope, moved without recursively cloning or encoding the provider's
/// value/type graph. The host adapter applies its inbound resource limits.
#[derive(Eq, PartialEq)]
pub struct WriteResultEnvelopeSpec {
    pub identity: WriteOutcomeIdentity,
    pub value: ValueEnvelope,
    pub business_reference: String,
    pub provider_transaction_id: Option<String>,
    pub committed_at: UtcInstant,
    pub evidence_refs: Vec<String>,
}
#[derive(Eq, PartialEq)]
pub struct WriteResultEnvelope(WriteResultEnvelopeSpec);
impl WriteResultEnvelope {
    pub fn new(s: WriteResultEnvelopeSpec) -> Result<Self, WriteExecutionError> {
        required(&s.business_reference, "$.business_reference")?;
        optional(&s.provider_transaction_id, "$.provider_transaction_id")?;
        evidence(&s.evidence_refs)?;
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &WriteResultEnvelopeSpec {
        &self.0
    }
    pub fn commit_state(&self) -> CommitState {
        CommitState::Committed
    }
}
#[derive(Eq, PartialEq)]
pub struct WriteFailureSpec {
    pub identity: WriteOutcomeIdentity,
    pub reason_code: String,
    pub message: String,
    pub provider_transaction_id: Option<String>,
    pub evidence_refs: Vec<String>,
}
fn failure(s: &WriteFailureSpec) -> Result<(), WriteExecutionError> {
    required(&s.reason_code, "$.reason_code")?;
    required(&s.message, "$.message")?;
    optional(&s.provider_transaction_id, "$.provider_transaction_id")?;
    evidence(&s.evidence_refs)
}
#[derive(Eq, PartialEq)]
pub struct WriteNotCommitted(WriteFailureSpec);
impl WriteNotCommitted {
    pub fn new(s: WriteFailureSpec) -> Result<Self, WriteExecutionError> {
        failure(&s)?;
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &WriteFailureSpec {
        &self.0
    }
    pub fn commit_state(&self) -> CommitState {
        CommitState::NotCommitted
    }
}
#[derive(Eq, PartialEq)]
pub struct WriteCommitUnknown(WriteFailureSpec);
impl WriteCommitUnknown {
    pub fn new(s: WriteFailureSpec) -> Result<Self, WriteExecutionError> {
        failure(&s)?;
        Ok(Self(s))
    }
    pub fn metadata(&self) -> &WriteFailureSpec {
        &self.0
    }
    pub fn commit_state(&self) -> CommitState {
        CommitState::UnknownCommitState
    }
}
#[derive(Eq, PartialEq)]
pub enum WriteExecutionOutcome {
    Committed(WriteResultEnvelope),
    NotCommitted(WriteNotCommitted),
    Unknown(WriteCommitUnknown),
}
impl WriteExecutionOutcome {
    pub fn identity(&self) -> &WriteOutcomeIdentity {
        match self {
            Self::Committed(v) => &v.metadata().identity,
            Self::NotCommitted(v) => &v.metadata().identity,
            Self::Unknown(v) => &v.metadata().identity,
        }
    }
    pub fn commit_state(&self) -> CommitState {
        match self {
            Self::Committed(v) => v.commit_state(),
            Self::NotCommitted(v) => v.commit_state(),
            Self::Unknown(v) => v.commit_state(),
        }
    }
}
macro_rules! safe_debug {($($ty:ty),*)=>{$(impl fmt::Debug for $ty {fn fmt(&self,f:&mut fmt::Formatter<'_>)->fmt::Result {f.debug_struct(stringify!($ty)).finish_non_exhaustive()}})*};}
safe_debug!(
    WriteOutcomeIdentitySpec,
    WriteOutcomeIdentity,
    WriteResultEnvelopeSpec,
    WriteResultEnvelope,
    WriteFailureSpec,
    WriteNotCommitted,
    WriteCommitUnknown,
    WriteExecutionOutcome
);
