mod limits;
mod lower;

use nsl_contract::{ContractEncodingError, ToolContractCatalog};
use nsl_core::{Integer, PayloadHash, RiskLevel};
use nsl_ir::{
    BuildMetadata, BuildSource, ContextSpec, InputSpec, IrVersion, NsoCodec, NsoCodecError,
    NsoLimit, NsoLimits, OutputField, RequiredTool, RequiredToolKind, ResourceLimits, SkillObject,
    source_manifest_sha256,
};
use nsl_source::ast::Ast;
use nsl_source::{
    ParseError, ParseLimits, ParseMode, Parser, SourceFile, SourceId, SourceLanguageVersion,
    SourceSpan,
};
use std::fmt;

use crate::approvals::{ApprovalAnalysisError, ApprovalBindingAnalyzer};
use crate::bounds::{BoundError, StaticBoundAnalyzer};
use crate::diagnostics::SourceDiagnosticContext;
use crate::includes::{
    IncludeEdge, IncludeError, IncludeOptions, IncludeResolver, SourceBundleBuilder, build_source,
};
use crate::types::{StaticTypeChecker, TypeCheckError, ast_type};
use crate::write_safety::{WriteSafetyAnalysis, WriteSafetyAnalyzer, WriteSafetyError};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CompilerOptions {
    pub parse_limits: ParseLimits,
    pub include_options: IncludeOptions,
    pub nso_limits: NsoLimits,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CompilerOutputLimit {
    pub limit: NsoLimit,
    pub span: SourceSpan,
}
impl fmt::Display for CompilerOutputLimit {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "compiler output exceeds the {:?} limit", self.limit)
    }
}
impl std::error::Error for CompilerOutputLimit {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CompilerDiagnostic {
    pub code: &'static str,
    pub phase: &'static str,
    pub message: String,
    pub span: SourceSpan,
    pub logical_path: Option<String>,
    pub snippet: Option<String>,
}
impl fmt::Display for CompilerDiagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for CompilerDiagnostic {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CompileError {
    Parse(ParseError),
    Include(IncludeError),
    Approval(ApprovalAnalysisError),
    Type(TypeCheckError),
    Safety(WriteSafetyError),
    Bounds(BoundError),
    Contract(ContractEncodingError),
    Codec(NsoCodecError),
    Diagnostic(Box<CompilerDiagnostic>),
    OutputLimit(CompilerOutputLimit),
    InvalidModel(&'static str),
}
impl fmt::Display for CompileError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Parse(e) => e.fmt(f),
            Self::Include(e) => e.fmt(f),
            Self::Approval(e) => e.fmt(f),
            Self::Type(e) => e.fmt(f),
            Self::Safety(e) => e.fmt(f),
            Self::Bounds(e) => e.fmt(f),
            Self::Contract(e) => e.fmt(f),
            Self::Codec(e) => e.fmt(f),
            Self::Diagnostic(e) => e.fmt(f),
            Self::OutputLimit(e) => e.fmt(f),
            Self::InvalidModel(e) => f.write_str(e),
        }
    }
}
impl std::error::Error for CompileError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(match self {
            Self::Parse(e) => e,
            Self::Include(e) => e,
            Self::Approval(e) => e,
            Self::Type(e) => e,
            Self::Safety(e) => e,
            Self::Bounds(e) => e,
            Self::Contract(e) => e,
            Self::Codec(e) => e,
            Self::Diagnostic(e) => e,
            Self::OutputLimit(e) => e,
            Self::InvalidModel(_) => return None,
        })
    }
}

