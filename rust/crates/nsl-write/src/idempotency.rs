mod models;
use crate::WriteIntent;
pub use models::*;
use nsl_contract::ToolContract;
use nsl_core::{IdempotencyKey, UtcInstant, ValueData, canonical_json};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fmt, future::Future, pin::Pin};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IdempotencyErrorKind {
    Identity,
    Type,
    Retention,
    State,
    Conflict,
    Port,
    ContractEncoding,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IdempotencyError {
    pub kind: IdempotencyErrorKind,
    pub path: String,
    pub message: &'static str,
}
impl IdempotencyError {
    pub fn detail_code(&self) -> Option<&'static str> {
        match self.kind {
            IdempotencyErrorKind::Retention => Some("IDEMPOTENCY_RETENTION_UNAVAILABLE"),
            IdempotencyErrorKind::State => Some("IDEMPOTENCY_STATE_INVALID"),
            IdempotencyErrorKind::Conflict => Some("IDEMPOTENCY_CONFLICT"),
            _ => None,
        }
    }
}
impl fmt::Display for IdempotencyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.path, self.message)
    }
}
impl std::error::Error for IdempotencyError {}
fn error(kind: IdempotencyErrorKind, path: &str, message: &'static str) -> IdempotencyError {
    IdempotencyError {
        kind,
        path: nsl_core::redact_text(path),
        message,
    }
}
fn required(value: &str, path: &str) -> Result<(), IdempotencyError> {
    crate::required(value, path).map_err(|_| {
        error(
            IdempotencyErrorKind::Identity,
            path,
            "expected a nonempty credential-free string",
        )
    })
}
fn hash(value: &str, path: &str) -> Result<nsl_core::PayloadHash, IdempotencyError> {
    required(value, path)?;
    value.parse().map_err(|_| {
        error(
            IdempotencyErrorKind::Identity,
            path,
            "expected a lowercase SHA-256 value",
        )
    })
}
fn key(value: &str, path: &str) -> Result<IdempotencyKey, IdempotencyError> {
    required(value, path)?;
    value.parse().map_err(|_| {
        error(
            IdempotencyErrorKind::Identity,
            path,
            "expected an idem SHA-256 prefix and 64-character suffix",
        )
    })
}

#[derive(Clone, Copy, Debug, Default)]
pub struct IdempotencyKeyFactory;
impl IdempotencyKeyFactory {
    pub fn create(
        &self,
        tenant_id: &str,
        business_operation_id: &str,
        node_id: &str,
    ) -> Result<IdempotencyKey, IdempotencyError> {
        for (name, value) in [
            ("tenant_id", tenant_id),
            ("business_operation_id", business_operation_id),
            ("write_node_id", node_id),
        ] {
            required(value, &format!("$.{name}"))?;
        }
        let data = ValueData::Object(
            [
                ("tenant_id", tenant_id),
                ("business_operation_id", business_operation_id),
                ("write_node_id", node_id),
            ]
            .into_iter()
            .map(|(k, v)| (k.into(), ValueData::String(v.into())))
            .collect(),
        );
        let mut h = Sha256::new();
        h.update(b"NSL-IDEMPOTENCY-KEY-V1\0");
        h.update(canonical_json(&data).expect("string-only identity"));
        let hash = nsl_core::PayloadHash::from_digest(h.finalize().into());
        Ok(format!("idem:{hash}").parse().expect("generated identity"))
    }
}
pub fn idempotency_identity(intent: &WriteIntent) -> Result<IdempotencyIdentity, IdempotencyError> {
    let s = intent.metadata();
    let key = IdempotencyKeyFactory.create(&s.tenant_id, &s.business_operation_id, &s.node_id)?;
    IdempotencyIdentity::new(IdempotencyIdentitySpec {
        tenant_id: s.tenant_id.clone(),
        business_operation_id: s.business_operation_id.clone(),
        write_node_id: s.node_id.clone(),
        idempotency_key: key.to_string(),
        payload_hash: s.payload_hash.clone(),
    })
}

