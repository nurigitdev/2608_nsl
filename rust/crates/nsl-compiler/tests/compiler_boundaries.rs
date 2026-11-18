#[path = "approval_support/mod.rs"]
mod source_support;
mod type_support;

use nsl_compiler::includes::{IncludeOptions, IncludeResolveError, IncludeResolver};
use nsl_compiler::{CompileError, CompilerOptions, NslCompiler};
use nsl_ir::{NsoCodec, NsoLimit, NsoLimits};
use nsl_source::{ParseLimits, SourceFile};
use source_support::decode;
use std::{collections::HashMap, error::Error};

fn options(limits: NsoLimits) -> CompilerOptions {
    CompilerOptions {
        parse_limits: ParseLimits { max_nesting: 64 },
        include_options: IncludeOptions::default(),
        nso_limits: limits,
    }
}
fn generous() -> NsoLimits {
    NsoLimits {
        max_bytes: 2_000_000,
        max_depth: 256,
        max_nodes: 100_000,
    }
}
struct Memory(Vec<SourceFile>);
impl IncludeResolver for Memory {
    fn resolve(&self, _: &SourceFile, path: &str) -> Result<SourceFile, IncludeResolveError> {
        self.0
            .iter()
            .find(|s| s.logical_path() == path)
            .cloned()
            .ok_or(IncludeResolveError)
    }
}

#[test]
fn exact_oracle_output_limits_accept_equality_and_reject_one_below() {
    let metrics = include_str!("fixtures/compiler-limit-oracle.tsv")
        .lines()
        .filter(|row| !row.starts_with('#'))
        .map(|row| {
            let c = row.split('\t').collect::<Vec<_>>();
            (
                (c[0], c[1]),
                NsoLimits {
                    max_bytes: c[2].parse().unwrap(),
                    max_depth: c[3].parse().unwrap(),
                    max_nodes: c[4].parse().unwrap(),
                },
            )
        })
        .collect::<HashMap<_, _>>();
    let catalog = type_support::catalog();
    let mut count = 0;
    for header in include_str!("fixtures/compiler-oracle.tsv")
        .lines()
        .filter(|row| row.starts_with("case\t"))
    {
        let c = header.split('\t').collect::<Vec<_>>();
        let Some(limits) = metrics.get(&(c[1], c[2])).copied() else {
            continue;
        };
        let source = SourceFile::from_text("compiler/root.ns", &decode(c[3]), None).unwrap();
        let memory = Memory(if c[4] == "~" {
            vec![]
        } else {
            vec![SourceFile::from_text("compiler/part.ns", &decode(c[4]), None).unwrap()]
        });
        let result = NslCompiler::new(&catalog, options(limits))
            .with_include_resolver(&memory)
            .compile(source.clone())
            .unwrap_or_else(|e| panic!("{} exact limits: {e:?}", c[1]));
        assert_eq!(result.nso_bytes.len(), limits.max_bytes);
        assert_eq!(
            NsoCodec::encode(&result.skill, limits.max_bytes).unwrap(),
            result.nso_bytes
        );
        assert_eq!(
            NsoCodec::decode(&result.nso_bytes, limits).unwrap(),
            result.skill
        );
        for dimension in [NsoLimit::Bytes, NsoLimit::Depth, NsoLimit::Nodes] {
            let mut under = limits;
            match dimension {
                NsoLimit::Bytes => under.max_bytes -= 1,
                NsoLimit::Depth => under.max_depth -= 1,
                NsoLimit::Nodes => under.max_nodes -= 1,
            }
            let error = NslCompiler::new(&catalog, options(under))
                .with_include_resolver(&memory)
                .compile(source.clone())
                .unwrap_err();
            let abort = error
                .output_limit()
                .unwrap_or_else(|| panic!("{} {dimension:?}: {error:?}", c[1]));
            assert_eq!(abort.limit, dimension, "{}", c[1]);
            assert!(error.diagnostic().is_none());
            assert!(
                [
                    source.source_id().as_str(),
                    memory.0.first().map_or("", |s| s.source_id().as_str())
                ]
                .contains(&abort.span.source_id().as_str())
            );
            assert!(error.source().is_some());
            assert!(!error.to_string().is_empty());
            let mut over = limits;
            match dimension {
                NsoLimit::Bytes => over.max_bytes += 1,
                NsoLimit::Depth => over.max_depth += 1,
                NsoLimit::Nodes => over.max_nodes += 1,
            }
            assert_eq!(
                result,
                NslCompiler::new(&catalog, options(over))
                    .with_include_resolver(&memory)
                    .compile(source.clone())
                    .unwrap()
            );
        }
        count += 1;
    }
    assert_eq!(count, 47);
}

fn simple(body: &str) -> SourceFile {
    SourceFile::memory(&format!(
        "language NSL \"0.2\"; skill TEST {{version \"1\"; risk READ_ONLY; limits {{tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1;}} {body} }}"
    ))
}

