#![forbid(unsafe_code)]

mod canonical_json;
mod credentials;
mod decimal;
mod identity;
mod money;
mod oracle_unicode;
mod status;
mod type_ref;
mod utc_instant;
mod value;

pub use canonical_json::{CanonicalJsonError, MAX_JSON_INTEGER_DIGITS, canonical_json};
pub use credentials::{
    CredentialMaterialError, REDACTED, ensure_no_credential_material, redact_text,
};
pub use decimal::{DecimalError, NslDecimal};
pub use identity::{
    ApprovalRequestId, BusinessOperationId, IdempotencyKey, IdentityError, PayloadHash,
};
pub use money::{Money, MoneyError, MoneySumError, sum_money};
pub use status::{
    ApprovalDecision, ApprovalStatus, CheckStatus, CommitState, Completeness, DataClassification,
    ExecutionStatus, PreWriteEligibility, Presence, RiskLevel, ToolCapability, UnknownStatus,
    VerificationStatus,
};
pub use type_ref::{TypeError, TypeKind, TypeRef};
pub use value::{CurrencyCode, Integer, Value, ValueData, ValueEnvelope, ValueError};

pub use utc_instant::{UtcInstant, UtcInstantError};
