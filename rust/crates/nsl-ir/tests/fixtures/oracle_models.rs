// Generated from the frozen Oracle's decoded IR by fixtures/generate.py.
fn ir_1_0() -> SkillObject {
    SkillObject {
        ir_version: "1.0".parse().unwrap(),
        language_version: "0.1".into(),
        skill_id: "FINANCE.PROJECT_BUDGET_CHECK".into(),
        skill_version: "1.0.0".into(),
        risk: "READ_VALIDATE".into(),
        semantics_profile: "NSL-0.1-STRICT".into(),
        semantic_hash: "sha256:ba75f7b96d08f0a8bff6ff1917b127c36cf2a0c96f1d29b7dc883674a4678b56"
            .parse()
            .unwrap(),
        features: [
            "CHECK".into(),
            "EMIT".into(),
            "FOREACH".into(),
            "LET".into(),
            "LIMITS".into(),
            "READ".into(),
        ]
        .into(),
        symbols: vec![
            SymbolSpec {
                name: "year".into(),
                type_info: ty(TypeKind::Primitive("Year".into())),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0001".into(),
                category: "INPUT".into(),
            },
            SymbolSpec {
                name: "team_id".into(),
                type_info: ty(TypeKind::Domain("TeamId".into())),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0002".into(),
                category: "CONTEXT".into(),
            },
            SymbolSpec {
                name: "parents".into(),
                type_info: ty(TypeKind::List(Box::new(ty(TypeKind::Record {
                    name: "ParentProject".into(),
                    fields: vec![
                        (
                            "project_code".into(),
                            ty(TypeKind::Domain("ProjectCode".into())),
                        ),
                        ("budget".into(), ty(TypeKind::Money("KRW".parse().unwrap()))),
                    ],
                })))),
                classification: "CONFIDENTIAL".parse().unwrap(),
                symbol_id: "s0003".into(),
                category: "VARIABLE".into(),
            },
            SymbolSpec {
                name: "parent".into(),
                type_info: ty(TypeKind::Record {
                    name: "ParentProject".into(),
                    fields: vec![
                        (
                            "project_code".into(),
                            ty(TypeKind::Domain("ProjectCode".into())),
                        ),
                        ("budget".into(), ty(TypeKind::Money("KRW".parse().unwrap()))),
                    ],
                }),
                classification: "CONFIDENTIAL".parse().unwrap(),
                symbol_id: "s0004".into(),
                category: "ITERATOR".into(),
            },
            SymbolSpec {
                name: "children".into(),
                type_info: ty(TypeKind::List(Box::new(ty(TypeKind::Record {
                    name: "ChildProject".into(),
                    fields: vec![
                        (
                            "project_code".into(),
                            ty(TypeKind::Domain("ProjectCode".into())),
                        ),
                        (
                            "parent_project".into(),
                            ty(TypeKind::Domain("ProjectCode".into())),
                        ),
                        (
                            "expense_amount".into(),
                            ty(TypeKind::Money("KRW".parse().unwrap())),
                        ),
                    ],
                })))),
                classification: "CONFIDENTIAL".parse().unwrap(),
                symbol_id: "s0005".into(),
                category: "VARIABLE".into(),
            },
            SymbolSpec {
                name: "spent".into(),
                type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                classification: "CONFIDENTIAL".parse().unwrap(),
                symbol_id: "s0006".into(),
                category: "VARIABLE".into(),
            },
            SymbolSpec {
                name: "remaining".into(),
                type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                classification: "CONFIDENTIAL".parse().unwrap(),
                symbol_id: "s0007".into(),
                category: "VARIABLE".into(),
            },
            SymbolSpec {
                name: "BUDGET_LIMIT".into(),
                type_info: ty(TypeKind::Record {
                    name: "CheckResult".into(),
                    fields: vec![(
                        "status".into(),
                        ty(TypeKind::Enum {
                            name: "CheckStatus".into(),
                            values: vec!["PASS".into(), "FAIL".into(), "UNKNOWN".into()],
                        }),
                    )],
                }),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0008".into(),
                category: "CHECK".into(),
            },
        ],
        required_tools: vec![
            RequiredTool {
                kind: RequiredToolKind::ReadV1_0,
                contract_hash:
                    "sha256:f673bce4e17a04cdec1b829b4c9c56d3ce01e22a7434b97605af6362500f488c"
                        .parse()
                        .unwrap(),
                output_classification: "CONFIDENTIAL".parse().unwrap(),
                tool_ref: "tool0001".into(),
                tool_id: "PROJECT.LIST_PARENT_PROJECTS".into(),
                version: "1.0.0".into(),
                required_scope: "project:budget:read".into(),
            },
            RequiredTool {
                kind: RequiredToolKind::ReadV1_0,
                contract_hash:
                    "sha256:a1e536cb1dd89c8177eedc53e9c3a5edf5eeef592617af9a110771fdabee9966"
                        .parse()
                        .unwrap(),
                output_classification: "CONFIDENTIAL".parse().unwrap(),
                tool_ref: "tool0002".into(),
                tool_id: "PROJECT.LIST_CHILD_PROJECTS".into(),
                version: "1.0.0".into(),
                required_scope: "project:budget:read".into(),
            },
        ],
        limits: ResourceLimits {
            tool_calls: "11".parse().unwrap(),
            loop_iterations: "10".parse().unwrap(),
            emitted_rows: "10".parse().unwrap(),
            collection_size: "1000".parse().unwrap(),
            duration_ms: "60000".parse().unwrap(),
        },
        inputs: vec![InputSpec {
            name: "year".into(),
            type_info: ty(TypeKind::Primitive("Year".into())),
            classification: "INTERNAL".parse().unwrap(),
            symbol_id: "s0001".into(),
            required: true,
        }],
        contexts: vec![ContextSpec {
            name: "team_id".into(),
            type_info: ty(TypeKind::Domain("TeamId".into())),
            classification: "INTERNAL".parse().unwrap(),
            symbol_id: "s0002".into(),
            path: vec!["user".into(), "team_id".into()],
        }],
        outputs: vec![
            OutputField {
                name: "parent_project".into(),
                type_info: ty(TypeKind::Domain("ProjectCode".into())),
                classification: "CONFIDENTIAL".parse().unwrap(),
            },
            OutputField {
                name: "budget".into(),
                type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                classification: "CONFIDENTIAL".parse().unwrap(),
            },
            OutputField {
                name: "spent".into(),
                type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                classification: "CONFIDENTIAL".parse().unwrap(),
            },
            OutputField {
                name: "remaining".into(),
                type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                classification: "CONFIDENTIAL".parse().unwrap(),
            },
            OutputField {
                name: "status".into(),
                type_info: ty(TypeKind::Enum {
                    name: "CheckStatus".into(),
                    values: vec!["PASS".into(), "FAIL".into(), "UNKNOWN".into()],
                }),
                classification: "INTERNAL".parse().unwrap(),
            },
        ],
        body: vec![
            Statement {
                node_id: "stmt0001".into(),
                kind: StatementKind::Let {
                    target_symbol_id: "s0003".into(),
                    value: Expression {
                        node_id: "read0001".into(),
                        type_info: ty(TypeKind::List(Box::new(ty(TypeKind::Record {
                            name: "ParentProject".into(),
                            fields: vec![
                                (
                                    "project_code".into(),
                                    ty(TypeKind::Domain("ProjectCode".into())),
                                ),
                                ("budget".into(), ty(TypeKind::Money("KRW".parse().unwrap()))),
                            ],
                        })))),
                        kind: ExpressionKind::Read {
                            tool_ref: "tool0001".into(),
                            arguments: vec![
                                NamedExpression {
                                    name: "year".into(),
                                    value: Expression {
                                        node_id: "expr0001".into(),
                                        type_info: ty(TypeKind::Primitive("Year".into())),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0001".into(),
                                        },
                                    },
                                },
                                NamedExpression {
                                    name: "team_id".into(),
                                    value: Expression {
                                        node_id: "expr0002".into(),
                                        type_info: ty(TypeKind::Domain("TeamId".into())),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0002".into(),
                                        },
                                    },
                                },
                            ],
                            result_policy: ResultPolicy {
                                required: true,
                                accept_partial: false,
                                empty_is_valid: true,
                            },
                        },
                    },
                },
            },
            Statement {
                node_id: "stmt0005".into(),
                kind: StatementKind::Foreach {
                    iterator_symbol_id: "s0004".into(),
                    collection: Expression {
                        node_id: "expr0003".into(),
                        type_info: ty(TypeKind::List(Box::new(ty(TypeKind::Record {
                            name: "ParentProject".into(),
                            fields: vec![
                                (
                                    "project_code".into(),
                                    ty(TypeKind::Domain("ProjectCode".into())),
                                ),
                                ("budget".into(), ty(TypeKind::Money("KRW".parse().unwrap()))),
                            ],
                        })))),
                        kind: ExpressionKind::SymbolRef {
                            symbol_id: "s0003".into(),
                        },
                    },
                    max_iterations: "10".parse().unwrap(),
                    body: vec![
                        Statement {
                            node_id: "stmt0002".into(),
                            kind: StatementKind::Let {
                                target_symbol_id: "s0005".into(),
                                value: Expression {
                                    node_id: "read0002".into(),
                                    type_info: ty(TypeKind::List(Box::new(ty(TypeKind::Record {
                                        name: "ChildProject".into(),
                                        fields: vec![
                                            (
                                                "project_code".into(),
                                                ty(TypeKind::Domain("ProjectCode".into())),
                                            ),
                                            (
                                                "parent_project".into(),
                                                ty(TypeKind::Domain("ProjectCode".into())),
                                            ),
                                            (
                                                "expense_amount".into(),
                                                ty(TypeKind::Money("KRW".parse().unwrap())),
                                            ),
                                        ],
                                    })))),
                                    kind: ExpressionKind::Read {
                                        tool_ref: "tool0002".into(),
                                        arguments: vec![
                                            NamedExpression {
                                                name: "parent_project".into(),
                                                value: Expression {
                                                    node_id: "expr0005".into(),
                                                    type_info: ty(TypeKind::Domain(
                                                        "ProjectCode".into(),
                                                    )),
                                                    kind: ExpressionKind::Field {
                                                        source: Box::new(Expression {
                                                            node_id: "expr0004".into(),
                                                            type_info: ty(TypeKind::Record {
                                                                name: "ParentProject".into(),
                                                                fields: vec![
                                                                    (
                                                                        "project_code".into(),
                                                                        ty(TypeKind::Domain(
                                                                            "ProjectCode".into(),
                                                                        )),
                                                                    ),
                                                                    (
                                                                        "budget".into(),
                                                                        ty(TypeKind::Money(
                                                                            "KRW".parse().unwrap(),
                                                                        )),
                                                                    ),
                                                                ],
                                                            }),
                                                            kind: ExpressionKind::SymbolRef {
                                                                symbol_id: "s0004".into(),
                                                            },
                                                        }),
                                                        field: "project_code".into(),
                                                    },
                                                },
                                            },
                                            NamedExpression {
                                                name: "year".into(),
                                                value: Expression {
                                                    node_id: "expr0006".into(),
                                                    type_info: ty(TypeKind::Primitive(
                                                        "Year".into(),
                                                    )),
                                                    kind: ExpressionKind::SymbolRef {
                                                        symbol_id: "s0001".into(),
                                                    },
                                                },
                                            },
                                        ],
                                        result_policy: ResultPolicy {
                                            required: true,
                                            accept_partial: false,
                                            empty_is_valid: true,
                                        },
                                    },
                                },
                            },
                        },
                        Statement {
                            node_id: "stmt0003".into(),
                            kind: StatementKind::Let {
                                target_symbol_id: "s0006".into(),
                                value: Expression {
                                    node_id: "expr0009".into(),
                                    type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                                    kind: ExpressionKind::Call {
                                        function: "sum".into(),
                                        arguments: vec![Expression {
                                            node_id: "expr0008".into(),
                                            type_info: ty(TypeKind::List(Box::new(ty(
                                                TypeKind::Money("KRW".parse().unwrap()),
                                            )))),
                                            kind: ExpressionKind::Projection {
                                                source: Box::new(Expression {
                                                    node_id: "expr0007".into(),
                                                    type_info: ty(TypeKind::List(Box::new(ty(
                                                        TypeKind::Record {
                                                            name: "ChildProject".into(),
                                                            fields: vec![
                                                                (
                                                                    "project_code".into(),
                                                                    ty(TypeKind::Domain(
                                                                        "ProjectCode".into(),
                                                                    )),
                                                                ),
                                                                (
                                                                    "parent_project".into(),
                                                                    ty(TypeKind::Domain(
                                                                        "ProjectCode".into(),
                                                                    )),
                                                                ),
                                                                (
                                                                    "expense_amount".into(),
                                                                    ty(TypeKind::Money(
                                                                        "KRW".parse().unwrap(),
                                                                    )),
                                                                ),
                                                            ],
                                                        },
                                                    )))),
                                                    kind: ExpressionKind::SymbolRef {
                                                        symbol_id: "s0005".into(),
                                                    },
                                                }),
                                                field: "expense_amount".into(),
                                            },
                                        }],
                                    },
                                },
                            },
                        },
                        Statement {
                            node_id: "stmt0004".into(),
                            kind: StatementKind::Let {
                                target_symbol_id: "s0007".into(),
                                value: Expression {
                                    node_id: "expr0013".into(),
                                    type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                                    kind: ExpressionKind::Binary {
                                        operator: "SUB".into(),
                                        left: Box::new(Expression {
                                            node_id: "expr0011".into(),
                                            type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                                            kind: ExpressionKind::Field {
                                                source: Box::new(Expression {
                                                    node_id: "expr0010".into(),
                                                    type_info: ty(TypeKind::Record {
                                                        name: "ParentProject".into(),
                                                        fields: vec![
                                                            (
                                                                "project_code".into(),
                                                                ty(TypeKind::Domain(
                                                                    "ProjectCode".into(),
                                                                )),
                                                            ),
                                                            (
                                                                "budget".into(),
                                                                ty(TypeKind::Money(
                                                                    "KRW".parse().unwrap(),
                                                                )),
                                                            ),
                                                        ],
                                                    }),
                                                    kind: ExpressionKind::SymbolRef {
                                                        symbol_id: "s0004".into(),
                                                    },
                                                }),
                                                field: "budget".into(),
                                            },
                                        }),
                                        right: Box::new(Expression {
                                            node_id: "expr0012".into(),
                                            type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                                            kind: ExpressionKind::SymbolRef {
                                                symbol_id: "s0006".into(),
                                            },
                                        }),
                                    },
                                },
                            },
                        },
                        Statement {
                            node_id: "check0001".into(),
                            kind: StatementKind::Check {
                                condition: Expression {
                                    node_id: "expr0017".into(),
                                    type_info: ty(TypeKind::Primitive("Bool".into())),
                                    kind: ExpressionKind::Binary {
                                        operator: "LE".into(),
                                        left: Box::new(Expression {
                                            node_id: "expr0014".into(),
                                            type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                                            kind: ExpressionKind::SymbolRef {
                                                symbol_id: "s0006".into(),
                                            },
                                        }),
                                        right: Box::new(Expression {
                                            node_id: "expr0016".into(),
                                            type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                                            kind: ExpressionKind::Field {
                                                source: Box::new(Expression {
                                                    node_id: "expr0015".into(),
                                                    type_info: ty(TypeKind::Record {
                                                        name: "ParentProject".into(),
                                                        fields: vec![
                                                            (
                                                                "project_code".into(),
                                                                ty(TypeKind::Domain(
                                                                    "ProjectCode".into(),
                                                                )),
                                                            ),
                                                            (
                                                                "budget".into(),
                                                                ty(TypeKind::Money(
                                                                    "KRW".parse().unwrap(),
                                                                )),
                                                            ),
                                                        ],
                                                    }),
                                                    kind: ExpressionKind::SymbolRef {
                                                        symbol_id: "s0004".into(),
                                                    },
                                                }),
                                                field: "budget".into(),
                                            },
                                        }),
                                    },
                                },
                                check_id: "BUDGET_LIMIT".into(),
                                severity: "ERROR".into(),
                                on_fail: "REPORT".into(),
                                message: "자 프로젝트 지출 합계가 모 프로젝트 예산을 초과했습니다."
                                    .into(),
                                result_symbol_id: "s0008".into(),
                            },
                        },
                        Statement {
                            node_id: "emit0001".into(),
                            kind: StatementKind::Emit {
                                fields: vec![
                                    NamedExpression {
                                        name: "parent_project".into(),
                                        value: Expression {
                                            node_id: "expr0019".into(),
                                            type_info: ty(TypeKind::Domain("ProjectCode".into())),
                                            kind: ExpressionKind::Field {
                                                source: Box::new(Expression {
                                                    node_id: "expr0018".into(),
                                                    type_info: ty(TypeKind::Record {
                                                        name: "ParentProject".into(),
                                                        fields: vec![
                                                            (
                                                                "project_code".into(),
                                                                ty(TypeKind::Domain(
                                                                    "ProjectCode".into(),
                                                                )),
                                                            ),
                                                            (
                                                                "budget".into(),
                                                                ty(TypeKind::Money(
                                                                    "KRW".parse().unwrap(),
                                                                )),
                                                            ),
                                                        ],
                                                    }),
                                                    kind: ExpressionKind::SymbolRef {
                                                        symbol_id: "s0004".into(),
                                                    },
                                                }),
                                                field: "project_code".into(),
                                            },
                                        },
                                    },
                                    NamedExpression {
                                        name: "budget".into(),
                                        value: Expression {
                                            node_id: "expr0021".into(),
                                            type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                                            kind: ExpressionKind::Field {
                                                source: Box::new(Expression {
                                                    node_id: "expr0020".into(),
                                                    type_info: ty(TypeKind::Record {
                                                        name: "ParentProject".into(),
                                                        fields: vec![
                                                            (
                                                                "project_code".into(),
                                                                ty(TypeKind::Domain(
                                                                    "ProjectCode".into(),
                                                                )),
                                                            ),
                                                            (
                                                                "budget".into(),
                                                                ty(TypeKind::Money(
                                                                    "KRW".parse().unwrap(),
                                                                )),
                                                            ),
                                                        ],
                                                    }),
                                                    kind: ExpressionKind::SymbolRef {
                                                        symbol_id: "s0004".into(),
                                                    },
                                                }),
                                                field: "budget".into(),
                                            },
                                        },
                                    },
                                    NamedExpression {
                                        name: "spent".into(),
                                        value: Expression {
                                            node_id: "expr0022".into(),
                                            type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                                            kind: ExpressionKind::SymbolRef {
                                                symbol_id: "s0006".into(),
                                            },
                                        },
                                    },
                                    NamedExpression {
                                        name: "remaining".into(),
                                        value: Expression {
                                            node_id: "expr0023".into(),
                                            type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                                            kind: ExpressionKind::SymbolRef {
                                                symbol_id: "s0007".into(),
                                            },
                                        },
                                    },
                                    NamedExpression {
                                        name: "status".into(),
                                        value: Expression {
                                            node_id: "expr0025".into(),
                                            type_info: ty(TypeKind::Enum {
                                                name: "CheckStatus".into(),
                                                values: vec![
                                                    "PASS".into(),
                                                    "FAIL".into(),
                                                    "UNKNOWN".into(),
                                                ],
                                            }),
                                            kind: ExpressionKind::Field {
                                                source: Box::new(Expression {
                                                    node_id: "expr0024".into(),
                                                    type_info: ty(TypeKind::Record {
                                                        name: "CheckResult".into(),
                                                        fields: vec![(
                                                            "status".into(),
                                                            ty(TypeKind::Enum {
                                                                name: "CheckStatus".into(),
                                                                values: vec![
                                                                    "PASS".into(),
                                                                    "FAIL".into(),
                                                                    "UNKNOWN".into(),
                                                                ],
                                                            }),
                                                        )],
                                                    }),
                                                    kind: ExpressionKind::SymbolRef {
                                                        symbol_id: "s0008".into(),
                                                    },
                                                }),
                                                field: "status".into(),
                                            },
                                        },
                                    },
                                ],
                            },
                        },
                    ],
                },
            },
        ],
        analysis: StaticAnalysis {
            max_tool_calls: "11".parse().unwrap(),
            max_loop_iterations: "10".parse().unwrap(),
            max_emit_records: "10".parse().unwrap(),
            bounded: true,
        },
        build: BuildMetadata {
            root_source: "memory/root.ns".into(),
            source_bundle_sha256:
                "sha256:c01e330d1c06d9668bf8ec94cfc1b2221a620e338ba816d57e73c424729855d6"
                    .parse()
                    .unwrap(),
            sources: vec![BuildSource {
                logical_path: "memory/root.ns".into(),
                content_hash:
                    "sha256:9151a3e27f7284ee305489f256cca91d1e1c4d550d82d80dd765af1e5a72e103"
                        .parse()
                        .unwrap(),
                size_bytes: "1674".parse().unwrap(),
                is_root: true,
            }],
        },
    }
}
fn ir_1_1() -> SkillObject {
    SkillObject {
        ir_version: "1.1".parse().unwrap(),
        language_version: "0.2".into(),
        skill_id: "PROJECT.PROJECT.DEACTIVATE".into(),
        skill_version: "1.0.0".into(),
        risk: "WRITE".into(),
        semantics_profile: "NSL-0.2-STRICT".into(),
        semantic_hash: "sha256:52ea296addf61c508ea784ad329348488244ce8f8ec306d299f9deba7dc87a2d"
            .parse()
            .unwrap(),
        features: [
            "APPROVAL".into(),
            "CHECK".into(),
            "EMIT".into(),
            "FOREACH".into(),
            "LET".into(),
            "LIMITS".into(),
            "READ".into(),
            "WRITE".into(),
        ]
        .into(),
        symbols: vec![
            SymbolSpec {
                name: "tenant_id".into(),
                type_info: ty(TypeKind::Primitive("String".into())),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0001".into(),
                category: "INPUT".into(),
            },
            SymbolSpec {
                name: "requester_subject_id".into(),
                type_info: ty(TypeKind::Primitive("String".into())),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0002".into(),
                category: "INPUT".into(),
            },
            SymbolSpec {
                name: "project_id".into(),
                type_info: ty(TypeKind::Primitive("String".into())),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0003".into(),
                category: "INPUT".into(),
            },
            SymbolSpec {
                name: "expected_version".into(),
                type_info: ty(TypeKind::Primitive("Int".into())),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0004".into(),
                category: "INPUT".into(),
            },
            SymbolSpec {
                name: "project".into(),
                type_info: ty(TypeKind::Record {
                    name: "Project".into(),
                    fields: vec![
                        (
                            "project_id".into(),
                            ty(TypeKind::Primitive("String".into())),
                        ),
                        (
                            "project_code".into(),
                            ty(TypeKind::Domain("ProjectCode".into())),
                        ),
                        ("name".into(), ty(TypeKind::Primitive("String".into()))),
                        ("status".into(), ty(TypeKind::Primitive("String".into()))),
                        (
                            "entity_version".into(),
                            ty(TypeKind::Primitive("Int".into())),
                        ),
                    ],
                }),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0005".into(),
                category: "VARIABLE".into(),
            },
        ],
        required_tools: vec![RequiredTool {
            kind: RequiredToolKind::V1_1 {
                capability: "WRITE".parse().unwrap(),
                risk_level: "R3".parse().unwrap(),
            },
            contract_hash:
                "sha256:bed62697be2ec05f0336e116ffcd315ed733a3617521729ed23d9feafee3c430"
                    .parse()
                    .unwrap(),
            output_classification: "INTERNAL".parse().unwrap(),
            tool_ref: "tool0001".into(),
            tool_id: "PROJECT.PROJECT.DEACTIVATE".into(),
            version: "1.0.0".into(),
            required_scope: "project:project:admin".into(),
        }],
        limits: ResourceLimits {
            tool_calls: "1".parse().unwrap(),
            loop_iterations: "1".parse().unwrap(),
            emitted_rows: "1".parse().unwrap(),
            collection_size: "1".parse().unwrap(),
            duration_ms: "60000".parse().unwrap(),
        },
        inputs: vec![
            InputSpec {
                name: "tenant_id".into(),
                type_info: ty(TypeKind::Primitive("String".into())),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0001".into(),
                required: true,
            },
            InputSpec {
                name: "requester_subject_id".into(),
                type_info: ty(TypeKind::Primitive("String".into())),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0002".into(),
                required: true,
            },
            InputSpec {
                name: "project_id".into(),
                type_info: ty(TypeKind::Primitive("String".into())),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0003".into(),
                required: true,
            },
            InputSpec {
                name: "expected_version".into(),
                type_info: ty(TypeKind::Primitive("Int".into())),
                classification: "INTERNAL".parse().unwrap(),
                symbol_id: "s0004".into(),
                required: true,
            },
        ],
        contexts: vec![],
        outputs: vec![
            OutputField {
                name: "project_id".into(),
                type_info: ty(TypeKind::Primitive("String".into())),
                classification: "INTERNAL".parse().unwrap(),
            },
            OutputField {
                name: "project_code".into(),
                type_info: ty(TypeKind::Domain("ProjectCode".into())),
                classification: "INTERNAL".parse().unwrap(),
            },
            OutputField {
                name: "name".into(),
                type_info: ty(TypeKind::Primitive("String".into())),
                classification: "INTERNAL".parse().unwrap(),
            },
            OutputField {
                name: "status".into(),
                type_info: ty(TypeKind::Primitive("String".into())),
                classification: "INTERNAL".parse().unwrap(),
            },
            OutputField {
                name: "entity_version".into(),
                type_info: ty(TypeKind::Primitive("Int".into())),
                classification: "INTERNAL".parse().unwrap(),
            },
        ],
        body: vec![
            Statement {
                node_id: "approval0001".into(),
                kind: StatementKind::Approval(ApprovalStatement {
                    approval_id: "PROJECT_DEACTIVATE".into(),
                    message: "Deactivate the governed Project?".into(),
                    policy: ApprovalPolicy { ttl_seconds: None },
                }),
            },
            Statement {
                node_id: "stmt0001".into(),
                kind: StatementKind::Let {
                    target_symbol_id: "s0005".into(),
                    value: Expression {
                        node_id: "write0001".into(),
                        type_info: ty(TypeKind::Record {
                            name: "Project".into(),
                            fields: vec![
                                (
                                    "project_id".into(),
                                    ty(TypeKind::Primitive("String".into())),
                                ),
                                (
                                    "project_code".into(),
                                    ty(TypeKind::Domain("ProjectCode".into())),
                                ),
                                ("name".into(), ty(TypeKind::Primitive("String".into()))),
                                ("status".into(), ty(TypeKind::Primitive("String".into()))),
                                (
                                    "entity_version".into(),
                                    ty(TypeKind::Primitive("Int".into())),
                                ),
                            ],
                        }),
                        kind: ExpressionKind::Write(WriteExpression {
                            tool_ref: "tool0001".into(),
                            arguments: vec![
                                NamedExpression {
                                    name: "tenant_id".into(),
                                    value: Expression {
                                        node_id: "expr0001".into(),
                                        type_info: ty(TypeKind::Primitive("String".into())),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0001".into(),
                                        },
                                    },
                                },
                                NamedExpression {
                                    name: "requester_subject_id".into(),
                                    value: Expression {
                                        node_id: "expr0002".into(),
                                        type_info: ty(TypeKind::Primitive("String".into())),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0002".into(),
                                        },
                                    },
                                },
                                NamedExpression {
                                    name: "project_id".into(),
                                    value: Expression {
                                        node_id: "expr0003".into(),
                                        type_info: ty(TypeKind::Primitive("String".into())),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0003".into(),
                                        },
                                    },
                                },
                                NamedExpression {
                                    name: "expected_version".into(),
                                    value: Expression {
                                        node_id: "expr0004".into(),
                                        type_info: ty(TypeKind::Primitive("Int".into())),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0004".into(),
                                        },
                                    },
                                },
                            ],
                            approval_ref: "PROJECT_DEACTIVATE".into(),
                        }),
                    },
                },
            },
            Statement {
                node_id: "emit0001".into(),
                kind: StatementKind::Emit {
                    fields: vec![
                        NamedExpression {
                            name: "project_id".into(),
                            value: Expression {
                                node_id: "expr0006".into(),
                                type_info: ty(TypeKind::Primitive("String".into())),
                                kind: ExpressionKind::Field {
                                    source: Box::new(Expression {
                                        node_id: "expr0005".into(),
                                        type_info: ty(TypeKind::Record {
                                            name: "Project".into(),
                                            fields: vec![
                                                (
                                                    "project_id".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "project_code".into(),
                                                    ty(TypeKind::Domain("ProjectCode".into())),
                                                ),
                                                (
                                                    "name".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "status".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "entity_version".into(),
                                                    ty(TypeKind::Primitive("Int".into())),
                                                ),
                                            ],
                                        }),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0005".into(),
                                        },
                                    }),
                                    field: "project_id".into(),
                                },
                            },
                        },
                        NamedExpression {
                            name: "project_code".into(),
                            value: Expression {
                                node_id: "expr0008".into(),
                                type_info: ty(TypeKind::Domain("ProjectCode".into())),
                                kind: ExpressionKind::Field {
                                    source: Box::new(Expression {
                                        node_id: "expr0007".into(),
                                        type_info: ty(TypeKind::Record {
                                            name: "Project".into(),
                                            fields: vec![
                                                (
                                                    "project_id".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "project_code".into(),
                                                    ty(TypeKind::Domain("ProjectCode".into())),
                                                ),
                                                (
                                                    "name".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "status".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "entity_version".into(),
                                                    ty(TypeKind::Primitive("Int".into())),
                                                ),
                                            ],
                                        }),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0005".into(),
                                        },
                                    }),
                                    field: "project_code".into(),
                                },
                            },
                        },
                        NamedExpression {
                            name: "name".into(),
                            value: Expression {
                                node_id: "expr0010".into(),
                                type_info: ty(TypeKind::Primitive("String".into())),
                                kind: ExpressionKind::Field {
                                    source: Box::new(Expression {
                                        node_id: "expr0009".into(),
                                        type_info: ty(TypeKind::Record {
                                            name: "Project".into(),
                                            fields: vec![
                                                (
                                                    "project_id".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "project_code".into(),
                                                    ty(TypeKind::Domain("ProjectCode".into())),
                                                ),
                                                (
                                                    "name".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "status".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "entity_version".into(),
                                                    ty(TypeKind::Primitive("Int".into())),
                                                ),
                                            ],
                                        }),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0005".into(),
                                        },
                                    }),
                                    field: "name".into(),
                                },
                            },
                        },
                        NamedExpression {
                            name: "status".into(),
                            value: Expression {
                                node_id: "expr0012".into(),
                                type_info: ty(TypeKind::Primitive("String".into())),
                                kind: ExpressionKind::Field {
                                    source: Box::new(Expression {
                                        node_id: "expr0011".into(),
                                        type_info: ty(TypeKind::Record {
                                            name: "Project".into(),
                                            fields: vec![
                                                (
                                                    "project_id".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "project_code".into(),
                                                    ty(TypeKind::Domain("ProjectCode".into())),
                                                ),
                                                (
                                                    "name".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "status".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "entity_version".into(),
                                                    ty(TypeKind::Primitive("Int".into())),
                                                ),
                                            ],
                                        }),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0005".into(),
                                        },
                                    }),
                                    field: "status".into(),
                                },
                            },
                        },
                        NamedExpression {
                            name: "entity_version".into(),
                            value: Expression {
                                node_id: "expr0014".into(),
                                type_info: ty(TypeKind::Primitive("Int".into())),
                                kind: ExpressionKind::Field {
                                    source: Box::new(Expression {
                                        node_id: "expr0013".into(),
                                        type_info: ty(TypeKind::Record {
                                            name: "Project".into(),
                                            fields: vec![
                                                (
                                                    "project_id".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "project_code".into(),
                                                    ty(TypeKind::Domain("ProjectCode".into())),
                                                ),
                                                (
                                                    "name".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "status".into(),
                                                    ty(TypeKind::Primitive("String".into())),
                                                ),
                                                (
                                                    "entity_version".into(),
                                                    ty(TypeKind::Primitive("Int".into())),
                                                ),
                                            ],
                                        }),
                                        kind: ExpressionKind::SymbolRef {
                                            symbol_id: "s0005".into(),
                                        },
                                    }),
                                    field: "entity_version".into(),
                                },
                            },
                        },
                    ],
                },
            },
        ],
        analysis: StaticAnalysis {
            max_tool_calls: "1".parse().unwrap(),
            max_loop_iterations: "0".parse().unwrap(),
            max_emit_records: "1".parse().unwrap(),
            bounded: true,
        },
        build: BuildMetadata {
            root_source: "memory/root.ns".into(),
            source_bundle_sha256:
                "sha256:7a0d31c5c1dd10d611a48f6baffb92545ab3c3261e5d2d3b2108936db72ff8d4"
                    .parse()
                    .unwrap(),
            sources: vec![BuildSource {
                logical_path: "memory/root.ns".into(),
                content_hash:
                    "sha256:fbbd7f491843c52b43f93821e73c6f23596c48767e52014ba8bc9e5a07c98aaa"
                        .parse()
                        .unwrap(),
                size_bytes: "1346".parse().unwrap(),
                is_root: true,
            }],
        },
    }
}
fn literal_cases() -> [(Expression, &'static str); 5] {
    [
        (
            Expression {
                node_id: "literal-0".into(),
                type_info: ty(TypeKind::Primitive("Bool".into())),
                kind: ExpressionKind::Literal(LiteralValue::Bool(false)),
            },
            "{\"kind\":\"literal\",\"node_id\":\"literal-0\",\"type\":{\"kind\":\"primitive\",\"name\":\"Bool\"},\"value\":false}",
        ),
        (
            Expression {
                node_id: "literal-1".into(),
                type_info: ty(TypeKind::Primitive("Int".into())),
                kind: ExpressionKind::Literal(LiteralValue::Int(
                    "340282366920938463463374607431768211457".parse().unwrap(),
                )),
            },
            "{\"kind\":\"literal\",\"node_id\":\"literal-1\",\"type\":{\"kind\":\"primitive\",\"name\":\"Int\"},\"value\":340282366920938463463374607431768211457}",
        ),
        (
            Expression {
                node_id: "literal-2".into(),
                type_info: ty(TypeKind::Primitive("String".into())),
                kind: ExpressionKind::Literal(LiteralValue::String("예산".into())),
            },
            "{\"kind\":\"literal\",\"node_id\":\"literal-2\",\"type\":{\"kind\":\"primitive\",\"name\":\"String\"},\"value\":\"예산\"}",
        ),
        (
            Expression {
                node_id: "literal-3".into(),
                type_info: ty(TypeKind::Primitive("Decimal".into())),
                kind: ExpressionKind::Literal(LiteralValue::Decimal("-0.00".parse().unwrap())),
            },
            "{\"kind\":\"literal\",\"node_id\":\"literal-3\",\"type\":{\"kind\":\"primitive\",\"name\":\"Decimal\"},\"value\":{\"$type\":\"Decimal\",\"value\":\"-0.00\"}}",
        ),
        (
            Expression {
                node_id: "literal-4".into(),
                type_info: ty(TypeKind::Money("KRW".parse().unwrap())),
                kind: ExpressionKind::Literal(LiteralValue::Money(Money::new(
                    "0.10".parse().unwrap(),
                    "KRW".parse().unwrap(),
                ))),
            },
            "{\"kind\":\"literal\",\"node_id\":\"literal-4\",\"type\":{\"currency\":\"KRW\",\"kind\":\"money\"},\"value\":{\"$type\":\"Money\",\"amount\":\"0.10\",\"currency\":\"KRW\"}}",
        ),
    ]
}
fn approval_cases() -> [(Statement, &'static str); 3] {
    [
        (
            Statement {
                node_id: "approval-1".into(),
                kind: StatementKind::Approval(ApprovalStatement {
                    approval_id: "DEMO".into(),
                    message: "".into(),
                    policy: ApprovalPolicy { ttl_seconds: None },
                }),
            },
            "{\"approval_id\":\"DEMO\",\"kind\":\"approval\",\"message\":\"\",\"node_id\":\"approval-1\",\"policy\":{\"required\":true}}",
        ),
        (
            Statement {
                node_id: "approval-1".into(),
                kind: StatementKind::Approval(ApprovalStatement {
                    approval_id: "DEMO".into(),
                    message: "".into(),
                    policy: ApprovalPolicy {
                        ttl_seconds: Some("1".parse().unwrap()),
                    },
                }),
            },
            "{\"approval_id\":\"DEMO\",\"kind\":\"approval\",\"message\":\"\",\"node_id\":\"approval-1\",\"policy\":{\"required\":true,\"ttl_seconds\":1}}",
        ),
        (
            Statement {
                node_id: "approval-1".into(),
                kind: StatementKind::Approval(ApprovalStatement {
                    approval_id: "DEMO".into(),
                    message: "".into(),
                    policy: ApprovalPolicy {
                        ttl_seconds: Some("18446744073709551616".parse().unwrap()),
                    },
                }),
            },
            "{\"approval_id\":\"DEMO\",\"kind\":\"approval\",\"message\":\"\",\"node_id\":\"approval-1\",\"policy\":{\"required\":true,\"ttl_seconds\":18446744073709551616}}",
        ),
    ]
}
fn oracle_default_result_policy() -> ResultPolicy {
    ResultPolicy {
        required: true,
        accept_partial: false,
        empty_is_valid: true,
    }
}