impl CompileError {
    /// Common semantic diagnostic view; typed resource/admission errors have no
    /// NSL code and retain their specific error variant instead.
    pub fn diagnostic(&self) -> Option<CompilerDiagnostic> {
        macro_rules! view {
            ($e:expr) => {{
                let e = $e;
                Some(CompilerDiagnostic {
                    code: e.kind.code(),
                    phase: "SEMANTIC",
                    message: e.message.clone(),
                    span: e.span.clone(),
                    logical_path: e.logical_path.clone(),
                    snippet: e.snippet.clone(),
                })
            }};
        }
        match self {
            Self::Diagnostic(e) => Some((**e).clone()),
            Self::Approval(ApprovalAnalysisError::Diagnostic(e)) => view!(e),
            Self::Type(TypeCheckError::Diagnostic(e)) => view!(e),
            Self::Type(TypeCheckError::Symbol(
                crate::symbols::SymbolResolutionError::Diagnostic(e),
            )) => {
                let rendered = e.to_string();
                Some(CompilerDiagnostic {
                    code: e.kind.code(),
                    phase: "SEMANTIC",
                    message: rendered
                        .split_once(": ")
                        .expect("symbol diagnostic prefix")
                        .1
                        .into(),
                    span: e.span.clone(),
                    logical_path: e.logical_path.clone(),
                    snippet: e.snippet.clone(),
                })
            }
            Self::Safety(WriteSafetyError::Diagnostic(e)) => view!(e),
            Self::Bounds(BoundError::Diagnostic(e)) => view!(e),
            Self::Include(IncludeError::Diagnostic(e)) => {
                e.span.as_ref().map(|span| CompilerDiagnostic {
                    code: e.kind.code(),
                    phase: "INCLUDE",
                    message: e.to_string(),
                    span: span.clone(),
                    logical_path: Some(e.logical_path.clone()),
                    snippet: None,
                })
            }
            _ => None,
        }
    }

    pub fn output_limit(&self) -> Option<&CompilerOutputLimit> {
        if let Self::OutputLimit(error) = self {
            Some(error)
        } else {
            None
        }
    }
}
macro_rules! conversion {
    ($ty:ty,$variant:ident) => {
        impl From<$ty> for CompileError {
            fn from(value: $ty) -> Self {
                Self::$variant(value)
            }
        }
    };
}
conversion!(ParseError, Parse);
conversion!(IncludeError, Include);
conversion!(ApprovalAnalysisError, Approval);
conversion!(TypeCheckError, Type);
conversion!(WriteSafetyError, Safety);
conversion!(BoundError, Bounds);
conversion!(ContractEncodingError, Contract);

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceManifestEntry {
    pub source_id: SourceId,
    pub logical_path: String,
    pub content_hash: PayloadHash,
    pub size_bytes: Integer,
    pub is_root: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CompilationResult {
    pub skill: SkillObject,
    pub nso_bytes: Vec<u8>,
    pub semantic_hash: PayloadHash,
    pub source_bundle_hash: PayloadHash,
    pub source_manifest: Vec<SourceManifestEntry>,
    pub include_edges: Vec<IncludeEdge>,
    pub write_safety: WriteSafetyAnalysis,
}

pub struct NslCompiler<'a> {
    catalog: &'a ToolContractCatalog,
    resolver: Option<&'a dyn IncludeResolver>,
    options: CompilerOptions,
}
impl<'a> NslCompiler<'a> {
    pub fn new(catalog: &'a ToolContractCatalog, options: CompilerOptions) -> Self {
        Self {
            catalog,
            resolver: None,
            options,
        }
    }
    pub fn with_include_resolver(mut self, resolver: &'a dyn IncludeResolver) -> Self {
        self.resolver = Some(resolver);
        self
    }