#[test]
fn zero_one_and_worst_case_sources_fail_with_typed_output_limits() {
    let catalog = type_support::catalog();
    let source = simple("let x = 1;");
    for value in [0, 1] {
        for kind in [NsoLimit::Bytes, NsoLimit::Depth, NsoLimit::Nodes] {
            let mut limits = generous();
            match kind {
                NsoLimit::Bytes => limits.max_bytes = value,
                NsoLimit::Depth => limits.max_depth = value,
                NsoLimit::Nodes => limits.max_nodes = value,
            };
            let error = NslCompiler::new(&catalog, options(limits))
                .compile(source.clone())
                .unwrap_err();
            assert_eq!(error.output_limit().unwrap().limit, kind);
            assert!(error.diagnostic().is_none());
        }
    }
    let chain = simple(&format!("let x = {}1;", "1 + ".repeat(8192)));
    let mut limits = generous();
    limits.max_depth = 32;
    let error = NslCompiler::new(&catalog, options(limits))
        .compile(chain)
        .unwrap_err();
    assert_eq!(error.output_limit().unwrap().limit, NsoLimit::Depth);
    let declarations = (0..10000)
        .map(|i| format!("x{i}: Int;"))
        .collect::<String>();
    let header = simple(&format!("input {{{declarations}}}"));
    let mut limits = generous();
    limits.max_nodes = 100;
    let error = NslCompiler::new(&catalog, options(limits))
        .compile(header)
        .unwrap_err();
    assert_eq!(error.output_limit().unwrap().limit, NsoLimit::Nodes);
    let decimal = simple(&format!("let x = {}.0;", "1".repeat(4000)));
    let mut limits = generous();
    limits.max_bytes = 100;
    let error = NslCompiler::new(&catalog, options(limits))
        .compile(decimal)
        .unwrap_err();
    assert_eq!(error.output_limit().unwrap().limit, NsoLimit::Bytes);
}

#[test]
fn typed_frontend_admission_and_error_chains_remain_available() {
    let catalog = type_support::catalog();
    let mut opts = options(generous());
    opts.parse_limits.max_nesting = 1;
    let error = NslCompiler::new(&catalog, opts)
        .compile(simple("let x = ((1));"))
        .unwrap_err();
    assert!(matches!(error, CompileError::Parse(_)));
    assert!(error.diagnostic().is_none());
    assert!(error.source().is_some());
    let error = NslCompiler::new(&catalog, options(generous()))
        .compile(simple("let x = missing;"))
        .unwrap_err();
    assert_eq!(error.diagnostic().unwrap().code, "NSL-E2001");
    assert!(error.to_string().contains("missing"));
    assert!(error.source().is_some());
    let secret = NslCompiler::new(&catalog, options(generous()))
        .compile(simple("let x = \"Bearer example-token\";"))
        .unwrap_err();
    assert!(matches!(secret, CompileError::Codec(_)));
    assert!(secret.diagnostic().is_none());
}

#[test]
fn compiler_preserves_catalog_and_dependency_failures_and_can_be_reused() {
    use nsl_contract::{ToolContract, ToolContractCatalog, ToolContractOptions, ToolContractSpec};
    use nsl_core::DataClassification;
    let catalog = type_support::catalog();
    let memory = Memory(vec![]);
    let compiler = NslCompiler::new(&catalog, options(generous())).with_include_resolver(&memory);
    let error = compiler
        .compile(simple("include \"missing.ns\";"))
        .unwrap_err();
    assert!(matches!(error, CompileError::Include(_)));
    assert_eq!(error.diagnostic().unwrap().code, "NSL-E2309");
    assert!(error.source().is_some());
    assert!(error.to_string().contains("NSL-E2309"));
    // A failure must not retain counters, partial symbols or approval state.
    let good = simple("let x = 1;");
    let expected = compiler.compile(good.clone()).unwrap();
    assert_eq!(expected, compiler.compile(good).unwrap());
    for capability in ["CUSTOM", "TRANSACTION"] {
        let contract = ToolContract::new(ToolContractSpec {
            tool_id: "TEST.API".into(),
            version: "1.0.0".into(),
            capability: capability.into(),
            input_types: vec![],
            output_type: type_support::primitive("Int"),
            required_scope: "test:scope".into(),
            output_classification: DataClassification::Public,
            options: ToolContractOptions::default(),
        })
        .unwrap();
        let catalog = ToolContractCatalog::new([contract]).unwrap();
        let source = simple("requires {tool TEST.API version \"1.0.0\";}");
        let result = NslCompiler::new(&catalog, options(generous())).compile(source.clone());
        if capability == "CUSTOM" {
            let error = result.unwrap_err();
            assert!(matches!(error, CompileError::Codec(_)));
            assert!(error.diagnostic().is_none());
            assert!(error.source().is_some());
            assert!(error.to_string().contains("capability"));
        } else {
            result.unwrap();
        }
    }
    let contract = ToolContract::new(ToolContractSpec {
        tool_id: "TEST.API".into(),
        version: "1.0.0".into(),
        capability: "READ".into(),
        input_types: vec![],
        output_type: type_support::optional(type_support::primitive("Int")),
        required_scope: "test:scope".into(),
        output_classification: DataClassification::Public,
        options: ToolContractOptions::default(),
    })
    .unwrap();
    let catalog = ToolContractCatalog::new([contract]).unwrap();
    let error = NslCompiler::new(&catalog, options(generous()))
        .compile(simple(
            "requires {tool TEST.API version \"1.0.0\";} input {x: Int; x: Int;}",
        ))
        .unwrap_err();
    assert!(matches!(error, CompileError::Contract(_)));
    assert!(error.diagnostic().is_none());
    assert!(error.source().is_some());
    assert!(matches!(
        error,
        CompileError::Contract(nsl_contract::ContractEncodingError::Type(
            nsl_core::TypeError::InternalOptional
        ))
    ));
    assert!(!error.to_string().is_empty());
    let internal = CompileError::InvalidModel("invalid typed AST");
    assert_eq!(internal.to_string(), "invalid typed AST");
    assert!(internal.source().is_none());
    assert!(internal.diagnostic().is_none());
}

