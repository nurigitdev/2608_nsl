use std::{collections::BTreeMap, error::Error};

use nsl_contract::{
    CatalogErrorKind, CommitConfirmation, CommitSemantics, ContractEncodingError,
    IdempotencySemantics, PostWriteVerification, ToolContract, ToolContractCatalog,
    ToolContractOptions, ToolContractSpec, WriteSemantics, WriteSemanticsSpec,
};
use nsl_core::{DataClassification, TypeError, TypeKind, TypeRef};

const ORACLE: &str = include_str!("fixtures/contract-identity-oracle.tsv");

fn primitive(name: &str) -> TypeRef {
    TypeRef::new(TypeKind::Primitive(name.into())).unwrap()
}

fn string() -> TypeRef {
    primitive("String")
}

fn domain(name: &str) -> TypeRef {
    TypeRef::new(TypeKind::Domain(name.into())).unwrap()
}

fn list(item: TypeRef) -> TypeRef {
    TypeRef::new(TypeKind::List(Box::new(item))).unwrap()
}

fn money() -> TypeRef {
    TypeRef::new(TypeKind::Money("KRW".parse().unwrap())).unwrap()
}

fn named(items: impl IntoIterator<Item = (&'static str, TypeRef)>) -> Vec<(String, TypeRef)> {
    items
        .into_iter()
        .map(|(key, value)| (key.into(), value))
        .collect()
}

fn record(name: &str, fields: impl IntoIterator<Item = (&'static str, TypeRef)>) -> TypeRef {
    TypeRef::new(TypeKind::Record {
        name: name.into(),
        fields: named(fields),
    })
    .unwrap()
}

fn tool(
    id: &str,
    scope: &str,
    classification: DataClassification,
    inputs: impl IntoIterator<Item = (&'static str, TypeRef)>,
    output: TypeRef,
) -> ToolContractSpec {
    ToolContractSpec {
        tool_id: id.into(),
        version: "1.0.0".into(),
        capability: "READ".into(),
        input_types: named(inputs),
        output_type: output,
        required_scope: scope.into(),
        output_classification: classification,
        options: ToolContractOptions::default(),
    }
}

fn verification(
    tool: Option<&str>,
    schema: Option<TypeRef>,
    hint: Option<&str>,
) -> PostWriteVerification {
    PostWriteVerification::new(tool.map(str::to_owned), schema, hint.map(str::to_owned)).unwrap()
}

fn write_semantics(verification: Option<PostWriteVerification>) -> WriteSemantics {
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
        verification,
    })
    .unwrap()
}

fn controlled(
    mut tool: ToolContractSpec,
    operation: &str,
    verifier: &str,
    hint: &str,
) -> ToolContractSpec {
    let verifier = verification(Some(verifier), Some(tool.output_type.clone()), Some(hint));
    tool.capability = "WRITE".into();
    tool.options.operation_kind = Some(operation.into());
    tool.options.risk_level = Some("R3".into());
    tool.options.write_semantics = Some(write_semantics(Some(verifier)));
    tool
}

fn create() -> ToolContractSpec {
    let spec = tool(
        "PROJECT.MEMBERSHIP.ADD_MANY",
        "project:membership:manage",
        DataClassification::Internal,
        [
            ("tenant_id", string()),
            ("requester_subject_id", string()),
            ("project_id", string()),
            ("target_subject_ids", list(string())),
            ("member_role", string()),
            ("resolution_evidence_hash", string()),
        ],
        record(
            "ProjectMembershipBatch",
            [
                ("operation_status", string()),
                ("requested_count", primitive("Int")),
                ("resolved_count", primitive("Int")),
                ("added_count", primitive("Int")),
                ("reactivated_count", primitive("Int")),
                ("unchanged_count", primitive("Int")),
                ("rejected_count", primitive("Int")),
                ("project_id", string()),
                ("member_role", string()),
                ("business_result_ref", string()),
            ],
        ),
    );
    controlled(
        spec,
        "CREATE",
        "PROJECT.MEMBERSHIP.LIST",
        "FILTERED_READ_AFTER_WRITE_STRONG",
    )
}

fn draft() -> ToolContractSpec {
    let spec = tool(
        "FINANCE.CREATE_VOUCHER_DRAFT",
        "finance:voucher:write",
        DataClassification::Confidential,
        [
            ("account_code", domain("AccountCode")),
            ("project_code", domain("ProjectCode")),
            ("accounting_period", domain("AccountingPeriod")),
            ("amount", money()),
        ],
        record("VoucherDraft", [("voucher_no", string())]),
    );
    controlled(
        spec,
        "CREATE_DRAFT",
        "FINANCE.GET_VOUCHER",
        "READ_AFTER_WRITE_STRONG",
    )
}

fn update() -> ToolContractSpec {
    let spec = tool(
        "PROJECT.PROJECT.DEACTIVATE",
        "project:project:admin",
        DataClassification::Internal,
        [
            ("tenant_id", string()),
            ("requester_subject_id", string()),
            ("project_id", string()),
            ("expected_version", primitive("Int")),
        ],
        record(
            "Project",
            [
                ("project_id", string()),
                ("project_code", domain("ProjectCode")),
                ("name", string()),
                ("status", string()),
                ("entity_version", primitive("Int")),
            ],
        ),
    );
    controlled(
        spec,
        "UPDATE",
        "PROJECT.PROJECT.GET",
        "READ_AFTER_WRITE_STRONG",
    )
}

fn read() -> ToolContractSpec {
    tool(
        "PROJECT.LIST_PARENT_PROJECTS",
        "project:budget:read",
        DataClassification::Confidential,
        [("year", primitive("Year")), ("team_id", domain("TeamId"))],
        list(record(
            "ParentProject",
            [("project_code", domain("ProjectCode")), ("budget", money())],
        )),
    )
}

fn oracle_rows(kind: &str) -> impl Iterator<Item = Vec<&'static str>> {
    ORACLE
        .strip_prefix("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a\n")
        .unwrap()
        .lines()
        .map(|line| line.split('\t').collect::<Vec<_>>())
        .filter(move |row| row[0] == kind)
}

