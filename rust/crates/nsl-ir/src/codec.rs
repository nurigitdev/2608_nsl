mod bounded;
mod decode;
mod encode;
mod hash;
mod json;

use std::fmt;

pub use hash::{artifact_sha256, source_manifest_sha256};

use nsl_core::{DecimalError, TypeError};

use crate::{NsoValidationError, SkillObject, validate_nso_document};

/// Inclusive NSO input/output limits. The root is at depth 0 and counts as one
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
    SourceManifestMismatch,
    SemanticHashMismatch,
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
            Self::SourceManifestMismatch => {
                formatter.write_str("source bundle manifest hash mismatch")
            }
            Self::SemanticHashMismatch => formatter.write_str("semantic hash mismatch"),
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

/// Canonical JSON and integrity checks for IR 1.0 and 1.1.
pub struct NsoCodec;

impl NsoCodec {
    /// Compiler output boundary, using the same JSON units as decode limits.
    pub fn encode_with_limits(skill: &SkillObject, limits: NsoLimits) -> Result<Vec<u8>> {
        bounded::preflight(skill, limits)?;
        let data =
            encode::project_with_budget(skill, encode::DecimalBudget::Total(limits.max_bytes))?;
        let bytes = bounded::canonical(&data, limits)?;
        validate_nso_document(&data).map_err(NsoCodecError::Validation)?;
        Ok(bytes)
    }
    /// Encode a validated wire document. The budget is per fixed-point token.
    pub fn encode(skill: &SkillObject, max_decimal_bytes: usize) -> Result<Vec<u8>> {
        let data = encode::project(skill, max_decimal_bytes)?;
        validate_nso_document(&data).map_err(NsoCodecError::Validation)?;
        json::canonical(&data)
    }

    /// Read UTF-8 JSON under the caller's bounds and construct the typed NSO.
    /// Source bundle and semantic digests are verified before returning the model.
    pub fn decode(bytes: &[u8], limits: NsoLimits) -> Result<SkillObject> {
        let document = json::parse(bytes, limits)?;
        validate_nso_document(&document).map_err(NsoCodecError::Validation)?;
        let model = decode::skill(&document)?;
        let budget = encode::DecimalBudget::Total(limits.max_bytes);
        if hash::semantic_sha256(&model, budget)? != model.semantic_hash {
            return Err(NsoCodecError::SemanticHashMismatch);
        }
        Ok(model)
    }
}

type Result<T> = std::result::Result<T, NsoCodecError>;