#[derive(Clone, Copy, Debug, Default)]
pub struct IdempotencyRetentionValidator;
impl IdempotencyRetentionValidator {
    pub fn validate<'a>(
        &self,
        intent: &WriteIntent,
        contract: &ToolContract,
        evidence: &'a ProviderIdempotencyEvidence,
        now: UtcInstant,
        required_until: UtcInstant,
    ) -> Result<&'a ProviderIdempotencyEvidence, IdempotencyError> {
        let rejected = |path, msg| error(IdempotencyErrorKind::Retention, path, msg);
        if required_until <= now {
            return Err(rejected(
                "$.required_until",
                "required idempotency horizon must end after now",
            ));
        }
        if contract.tool_id() != intent.metadata().tool_id
            || evidence.metadata().tool_id != intent.metadata().tool_id
        {
            return Err(rejected(
                "$.provider_evidence.tool_id",
                "idempotency evidence tool binding mismatch",
            ));
        }
        let contract_hash = contract.contract_hash().map_err(|_| {
            error(
                IdempotencyErrorKind::ContractEncoding,
                "$.contract",
                "Tool Contract cannot be canonically encoded",
            )
        })?;
        if evidence.contract_hash() != &contract_hash {
            return Err(rejected(
                "$.provider_evidence.contract_hash",
                "idempotency evidence contract binding mismatch",
            ));
        }
        let semantics = contract
            .write_semantics()
            .filter(|_| contract.capability().known() == Some(nsl_core::ToolCapability::Write))
            .ok_or_else(|| rejected("$.contract", "idempotency requires a WRITE Tool Contract"))?;
        let idem = semantics.idempotency();
        if !idem.required()
            || idem.scope() != "TOOL_AND_TENANT"
            || idem.duplicate_behavior() != "RETURN_ORIGINAL_RESULT"
            || idem.payload_conflict_behavior() != "IDEMPOTENCY_CONFLICT"
        {
            return Err(rejected(
                "$.contract.write_semantics.idempotency",
                "Tool Contract lacks the required durable duplicate semantics",
            ));
        }
        let e = evidence.metadata();
        if e.retention_policy != idem.retention() {
            return Err(rejected(
                "$.provider_evidence.retention_policy",
                "idempotency retention policy binding mismatch",
            ));
        }
        if !e.durable_duplicate_guarantee {
            return Err(rejected(
                "$.provider_evidence.durable_duplicate_guarantee",
                "provider durable duplicate guarantee is absent",
            ));
        }
        if e.verified_at > now {
            return Err(rejected(
                "$.provider_evidence.verified_at",
                "provider idempotency evidence is from the future",
            ));
        }
        if now >= e.guaranteed_until {
            return Err(rejected(
                "$.provider_evidence.guaranteed_until",
                "provider idempotency guarantee has expired",
            ));
        }
        if e.guaranteed_until < required_until {
            return Err(rejected(
                "$.provider_evidence.guaranteed_until",
                "provider idempotency guarantee does not cover recovery horizon",
            ));
        }
        Ok(evidence)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct IdempotencyPortError;
pub type IdempotencyEvidenceFuture<'a> =
    Pin<Box<dyn Future<Output = Result<IdempotencyReservation, IdempotencyPortError>> + Send + 'a>>;
