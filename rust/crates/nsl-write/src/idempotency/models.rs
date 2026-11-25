use super::{IdempotencyError, IdempotencyErrorKind, error, hash, key, required};
use nsl_core::{BusinessOperationId, IdempotencyKey, PayloadHash, UtcInstant, ValueData};
use std::fmt;
fn data(items: impl IntoIterator<Item = (&'static str, ValueData)>) -> ValueData {
    ValueData::Object(items.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
fn text(s: impl Into<String>) -> ValueData {
    ValueData::String(s.into())
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IdempotencyIdentitySpec {
    pub tenant_id: String,
    pub business_operation_id: String,
    pub write_node_id: String,
    pub idempotency_key: String,
    pub payload_hash: String,
}
#[derive(Clone, Eq, PartialEq)]
pub struct IdempotencyIdentity {
    spec: IdempotencyIdentitySpec,
    business: BusinessOperationId,
    key: IdempotencyKey,
    payload: PayloadHash,
}
impl IdempotencyIdentity {
    pub fn new(s: IdempotencyIdentitySpec) -> Result<Self, IdempotencyError> {
        for (name, v) in [
            ("tenant_id", &s.tenant_id),
            ("business_operation_id", &s.business_operation_id),
            ("write_node_id", &s.write_node_id),
        ] {
            required(v, &format!("$.identity.{name}"))?;
        }
        let key = key(&s.idempotency_key, "$.identity.idempotency_key")?;
        let payload = hash(&s.payload_hash, "$.identity.payload_hash")?;
        let business = s.business_operation_id.parse().expect("validated identity");
        Ok(Self {
            spec: s,
            business,
            key,
            payload,
        })
    }
    pub fn metadata(&self) -> &IdempotencyIdentitySpec {
        &self.spec
    }
    pub fn business_operation_id(&self) -> &BusinessOperationId {
        &self.business
    }
    pub fn idempotency_key(&self) -> &IdempotencyKey {
        &self.key
    }
    pub fn payload_hash(&self) -> &PayloadHash {
        &self.payload
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.spec;
        data([
            ("tenant_id", text(&s.tenant_id)),
            ("business_operation_id", text(&s.business_operation_id)),
            ("write_node_id", text(&s.write_node_id)),
            ("idempotency_key", text(&s.idempotency_key)),
            ("payload_hash", text(&s.payload_hash)),
        ])
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProviderIdempotencyEvidenceSpec {
    pub tool_id: String,
    pub contract_hash: String,
    pub retention_policy: String,
    pub provider_guarantee_ref: String,
    pub verified_at: UtcInstant,
    pub guaranteed_until: UtcInstant,
    pub durable_duplicate_guarantee: bool,
}
#[derive(Clone, Eq, PartialEq)]
pub struct ProviderIdempotencyEvidence {
    spec: ProviderIdempotencyEvidenceSpec,
    contract_hash: PayloadHash,
}
impl ProviderIdempotencyEvidence {
    pub fn new(s: ProviderIdempotencyEvidenceSpec) -> Result<Self, IdempotencyError> {
        for (name, v) in [
            ("tool_id", &s.tool_id),
            ("retention_policy", &s.retention_policy),
            ("provider_guarantee_ref", &s.provider_guarantee_ref),
        ] {
            required(v, &format!("$.provider_evidence.{name}"))?;
        }
        let contract_hash = hash(&s.contract_hash, "$.provider_evidence.contract_hash")?;
        if s.guaranteed_until <= s.verified_at {
            return Err(error(
                IdempotencyErrorKind::Identity,
                "$.provider_evidence.guaranteed_until",
                "provider idempotency guarantee must end after verification",
            ));
        }
        Ok(Self {
            spec: s,
            contract_hash,
        })
    }
    pub fn metadata(&self) -> &ProviderIdempotencyEvidenceSpec {
        &self.spec
    }
    pub fn contract_hash(&self) -> &PayloadHash {
        &self.contract_hash
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.spec;
        data([
            ("tool_id", text(&s.tool_id)),
            ("contract_hash", text(&s.contract_hash)),
            ("retention_policy", text(&s.retention_policy)),
            ("provider_guarantee_ref", text(&s.provider_guarantee_ref)),
            ("verified_at", text(s.verified_at.to_string())),
            ("guaranteed_until", text(s.guaranteed_until.to_string())),
            (
                "durable_duplicate_guarantee",
                ValueData::Bool(s.durable_duplicate_guarantee),
            ),
        ])
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IdempotencyEvidenceRecordSpec {
    pub tenant_id: String,
    pub business_operation_id: String,
    pub write_node_id: String,
    pub tool_id: String,
    pub contract_hash: String,
    pub idempotency_key: String,
    pub payload_hash: String,
    pub provider_guarantee_ref: String,
    pub retained_until: UtcInstant,
}
#[derive(Clone, Eq, PartialEq)]
pub struct IdempotencyEvidenceRecord {
    spec: IdempotencyEvidenceRecordSpec,
    key: IdempotencyKey,
    payload: PayloadHash,
    contract_hash: PayloadHash,
    business: BusinessOperationId,
}
impl IdempotencyEvidenceRecord {
    pub fn new(s: IdempotencyEvidenceRecordSpec) -> Result<Self, IdempotencyError> {
        for (name, v) in [
            ("tenant_id", &s.tenant_id),
            ("business_operation_id", &s.business_operation_id),
            ("write_node_id", &s.write_node_id),
            ("tool_id", &s.tool_id),
            ("provider_guarantee_ref", &s.provider_guarantee_ref),
        ] {
            required(v, &format!("$.record.{name}"))?;
        }
        let contract_hash = hash(&s.contract_hash, "$.record.contract_hash")?;
        let key = key(&s.idempotency_key, "$.record.idempotency_key")?;
        let payload = hash(&s.payload_hash, "$.record.payload_hash")?;
        let business = s.business_operation_id.parse().expect("validated identity");
        Ok(Self {
            spec: s,
            key,
            payload,
            contract_hash,
            business,
        })
    }
    pub fn metadata(&self) -> &IdempotencyEvidenceRecordSpec {
        &self.spec
    }
    pub fn idempotency_key(&self) -> &IdempotencyKey {
        &self.key
    }
    pub fn payload_hash(&self) -> &PayloadHash {
        &self.payload
    }
    pub fn contract_hash(&self) -> &PayloadHash {
        &self.contract_hash
    }
    pub fn business_operation_id(&self) -> &BusinessOperationId {
        &self.business
    }
    pub fn to_data(&self) -> ValueData {
        let s = &self.spec;
        data([
            ("tenant_id", text(&s.tenant_id)),
            ("business_operation_id", text(&s.business_operation_id)),
            ("write_node_id", text(&s.write_node_id)),
            ("tool_id", text(&s.tool_id)),
            ("contract_hash", text(&s.contract_hash)),
            ("idempotency_key", text(&s.idempotency_key)),
            ("payload_hash", text(&s.payload_hash)),
            ("provider_guarantee_ref", text(&s.provider_guarantee_ref)),
            ("retained_until", text(s.retained_until.to_string())),
        ])
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IdempotencyDisposition {
    Created,
    Reused,
    Conflict,
}
impl IdempotencyDisposition {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Created => "CREATED",
            Self::Reused => "REUSED",
            Self::Conflict => "CONFLICT",
        }
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct IdempotencyReservation {
    disposition: IdempotencyDisposition,
    evidence: IdempotencyEvidenceRecord,
    business_result_ref: Option<String>,
}
impl IdempotencyReservation {
    pub fn new(
        disposition: IdempotencyDisposition,
        evidence: IdempotencyEvidenceRecord,
        business_result_ref: Option<String>,
    ) -> Result<Self, IdempotencyError> {
        if let Some(r) = &business_result_ref {
            required(r, "$.reservation.business_result_ref")?;
        }
        if disposition == IdempotencyDisposition::Conflict && business_result_ref.is_some() {
            return Err(error(
                IdempotencyErrorKind::Type,
                "$.reservation.business_result_ref",
                "a conflicting reservation cannot expose a business result",
            ));
        }
        Ok(Self {
            disposition,
            evidence,
            business_result_ref,
        })
    }
    pub fn disposition(&self) -> IdempotencyDisposition {
        self.disposition
    }
    pub fn evidence(&self) -> &IdempotencyEvidenceRecord {
        &self.evidence
    }
    pub fn business_result_ref(&self) -> Option<&str> {
        self.business_result_ref.as_deref()
    }
    pub fn to_data(&self) -> ValueData {
        data([
            ("disposition", text(self.disposition.as_str())),
            ("evidence", self.evidence.to_data()),
            (
                "business_result_ref",
                self.business_result_ref
                    .as_ref()
                    .map_or(ValueData::Null, text),
            ),
        ])
    }
}
macro_rules! safe_debug {($($t:ty),*)=>{$(impl fmt::Debug for $t {fn fmt(&self,f:&mut fmt::Formatter<'_>)->fmt::Result {f.debug_struct(stringify!($t)).finish_non_exhaustive()}})*};}
safe_debug!(
    IdempotencyIdentity,
    ProviderIdempotencyEvidence,
    IdempotencyEvidenceRecord,
    IdempotencyReservation
);
