mod decode;
mod encode;
mod json;

use std::fmt;

use nsl_core::{DecimalError, TypeError};

use crate::{NsoValidationError, SkillObject, validate_nso_document};

/// Inclusive limits for an NSO input. The root is at depth 0 and counts as one
/// node. Each JSON value counts once. Object keys do not count as nodes.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NsoLimits {
    pub max_bytes: usize,
    pub max_depth: usize,
    pub max_nodes: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NsoLimit {
    Bytes,
    Depth,
    Nodes,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum NsoCodecError {
    Limit(NsoLimit),
    Json { offset: usize, reason: &'static str },
    Validation(NsoValidationError),
    Decimal(DecimalError),
    Type(TypeError),
    IntegerTooWide,
    InconsistentSourceBundle,
    InvalidModelField(&'static str),
}

impl fmt::Display for NsoCodecError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Limit(limit) => write!(formatter, "NSO exceeds the {limit:?} limit"),
            Self::Json { offset, reason } => {
                write!(formatter, "invalid NSO JSON at byte {offset}: {reason}")
            }
            Self::Validation(error) => error.fmt(formatter),
            Self::Decimal(error) => error.fmt(formatter),
            Self::Type(error) => error.fmt(formatter),
            Self::IntegerTooWide => formatter.write_str("NSO integer exceeds 4300 digits"),
            Self::InconsistentSourceBundle => {
                formatter.write_str("source bundle digest differs between hashes and build")
            }
            Self::InvalidModelField(field) => write!(formatter, "invalid NSO model field {field}"),
        }
    }
}

impl std::error::Error for NsoCodecError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Validation(error) => Some(error),
            Self::Decimal(error) => Some(error),
            Self::Type(error) => Some(error),
            _ => None,
        }
    }
}

/// Canonical JSON codec for structured IR 1.0 and 1.1. Hash verification is
/// separate from JSON and schema validation.
pub struct NsoCodec;

impl NsoCodec {
    /// Encode a validated wire document. The budget is per fixed-point token.
    pub fn encode(skill: &SkillObject, max_decimal_bytes: usize) -> Result<Vec<u8>> {
        let data = encode::project(skill, max_decimal_bytes)?;
        validate_nso_document(&data).map_err(NsoCodecError::Validation)?;
        json::canonical(&data)
    }

    /// Read UTF-8 JSON under the caller's bounds and construct the typed NSO.
    /// The returned model still requires integrity verification before admission.
    pub fn decode(bytes: &[u8], limits: NsoLimits) -> Result<SkillObject> {
        let document = json::parse(bytes, limits)?;
        validate_nso_document(&document).map_err(NsoCodecError::Validation)?;
        decode::skill(&document)
    }
}

type Result<T> = std::result::Result<T, NsoCodecError>;