/// Host-owned durable, atomic reservation boundary, scoped by tenant/tool/key.
///
/// The host must persist the first reservation before acknowledging CREATED,
/// retain duplicate detection through `retained_until` across process restarts,
/// and serialize concurrent reservations for the same scoped key. An identical
/// payload returns REUSED with the original business result when available;
/// changed payload returns CONFLICT without replacing the original reservation.
/// REUSED with no result means pending/unknown, never proof of a committed WRITE.
///
/// After checking durable state, the acknowledgement must echo the *entire
/// incoming* evidence record, even on reuse when the guarantee reference or
/// retention horizon changes. This is separate from the original stored result.
/// An I/O error or dropped future can occur after persistence and implies neither
/// rollback nor permission to repeat the business operation. The coordinator
/// changes its borrowed checkpoint maps only after a matching non-conflict ack.
pub trait IdempotencyEvidencePort: Send + Sync {
    fn reserve<'a>(
        &'a self,
        evidence: &'a IdempotencyEvidenceRecord,
    ) -> IdempotencyEvidenceFuture<'a>;
}
/// Borrowed Runtime projection; retains the existing raw checkpoint map models.
pub struct IdempotencyContext<'a> {
    pub tenant_id: &'a str,
    pub business_operation_id: Option<&'a str>,
    pub keys_by_write_node: &'a mut BTreeMap<String, String>,
    pub payload_hashes_by_write_node: &'a mut BTreeMap<String, String>,
}
pub struct IdempotencyRequest<'a> {
    pub intent: &'a WriteIntent,
    pub context: IdempotencyContext<'a>,
    pub contract: &'a ToolContract,
    pub provider_evidence: &'a ProviderIdempotencyEvidence,
    pub now: UtcInstant,
    pub required_until: UtcInstant,
}
pub struct IdempotencyCoordinator<'a> {
    port: &'a dyn IdempotencyEvidencePort,
}
impl<'a> IdempotencyCoordinator<'a> {
    pub fn new(port: &'a dyn IdempotencyEvidencePort) -> Self {
        Self { port }
    }
    pub async fn reserve(
        &self,
        input: IdempotencyRequest<'_>,
    ) -> Result<IdempotencyReservation, IdempotencyError> {
        let IdempotencyRequest {
            intent,
            context,
            contract,
            provider_evidence,
            now,
            required_until,
        } = input;
        let s = intent.metadata();
        let state = |path, msg| error(IdempotencyErrorKind::State, path, msg);
        if context.tenant_id != s.tenant_id {
            return Err(state(
                "$.context.tenant_id",
                "idempotency tenant binding mismatch",
            ));
        }
        let business = context.business_operation_id.ok_or_else(|| {
            state(
                "$.context.business_operation_id",
                "execution context lacks a business operation id",
            )
        })?;
        if business != s.business_operation_id {
            return Err(state(
                "$.context.business_operation_id",
                "idempotency business operation binding mismatch",
            ));
        }
        let evidence = IdempotencyRetentionValidator.validate(
            intent,
            contract,
            provider_evidence,
            now,
            required_until,
        )?;
        let identity = idempotency_identity(intent)?;
        if context
            .keys_by_write_node
            .get(&s.node_id)
            .is_some_and(|k| k != identity.idempotency_key().as_str())
        {
            return Err(state(
                &format!("$.context.keys_by_write_node[{:?}]", s.node_id),
                "checkpoint idempotency key does not match semantic identity",
            ));
        }
        if context
            .payload_hashes_by_write_node
            .get(&s.node_id)
            .is_some_and(|h| h != intent.payload_hash().as_str())
        {
            return Err(error(
                IdempotencyErrorKind::Conflict,
                &format!("$.context.payload_hashes_by_write_node[{:?}]", s.node_id),
                "idempotency key is already bound to a different payload",
            ));
        }
        let record = IdempotencyEvidenceRecord::new(IdempotencyEvidenceRecordSpec {
            tenant_id: s.tenant_id.clone(),
            business_operation_id: s.business_operation_id.clone(),
            write_node_id: s.node_id.clone(),
            tool_id: s.tool_id.clone(),
            contract_hash: evidence.metadata().contract_hash.clone(),
            idempotency_key: identity.idempotency_key().to_string(),
            payload_hash: s.payload_hash.clone(),
            provider_guarantee_ref: evidence.metadata().provider_guarantee_ref.clone(),
            retained_until: evidence.metadata().guaranteed_until,
        })?;
        let reservation = self.port.reserve(&record).await.map_err(|_| {
            error(
                IdempotencyErrorKind::Port,
                "$.reservation",
                "durable idempotency reservation failed",
            )
        })?;
        if reservation.evidence() != &record {
            return Err(state(
                "$.reservation.evidence",
                "idempotency evidence port returned mismatched evidence",
            ));
        }
        if reservation.disposition() == IdempotencyDisposition::Conflict {
            return Err(error(
                IdempotencyErrorKind::Conflict,
                "$.reservation",
                "idempotency key is already bound to a different payload",
            ));
        }
        context
            .keys_by_write_node
            .insert(s.node_id.clone(), record.idempotency_key().to_string());
        context
            .payload_hashes_by_write_node
            .insert(s.node_id.clone(), record.payload_hash().to_string());
        Ok(reservation)
    }
}
