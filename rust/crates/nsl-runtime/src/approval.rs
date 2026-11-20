mod model;
mod summary;
pub use model::*;
pub use summary::*;

use nsl_core::CredentialMaterialError;
use std::fmt;
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ApprovalModelError {
    Required { field: String },
    Hash { field: String },
    Credential(CredentialMaterialError),
    ExpiryBeforeDecision,
}
impl fmt::Display for ApprovalModelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Required { field } => write!(f, "{field} must be a non-empty string"),
            Self::Hash { field } => write!(f, "{field} must be a lowercase SHA-256 value"),
            Self::Credential(e) => e.fmt(f),
            Self::ExpiryBeforeDecision => {
                f.write_str("ApprovalEvidence.expires_at precedes decided_at")
            }
        }
    }
}
impl std::error::Error for ApprovalModelError {}
impl From<CredentialMaterialError> for ApprovalModelError {
    fn from(e: CredentialMaterialError) -> Self {
        Self::Credential(e)
    }
}
fn required(value: &str, field: &str) -> Result<(), ApprovalModelError> {
    if crate::data::blank(value) {
        return Err(ApprovalModelError::Required {
            field: field.into(),
        });
    }
    nsl_core::ensure_no_credential_material(&nsl_core::ValueData::String(value.into()))?;
    Ok(())
}
