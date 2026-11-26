mod models;
use crate::{PersistedWriteOutcome, WriteExecutionOutcome};
pub use models::*;
use nsl_contract::{ContractEncodingError, ToolContract};
use nsl_core::{CommitState, TypeError, VerificationStatus};
use std::{collections::BTreeMap, fmt, future::Future, pin::Pin};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VerificationErrorKind {
    Model,
    Binding,
    Type(TypeError),
    ContractEncoding(ContractEncodingError),
    Cancelled,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct VerificationError {
    pub kind: VerificationErrorKind,
    pub path: String,
    pub message: &'static str,
}
impl VerificationError {
    pub fn detail_code(&self) -> Option<&'static str> {
        None
    }
}
impl fmt::Display for VerificationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.path, self.message)
    }
}
impl std::error::Error for VerificationError {}
fn error(kind: VerificationErrorKind, path: &str, message: &'static str) -> VerificationError {
    VerificationError {
        kind,
        path: nsl_core::redact_text(path),
        message,
    }
}
fn required(s: &str, path: &str) -> Result<(), VerificationError> {
    crate::requested::required(s, path).map_err(|_| {
        error(
            VerificationErrorKind::Model,
            path,
            "expected nonempty credential-free text",
        )
    })
}
fn optional(s: &Option<String>, path: &str) -> Result<(), VerificationError> {
    if let Some(s) = s {
        required(s, path)?;
    }
    Ok(())
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VerificationPortError {
    Timeout,
    Dependency,
    Malformed,
    Cancelled,
}
pub type VerificationFuture<'a> = Pin<
    Box<dyn Future<Output = Result<VerificationObservation, VerificationPortError>> + Send + 'a>,
>;
/// Privileged host READ-only adapter. It must enforce the method's actual READ
/// capability, tenant authority and inbound budgets; no business WRITE/retry.
pub trait PostWriteVerificationPort: Send + Sync {
    fn read<'a>(&'a self, request: &'a VerificationRequest) -> VerificationFuture<'a>;
}
pub struct VerificationContext<'a> {
    pub tenant_id: &'a str,
    pub business_operation_id: Option<&'a str>,
    pub keys_by_write_node: &'a BTreeMap<String, String>,
    pub payload_hashes_by_write_node: &'a BTreeMap<String, String>,
}
pub struct PostWriteVerifier;
impl PostWriteVerifier {
    /// Only a receipt-validated outcome can enter. Original WRITE/result data
    /// remain borrowed and unchanged on verification failure or cancellation.
    pub async fn verify(
        &self,
        persisted: &PersistedWriteOutcome,
        contract: &ToolContract,
        context: VerificationContext<'_>,
        port: Option<&dyn PostWriteVerificationPort>,
    ) -> Result<VerificationResult, VerificationError> {
        let WriteExecutionOutcome::Committed(result) = persisted.invocation().outcome() else {
            return Err(error(
                VerificationErrorKind::Model,
                "$.outcome",
                "verification requires a committed result",
            ));
        };
        let s = result.metadata();
        let identity = s.identity.metadata();
        if contract.capability().as_str() != "WRITE" || contract.tool_id() != identity.tool_id {
            return Err(error(
                VerificationErrorKind::Binding,
                "$.contract",
                "verification requires the exact WRITE contract",
            ));
        }
        let hash = contract.contract_hash().map_err(|e| {
            error(
                VerificationErrorKind::ContractEncoding(e),
                "$.contract",
                "WRITE contract cannot be encoded",
            )
        })?;
        if hash.as_str() != persisted.invocation().request().metadata().contract_hash {
            return Err(error(
                VerificationErrorKind::Binding,
                "$.contract_hash",
                "verification contract differs from the original WRITE contract",
            ));
        }
        if context.tenant_id != identity.tenant_id
            || context.business_operation_id != Some(identity.business_operation_id.as_str())
        {
            return Err(error(
                VerificationErrorKind::Binding,
                "$.context",
                "verification context differs from committed WRITE identity",
            ));
        }
        if context.keys_by_write_node.get(&identity.node_id) != Some(&identity.idempotency_key)
            || context.payload_hashes_by_write_node.get(&identity.node_id)
                != Some(&identity.payload_hash)
        {
            return Err(error(
                VerificationErrorKind::Binding,
                "$.context.idempotency",
                "verification idempotency state differs from committed WRITE",
            ));
        }
        let verification = contract.write_semantics().and_then(|s| s.verification());
        let tool = verification.and_then(|v| v.tool_id());
        let mut spec = VerificationResultSpec {
            identity: s.identity.clone(),
            commit_state: CommitState::Committed,
            status: VerificationStatus::NotRequested,
            business_reference: s.business_reference.clone(),
            verification_tool_id: tool.map(str::to_owned),
            reason_code: None,
            evidence_ref: None,
            observed_at: None,
        };
        let Some(tool) = tool else {
            return VerificationResult::new(spec);
        };
        let port = port.ok_or_else(|| {
            error(
                VerificationErrorKind::Model,
                "$.port",
                "verification requires a READ-only port",
            )
        })?;
        let request = VerificationRequest::new(VerificationRequestSpec {
            identity: s.identity.clone(),
            verification_tool_id: tool.into(),
            business_reference: s.business_reference.clone(),
            provider_transaction_id: s.provider_transaction_id.clone(),
            reference_schema: verification.and_then(|v| v.reference_schema()).cloned(),
            consistency_hint: verification
                .and_then(|v| v.consistency_hint())
                .map(str::to_owned),
        })?;
        let expected_hash = request.request_hash()?;
        let observation = match port.read(&request).await {
            Ok(v) => v,
            Err(VerificationPortError::Cancelled) => {
                return Err(error(
                    VerificationErrorKind::Cancelled,
                    "$.port",
                    "verification was cancelled",
                ));
            }
            Err(kind) => {
                spec.status = VerificationStatus::Unknown;
                spec.reason_code = Some(
                    match kind {
                        VerificationPortError::Timeout => "VERIFICATION_TIMEOUT",
                        VerificationPortError::Malformed => "VERIFICATION_RESPONSE_MALFORMED",
                        _ => "VERIFICATION_DEPENDENCY_FAILURE",
                    }
                    .into(),
                );
                return VerificationResult::new(spec);
            }
        };
        let observed = observation.metadata();
        let rejected = if observed.request_hash != expected_hash.as_str()
            || observed.business_reference != s.business_reference
        {
            Some("VERIFICATION_BINDING_MISMATCH")
        } else if observed.observed_at < s.committed_at {
            Some("VERIFICATION_OBSERVATION_STALE")
        } else {
            None
        };
        if let Some(reason) = rejected {
            spec.status = VerificationStatus::Unknown;
            spec.reason_code = Some(reason.into());
        } else {
            spec.status = if observed.matched {
                VerificationStatus::Verified
            } else {
                VerificationStatus::Failed
            };
            spec.reason_code = observed.reason_code.clone();
            spec.evidence_ref = Some(observed.evidence_ref.clone());
            spec.observed_at = Some(observed.observed_at);
        }
        VerificationResult::new(spec)
    }
}
