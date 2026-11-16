use std::{cell::RefCell, collections::VecDeque, error::Error};

use nsl_compiler::includes::{
    IncludeDiagnosticKind, IncludeError, IncludeIdentityConflictKind, IncludeOptions,
    IncludeResolveError, IncludeResolver, SourceBundleBuilder, canonical_include_path,
};
use nsl_source::{ParseError, ParseLimits, SourceFile};

const PREFIX: &str = "language NSL \"0.2\"; skill TEST { version \"1\"; risk READ_ONLY; limits { tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1; }\n";
const PARSE_LIMITS: ParseLimits = ParseLimits { max_nesting: 128 };

fn source(path: &str, text: &str, id: Option<&str>) -> SourceFile {
    SourceFile::from_text(path, text, id).unwrap()
}

fn root(path: &str, declarations: &str) -> SourceFile {
    source(path, &format!("{PREFIX}{declarations}\n}}"), None)
}

struct Scripted {
    replies: RefCell<VecDeque<(&'static str, SourceFile)>>,
}

impl Scripted {
    fn new(replies: impl Into<VecDeque<(&'static str, SourceFile)>>) -> Self {
        Self {
            replies: RefCell::new(replies.into()),
        }
    }
}

impl IncludeResolver for Scripted {
    fn resolve(&self, _: &SourceFile, path: &str) -> Result<SourceFile, IncludeResolveError> {
        let (expected, source) = self
            .replies
            .borrow_mut()
            .pop_front()
            .expect("unexpected resolver call");
        assert_eq!(path, expected);
        Ok(source)
    }
}

#[test]
fn only_relative_posix_paths_inside_the_configured_root_are_admitted() {
    let resolver = Scripted::new([]);
    for (path, scope) in [
        ("/skills/root.ns", "."),
        ("//server/root.ns", "."),
        ("C:/skills/root.ns", "."),
        ("C:root.ns", "."),
        ("skills\\root.ns", "."),
        ("skills/.././C:root.ns", "."),
        ("../root.ns", "."),
        ("skills/\0root.ns", "."),
        ("skills/root.ns", "/skills"),
        ("skills/root.ns", "C:/skills"),
        ("skills/root.ns", ""),
        ("skills/root.ns", "skills/../.."),
    ] {
        let root = root(path, "include \"base.ns\";");
        let options = IncludeOptions {
            include_root: scope.into(),
            ..IncludeOptions::default()
        };
        let error = SourceBundleBuilder::new(&resolver, options, PARSE_LIMITS)
            .build(root)
            .unwrap_err();
        let IncludeError::Diagnostic(error) = error else {
            panic!("expected path diagnostic");
        };
        assert_eq!(
            error.kind,
            IncludeDiagnosticKind::PathOutsideRoot,
            "{path} {scope}"
        );
        assert_eq!(error.logical_path, path);
        assert!(error.span.is_none());
    }
    assert_eq!(
        canonical_include_path("./root.ns", "./base.ns", ".").unwrap(),
        "base.ns"
    );
    assert_eq!(
        canonical_include_path("skills/app/root.ns", "./../공통.ns", "skills/").unwrap(),
        "skills/공통.ns"
    );
    assert_eq!(
        canonical_include_path("1:root.ns", "2:fragment.ns", ".").unwrap(),
        "2:fragment.ns"
    );
    assert!(canonical_include_path("skills/root.ns", "./../skills/base.ns", "./skills/").is_err());
    let error = canonical_include_path("root.txt", "base.ns", ".").unwrap_err();
    assert_eq!(
        error.to_string(),
        "expected a relative POSIX path within the include root"
    );
}

#[test]
fn mismatched_source_identity_is_rejected_at_the_second_declaration() {
    const PATH: &str = "skills/part.ns";
    const OTHER: &str = "skills/other.ns";
    let original = source(PATH, "// 첫 내용", Some("id:first"));
    for (declaration, expected_path, next, kind) in [
        (
            "include \"./part.ns\";",
            PATH,
            source(PATH, original.text(), Some("id:second")),
            IncludeIdentityConflictKind::PathRebound,
        ),
        (
            "include \"other.ns\";",
            OTHER,
            source(OTHER, original.text(), Some("id:first")),
            IncludeIdentityConflictKind::SourceIdRebound,
        ),
        (
            "include \"./part.ns\";",
            PATH,
            source(PATH, "// 바뀐 내용", Some("id:first")),
            IncludeIdentityConflictKind::ContentChanged,
        ),
    ] {
        let resolver = Scripted::new([(PATH, original.clone()), (expected_path, next)]);
        let root = root(
            "skills/root.ns",
            &format!("include \"part.ns\";\n{declaration}"),
        );
        let start = root.text().find(declaration).unwrap();
        let expected_span = root
            .span_at_bytes(start..start + declaration.len())
            .unwrap();
        let options = IncludeOptions {
            max_include_files: 1,
            ..IncludeOptions::default()
        };
        let result = SourceBundleBuilder::new(&resolver, options, PARSE_LIMITS).build(root);
        let error = result.unwrap_err();
        let IncludeError::IdentityConflict(ref conflict) = error else {
            panic!("expected identity conflict: {error:?}");
        };
        assert_eq!(conflict.kind, kind);
        assert_eq!(conflict.span, expected_span);
        assert!(!error.to_string().contains("NSL-E"));
        assert_eq!(error.source().unwrap().to_string(), error.to_string());
        assert!(resolver.replies.borrow().is_empty());
    }
}

#[test]
fn canonical_aliases_of_the_active_root_are_cycles() {
    let root = root("skills/./root.ns", "include \"./root.ns\";");
    let response = source(
        "skills/root.ns",
        root.text(),
        Some(root.source_id().as_str()),
    );
    let resolver = Scripted::new([("skills/root.ns", response)]);
    let error = SourceBundleBuilder::new(&resolver, IncludeOptions::default(), PARSE_LIMITS)
        .build(root)
        .unwrap_err();
    assert!(
        matches!(error, IncludeError::Diagnostic(error) if error.kind == IncludeDiagnosticKind::Cycle)
    );
}

#[test]
fn parser_nesting_errors_preserve_the_included_source() {
    let root = root("skills/root.ns", "include \"part.ns\";");
    let fragment = source(
        "skills/part.ns",
        "context { x: List<Int> from \"items\"; }",
        None,
    );
    let start = fragment.text().find("List").unwrap();
    let expected_span = fragment.span_at_bytes(start..start + "List".len()).unwrap();
    let resolver = Scripted::new([("skills/part.ns", fragment)]);
    let error = SourceBundleBuilder::new(
        &resolver,
        IncludeOptions::default(),
        ParseLimits { max_nesting: 0 },
    )
    .build(root)
    .unwrap_err();
    assert!(
        matches!(error, IncludeError::Parse { logical_path, error: ParseError::Nesting(error) }
        if logical_path == "skills/part.ns" && error.span == expected_span)
    );
}

#[test]
fn large_caller_selected_include_depth_does_not_require_a_large_stack() {
    struct Chain(usize);
    impl IncludeResolver for Chain {
        fn resolve(&self, _: &SourceFile, path: &str) -> Result<SourceFile, IncludeResolveError> {
            let index: usize = path
                .strip_prefix("chain/")
                .unwrap()
                .strip_suffix(".ns")
                .unwrap()
                .parse()
                .unwrap();
            let text = if index < self.0 {
                format!("include \"{}.ns\";", index + 1)
            } else {
                String::new()
            };
            Ok(source(path, &text, None))
        }
    }

    std::thread::Builder::new()
        .stack_size(128 * 1024)
        .spawn(|| {
            let depth = 2_048;
            let resolver = Chain(depth);
            let options = IncludeOptions {
                max_include_depth: depth,
                max_include_files: depth,
                ..IncludeOptions::default()
            };
            let root = root("chain/root.ns", "include \"1.ns\";");
            let bundle = SourceBundleBuilder::new(&resolver, options, PARSE_LIMITS)
                .build(root)
                .unwrap();
            assert_eq!(bundle.fragments().len(), depth);
            assert_eq!(bundle.edges().len(), depth);
            assert_eq!(bundle.manifest().count(), depth + 1);
            assert!(bundle.compose().unwrap().includes.is_empty());
        })
        .unwrap()
        .join()
        .unwrap();
}