#[test]
fn output_preflight_rejects_wide_bodies_strings_paths_and_nested_types() {
    let catalog = type_support::catalog();
    for (body, limits, expected) in [
        (
            format!("let x = \"{}\";", "가".repeat(40)),
            NsoLimits {
                max_bytes: 100,
                ..generous()
            },
            NsoLimit::Bytes,
        ),
        (
            format!(
                "input {{ x: {}Int{}; }}",
                "List<".repeat(20),
                ">".repeat(20)
            ),
            NsoLimits {
                max_depth: 16,
                ..generous()
            },
            NsoLimit::Depth,
        ),
        (
            format!("let x = a{};", ".a".repeat(100)),
            NsoLimits {
                max_depth: 16,
                ..generous()
            },
            NsoLimit::Depth,
        ),
        (
            (0..100).map(|i| format!("let x{i} = 1;")).collect(),
            NsoLimits {
                max_nodes: 10,
                ..generous()
            },
            NsoLimit::Nodes,
        ),
    ] {
        let error = NslCompiler::new(&catalog, options(limits))
            .compile(simple(&body))
            .unwrap_err();
        assert_eq!(error.output_limit().unwrap().limit, expected);
        assert!(error.diagnostic().is_none());
        assert!(error.source().is_some());
    }
}

#[test]
fn public_diagnostics_redact_once_while_preserving_original_source_identity() {
    use nsl_compiler::includes::{IncludeDiagnosticKind, IncludeError};
    let catalog = type_support::catalog();
    let source = SourceFile::from_text("compiler/Bearer synthetic.ns",         "language NSL \"0.2\"; skill TEST {version \"1\"; risk READ_ONLY; limits {tool_calls 1;loop_iterations 1;emitted_rows 1;collection_size 1;} requires {tool MISSING version \"api_key=synthetic\";} }"
,None).unwrap();
    let original = source.clone();
    let compiler = NslCompiler::new(&catalog, options(generous()));
    let error = compiler.compile(source).unwrap_err();
    let CompileError::Type(nsl_compiler::types::TypeCheckError::Diagnostic(ref value)) = error
    else {
        panic!("contract diagnostic")
    };
    assert!(!value.message.contains("synthetic"));
    assert_eq!(
        value.logical_path.as_deref(),
        Some("compiler/Bearer [REDACTED]")
    );
    assert_eq!(value.span.source_id(), original.source_id());
    let diagnostic = error.diagnostic().unwrap();
    assert_eq!(diagnostic.code, "NSL-E4001");
    assert_eq!(diagnostic.phase, "SEMANTIC");
    assert_eq!(diagnostic.span, value.span);
    assert_eq!(diagnostic.message, value.message); // no second non-idempotent redaction
    assert_eq!(diagnostic.snippet, value.snippet);
    assert!(original.logical_path().contains("synthetic"));
    assert!(original.text().contains("api_key=synthetic"));

    let root=SourceFile::from_text("compiler/Bearer synthetic.ns",        "language NSL \"0.2\"; skill TEST {version \"1\";risk READ_ONLY;limits {tool_calls 1;loop_iterations 1;emitted_rows 1;collection_size 1;} include \"part.ns\";}"
,None).unwrap();
    let memory = Memory(vec![]);
    // Include adapters expose this fixed, codeless failure before the compiler
    // maps it to a source-aware NSL diagnostic. Provider details stay outside.
    assert_eq!(
        IncludeResolveError.to_string(),
        "include source could not be resolved"
    );
    assert!(IncludeResolveError.source().is_none());
    let error = NslCompiler::new(&catalog, options(generous()))
        .with_include_resolver(&memory)
        .compile(root.clone())
        .unwrap_err();
    let CompileError::Include(IncludeError::Diagnostic(ref value)) = error else {
        panic!("include resolution")
    };
    assert_eq!(value.kind, IncludeDiagnosticKind::ResolutionFailed);
    assert_eq!(value.logical_path, "compiler/Bearer [REDACTED]");
    assert_eq!(&value.source_id, root.source_id());
    assert_eq!(value.span.as_ref().unwrap().source_id(), root.source_id());
    assert_eq!(
        error.diagnostic().unwrap().logical_path.as_deref(),
        Some("compiler/Bearer [REDACTED]")
    );
}
