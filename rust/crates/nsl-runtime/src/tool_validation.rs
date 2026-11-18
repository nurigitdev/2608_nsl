use crate::{
    ResultEncodingError, ToolCallError, ToolCallRequest, ToolExecutionError, ToolResultEnvelope,
};
use nsl_contract::ToolContract;
use nsl_core::{PayloadHash, TypeKind, TypeRef, Value, canonical_json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub fn tool_result_hash(
    value: &Value,
    max_decimal_bytes: usize,
) -> Result<PayloadHash, ResultEncodingError> {
    let data = value.to_data(max_decimal_bytes)?;
    Ok(PayloadHash::from_digest(
        Sha256::digest(canonical_json(&data)?).into(),
    ))
}

/// Tool contracts intentionally do not accept internal Optional, even though
/// Core value models do. Empty lists are vacuously valid, as in the Oracle.
pub fn tool_value_conforms_to_type(value: &Value, ty: &TypeRef) -> bool {
    let mut pending = vec![(value, ty)];
    while let Some((value, ty)) = pending.pop() {
        match (ty.kind(), value) {
            (TypeKind::Optional(_), _) => return false,
            (TypeKind::List(item), Value::List(items)) => {
                pending.extend(items.iter().map(|v| (v, item.as_ref())))
            }
            (TypeKind::Record { fields, .. }, Value::Record(values)) => {
                let expected: BTreeMap<_, _> = fields.iter().map(|(k, v)| (k, v)).collect();
                if expected.len() != values.len() {
                    return false;
                }
                for (name, ty) in expected {
                    let Some(value) = values.get(name) else {
                        return false;
                    };
                    pending.push((value, ty));
                }
            }
            _ if !ty.accepts(value) => return false,
            _ => {}
        }
    }
    true
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ToolContractValidator;
impl ToolContractValidator {
    pub fn validate_request(
        &self,
        request: &ToolCallRequest,
        contract: &ToolContract,
    ) -> Result<(), ToolCallError> {
        if request.contract_hash != contract.contract_hash()?.as_str() {
            return Err(ToolExecutionError::new(
                "TOOL_CONTRACT_MISMATCH",
                format!("contract changed: {}", request.tool_id),
            )
            .into());
        }
        if request.arguments.len() != contract.input_types().len()
            || contract
                .input_types()
                .iter()
                .any(|(k, _)| !request.arguments.contains_key(k))
        {
            return Err(ToolExecutionError::new(
                "TOOL_ARGUMENT_MISMATCH",
                format!("argument names do not match contract: {}", request.tool_id),
            )
            .into());
        }
        // Preserve declaration order, including which failing argument wins.
        for (name, ty) in contract.input_types() {
            let argument = &request.arguments[name];
            if argument.type_ref() != ty || !tool_value_conforms_to_type(argument.value(), ty) {
                return Err(ToolExecutionError::new(
                    "TOOL_ARGUMENT_TYPE_MISMATCH",
                    format!(
                        "argument type does not match contract: {}.{name}",
                        request.tool_id
                    ),
                )
                .into());
            }
        }
        Ok(())
    }

    /// An absent structured envelope represents a malformed adapter response;
    /// closed Rust metadata types exclude Python's wrong-type states.
    pub fn validate_result_structure<'a>(
        &self,
        result: Option<&'a ToolResultEnvelope>,
        request: &ToolCallRequest,
    ) -> Result<&'a ToolResultEnvelope, ToolExecutionError> {
        let result = result.ok_or_else(|| {
            ToolExecutionError::new(
                "MALFORMED_TOOL_RESULT",
                format!(
                    "tool result is not a structured envelope: {}",
                    request.tool_id
                ),
            )
        })?;
        if result.invocation_id != request.invocation_id
            || result.tool_id != request.tool_id
            || result.tool_version != request.tool_version
        {
            return Err(ToolExecutionError::new(
                "TOOL_RESULT_IDENTITY_MISMATCH",
                format!(
                    "tool result identity does not match request: {}",
                    request.tool_id
                ),
            ));
        }
        if result.snapshot_ref.as_ref().is_some_and(String::is_empty) {
            return Err(ToolExecutionError::new(
                "MALFORMED_TOOL_RESULT",
                format!("tool result metadata is malformed: {}", request.tool_id),
            ));
        }
        Ok(result)
    }
    pub fn validate_result_value(
        &self,
        value: &Value,
        contract: &ToolContract,
    ) -> Result<(), ToolExecutionError> {
        if !tool_value_conforms_to_type(value, contract.output_type()) {
            return Err(ToolExecutionError::new(
                "OUTPUT_CONTRACT_VIOLATION",
                format!("result does not match contract: {}", contract.tool_id()),
            ));
        }
        Ok(())
    }
    pub fn validate_result(
        &self,
        result: &ToolResultEnvelope,
        contract: &ToolContract,
    ) -> Result<(), ToolExecutionError> {
        if &result.type_info != contract.output_type()
            || result.classification != contract.output_classification()
        {
            return Err(ToolExecutionError::new(
                "OUTPUT_CONTRACT_VIOLATION",
                format!(
                    "result metadata does not match contract: {}",
                    contract.tool_id()
                ),
            ));
        }
        self.validate_result_value(&result.value, contract)
    }
    pub fn validate_result_hash(
        &self,
        result: &ToolResultEnvelope,
        max_decimal_bytes: usize,
    ) -> Result<(), ToolCallError> {
        if result.result_hash != tool_result_hash(&result.value, max_decimal_bytes)?.as_str() {
            return Err(ToolExecutionError::new(
                "TOOL_RESULT_HASH_MISMATCH",
                format!("tool result hash does not match value: {}", result.tool_id),
            )
            .into());
        }
        Ok(())
    }
}
