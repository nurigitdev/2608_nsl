//! Resolve source bundles and compose their parsed declarations.

mod compose;
mod error;
mod path;

use std::collections::{HashMap, HashSet};

use nsl_core::PayloadHash;
use nsl_ir::{BuildMetadata, BuildSource, source_manifest_sha256};
use nsl_source::ast::{Ast, AstIncludeDeclaration, AstIncludeFragment, AstSkill};
use nsl_source::{ParseLimits, ParseMode, Parser, SourceFile, SourceId, SourceSpan};
use sha2::{Digest, Sha256};

pub use error::{
    IncludeDiagnostic, IncludeDiagnosticKind, IncludeError, IncludeIdentityConflict,
    IncludeIdentityConflictKind, IncludeResolveError,
};
pub use path::{InvalidIncludePath, canonical_include_path};

use error::diagnostic;
use path::{normalized_parts, source_parts, target_path};

/// A resolver receives the validated canonical logical path for the current include.
pub trait IncludeResolver {
    fn resolve(
        &self,
        including_source: &SourceFile,
        canonical_path: &str,
    ) -> Result<SourceFile, IncludeResolveError>;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IncludeOptions {
    pub include_root: String,
    pub max_include_depth: usize,
    pub max_include_files: usize,
    pub max_total_source_bytes: usize,
}

impl Default for IncludeOptions {
    fn default() -> Self {
        Self {
            include_root: ".".into(),
            max_include_depth: 16,
            max_include_files: 100,
            max_total_source_bytes: 10 * 1024 * 1024,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ParsedSource<T> {
    pub source: SourceFile,
    pub ast: T,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IncludeEdge {
    pub from_source: SourceId,
    pub to_source: SourceId,
    pub include_span: SourceSpan,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceBundle {
    root: ParsedSource<Box<AstSkill>>,
    fragments: Vec<ParsedSource<AstIncludeFragment>>,
    edges: Vec<IncludeEdge>,
    build: BuildMetadata,
}

impl SourceBundle {
    pub fn root(&self) -> &ParsedSource<Box<AstSkill>> {
        &self.root
    }

    pub fn fragments(&self) -> &[ParsedSource<AstIncludeFragment>] {
        &self.fragments
    }

    pub fn edges(&self) -> &[IncludeEdge] {
        &self.edges
    }

    pub fn sources(&self) -> impl Iterator<Item = &SourceFile> {
        std::iter::once(&self.root.source).chain(self.fragments.iter().map(|unit| &unit.source))
    }

    /// Manifest entries with their source IDs, in root-first depth-first order.
    pub fn manifest(&self) -> impl Iterator<Item = (&SourceId, &BuildSource)> {
        self.sources()
            .map(SourceFile::source_id)
            .zip(&self.build.sources)
    }

    pub fn build_metadata(&self) -> &BuildMetadata {
        &self.build
    }
}

pub struct SourceBundleBuilder<'a> {
    resolver: &'a dyn IncludeResolver,
    options: IncludeOptions,
    parse_limits: ParseLimits,
}

#[derive(Clone, Copy, Eq, Hash, PartialEq)]
enum Unit {
    Root,
    Fragment(usize),
}

struct Visit {
    unit: Unit,
    next_include: usize,
}

impl<'a> SourceBundleBuilder<'a> {
    pub fn new(
        resolver: &'a dyn IncludeResolver,
        options: IncludeOptions,
        parse_limits: ParseLimits,
    ) -> Self {
        Self {
            resolver,
            options,
            parse_limits,
        }
    }

    pub fn build(&self, root_source: SourceFile) -> Result<SourceBundle, IncludeError> {
        use IncludeDiagnosticKind::*;

        let mut total_bytes = root_source.text().len();
        if total_bytes > self.options.max_total_source_bytes {
            return Err(diagnostic(BundleSizeLimit, &root_source, None));
        }
        let root_path = source_parts(root_source.logical_path())
            .map_err(|_| diagnostic(PathOutsideRoot, &root_source, None))?
            .join("/");
        let scope = normalized_parts(&self.options.include_root)
            .map_err(|_| diagnostic(PathOutsideRoot, &root_source, None))?;
        let Ast::RootSkill(root_ast) = self.parse(&root_source, ParseMode::RootSkill)? else {
            unreachable!("root parse mode returns a skill");
        };
        let fragment_mode = ParseMode::IncludeFragment {
            language_version: root_ast.language_version,
        };
        let root = ParsedSource {
            source: root_source,
            ast: root_ast,
        };
        let mut fragments = Vec::new();
        let mut edges = Vec::new();
        let mut by_path = HashMap::from([(root_path, Unit::Root)]);
        let mut known_ids = HashSet::from([root.source.source_id().clone()]);
        let mut active = HashSet::from([Unit::Root]);
        let mut stack = vec![Visit {
            unit: Unit::Root,
            next_include: 0,
        }];

        while let Some(visit) = stack.last_mut() {
            let (including, declarations) = source_and_includes(&root, &fragments, visit.unit);
            let Some(declaration) = declarations.get(visit.next_include) else {
                active.remove(&visit.unit);
                stack.pop();
                continue;
            };
            visit.next_include += 1;
            let error = |kind| diagnostic(kind, including, Some(&declaration.span));
            if stack.len() > self.options.max_include_depth {
                return Err(error(DepthLimit));
            }
            // The root was validated at admission and all fragments have a canonical path.
            let mut parent = source_parts(including.logical_path()).expect("validated source path");
            parent.pop();
            let target = target_path(parent, &declaration.path, &scope)
                .map_err(|_| error(PathOutsideRoot))?;
            let source = self
                .resolver
                .resolve(including, &target)
                .map_err(|_| error(ResolutionFailed))?;
            if source.logical_path() != target {
                return Err(error(PathOutsideRoot));
            }

            let conflict = |kind| {
                IncludeError::IdentityConflict(IncludeIdentityConflict {
                    kind,
                    span: declaration.span.clone(),
                })
            };
            let known = by_path.get(&target).copied();
            if let Some(unit) = known {
                let (previous, _) = source_and_includes(&root, &fragments, unit);
                if previous.source_id() != source.source_id() {
                    return Err(conflict(IncludeIdentityConflictKind::PathRebound));
                }
                if previous.text() != source.text() {
                    return Err(conflict(IncludeIdentityConflictKind::ContentChanged));
                }
                if active.contains(&unit) {
                    return Err(error(Cycle));
                }
            } else if known_ids.contains(source.source_id()) {
                return Err(conflict(IncludeIdentityConflictKind::SourceIdRebound));
            }

            edges.push(IncludeEdge {
                from_source: including.source_id().clone(),
                to_source: source.source_id().clone(),
                include_span: declaration.span.clone(),
            });
            if known.is_some() {
                continue;
            }
            if fragments.len() >= self.options.max_include_files {
                return Err(error(FileLimit));
            }
            let bytes = source.text().len();
            if bytes > self.options.max_total_source_bytes - total_bytes {
                return Err(error(BundleSizeLimit));
            }
            total_bytes += bytes;
            let Ast::IncludeFragment(ast) = self.parse(&source, fragment_mode)? else {
                unreachable!("fragment parse mode returns a fragment");
            };
            let unit = Unit::Fragment(fragments.len());
            by_path.insert(target, unit);
            known_ids.insert(source.source_id().clone());
            active.insert(unit);
            fragments.push(ParsedSource { source, ast });
            stack.push(Visit {
                unit,
                next_include: 0,
            });
        }

        let sources: Vec<_> = std::iter::once(build_source(&root.source, true))
            .chain(
                fragments
                    .iter()
                    .map(|unit| build_source(&unit.source, false)),
            )
            .collect();
        let build = BuildMetadata {
            source_bundle_sha256: source_manifest_sha256(&sources)
                .map_err(IncludeError::Manifest)?,
            root_source: root.source.logical_path().to_owned(),
            sources,
        };
        Ok(SourceBundle {
            root,
            fragments,
            edges,
            build,
        })
    }

    fn parse(&self, source: &SourceFile, mode: ParseMode) -> Result<Ast, IncludeError> {
        Parser::parse(source, mode, self.parse_limits).map_err(|error| IncludeError::Parse {
            logical_path: nsl_core::redact_text(source.logical_path()),
            error,
        })
    }
}

fn source_and_includes<'a>(
    root: &'a ParsedSource<Box<AstSkill>>,
    fragments: &'a [ParsedSource<AstIncludeFragment>],
    unit: Unit,
) -> (&'a SourceFile, &'a [AstIncludeDeclaration]) {
    match unit {
        Unit::Root => (&root.source, &root.ast.includes),
        Unit::Fragment(index) => {
            let fragment = &fragments[index];
            (&fragment.source, &fragment.ast.includes)
        }
    }
}

pub(crate) fn build_source(source: &SourceFile, is_root: bool) -> BuildSource {
    let bytes = source.text().as_bytes();
    BuildSource {
        logical_path: source.logical_path().to_owned(),
        content_hash: PayloadHash::from_digest(Sha256::digest(bytes).into()),
        size_bytes: bytes
            .len()
            .to_string()
            .parse()
            .expect("canonical source size"),
        is_root,
    }
}