#[test]
fn canonical_contract_bytes_and_sha256_match_frozen_oracle() {
    let mut omitted = create();
    omitted.options.write_semantics = Some(write_semantics(None));
    let mut reference = draft();
    let verifier = verification(None, Some(reference.output_type.clone()), None);
    reference.options.write_semantics = Some(write_semantics(Some(verifier)));
    let mut hint = update();
    let verifier = verification(None, None, Some(" 지연 허용 "));
    hint.options.write_semantics = Some(write_semantics(Some(verifier)));
    let mut unicode = read();
    unicode.tool_id = "TOOL.조회😀".into();
    unicode.capability = " Legacy.조회 ".into();
    unicode.input_types.clear();
    unicode.required_scope = " scope:/한😀\u{2028}\0\u{b}\u{8}\u{c}\n\r\t\"\\ ".into();
    unicode.options.empty_is_valid = false;
    unicode.options.timeout_ms = 2_147_483_647;
    unicode.options.risk = "CUSTOM".into();
    unicode.options.risk_level = Some("R0".into());
    let mut specs = BTreeMap::from([
        ("CREATE", create()),
        ("CREATE_DRAFT", draft()),
        ("UPDATE", update()),
        ("READ", read()),
        ("create_without_verification", omitted),
        ("draft_with_reference_only", reference),
        ("update_with_hint_only", hint),
        ("legacy_unicode", unicode),
    ]);
    for row in oracle_rows("contract") {
        let [_, label, digest, bytes] = row.as_slice() else {
            panic!("invalid identity row: {row:?}");
        };
        let contract = ToolContract::new(specs.remove(label).unwrap()).unwrap();
        assert_eq!(
            contract.canonical_bytes().unwrap(),
            bytes.as_bytes(),
            "{label}"
        );
        assert_eq!(
            contract.contract_hash().unwrap().as_str(),
            *digest,
            "{label}"
        );
    }
    assert!(specs.is_empty());
}

#[test]
fn catalog_rejects_unknown_wrong_version_and_same_identity_with_a_different_hash() {
    let create = ToolContract::new(create()).unwrap();
    let draft = ToolContract::new(draft()).unwrap();
    let update = ToolContract::new(update()).unwrap();
    let catalog =
        ToolContractCatalog::new([create.clone(), draft.clone(), update.clone()]).unwrap();
    let mut altered = self::create();
    altered.required_scope = "test:other".into();
    let altered = ToolContract::new(altered).unwrap();
    assert_ne!(
        create.contract_hash().unwrap(),
        altered.contract_hash().unwrap()
    );
    let mut cases = BTreeMap::from([
        (
            "unknown",
            catalog.resolve("PROJECT.UNKNOWN", "1.0.0").unwrap_err(),
        ),
        (
            "wrong_version",
            catalog.resolve(update.tool_id(), "1.0.1").unwrap_err(),
        ),
        (
            "duplicate_identity",
            ToolContractCatalog::new([create, draft, update, altered]).unwrap_err(),
        ),
    ]);
    for row in oracle_rows("catalog_error") {
        let [_, label, expected] = row.as_slice() else {
            panic!("invalid catalog row: {row:?}");
        };
        let kind = match cases.remove(label).unwrap().kind {
            CatalogErrorKind::UnknownToolContract => "UnknownToolContractError",
            CatalogErrorKind::IncompatibleToolVersion => "IncompatibleToolVersionError",
            CatalogErrorKind::DuplicateToolContract => "DuplicateToolContractError",
            other => panic!("unexpected rejection: {other:?}"),
        };
        assert_eq!(kind, *expected, "{label}");
    }
    assert!(cases.is_empty());
}

#[test]
fn optional_types_are_rejected_on_projection_encoding_and_hashing() {
    let direct = TypeRef::new(TypeKind::Optional(Box::new(string()))).unwrap();
    let nested = list(record("Nested", [("field", list(direct.clone()))]));
    for ty in [direct, nested] {
        let mut input = create();
        input.input_types[0].1 = ty.clone();
        let mut output = draft();
        output.output_type = ty.clone();
        let mut verification_schema = update();
        verification_schema.options.write_semantics =
            Some(write_semantics(Some(verification(None, Some(ty), None))));
        for spec in [input, output, verification_schema] {
            let contract = ToolContract::new(spec).unwrap();
            assert_eq!(
                contract.to_canonical_data(),
                Err(TypeError::InternalOptional)
            );
            let expected = ContractEncodingError::Type(TypeError::InternalOptional);
            let bytes_error = contract.canonical_bytes().unwrap_err();
            assert_eq!(bytes_error, expected);
            assert_eq!(contract.contract_hash(), Err(expected));
            assert_eq!(
                bytes_error.source().unwrap().downcast_ref::<TypeError>(),
                Some(&TypeError::InternalOptional),
            );
            assert_eq!(
                bytes_error.to_string(),
                TypeError::InternalOptional.to_string()
            );
        }
    }
}
