use crate::data::{object, text};
use crate::{
    ApprovalSummary, CheckResult, EmitRecord, ResourceUsage, RuntimeErrorInfo, SuspensionInfo,
    ValidationSummary, VerificationSummary, WriteSummary,
};
use nsl_core::{
    CanonicalJsonError, Completeness, DecimalError, ExecutionStatus, PreWriteEligibility,
    TypeError, Value, ValueData, canonical_json,
};
use std::fmt;
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ResultEncodingError {
    Decimal(DecimalError),
    Type(TypeError),
    Json(CanonicalJsonError),
}
impl fmt::Display for ResultEncodingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Decimal(e) => e.fmt(f),
            Self::Type(e) => e.fmt(f),
            Self::Json(e) => e.fmt(f),
        }
    }
}
impl std::error::Error for ResultEncodingError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(match self {
            Self::Decimal(e) => e,
            Self::Type(e) => e,
            Self::Json(e) => e,
        })
    }
}
impl From<DecimalError> for ResultEncodingError {
    fn from(e: DecimalError) -> Self {
        Self::Decimal(e)
    }
}
impl From<TypeError> for ResultEncodingError {
    fn from(e: TypeError) -> Self {
        Self::Type(e)
    }
}
impl From<CanonicalJsonError> for ResultEncodingError {
    fn from(e: CanonicalJsonError) -> Self {
        Self::Json(e)
    }
}
impl EmitRecord {
    pub fn to_data(&self, max_decimal_bytes: usize) -> Result<ValueData, ResultEncodingError> {
        let fields = self
            .fields()
            .iter()
            .map(|(name, field)| {
                Ok(object([
                    ("name", text(name)),
                    ("type", field.type_ref().to_data()?),
                    ("value", field.value().to_data(max_decimal_bytes)?),
                    ("presence", text(field.presence().as_str())),
                    ("completeness", text(field.completeness().as_str())),
                    ("classification", text(field.classification().as_str())),
                    (
                        "provenance_refs",
                        crate::data::strings(field.provenance_refs()),
                    ),
                ]))
            })
            .collect::<Result<Vec<_>, ResultEncodingError>>()?;
        Ok(object([("fields", ValueData::Array(fields))]))
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionResult {
    pub execution_id: String,
    pub skill_id: String,
    pub skill_version: String,
    pub semantic_hash: String,
    pub status: ExecutionStatus,
    pub completeness: Completeness,
    pub checks: Vec<CheckResult>,
    pub outputs: Vec<EmitRecord>,
    pub resources: ResourceUsage,
    pub error: Option<RuntimeErrorInfo>,
    pub validation: Option<ValidationSummary>,
    pub pre_write_eligibility: Option<PreWriteEligibility>,
    pub approval: Option<ApprovalSummary>,
    pub write: Option<WriteSummary>,
    pub verification: Option<VerificationSummary>,
    pub suspension: Option<SuspensionInfo>,
}
impl ExecutionResult {
    pub fn to_data(&self, max_decimal_bytes: usize) -> Result<ValueData, ResultEncodingError> {
        let extensions = [
            (
                "validation",
                self.validation
                    .as_ref()
                    .map_or(ValueData::Null, ValidationSummary::to_data),
            ),
            (
                "pre_write_eligibility",
                self.pre_write_eligibility
                    .map_or(ValueData::Null, |v| text(v.as_str())),
            ),
            (
                "approval",
                self.approval
                    .as_ref()
                    .map_or(ValueData::Null, ApprovalSummary::to_data),
            ),
            (
                "write",
                self.write
                    .as_ref()
                    .map_or(ValueData::Null, WriteSummary::to_data),
            ),
            (
                "verification",
                self.verification
                    .as_ref()
                    .map_or(ValueData::Null, VerificationSummary::to_data),
            ),
            (
                "suspension",
                self.suspension
                    .as_ref()
                    .map_or(ValueData::Null, SuspensionInfo::to_data),
            ),
        ];
        let extended = extensions.iter().any(|(_, v)| *v != ValueData::Null);
        let data = object([
            ("schema_version", text(if extended { "1.1" } else { "1.0" })),
            ("execution_id", text(&self.execution_id)),
            ("skill_id", text(&self.skill_id)),
            ("skill_version", text(&self.skill_version)),
            ("semantic_hash", text(&self.semantic_hash)),
            ("status", text(self.status.as_str())),
            ("completeness", text(self.completeness.as_str())),
            (
                "checks",
                ValueData::Array(self.checks.iter().map(CheckResult::to_data).collect()),
            ),
            (
                "outputs",
                ValueData::Array(
                    self.outputs
                        .iter()
                        .map(|r| r.to_data(max_decimal_bytes))
                        .collect::<Result<_, _>>()?,
                ),
            ),
            ("resources", self.resources.to_data()),
            (
                "error",
                self.error
                    .as_ref()
                    .map_or(ValueData::Null, RuntimeErrorInfo::to_data),
            ),
        ]);
        let ValueData::Object(mut fields) = data else {
            unreachable!("result object")
        };
        if extended {
            fields.extend(extensions.into_iter().map(|(k, v)| (k.into(), v)));
        }
        Ok(ValueData::Object(fields))
    }
    pub fn to_json(&self, max_decimal_bytes: usize) -> Result<Vec<u8>, ResultEncodingError> {
        Ok(canonical_json(&self.to_data(max_decimal_bytes)?)?)
    }
    pub fn semantic_view(
        &self,
        max_decimal_bytes: usize,
    ) -> Result<ValueData, ResultEncodingError> {
        let ValueData::Object(mut fields) = self.to_data(max_decimal_bytes)? else {
            unreachable!("result object")
        };
        fields.remove("schema_version");
        fields.remove("execution_id");
        let checks = self
            .checks
            .iter()
            .map(|c| {
                let ValueData::Object(mut fields) = c.to_data() else {
                    unreachable!("check object")
                };
                fields.remove("provenance_refs");
                ValueData::Object(fields)
            })
            .collect();
        fields.insert("checks".into(), ValueData::Array(checks));
        fields.insert(
            "outputs".into(),
            ValueData::Array(
                self.outputs
                    .iter()
                    .map(|r| Value::Record(r.values()).to_data(max_decimal_bytes))
                    .collect::<Result<_, _>>()?,
            ),
        );
        Ok(ValueData::Object(fields))
    }
}
