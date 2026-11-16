use std::{collections::BTreeMap, fmt};

use nsl_core::{CanonicalJsonError, Integer, PayloadHash, TypeError, ValueData, canonical_json};
use sha2::{Digest, Sha256};

use crate::{PostWriteVerification, ToolContract, WriteSemantics};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ContractEncodingError {
    Type(TypeError),
    Json(CanonicalJsonError),
}

impl fmt::Display for ContractEncodingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Type(error) => error.fmt(f),
            Self::Json(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for ContractEncodingError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Type(error) => Some(error),
            Self::Json(error) => Some(error),
        }
    }
}

impl ToolContract {
    /// Project the provider-independent contract using external type documents.
    pub fn to_canonical_data(&self) -> Result<ValueData, TypeError> {
        let inputs = self
            .input_types()
            .iter()
            .map(|(name, ty)| {
                Ok(object([
                    ("name", name.as_str().into()),
                    ("type", ty.to_data()?),
                ]))
            })
            .collect::<Result<Vec<_>, TypeError>>()?;
        let output = self.output_type().to_data()?;
        let mut data = fields([
            ("tool_id", self.tool_id().into()),
            ("version", self.version().into()),
            ("capability", self.capability().as_str().into()),
            ("risk", self.risk().into()),
            ("inputs", ValueData::Array(inputs)),
            ("output", output),
            ("required_scope", self.required_scope().into()),
            (
                "output_classification",
                self.output_classification().as_str().into(),
            ),
            (
                "timeout_ms",
                ValueData::Number(Integer::from(i64::from(self.timeout_ms()))),
            ),
            ("empty_is_valid", ValueData::Bool(self.empty_is_valid())),
        ]);
        if let Some(operation) = self.operation_kind() {
            data.insert("operation_kind".into(), operation.as_str().into());
        }
        if let Some(level) = self.risk_level() {
            data.insert("risk_level".into(), level.as_str().into());
        }
        if let Some(write) = self.write_semantics() {
            data.insert("write_semantics".into(), write_data(write)?);
        }
        Ok(ValueData::Object(data))
    }

    /// Encode the canonical contract bytes used by the frozen Oracle's SHA-256.
    pub fn canonical_bytes(&self) -> Result<Vec<u8>, ContractEncodingError> {
        let data = self
            .to_canonical_data()
            .map_err(ContractEncodingError::Type)?;
        canonical_json(&data).map_err(ContractEncodingError::Json)
    }

    pub fn contract_hash(&self) -> Result<PayloadHash, ContractEncodingError> {
        let bytes = self.canonical_bytes()?;
        Ok(PayloadHash::from_digest(Sha256::digest(bytes).into()))
    }
}

fn fields<const N: usize>(entries: [(&str, ValueData); N]) -> BTreeMap<String, ValueData> {
    entries
        .into_iter()
        .map(|(key, value)| (key.into(), value))
        .collect()
}

fn object<const N: usize>(entries: [(&str, ValueData); N]) -> ValueData {
    ValueData::Object(fields(entries))
}

fn write_data(write: &WriteSemantics) -> Result<ValueData, TypeError> {
    let idem = write.idempotency();
    let mut data = fields([
        ("side_effect", ValueData::Bool(write.side_effect())),
        ("atomicity", write.atomicity().as_str().into()),
        (
            "idempotency",
            object([
                ("required", ValueData::Bool(idem.required())),
                ("scope", idem.scope().into()),
                ("retention", idem.retention().into()),
                ("duplicate_behavior", idem.duplicate_behavior().into()),
                (
                    "payload_conflict_behavior",
                    idem.payload_conflict_behavior().into(),
                ),
            ]),
        ),
        (
            "commit",
            object([(
                "confirmation",
                write.commit().confirmation().as_str().into(),
            )]),
        ),
        (
            "approval",
            object([("required", ValueData::Bool(write.approval_required()))]),
        ),
        (
            "preconditions",
            object([(
                "supported",
                ValueData::Bool(write.preconditions_supported()),
            )]),
        ),
    ]);
    if let Some(verification) = write.verification() {
        data.insert("verification".into(), verification_data(verification)?);
    }
    Ok(ValueData::Object(data))
}

fn verification_data(verification: &PostWriteVerification) -> Result<ValueData, TypeError> {
    let mut data = BTreeMap::new();
    if let Some(tool_id) = verification.tool_id() {
        data.insert("tool_id".into(), tool_id.into());
    }
    if let Some(schema) = verification.reference_schema() {
        data.insert("reference_schema".into(), schema.to_data()?);
    }
    if let Some(hint) = verification.consistency_hint() {
        data.insert("consistency_hint".into(), hint.into());
    }
    Ok(ValueData::Object(data))
}
