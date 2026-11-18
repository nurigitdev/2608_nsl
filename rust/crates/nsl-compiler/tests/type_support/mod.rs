#![allow(dead_code)]

use nsl_contract::{
    CommitConfirmation, CommitSemantics, IdempotencySemantics, ToolContract, ToolContractCatalog,
    ToolContractOptions, ToolContractSpec, WriteSemantics, WriteSemanticsSpec,
};
use nsl_core::{DataClassification, TypeKind, TypeRef};
use nsl_source::ast::{Ast, AstSkill};
use nsl_source::{ParseLimits, ParseMode, Parser, SourceFile};

pub fn ty(kind: TypeKind) -> TypeRef {
    TypeRef::new(kind).unwrap()
}
pub fn primitive(name: &str) -> TypeRef {
    ty(TypeKind::Primitive(name.into()))
}
pub fn list(item: TypeRef) -> TypeRef {
    ty(TypeKind::List(Box::new(item)))
}
pub fn optional(item: TypeRef) -> TypeRef {
    ty(TypeKind::Optional(Box::new(item)))
}
pub fn record(name: &str, fields: Vec<(&str, TypeRef)>) -> TypeRef {
    ty(TypeKind::Record {
        name: name.into(),
        fields: fields
            .into_iter()
            .map(|(name, ty)| (name.into(), ty))
            .collect(),
    })
}
pub fn row() -> TypeRef {
    record(
        "Row",
        vec![
            ("number", primitive("Int")),
            ("cost", ty(TypeKind::Money("KRW".parse().unwrap()))),
            ("nested", record("Meta", vec![("flag", primitive("Bool"))])),
            ("values", list(primitive("Int"))),
        ],
    )
}
pub fn types() -> Vec<TypeRef> {
    let int = primitive("Int");
    let status = ty(TypeKind::Enum {
        name: "CheckStatus".into(),
        values: ["PASS", "FAIL", "UNKNOWN"].map(String::from).into(),
    });
    vec![
        int.clone(),
        primitive("Decimal"),
        primitive("Bool"),
        primitive("String"),
        primitive("Date"),
        primitive("DateTime"),
        primitive("Year"),
        ty(TypeKind::Money("KRW".parse().unwrap())),
        ty(TypeKind::Money("USD".parse().unwrap())),
        ty(TypeKind::Domain("TeamId".into())),
        ty(TypeKind::Domain("EmployeeId".into())),
        list(int.clone()),
        list(primitive("Bool")),
        row(),
        list(row()),
        status.clone(),
        record("CheckResult", vec![("status", status.clone())]),
        optional(int.clone()),
        optional(row()),
        list(optional(int)),
        list(list(row())),
        list(ty(TypeKind::Money("KRW".parse().unwrap()))),
        list(primitive("Decimal")),
        list(primitive("String")),
        list(status),
    ]
}
pub fn describe(ty: &TypeRef) -> String {
    match ty.kind() {
        TypeKind::Primitive(name) => name.clone(),
        TypeKind::Domain(name) => format!("${name}"),
        TypeKind::Money(currency) => format!("Money<{}>", currency.as_str()),
        TypeKind::List(item) => format!("List<{}>", describe(item)),
        TypeKind::Optional(item) => format!("Optional<{}>", describe(item)),
        TypeKind::Enum { name, values } => format!("{name}[{}]", values.join(",")),
        TypeKind::Record { name, fields } => format!(
            "{name}{{{}}}",
            fields
                .iter()
                .map(|(key, value)| format!("{key}:{}", describe(value)))
                .collect::<Vec<_>>()
                .join(",")
        ),
    }
}
pub fn encode(value: &str) -> String {
    if value.is_empty() {
        "-".into()
    } else {
        value.bytes().map(|byte| format!("{byte:02x}")).collect()
    }
}
pub fn decode(value: &str) -> String {
    if value == "-" {
        return String::new();
    }
    String::from_utf8(
        (0..value.len())
            .step_by(2)
            .map(|index| u8::from_str_radix(&value[index..index + 2], 16).unwrap())
            .collect(),
    )
    .unwrap()
}
pub fn parse(source: &SourceFile, max_nesting: usize) -> AstSkill {
    let Ast::RootSkill(skill) =
        Parser::parse(source, ParseMode::RootSkill, ParseLimits { max_nesting }).unwrap()
    else {
        panic!("root skill")
    };
    *skill
}
pub fn source(declarations: &str, body: &str) -> SourceFile {
    SourceFile::from_text("types/skill.ns", &format!("language NSL \"0.2\"; skill TEST {{ version \"1\"; risk READ_ONLY; requires {{}} limits {{ tool_calls 100; loop_iterations 100; emitted_rows 100; collection_size 100; }} {declarations} {body} }}"), None).unwrap()
}
pub fn catalog() -> ToolContractCatalog {
    use DataClassification::*;
    let specs = [
        (
            "TEST.RECORD",
            vec![("z", primitive("Int")), ("a", primitive("Decimal"))],
            row(),
            Confidential,
            "READ",
        ),
        ("TEST.LIST", vec![], list(row()), Internal, "READ"),
        (
            "TEST.SCALAR",
            vec![("amount", primitive("Int"))],
            primitive("Decimal"),
            Public,
            "READ",
        ),
        (
            "TEST.CREATE",
            vec![("amount", primitive("Int"))],
            row(),
            Internal,
            "WRITE",
        ),
        (
            "TEST.TRANSACTION",
            vec![],
            primitive("Int"),
            Internal,
            "TRANSACTION",
        ),
        ("TEST.CUSTOM", vec![], primitive("Int"), Internal, "CUSTOM"),
        (
            "TEST.UNSPELLABLE",
            [
                "can't",
                "both\"'",
                "back\\slash",
                "a\t",
                "b\n",
                "c\r",
                "d\0",
                "e\u{a0}",
                "f\u{378}",
                "g\u{e0001}",
                "h\u{1fae9}",
                "한글😀",
                "e\u{301}",
            ]
            .into_iter()
            .map(|name| (name, primitive("Int")))
            .collect(),
            primitive("Int"),
            Internal,
            "READ",
        ),
    ];
    ToolContractCatalog::new(specs.into_iter().map(
        |(name, inputs, output, classification, capability)| {
            let mut options = ToolContractOptions::default();
            if capability == "WRITE" {
                options.operation_kind = Some("CREATE".into());
                options.risk_level = Some("R1".into());
                options.write_semantics = Some(
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
                );
            }
            ToolContract::new(ToolContractSpec {
                tool_id: name.into(),
                version: "1.0.0".into(),
                capability: capability.into(),
                input_types: inputs
                    .into_iter()
                    .map(|(key, ty)| (key.into(), ty))
                    .collect(),
                output_type: output,
                required_scope: "test:scope".into(),
                output_classification: classification,
                options,
            })
            .unwrap()
        },
    ))
    .unwrap()
}
