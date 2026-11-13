use std::{fmt, str::FromStr};

const SHA256_LENGTH: usize = 64;
const IDEMPOTENCY_PREFIX: &str = "idem:sha256:";
const PAYLOAD_PREFIX: &str = "sha256:";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IdentityError {
    Blank,
    InvalidIdempotencyKey,
    InvalidPayloadHash,
}

impl fmt::Display for IdentityError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::Blank => "identifier must contain a non-whitespace character",
            Self::InvalidIdempotencyKey => {
                "idempotency key must start with idem:sha256: and contain a 64-character suffix"
            }
            Self::InvalidPayloadHash => {
                "payload hash must start with sha256: and contain 64 lowercase hexadecimal digits"
            }
        })
    }
}

impl std::error::Error for IdentityError {}

macro_rules! identity_type {
    ($name:ident, $validate:ident) => {
        #[derive(Clone, Debug, Eq, Hash, PartialEq)]
        pub struct $name(String);

        impl $name {
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl FromStr for $name {
            type Err = IdentityError;

            fn from_str(value: &str) -> Result<Self, Self::Err> {
                $validate(value)?;
                Ok(Self(value.to_owned()))
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(self.as_str())
            }
        }
    };
}

identity_type!(BusinessOperationId, validate_nonblank);
identity_type!(ApprovalRequestId, validate_nonblank);
identity_type!(IdempotencyKey, validate_idempotency_key);
identity_type!(PayloadHash, validate_payload_hash);

fn validate_nonblank(value: &str) -> Result<(), IdentityError> {
    // Python str.strip() also treats U+001C through U+001F as whitespace.
    let whitespace =
        |character: char| character.is_whitespace() || matches!(character, '\u{1c}'..='\u{1f}');
    if value.trim_matches(whitespace).is_empty() {
        return Err(IdentityError::Blank);
    }
    Ok(())
}

fn validate_idempotency_key(value: &str) -> Result<(), IdentityError> {
    // The Oracle validates prefix and Python str length at this boundary.
    let suffix = value.strip_prefix(IDEMPOTENCY_PREFIX);
    if suffix.is_some_and(|suffix| suffix.chars().take(SHA256_LENGTH + 1).count() == SHA256_LENGTH)
    {
        return Ok(());
    }
    Err(IdentityError::InvalidIdempotencyKey)
}

fn validate_payload_hash(value: &str) -> Result<(), IdentityError> {
    let suffix = value.strip_prefix(PAYLOAD_PREFIX);
    if suffix.is_some_and(|suffix| {
        suffix.len() == SHA256_LENGTH
            && suffix
                .bytes()
                .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    }) {
        return Ok(());
    }
    Err(IdentityError::InvalidPayloadHash)
}
