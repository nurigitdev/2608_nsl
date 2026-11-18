use super::type_support;
use nsl_contract::{
    CommitConfirmation, CommitSemantics, IdempotencySemantics, ToolContract, ToolContractCatalog,
    ToolContractOptions, ToolContractSpec, WriteSemantics, WriteSemanticsSpec,
};
use nsl_core::DataClassification;

fn contract(
    name: &str,
    capability: &str,
    operation_and_risk: (&str, &str),
    scope: &str,
    classification: DataClassification,
    output: nsl_core::TypeRef,
    argument: &str,
) -> ToolContract {
    let options = if capability == "WRITE" {
        ToolContractOptions {
            operation_kind: Some(operation_and_risk.0.into()),
            risk_level: Some(operation_and_risk.1.into()),
            write_semantics: Some(
                WriteSemantics::new(WriteSemanticsSpec {
                    side_effect: true,
                    atomicity: "ATOMIC".into(),
                    idempotency: Some(
                        IdempotencySemantics::new(
                            true,
                            "TOOL_AND_TENANT",
                            "PROVIDER_DEFINED",
                            "RETURN_ORIGINAL_RESULT",
                            "IDEMPOTENCY_CONFLICT",
                        )
                        .unwrap(),
                    ),
                    commit: Some(CommitSemantics::new(CommitConfirmation::Explicit)),
                    approval_required: true,
                    preconditions_supported: true,
                    verification: None,
                })
                .unwrap(),
            ),
            ..ToolContractOptions::default()
        }
    } else {
        ToolContractOptions::default()
    };
    ToolContract::new(ToolContractSpec {
        tool_id: name.into(),
        version: "1.0.0".into(),
        capability: capability.into(),
        input_types: vec![(argument.into(), type_support::primitive("Int"))],
        output_type: output,
        required_scope: scope.into(),
        output_classification: classification,
        options,
    })
    .unwrap()
}
pub fn catalog(profile: &str) -> ToolContractCatalog {
    use DataClassification::{Internal, Public};
    let int = || type_support::primitive("Int");
    let tool = |name, capability, scope, classification, output, argument| {
        contract(
            name,
            capability,
            ("CREATE", "R1"),
            scope,
            classification,
            output,
            argument,
        )
    };
    let contracts = match profile {
        "legacy-read" | "legacy-write" => return legacy_catalog(profile),
        "types" => return type_support::catalog(),
        "empty" => vec![],
        "symbols" => vec![
            tool(
                "TOOL.LOOKUP",
                "READ",
                "test:read",
                Internal,
                int(),
                "amount",
            ),
            tool(
                "TOOL.CREATE",
                "WRITE",
                "test:write",
                Internal,
                int(),
                "amount",
            ),
        ],
        "bounds" => vec![
            tool("TEST.VALUE", "READ", "test:read", Public, int(), "amount"),
            tool(
                "TEST.LIST",
                "READ",
                "test:read",
                Public,
                type_support::list(int()),
                "seed",
            ),
            tool(
                "TEST.CREATE",
                "WRITE",
                "test:write",
                Public,
                int(),
                "amount",
            ),
        ],
        _ => {
            let c = profile.split(':').collect::<Vec<_>>();
            vec![contract(
                "TEST.WRITE",
                c[1],
                (c[2], c[3]),
                "test:write",
                Public,
                int(),
                "amount",
            )]
        }
    };
    ToolContractCatalog::new(contracts).unwrap()
}

fn legacy_catalog(profile: &str) -> ToolContractCatalog {
    use nsl_core::{TypeKind, TypeRef};
    let domain = |name: &str| TypeRef::new(TypeKind::Domain(name.into())).unwrap();
    let project = type_support::record(
        "Project",
        vec![
            ("project_id", type_support::primitive("String")),
            ("project_code", domain("ProjectCode")),
            ("name", type_support::primitive("String")),
            ("status", type_support::primitive("String")),
            ("entity_version", type_support::primitive("Int")),
        ],
    );
    let contracts = if profile == "legacy-read" {
        [
            (
                "PROJECT.LIST_PARENT_PROJECTS",
                vec![
                    ("year", type_support::primitive("Year")),
                    ("team_id", domain("TeamId")),
                ],
                type_support::record(
                    "ParentProject",
                    vec![
                        ("project_code", domain("ProjectCode")),
                        (
                            "budget",
                            TypeRef::new(TypeKind::Money("KRW".parse().unwrap())).unwrap(),
                        ),
                    ],
                ),
            ),
            (
                "PROJECT.LIST_CHILD_PROJECTS",
                vec![
                    ("parent_project", domain("ProjectCode")),
                    ("year", type_support::primitive("Year")),
                ],
                type_support::record(
                    "ChildProject",
                    vec![
                        ("project_code", domain("ProjectCode")),
                        ("parent_project", domain("ProjectCode")),
                        (
                            "expense_amount",
                            TypeRef::new(TypeKind::Money("KRW".parse().unwrap())).unwrap(),
                        ),
                    ],
                ),
            ),
        ]
        .into_iter()
        .map(|(tool_id, inputs, output)| {
            ToolContract::new(ToolContractSpec {
                tool_id: tool_id.into(),
                version: "1.0.0".into(),
                capability: "READ".into(),
                input_types: inputs.into_iter().map(|(n, t)| (n.into(), t)).collect(),
                output_type: type_support::list(output),
                required_scope: "project:budget:read".into(),
                output_classification: DataClassification::Confidential,
                options: ToolContractOptions::default(),
            })
            .unwrap()
        })
        .collect()
    } else {
        let semantics = WriteSemantics::new(WriteSemanticsSpec {
            side_effect: true,
            atomicity: "ATOMIC".into(),
            idempotency: Some(
                IdempotencySemantics::new(
                    true,
                    "TOOL_AND_TENANT",
                    "PROVIDER_DEFINED",
                    "RETURN_ORIGINAL_RESULT",
                    "IDEMPOTENCY_CONFLICT",
                )
                .unwrap(),
            ),
            commit: Some(CommitSemantics::new(CommitConfirmation::Explicit)),
            approval_required: true,
            preconditions_supported: true,
            verification: Some(
                nsl_contract::PostWriteVerification::new(
                    Some("PROJECT.PROJECT.GET".into()),
                    Some(project.clone()),
                    Some("READ_AFTER_WRITE_STRONG".into()),
                )
                .unwrap(),
            ),
        })
        .unwrap();
        vec![
            ToolContract::new(ToolContractSpec {
                tool_id: "PROJECT.PROJECT.DEACTIVATE".into(),
                version: "1.0.0".into(),
                capability: "WRITE".into(),
                input_types: vec![
                    ("tenant_id".into(), type_support::primitive("String")),
                    (
                        "requester_subject_id".into(),
                        type_support::primitive("String"),
                    ),
                    ("project_id".into(), type_support::primitive("String")),
                    ("expected_version".into(), type_support::primitive("Int")),
                ],
                output_type: project,
                required_scope: "project:project:admin".into(),
                output_classification: DataClassification::Internal,
                options: ToolContractOptions {
                    operation_kind: Some("UPDATE".into()),
                    risk_level: Some("R3".into()),
                    write_semantics: Some(semantics),
                    ..ToolContractOptions::default()
                },
            })
            .unwrap(),
        ]
    };
    ToolContractCatalog::new(contracts).unwrap()
}
