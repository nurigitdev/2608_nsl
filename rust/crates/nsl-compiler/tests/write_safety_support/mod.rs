use nsl_contract::{
    CommitConfirmation, CommitSemantics, ContractError, IdempotencySemantics, ToolContract,
    ToolContractOptions, ToolContractSpec, WriteSemantics, WriteSemanticsSpec,
};
use nsl_core::{DataClassification, TypeKind, TypeRef};

pub fn build(
    capability: &str,
    operation: &str,
    risk: &str,
    changed: Option<(&str, &str)>,
) -> Result<ToolContract, ContractError> {
    let mut options = ToolContractOptions::default();
    if capability == "WRITE" {
        let value = |name: &str| {
            changed
                .filter(|(field, _)| *field == name)
                .map(|(_, value)| value)
        };
        let semantics = WriteSemantics::new(WriteSemanticsSpec {
            side_effect: value("side_effect") != Some("false"),
            atomicity: value("atomicity").unwrap_or("ATOMIC").into(),
            idempotency: Some(IdempotencySemantics::new(
                value("required") != Some("false"),
                "TOOL_AND_TENANT",
                "PROVIDER_DEFINED",
                "RETURN_ORIGINAL_RESULT",
                "IDEMPOTENCY_CONFLICT",
            )?),
            commit: (value("commit") != Some("false"))
                .then_some(CommitSemantics::new(CommitConfirmation::Explicit)),
            approval_required: value("approval") != Some("false"),
            preconditions_supported: value("preconditions") != Some("false"),
            verification: None,
        })?;
        options.operation_kind = (operation != "NONE").then(|| operation.into());
        options.risk_level = (risk != "NONE").then(|| risk.into());
        options.write_semantics = Some(semantics);
    }
    let int = TypeRef::new(TypeKind::Primitive("Int".into())).unwrap();
    ToolContract::new(ToolContractSpec {
        tool_id: "TEST.WRITE".into(),
        version: "1.0.0".into(),
        capability: capability.into(),
        input_types: vec![("amount".into(), int.clone())],
        output_type: int,
        required_scope: "test:write".into(),
        output_classification: DataClassification::Public,
        options,
    })
}
