use std::{cell::Cell, fmt::Write};

use nsl_compiler::includes::{
    IncludeDiagnosticKind, IncludeError, IncludeOptions, IncludeResolveError, IncludeResolver,
    SourceBundleBuilder,
};
use nsl_source::{ParseLimits, SourceFile};

fn root(includes: &str) -> SourceFile {
    SourceFile::from_text(
        "skills/root.ns",
        &format!(
            "language NSL \"0.2\"; skill T {{ version \"1\"; risk READ_ONLY; \
             limits {{ tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1; }}\n{includes}}}"
        ),
        None,
    ).unwrap()
}

struct Resolve<F>(F);

impl<F: Fn(&str) -> SourceFile> IncludeResolver for Resolve<F> {
    fn resolve(&self, _: &SourceFile, path: &str) -> Result<SourceFile, IncludeResolveError> {
        Ok((self.0)(path))
    }
}

fn builder(resolver: &dyn IncludeResolver, options: IncludeOptions) -> SourceBundleBuilder<'_> {
    SourceBundleBuilder::new(resolver, options, ParseLimits { max_nesting: 64 })
}

fn expect_limit(
    error: IncludeError,
    kind: IncludeDiagnosticKind,
    includer: &SourceFile,
    declaration: &str,
) {
    let IncludeError::Diagnostic(error) = error else {
        panic!("expected a resource limit diagnostic");
    };
    let start = includer.text().rfind(declaration).unwrap();
    assert_eq!(error.kind, kind);
    assert_eq!(error.logical_path, includer.logical_path());
    assert_eq!(
        error.span,
        Some(
            includer
                .span_at_bytes(start..start + declaration.len())
                .unwrap()
        )
    );
}

#[test]
fn default_file_limit_counts_unique_sources_and_keeps_repeat_edges() {
    let options = IncludeOptions::default();
    let cap = options.max_include_files;
    assert_eq!(cap, 100);
    for count in [cap - 1, cap, cap + 1] {
        let mut includes = String::new();
        for index in 0..count {
            writeln!(
                includes,
                "include \"{index}.ns\"; include \"./sub/../{index}.ns\";"
            )
            .unwrap();
        }
        let attempts = Cell::new(0);
        let resolver = Resolve(|path: &str| {
            let attempt = attempts.get();
            assert_eq!(path, format!("skills/{}.ns", attempt / 2));
            attempts.set(attempt + 1);
            SourceFile::from_text(path, "", None).unwrap()
        });
        let source = root(&includes);
        let result = builder(&resolver, options.clone()).build(source.clone());
        if count <= cap {
            let bundle = result.unwrap();
            assert_eq!(bundle.fragments().len(), count);
            assert_eq!(bundle.edges().len(), count * 2);
            assert_eq!(bundle.manifest().count(), count + 1);
            assert_eq!(attempts.get(), count * 2);
        } else {
            expect_limit(
                result.unwrap_err(),
                IncludeDiagnosticKind::FileLimit,
                &source,
                &format!("include \"{cap}.ns\";"),
            );
            assert_eq!(attempts.get(), cap * 2 + 1);
        }
    }
}

#[test]
fn default_utf8_budget_counts_root_and_unique_sources_once() {
    let options = IncludeOptions::default();
    let cap = options.max_total_source_bytes;
    assert_eq!(cap, 10 * 1024 * 1024);
    let source = root("include \"small.ns\"; include \"./small.ns\"; include \"large.ns\";");
    let small = "// 예산😀\n";
    for total in [cap - 1, cap, cap + 1] {
        let needed = total - source.text().len() - small.len();
        let mut text = String::from("//");
        text.push_str(&"🦊".repeat((needed - 2) / 4));
        text.push_str(&" ".repeat(needed - text.len()));
        assert_eq!(text.len(), needed);
        let resolver = Resolve(|path: &str| {
            let content = match path {
                "skills/small.ns" => small,
                "skills/large.ns" => &text,
                _ => panic!("unexpected canonical path"),
            };
            SourceFile::from_text(path, content, None).unwrap()
        });
        let result = builder(&resolver, options.clone()).build(source.clone());
        if total <= cap {
            let bundle = result.unwrap();
            assert_eq!(
                bundle
                    .sources()
                    .map(|item| item.text().len())
                    .sum::<usize>(),
                total
            );
            assert_eq!(bundle.manifest().count(), 3);
            assert_eq!(bundle.edges().len(), 3);
        } else {
            expect_limit(
                result.unwrap_err(),
                IncludeDiagnosticKind::BundleSizeLimit,
                &source,
                "include \"large.ns\";",
            );
        }
    }
}

#[test]
fn depth_limit_precedes_resolution_even_for_a_previously_visited_target() {
    let mut options = IncludeOptions::default();
    let depth = options.max_include_depth;
    assert_eq!(depth, 16);
    let attempts = Cell::new(0);
    const SHARED: &str = "skills/shared.ns";
    const LAST_DECLARATION: &str = "include \"../shared.ns\";";
    let resolver = Resolve(|path: &str| {
        attempts.set(attempts.get() + 1);
        let text = if path == SHARED {
            String::new()
        } else {
            let index: usize = path
                .strip_prefix("skills/chain/")
                .unwrap()
                .strip_suffix(".ns")
                .unwrap()
                .parse()
                .unwrap();
            if index == depth {
                LAST_DECLARATION.into()
            } else {
                format!("include \"{}.ns\";", index + 1)
            }
        };
        SourceFile::from_text(path, &text, None).unwrap()
    });
    let source = root("include \"shared.ns\"; include \"chain/1.ns\";");
    let error = builder(&resolver, options.clone())
        .build(source.clone())
        .unwrap_err();
    let last =
        SourceFile::from_text(&format!("skills/chain/{depth}.ns"), LAST_DECLARATION, None).unwrap();
    expect_limit(
        error,
        IncludeDiagnosticKind::DepthLimit,
        &last,
        LAST_DECLARATION,
    );
    assert_eq!(attempts.replace(0), depth + 1);

    options.max_include_depth += 1;
    let bundle = builder(&resolver, options).build(source).unwrap();
    assert_eq!(bundle.fragments().len(), depth + 1);
    assert_eq!(bundle.edges().len(), depth + 2);
    assert_eq!(attempts.get(), depth + 2);
}
