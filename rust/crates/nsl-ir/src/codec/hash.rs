use nsl_core::PayloadHash;
use sha2::{Digest, Sha256};

use crate::{BuildMetadata, BuildSource, SkillObject};

use super::{
    Result,
    encode::{self, DecimalBudget},
    json,
};

fn sha256(bytes: &[u8]) -> PayloadHash {
    PayloadHash::from_digest(Sha256::digest(bytes).into())
}

/// Hash the manifest's canonical JSON array in the caller's entry order.
pub fn source_manifest_sha256(sources: &[BuildSource]) -> Result<PayloadHash> {
    let bytes = json::canonical(&encode::source_manifest(sources))?;
    Ok(sha256(&bytes))
}

/// Hash the exact bytes emitted by the NSO encoder.
pub fn artifact_sha256(canonical_nso: &[u8]) -> PayloadHash {
    sha256(canonical_nso)
}

impl BuildMetadata {
    /// The Oracle identifies a source bundle by its ordered source manifest.
    pub fn compute_source_bundle_sha256(&self) -> Result<PayloadHash> {
        source_manifest_sha256(&self.sources)
    }
}

impl SkillObject {
    pub fn compute_semantic_sha256_with_limits(
        &self,
        limits: super::NsoLimits,
    ) -> Result<PayloadHash> {
        super::bounded::preflight(self, limits)?;
        let data = encode::semantic_document(self, DecimalBudget::Total(limits.max_bytes))?;
        Ok(sha256(&super::bounded::canonical(&data, limits)?))
    }
    /// Hash the canonical IR fields, excluding build metadata and declared hashes.
    pub fn compute_semantic_sha256(&self, max_decimal_bytes: usize) -> Result<PayloadHash> {
        semantic_sha256(self, DecimalBudget::PerToken(max_decimal_bytes))
    }
}

pub(super) fn semantic_sha256(skill: &SkillObject, budget: DecimalBudget) -> Result<PayloadHash> {
    let document = encode::semantic_document(skill, budget)?;
    let bytes = json::canonical(&document)?;
    Ok(sha256(&bytes))
}
