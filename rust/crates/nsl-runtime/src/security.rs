use crate::data::{bigint, blank, integer, object, optional, strings, text};
use nsl_core::{
    CredentialMaterialError, DataClassification, Integer, ensure_no_credential_material,
};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ModelError {
    Invalid(&'static str),
    Credential(CredentialMaterialError),
}
impl fmt::Display for ModelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Invalid(s) => f.write_str(s),
            Self::Credential(e) => e.fmt(f),
        }
    }
}
impl std::error::Error for ModelError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Credential(e) => Some(e),
            _ => None,
        }
    }
}
impl From<CredentialMaterialError> for ModelError {
    fn from(e: CredentialMaterialError) -> Self {
        Self::Credential(e)
    }
}
pub(crate) fn required(value: &str, name: &'static str) -> Result<(), ModelError> {
    if blank(value) {
        return Err(ModelError::Invalid(name));
    }
    ensure_no_credential_material(&text(value))?;
    Ok(())
}
pub(crate) fn optional_identity(value: Option<&str>, name: &'static str) -> Result<(), ModelError> {
    if let Some(s) = value {
        required(s, name)?;
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PrincipalVerification {
    Unverified,
    Verified,
}
impl PrincipalVerification {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unverified => "UNVERIFIED",
            Self::Verified => "VERIFIED",
        }
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionPrincipal {
    pub tenant_id: String,
    pub subject_id: String,
    pub actor_type: String,
    pub roles: BTreeSet<String>,
    pub scopes: BTreeSet<String>,
    pub auth_context_ref: String,
    pub verification: PrincipalVerification,
    pub on_behalf_of: Option<String>,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AuthorizationError(pub &'static str);
impl fmt::Display for AuthorizationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for AuthorizationError {}
impl ExecutionPrincipal {
    // Explicit validation, as in the Oracle. Constructing a security-context
    // model does not turn host-supplied verification metadata into authority.
    pub fn validate(&self, require_verified: bool) -> Result<(), AuthorizationError> {
        let reject = |s| Err(AuthorizationError(s));
        if blank(&self.tenant_id) || blank(&self.subject_id) {
            return reject("tenant_id and subject_id are required");
        }
        if !matches!(self.actor_type.as_str(), "USER" | "SERVICE") {
            return reject("actor_type must be USER or SERVICE");
        }
        if blank(&self.auth_context_ref) {
            return reject("auth_context_ref is required");
        }
        if self.roles.iter().any(|s| blank(s)) {
            return reject("roles must be a set of non-empty strings");
        }
        if self.scopes.iter().any(|s| blank(s)) {
            return reject("scopes must be a set of non-empty strings");
        }
        if self.on_behalf_of.as_deref().is_some_and(blank) {
            return reject("on_behalf_of must be a non-empty string");
        }
        if require_verified && self.verification != PrincipalVerification::Verified {
            return reject("verified execution principal is required");
        }
        let data = object([
            ("tenant_id", text(&self.tenant_id)),
            ("subject_id", text(&self.subject_id)),
            ("actor_type", text(&self.actor_type)),
            (
                "roles",
                strings(&self.roles.iter().cloned().collect::<Vec<_>>()),
            ),
            (
                "scopes",
                strings(&self.scopes.iter().cloned().collect::<Vec<_>>()),
            ),
            ("auth_context_ref", text(&self.auth_context_ref)),
            ("on_behalf_of", optional(self.on_behalf_of.as_deref())),
        ]);
        ensure_no_credential_material(&data).map_err(|_| {
            AuthorizationError("credential material is forbidden in ExecutionPrincipal")
        })
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DataHandlingPolicy {
    max_trace_classification: DataClassification,
    snapshot_retention_days: Integer,
    audit_retention_days: Integer,
}
impl DataHandlingPolicy {
    pub fn new(
        classification: DataClassification,
        snapshot: Integer,
        audit: Integer,
    ) -> Result<Self, ModelError> {
        if bigint(&snapshot) <= 0.into() {
            return Err(ModelError::Invalid(
                "snapshot_retention_days must be positive",
            ));
        }
        if bigint(&audit) <= 0.into() {
            return Err(ModelError::Invalid("audit_retention_days must be positive"));
        }
        Ok(Self {
            max_trace_classification: classification,
            snapshot_retention_days: snapshot,
            audit_retention_days: audit,
        })
    }
    pub fn max_trace_classification(&self) -> DataClassification {
        self.max_trace_classification
    }
    pub fn snapshot_retention_days(&self) -> &Integer {
        &self.snapshot_retention_days
    }
    pub fn audit_retention_days(&self) -> &Integer {
        &self.audit_retention_days
    }
}
impl Default for DataHandlingPolicy {
    fn default() -> Self {
        Self {
            max_trace_classification: DataClassification::Internal,
            snapshot_retention_days: integer(30),
            audit_retention_days: integer(90),
        }
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionSecurityContext {
    pub principal: ExecutionPrincipal,
    pub data_policy: DataHandlingPolicy,
}
pub type SecurityContext = ExecutionSecurityContext;
impl ExecutionSecurityContext {
    pub fn tenant_id(&self) -> &str {
        &self.principal.tenant_id
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionIdentity {
    execution_id: String,
    business_operation_id: Option<String>,
}
impl ExecutionIdentity {
    pub fn new(
        execution_id: String,
        business_operation_id: Option<String>,
    ) -> Result<Self, ModelError> {
        required(
            &execution_id,
            "ExecutionIdentity.execution_id must be a non-empty string",
        )?;
        optional_identity(
            business_operation_id.as_deref(),
            "ExecutionIdentity.business_operation_id must be a non-empty string",
        )?;
        Ok(Self {
            execution_id,
            business_operation_id,
        })
    }
    pub fn execution_id(&self) -> &str {
        &self.execution_id
    }
    pub fn business_operation_id(&self) -> Option<&str> {
        self.business_operation_id.as_deref()
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionRequest {
    identity: ExecutionIdentity,
    inputs: BTreeMap<String, nsl_core::Value>,
    runtime_context: BTreeMap<String, nsl_core::Value>,
    security: ExecutionSecurityContext,
}
impl ExecutionRequest {
    pub fn new(
        execution_id: String,
        inputs: BTreeMap<String, nsl_core::Value>,
        runtime_context: BTreeMap<String, nsl_core::Value>,
        security: ExecutionSecurityContext,
        business_operation_id: Option<String>,
    ) -> Result<Self, ModelError> {
        required(
            &execution_id,
            "ExecutionRequest.execution_id must be a non-empty string",
        )?;
        optional_identity(
            business_operation_id.as_deref(),
            "ExecutionRequest.business_operation_id must be a non-empty string",
        )?;
        crate::data::screen(&inputs)?;
        crate::data::screen(&runtime_context)?;
        Ok(Self {
            identity: ExecutionIdentity {
                execution_id,
                business_operation_id,
            },
            inputs,
            runtime_context,
            security,
        })
    }
    pub fn identity(&self) -> &ExecutionIdentity {
        &self.identity
    }
    pub fn security_context(&self) -> &ExecutionSecurityContext {
        &self.security
    }
    pub fn inputs(&self) -> &BTreeMap<String, nsl_core::Value> {
        &self.inputs
    }
    pub fn runtime_context(&self) -> &BTreeMap<String, nsl_core::Value> {
        &self.runtime_context
    }
    pub fn validate_for_execution(&self, require_verified: bool) -> Result<(), AuthorizationError> {
        self.security.principal.validate(require_verified)
    }
}
