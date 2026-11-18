use crate::{ExecutionPrincipal, data::blank};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, fmt};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReadAuthorizationError {
    pub message: String,
    pub decision_ref: Option<String>,
}
impl fmt::Display for ReadAuthorizationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}
impl std::error::Error for ReadAuthorizationError {}
/// Trusted current-authority port. The returned reference correlates a decision;
/// a reference alone is not proof of authority.
pub trait ReadAuthorizationPort: Send + Sync {
    fn authorize(
        &self,
        principal: &ExecutionPrincipal,
        action: &str,
        required_scopes: &BTreeSet<String>,
    ) -> Result<String, ReadAuthorizationError>;
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StaticReadAuthorizer {
    pub policy_id: String,
    pub policy_version: String,
}
impl Default for StaticReadAuthorizer {
    fn default() -> Self {
        Self {
            policy_id: "NSL-VS-DEFAULT-DENY".into(),
            policy_version: "1".into(),
        }
    }
}
impl ReadAuthorizationPort for StaticReadAuthorizer {
    fn authorize(
        &self,
        principal: &ExecutionPrincipal,
        action: &str,
        required_scopes: &BTreeSet<String>,
    ) -> Result<String, ReadAuthorizationError> {
        let reject = |message: String| ReadAuthorizationError {
            message,
            decision_ref: None,
        };
        principal
            .validate(false)
            .map_err(|e| reject(e.to_string()))?;
        if blank(action) {
            return Err(reject("authorization action is required".into()));
        }
        if required_scopes.is_empty() {
            return Err(reject("explicit required scopes are required".into()));
        }
        if required_scopes.iter().any(|s| blank(s)) {
            return Err(reject("required scopes must be non-empty strings".into()));
        }
        let missing = required_scopes
            .difference(&principal.scopes)
            .cloned()
            .collect::<Vec<_>>();
        let effect = if missing.is_empty() { "ALLOW" } else { "DENY" };
        let scopes = required_scopes
            .iter()
            .cloned()
            .collect::<Vec<_>>()
            .join(",");
        let material = [
            self.policy_id.as_str(),
            self.policy_version.as_str(),
            principal.tenant_id.as_str(),
            principal.subject_id.as_str(),
            action,
            scopes.as_str(),
            effect,
        ]
        .join("|");
        let digest = Sha256::digest(material.as_bytes());
        let decision_ref = format!(
            "authz-{}",
            digest[..8]
                .iter()
                .map(|v| format!("{v:02x}"))
                .collect::<String>()
        );
        if !missing.is_empty() {
            return Err(ReadAuthorizationError {
                message: format!(
                    "missing required scope(s) for {action}: {}",
                    missing.join(", ")
                ),
                decision_ref: Some(decision_ref),
            });
        }
        Ok(decision_ref)
    }
}