    pub fn compile(&self, source: SourceFile) -> Result<CompilationResult, CompileError> {
        let Ast::RootSkill(initial) =
            Parser::parse(&source, ParseMode::RootSkill, self.options.parse_limits)?
        else {
            unreachable!("explicit root mode")
        };
        let (ast, sources, build, include_edges) =
            if let Some(resolver) = self.resolver.filter(|_| !initial.includes.is_empty()) {
                let bundle = SourceBundleBuilder::new(
                    resolver,
                    self.options.include_options.clone(),
                    self.options.parse_limits,
                )
                .build(source)?;
                (
                    bundle.compose()?,
                    bundle.sources().cloned().collect::<Vec<_>>(),
                    bundle.build_metadata().clone(),
                    bundle.edges().to_vec(),
                )
            } else {
                let entries = vec![build_source(&source, true)];
                let build = BuildMetadata {
                    source_bundle_sha256: source_manifest_sha256(&entries)
                        .map_err(CompileError::Codec)?,
                    root_source: source.logical_path().into(),
                    sources: entries,
                };
                (*initial, vec![source], build, Vec::new())
            };
        let span = &ast.span;
        limits::ast(&ast, self.options.nso_limits)?;
        let diagnostics = SourceDiagnosticContext::new(&sources);
        let diagnostic = |code, phase, message: String, span: &SourceSpan| {
            let (logical_path, snippet) = diagnostics.location(span);
            CompileError::Diagnostic(Box::new(CompilerDiagnostic {
                code,
                phase,
                message,
                span: span.clone(),
                logical_path,
                snippet,
            }))
        };
        let approvals = ApprovalBindingAnalyzer::new(&sources).analyze(&ast)?;
        if let Some(include) = ast.includes.first() {
            return Err(diagnostic(
                "NSL-E2300",
                "SEMANTIC",
                "include declarations require Source composition before lowering".into(),
                &include.span,
            ));
        }
        let declared_limits = ast.limits.as_ref().ok_or_else(|| {
            diagnostic(
                "NSL-E2310",
                "INCLUDE",
                "composed skill must define exactly one limits block".into(),
                span,
            )
        })?;
        let v02 = ast.language_version == SourceLanguageVersion::V0_2;
        if !matches!(ast.risk.as_str(), "READ_ONLY" | "READ_VALIDATE")
            && !(v02 && ast.risk == "WRITE")
        {
            return Err(diagnostic(
                "NSL-E2004",
                "SEMANTIC",
                format!("unsupported risk profile: {}", ast.risk),
                &ast.risk_span,
            ));
        }
        let checker = StaticTypeChecker::new(&sources);
        let contracts = checker.required_contracts(&ast, self.catalog)?;
        let write_safety = WriteSafetyAnalyzer::new(&sources).analyze(&approvals, &contracts)?;
        // Hash admission precedes ordinary declarations, as in the Oracle.
        let hashes = ast
            .requires
            .iter()
            .map(|required| contracts[required.tool_id.as_str()].contract_hash())
            .collect::<Result<Vec<_>, _>>()?;
        let (symbols, typed) = checker.analyze(&ast, self.catalog)?;
        let body = lower::lower(&ast, &symbols, &typed, &contracts)?;
        let limits = ResourceLimits {
            tool_calls: declared_limits.tool_calls.clone(),
            loop_iterations: declared_limits.loop_iterations.clone(),
            emitted_rows: declared_limits.emitted_rows.clone(),
            collection_size: declared_limits.collection_size.clone(),
            duration_ms: declared_limits.duration_ms.clone(),
        };
        let analysis = StaticBoundAnalyzer::new(&sources).check(
            &body,
            &limits,
            span,
            &declared_limits.span,
        )?;
        let required_tools = ast
            .requires
            .iter()
            .zip(hashes)
            .enumerate()
            .map(|(index, (required, hash))| {
                let contract = contracts[required.tool_id.as_str()];
                let kind = if v02 {
                    RequiredToolKind::V1_1 {
                        capability: contract.capability().known().ok_or(CompileError::Codec(
                            NsoCodecError::InvalidModelField("capability"),
                        ))?,
                        risk_level: contract.risk_level().unwrap_or(RiskLevel::R1),
                    }
                } else {
                    RequiredToolKind::ReadV1_0
                };
                Ok(RequiredTool {
                    tool_ref: format!("tool{:04}", index + 1),
                    tool_id: required.tool_id.clone(),
                    version: required.version.clone(),
                    kind,
                    contract_hash: hash,
                    required_scope: contract.required_scope().into(),
                    output_classification: contract.output_classification(),
                })
            })
            .collect::<Result<Vec<_>, CompileError>>()?;
        let binding_ids = symbols
            .bindings()
            .iter()
            .map(|binding| (binding.declaration, binding.id.to_string()))
            .collect::<std::collections::HashMap<_, _>>();
        let binding_id = |declaration| binding_ids[&declaration].clone();
        let mut skill = SkillObject {
            ir_version: if v02 {
                IrVersion::V1_1
            } else {
                IrVersion::V1_0
            },
            language_version: if v02 { "0.2" } else { "0.1" }.into(),
            skill_id: ast.skill_id.clone(),
            skill_version: ast.skill_version.clone(),
            risk: ast.risk.clone(),
            semantics_profile: if v02 {
                "NSL-0.2-STRICT"
            } else {
                "NSL-0.1-STRICT"
            }
            .into(),
            semantic_hash: PayloadHash::from_digest([0; 32]),
            features: ["READ", "FOREACH", "LET", "CHECK", "EMIT", "LIMITS"]
                .into_iter()
                .chain(if v02 {
                    vec!["APPROVAL", "WRITE"]
                } else {
                    vec![]
                })
                .map(String::from)
                .collect(),
            symbols: symbols
                .bindings()
                .iter()
                .map(|binding| {
                    let value = typed.symbol(binding.id).expect("typed symbol");
                    nsl_ir::SymbolSpec {
                        symbol_id: binding.id.to_string(),
                        name: binding.name.clone(),
                        category: binding.declaration.category().into(),
                        type_info: value.type_info.clone(),
                        classification: value.classification,
                    }
                })
                .collect(),
            required_tools,
            limits,
            inputs: ast
                .inputs
                .iter()
                .enumerate()
                .map(|(index, field)| InputSpec {
                    symbol_id: binding_id(crate::symbols::SymbolDeclaration::Input(index)),
                    name: field.name.clone(),
                    type_info: ast_type(&field.type_info),
                    required: true,
                    classification: field.classification,
                })
                .collect(),
            contexts: ast
                .contexts
                .iter()
                .enumerate()
                .map(|(index, field)| ContextSpec {
                    symbol_id: binding_id(crate::symbols::SymbolDeclaration::Context(index)),
                    name: field.name.clone(),
                    type_info: ast_type(&field.type_info),
                    path: field.path.clone(),
                    classification: field.classification,
                })
                .collect(),
            outputs: ast
                .outputs
                .iter()
                .map(|field| OutputField {
                    name: field.name.clone(),
                    type_info: ast_type(&field.type_info),
                    classification: field.classification,
                })
                .collect(),
            body,
            analysis,
            build,
        };
        let codec_error = |error| limits::codec(error, span);
        skill.semantic_hash = skill
            .compute_semantic_sha256_with_limits(self.options.nso_limits)
            .map_err(codec_error)?;
        let nso_bytes =
            NsoCodec::encode_with_limits(&skill, self.options.nso_limits).map_err(codec_error)?;
        let source_manifest = sources
            .iter()
            .zip(&skill.build.sources)
            .map(|(source, entry)| manifest(source, entry))
            .collect();
        Ok(CompilationResult {
            semantic_hash: skill.semantic_hash.clone(),
            source_bundle_hash: skill.build.source_bundle_sha256.clone(),
            source_manifest,
            include_edges,
            write_safety,
            skill,
            nso_bytes,
        })
    }
}

fn manifest(source: &SourceFile, entry: &BuildSource) -> SourceManifestEntry {
    SourceManifestEntry {
        source_id: source.source_id().clone(),
        logical_path: entry.logical_path.clone(),
        content_hash: entry.content_hash.clone(),
        size_bytes: entry.size_bytes.clone(),
        is_root: entry.is_root,
    }
}
