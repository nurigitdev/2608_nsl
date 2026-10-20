from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from .bounds import StaticBoundAnalyzer, UnboundedStructureError
from .core import (
    CHECK_RESULT,
    DataClassification,
    RiskLevel,
    ToolCapability,
    TypeRef,
    classification_allows,
)
from .diagnostics import CompileError, DiagnosticCode, DiagnosticPhase
from .ir import (
    ApprovalPolicy,
    ApprovalStatement,
    BinaryExpr,
    BuildMetadata,
    BuildSource,
    CallExpr,
    CheckStatement,
    ContextSpec,
    EmitStatement,
    FieldExpr,
    ForeachStatement,
    InputSpec,
    LetStatement,
    LiteralExpr,
    NsoCodec,
    OutputField,
    ProjectionExpr,
    ReadExpr,
    RequiredTool,
    ResourceLimits,
    ResultPolicy,
    SkillObject,
    Statement,
    SymbolRefExpr,
    SymbolSpec,
    WriteExpression,
)
from .includes import (
    IncludeEdge,
    IncludeOptions,
    IncludeResolver,
    SourceBundleBuilder,
    SourceComposer,
    SourceManifestEntry,
    manifest_entry,
)
from .integrity import source_manifest_sha256
from .semantic_diagnostics import SourceDiagnosticContext
from .source import SourceFile, SourceSpan, coerce_source
from .symbols import ScopeKind, SymbolBinding, SymbolNamespace, SymbolTable
from .syntax import (
    AstBinary,
    AstApprove,
    AstCall,
    AstCheck,
    AstEmit,
    AstExpression,
    AstForeach,
    AstLet,
    AstLiteral,
    AstNode,
    AstPath,
    AstRead,
    AstSkill,
    AstStatement,
    AstWrite,
    Lexer,
    Parser,
)
from .tools import (
    Atomicity,
    IdempotencySemantics,
    IncompatibleToolVersionError,
    OperationKind,
    ToolContract,
    ToolContractCatalog,
    UnknownToolContractError,
    WriteSemantics,
)
from .type_system import StaticTypeChecker


@dataclass(frozen=True, slots=True)
class CompilationResult:
    skill: SkillObject
    nso_bytes: bytes
    semantic_hash: str
    source_bundle_hash: str
    source_manifest: tuple[SourceManifestEntry, ...]
    include_edges: tuple[IncludeEdge, ...]
    write_safety: WriteSafetyAnalysis


@dataclass(frozen=True, slots=True)
class ApprovalSymbol:
    approval_id: str
    symbol_id: str
    declaration_span: SourceSpan | None
    declaration_order: int


@dataclass(frozen=True, slots=True)
class ApprovalWriteBinding:
    approval: ApprovalSymbol
    write: AstWrite
    write_order: int
    inside_foreach: bool


@dataclass(frozen=True, slots=True)
class ApprovalBindingAnalysis:
    approvals: tuple[ApprovalSymbol, ...]
    bindings: tuple[ApprovalWriteBinding, ...]


class ApprovalBindingAnalyzer:
    _APPROVAL_TYPE = TypeRef(kind="domain", name="Approval")

    def __init__(self, sources: tuple[SourceFile, ...]) -> None:
        self._symbols = SymbolTable(sources)
        self._diagnostics = SourceDiagnosticContext(sources)

    def analyze(self, ast: AstSkill) -> ApprovalBindingAnalysis:
        if ast.language_version != "0.2":
            return ApprovalBindingAnalysis((), ())

        events = tuple(self._walk(ast.body, inside_foreach=False))
        approvals: dict[str, ApprovalSymbol] = {}
        writes: list[tuple[int, AstWrite, bool]] = []
        for order, (node, inside_foreach) in enumerate(events, start=1):
            if isinstance(node, AstApprove):
                if inside_foreach:
                    raise self._diagnostics.error(
                        DiagnosticCode.SEM_APPROVAL_SCOPE,
                        "approval declarations must be in skill scope",
                        node.approval_id_span or node.span,
                    )
                if node.approval_id in approvals:
                    raise self._diagnostics.error(
                        DiagnosticCode.SEM_DUPLICATE_SYMBOL,
                        f"duplicate approval ID: {node.approval_id}",
                        node.approval_id_span or node.span,
                    )
                binding = self._symbols.declare(
                    node.approval_id,
                    SymbolNamespace.APPROVAL,
                    "APPROVAL",
                    self._APPROVAL_TYPE,
                    DataClassification.INTERNAL,
                    node.approval_id_span or node.span,
                )
                approvals[node.approval_id] = ApprovalSymbol(
                    node.approval_id,
                    binding.symbol_id,
                    node.approval_id_span or node.span,
                    order,
                )
            else:
                writes.append((order, node, inside_foreach))

        linked_ids: set[str] = set()
        bindings: list[ApprovalWriteBinding] = []
        for order, write, inside_foreach in writes:
            self._symbols.resolve_in_namespace(
                write.approval_id,
                SymbolNamespace.APPROVAL,
                write.approval_span or write.span,
                code=DiagnosticCode.SEM_UNKNOWN_APPROVAL,
                message=f"unknown approval ID: {write.approval_id}",
            )
            approval = approvals[write.approval_id]
            if approval.declaration_order >= order:
                raise self._diagnostics.error(
                    DiagnosticCode.SEM_APPROVAL_ORDER,
                    "approval must be declared before WRITE: "
                    f"{write.approval_id}",
                    write.approval_span or write.span,
                )
            if write.approval_id in linked_ids:
                raise self._diagnostics.error(
                    DiagnosticCode.SEM_APPROVAL_WRITE_NOT_ONE_TO_ONE,
                    "approval must be linked to exactly one WRITE: "
                    f"{write.approval_id}",
                    write.approval_span or write.span,
                )
            linked_ids.add(write.approval_id)
            bindings.append(
                ApprovalWriteBinding(approval, write, order, inside_foreach)
            )

        for approval_id, approval in approvals.items():
            if approval_id not in linked_ids:
                raise self._diagnostics.error(
                    DiagnosticCode.SEM_UNUSED_APPROVAL,
                    f"approval is not linked to a WRITE: {approval_id}",
                    approval.declaration_span,
                )
        return ApprovalBindingAnalysis(tuple(approvals.values()), tuple(bindings))

    @classmethod
    def _walk(
        cls, value: object, inside_foreach: bool
    ) -> tuple[tuple[AstApprove | AstWrite, bool], ...]:
        if isinstance(value, tuple):
            return tuple(
                event
                for item in value
                for event in cls._walk(item, inside_foreach)
            )
        if not isinstance(value, AstNode):
            return ()

        events: list[tuple[AstApprove | AstWrite, bool]] = []
        if isinstance(value, (AstApprove, AstWrite)):
            events.append((value, inside_foreach))
        for item in fields(value):
            if item.name.endswith("span"):
                continue
            child_inside = inside_foreach or (
                isinstance(value, AstForeach) and item.name == "body"
            )
            events.extend(cls._walk(getattr(value, item.name), child_inside))
        return tuple(events)


@dataclass(frozen=True, slots=True)
class WriteSafetyAnalysis:
    write_node_count: int
    approval_node_count: int
    approval_write_mapping_count: int
    unused_approval_count: int
    max_write_calls: int
    writes_inside_loop: bool
    all_writes_approved_exactly_once: bool
    all_write_operations_supported: bool
    all_write_tools_atomic: bool
    all_write_tools_idempotent: bool
    all_write_risk_metadata_present: bool
    requires_verified_package_for_write: bool


class WriteSafetyAnalyzer:
    def __init__(self, sources: tuple[SourceFile, ...]) -> None:
        self._diagnostics = SourceDiagnosticContext(sources)

    def analyze(
        self,
        approvals: ApprovalBindingAnalysis,
        contracts_by_id: dict[str, ToolContract],
    ) -> WriteSafetyAnalysis:
        write_count = len(approvals.bindings)
        if write_count > 1:
            raise self._diagnostics.error(
                DiagnosticCode.SEM_TOO_MANY_WRITES,
                "only one WRITE is allowed per skill in NSL-0.2-STRICT",
                approvals.bindings[1].write.span,
            )
        nested = next(
            (binding for binding in approvals.bindings if binding.inside_foreach),
            None,
        )
        if nested is not None:
            raise self._diagnostics.error(
                DiagnosticCode.SEM_WRITE_IN_FOREACH,
                "WRITE is not allowed inside FOREACH in NSL-0.2-STRICT",
                nested.write.span,
            )

        for binding in approvals.bindings:
            write = binding.write
            try:
                contract = contracts_by_id[write.tool_id]
            except KeyError as error:
                raise self._diagnostics.error(
                    DiagnosticCode.SEM_UNDECLARED_TOOL,
                    f"tool not declared in requires: {write.tool_id}",
                    write.span,
                ) from error
            if contract.capability != ToolCapability.WRITE:
                raise self._diagnostics.error(
                    DiagnosticCode.SEM_WRITE_CAPABILITY_REQUIRED,
                    f"WRITE requires a WRITE tool contract: {write.tool_id}",
                    write.span,
                )
            if not isinstance(contract.operation_kind, OperationKind):
                raise self._diagnostics.error(
                    DiagnosticCode.SEM_UNSUPPORTED_WRITE_OPERATION,
                    f"unsupported WRITE operation: {write.tool_id}",
                    write.span,
                )
            semantics = contract.write_semantics
            if not isinstance(semantics, WriteSemantics) or (
                semantics.atomicity is not Atomicity.ATOMIC
            ):
                raise self._diagnostics.error(
                    DiagnosticCode.SEM_WRITE_ATOMICITY_REQUIRED,
                    f"WRITE tool must be ATOMIC: {write.tool_id}",
                    write.span,
                )
            if not isinstance(semantics.idempotency, IdempotencySemantics) or (
                semantics.idempotency.required is not True
            ):
                raise self._diagnostics.error(
                    DiagnosticCode.SEM_WRITE_IDEMPOTENCY_REQUIRED,
                    f"WRITE tool must require idempotency: {write.tool_id}",
                    write.span,
                )
            if not isinstance(contract.risk_level, RiskLevel):
                raise self._diagnostics.error(
                    DiagnosticCode.SEM_WRITE_RISK_METADATA_REQUIRED,
                    f"WRITE tool risk metadata is invalid: {write.tool_id}",
                    write.span,
                )

        return WriteSafetyAnalysis(
            write_node_count=write_count,
            approval_node_count=len(approvals.approvals),
            approval_write_mapping_count=len(approvals.bindings),
            unused_approval_count=0,
            max_write_calls=write_count,
            writes_inside_loop=False,
            all_writes_approved_exactly_once=True,
            all_write_operations_supported=True,
            all_write_tools_atomic=True,
            all_write_tools_idempotent=True,
            all_write_risk_metadata_present=True,
            requires_verified_package_for_write=write_count > 0,
        )


@dataclass(frozen=True, slots=True)
class _LanguageLoweringProfile:
    source_language_version: str
    ir_version: str
    semantics_profile: str
    allowed_risks: frozenset[str]
    features: frozenset[str]


_BASE_FEATURES = frozenset(
    {"READ", "FOREACH", "LET", "CHECK", "EMIT", "LIMITS"}
)
_LANGUAGE_LOWERING_PROFILES = {
    "0.1": _LanguageLoweringProfile(
        source_language_version="0.1",
        ir_version="1.0",
        semantics_profile="NSL-0.1-STRICT",
        allowed_risks=frozenset({"READ_ONLY", "READ_VALIDATE"}),
        features=_BASE_FEATURES,
    ),
    "0.2": _LanguageLoweringProfile(
        source_language_version="0.2",
        ir_version="1.1",
        semantics_profile="NSL-0.2-STRICT",
        allowed_risks=frozenset({"READ_ONLY", "READ_VALIDATE", "WRITE"}),
        features=_BASE_FEATURES | {"APPROVAL", "WRITE"},
    ),
}


class NslCompiler:
    def __init__(
        self,
        tool_catalog: ToolContractCatalog,
        include_resolver: IncludeResolver | None = None,
        include_options: IncludeOptions = IncludeOptions(),
    ) -> None:
        self.tool_catalog = tool_catalog
        self.include_resolver = include_resolver
        self.include_options = include_options

    def compile(self, source: str | SourceFile) -> CompilationResult:
        source_file = coerce_source(source)
        ast = Parser(Lexer().tokenize_root(source_file), source_file).parse()
        source_manifest = (manifest_entry(source_file, is_root=True),)
        include_edges: tuple[IncludeEdge, ...] = ()
        source_files = (source_file,)
        if not isinstance(ast, AstSkill):
            raise TypeError("root parse mode must produce AstSkill")
        if ast.includes and self.include_resolver is not None:
            bundle = SourceBundleBuilder(
                self.include_resolver, self.include_options
            ).build(source_file)
            ast = SourceComposer().compose(bundle)
            source_manifest = bundle.manifest
            include_edges = bundle.edges
            source_files = bundle.sources
        approval_analysis = ApprovalBindingAnalyzer(source_files).analyze(ast)
        source_bundle_hash = source_manifest_sha256(source_manifest)
        build = BuildMetadata(
            source_bundle_sha256=source_bundle_hash,
            root_source=source_manifest[0].logical_path,
            sources=tuple(
                BuildSource(
                    logical_path=item.logical_path,
                    content_hash=item.content_hash,
                    size_bytes=item.size_bytes,
                    is_root=item.is_root,
                )
                for item in source_manifest
            ),
        )
        lowerer = _Lowerer(
            self.tool_catalog, ast, source_files, approval_analysis
        )
        skill = lowerer.lower(build).with_computed_hash()
        nso_bytes = NsoCodec.encode(skill)
        return CompilationResult(
            skill=skill,
            nso_bytes=nso_bytes,
            semantic_hash=skill.semantic_hash,
            source_bundle_hash=source_bundle_hash,
            source_manifest=source_manifest,
            include_edges=include_edges,
            write_safety=lowerer.write_safety,
        )


class _Lowerer:
    def __init__(
        self,
        catalog: ToolContractCatalog,
        ast: AstSkill,
        sources: tuple[SourceFile, ...],
        approval_analysis: ApprovalBindingAnalysis,
    ) -> None:
        self.catalog = catalog
        self.ast = ast
        self.sources = sources
        self.node_counters: dict[str, int] = {}
        self.symbol_table = SymbolTable(sources)
        self.type_checker = StaticTypeChecker(sources)
        self.diagnostics = SourceDiagnosticContext(sources)
        self.tools_by_id: dict[str, tuple[RequiredTool, ToolContract]] = {}
        self.approval_analysis = approval_analysis
        self.write_safety = WriteSafetyAnalysis(
            write_node_count=0,
            approval_node_count=0,
            approval_write_mapping_count=0,
            unused_approval_count=0,
            max_write_calls=0,
            writes_inside_loop=False,
            all_writes_approved_exactly_once=True,
            all_write_operations_supported=True,
            all_write_tools_atomic=True,
            all_write_tools_idempotent=True,
            all_write_risk_metadata_present=True,
            requires_verified_package_for_write=False,
        )

    def _node_id(self, kind: str) -> str:
        self.node_counters[kind] = self.node_counters.get(kind, 0) + 1
        return f"{kind}{self.node_counters[kind]:04d}"

    def _add_binding(
        self,
        name: str,
        category: str,
        type_info: TypeRef,
        classification: DataClassification,
        node: AstNode,
        namespace: SymbolNamespace = SymbolNamespace.VALUE,
    ) -> SymbolBinding:
        return self.symbol_table.declare(
            name,
            namespace,
            category,
            type_info,
            classification,
            node.span,
        )

    def lower(self, build: BuildMetadata) -> SkillObject:
        if self.ast.includes:
            raise self.diagnostics.error(
                DiagnosticCode.SEM_INCLUDE_REQUIRES_COMPOSITION,
                "include declarations require Source composition before lowering",
                self.ast.includes[0].span,
            )
        if self.ast.limits is None:
            raise self.diagnostics.error(
                DiagnosticCode.INC_REQUIRED_LIMITS_MISSING,
                "composed skill must define exactly one limits block",
                self.ast.span,
                DiagnosticPhase.INCLUDE,
            )
        profile = _LANGUAGE_LOWERING_PROFILES.get(self.ast.language_version)
        if profile is None:
            raise self.diagnostics.error(
                DiagnosticCode.SEM_UNSUPPORTED_LANGUAGE_VERSION,
                f"unsupported NSL version: {self.ast.language_version}",
                self.ast.language_version_span,
            )
        if self.ast.risk not in profile.allowed_risks:
            raise self.diagnostics.error(
                DiagnosticCode.SEM_UNSUPPORTED_RISK,
                f"unsupported risk profile: {self.ast.risk}",
                self.ast.risk_span,
            )

        contracts_by_id: dict[str, ToolContract] = {}
        for declaration in self.ast.requires:
            tool_id = declaration.tool_id
            version = declaration.version
            if tool_id in contracts_by_id:
                raise self.diagnostics.error(
                    DiagnosticCode.SEM_DUPLICATE_TOOL,
                    f"duplicate required tool: {tool_id}",
                    declaration.span,
                )
            try:
                contract = self.catalog.resolve(tool_id, version)
            except UnknownToolContractError as error:
                raise self.diagnostics.error(
                    DiagnosticCode.SEM_UNKNOWN_TOOL_CONTRACT,
                    f"unknown tool contract: {tool_id}@{version}",
                    declaration.span,
                ) from error
            except IncompatibleToolVersionError as error:
                raise self.diagnostics.error(
                    DiagnosticCode.SEM_INCOMPATIBLE_TOOL_VERSION,
                    f"incompatible tool version: {tool_id}@{version}",
                    declaration.span,
                ) from error
            if profile.source_language_version == "0.1" and (
                contract.capability != ToolCapability.READ
            ):
                raise self.diagnostics.error(
                    DiagnosticCode.SEM_WRITE_TOOL_FORBIDDEN,
                    f"{contract.capability} tool capability is forbidden "
                    f"in v0.1: {tool_id}",
                    declaration.span,
                )
            contracts_by_id[tool_id] = contract

        self.write_safety = WriteSafetyAnalyzer(self.sources).analyze(
            self.approval_analysis, contracts_by_id
        )

        required_tools: list[RequiredTool] = []
        for index, declaration in enumerate(self.ast.requires, start=1):
            contract = contracts_by_id[declaration.tool_id]
            requirement = RequiredTool(
                tool_ref=f"tool{index:04d}",
                tool_id=declaration.tool_id,
                version=declaration.version,
                capability=contract.capability,
                contract_hash=contract.contract_hash,
                required_scope=contract.required_scope,
                output_classification=contract.output_classification,
                risk_level=(
                    (contract.risk_level or RiskLevel.R1)
                    if profile.ir_version == "1.1"
                    else None
                ),
            )
            self.tools_by_id[declaration.tool_id] = (requirement, contract)
            required_tools.append(requirement)

        input_specs: list[InputSpec] = []
        for item in self.ast.inputs:
            binding = self._add_binding(
                item.name, "INPUT", item.type_info, item.classification, item
            )
            input_specs.append(
                InputSpec(
                    binding.symbol_id,
                    item.name,
                    item.type_info,
                    True,
                    item.classification,
                )
            )

        context_specs: list[ContextSpec] = []
        for item in self.ast.contexts:
            binding = self._add_binding(
                item.name, "CONTEXT", item.type_info, item.classification, item
            )
            context_specs.append(
                ContextSpec(
                    binding.symbol_id,
                    item.name,
                    item.type_info,
                    item.path,
                    item.classification,
                )
            )

        output_names = tuple(item.name for item in self.ast.outputs)
        if len(output_names) != len(set(output_names)):
            duplicate = next(
                name for name in output_names if output_names.count(name) > 1
            )
            duplicate_node = next(
                item for item in self.ast.outputs if item.name == duplicate
            )
            raise self.diagnostics.error(
                DiagnosticCode.SEM_EMIT_SCHEMA,
                f"duplicate output field: {duplicate}",
                duplicate_node.span,
            )
        outputs = tuple(
            OutputField(item.name, item.type_info, item.classification)
            for item in self.ast.outputs
        )
        limits = ResourceLimits(
            tool_calls=self.ast.limits.tool_calls,
            loop_iterations=self.ast.limits.loop_iterations,
            emitted_rows=self.ast.limits.emitted_rows,
            collection_size=self.ast.limits.collection_size,
            duration_ms=self.ast.limits.duration_ms,
        )
        body = self._lower_block(self.ast.body, outputs)
        try:
            analysis = StaticBoundAnalyzer().analyze(body)
        except UnboundedStructureError as error:
            raise self.diagnostics.error(
                DiagnosticCode.SEM_UNBOUNDED_STRUCTURE,
                f"static execution bound cannot be proven: {error}",
                self.ast.span,
            ) from error
        if analysis.max_tool_calls > limits.tool_calls:
            raise self.diagnostics.error(
                DiagnosticCode.SEM_TOOL_CALL_BOUND,
                f"static tool call bound {analysis.max_tool_calls} exceeds "
                f"declared limit {limits.tool_calls}",
                self.ast.limits.span,
            )
        if analysis.max_loop_iterations > limits.loop_iterations:
            raise self.diagnostics.error(
                DiagnosticCode.SEM_LOOP_BOUND,
                f"static loop bound {analysis.max_loop_iterations} exceeds "
                f"declared limit {limits.loop_iterations}",
                self.ast.limits.span,
            )
        if analysis.max_emit_records > limits.emitted_rows:
            raise self.diagnostics.error(
                DiagnosticCode.SEM_EMIT_BOUND,
                f"static emit bound {analysis.max_emit_records} exceeds "
                f"declared limit {limits.emitted_rows}",
                self.ast.limits.span,
            )

        return SkillObject(
            ir_version=profile.ir_version,
            language_version=self.ast.language_version,
            skill_id=self.ast.skill_id,
            skill_version=self.ast.skill_version,
            risk=self.ast.risk,
            semantics_profile=profile.semantics_profile,
            semantic_hash="",
            features=profile.features,
            symbols=tuple(
                SymbolSpec(
                    binding.symbol_id,
                    binding.name,
                    binding.category,
                    binding.type_info,
                    binding.classification,
                )
                for binding in self.symbol_table.bindings
            ),
            required_tools=tuple(required_tools),
            limits=limits,
            inputs=tuple(input_specs),
            contexts=tuple(context_specs),
            outputs=outputs,
            body=body,
            analysis=analysis,
            build=build,
        )

    def _lower_block(
        self, statements: tuple[AstStatement, ...], outputs: tuple[OutputField, ...]
    ) -> tuple[Statement, ...]:
        lowered: list[Statement] = []
        for statement in statements:
            if isinstance(statement, AstLet):
                expression, classification = self._lower_expr(statement.value)
                binding = self._add_binding(
                    statement.name,
                    "VARIABLE",
                    expression.type_info,
                    classification,
                    statement,
                )
                lowered.append(
                    LetStatement(self._node_id("stmt"), binding.symbol_id, expression)
                )
            elif isinstance(statement, AstForeach):
                collection, classification = self._lower_expr(statement.collection)
                item_type = self.type_checker.require_list(
                    collection.type_info,
                    DiagnosticCode.SEM_FOREACH_COLLECTION_TYPE,
                    "foreach collection must be List<T>",
                    statement.collection.span,
                )
                scope_id = self.symbol_table.enter_scope(ScopeKind.FOREACH)
                try:
                    iterator = self._add_binding(
                        statement.iterator,
                        "ITERATOR",
                        item_type,
                        classification,
                        statement,
                    )
                    body = self._lower_block(statement.body, outputs)
                finally:
                    self.symbol_table.leave_scope(scope_id)
                lowered.append(
                    ForeachStatement(
                        self._node_id("stmt"),
                        iterator.symbol_id,
                        collection,
                        statement.max_iterations,
                        body,
                    )
                )
            elif isinstance(statement, AstCheck):
                condition, _ = self._lower_expr(statement.condition)
                self.type_checker.require_bool(
                    condition.type_info, statement.condition.span
                )
                binding = self._add_binding(
                    statement.check_id,
                    "CHECK",
                    CHECK_RESULT,
                    DataClassification.INTERNAL,
                    statement,
                    SymbolNamespace.CHECK,
                )
                lowered.append(
                    CheckStatement(
                        self._node_id("check"),
                        statement.check_id,
                        condition,
                        statement.severity,
                        statement.on_fail,
                        statement.message,
                        binding.symbol_id,
                    )
                )
            elif isinstance(statement, AstEmit):
                declared = {item.name: item for item in outputs}
                field_names = tuple(name for name, _ in statement.fields)
                if (
                    len(field_names) != len(declared)
                    or len(field_names) != len(set(field_names))
                    or set(field_names) != set(declared)
                ):
                    raise self.diagnostics.error(
                        DiagnosticCode.SEM_EMIT_SCHEMA,
                        "EMIT fields must exactly match output schema",
                        statement.span,
                    )
                fields: list[tuple[str, Any]] = []
                for name, ast_expression in statement.fields:
                    expression, classification = self._lower_expr(ast_expression)
                    output = declared[name]
                    self.type_checker.require_exact(
                        expression.type_info,
                        output.type_info,
                        DiagnosticCode.SEM_OUTPUT_TYPE,
                        f"output type mismatch for {name}",
                        ast_expression.span,
                    )
                    if not classification_allows(classification, output.classification):
                        raise self.diagnostics.error(
                            DiagnosticCode.SEM_OUTPUT_CLASSIFICATION,
                            f"output classification for {name} is too weak",
                            ast_expression.span,
                        )
                    fields.append((name, expression))
                lowered.append(EmitStatement(self._node_id("emit"), tuple(fields)))
            elif isinstance(statement, AstApprove):
                lowered.append(
                    ApprovalStatement(
                        self._node_id("approval"),
                        statement.approval_id,
                        statement.message,
                        ApprovalPolicy(),
                    )
                )
            else:
                raise TypeError(statement)
        return tuple(lowered)

    def _lower_expr(self, ast: AstExpression) -> tuple[Any, DataClassification]:
        if isinstance(ast, AstLiteral):
            return (
                LiteralExpr(self._node_id("expr"), ast.value, ast.type_info),
                DataClassification.PUBLIC,
            )
        if isinstance(ast, AstPath):
            binding = self.symbol_table.resolve(ast.parts[0], ast.span)
            expression: Any = SymbolRefExpr(
                self._node_id("expr"), binding.symbol_id, binding.type_info
            )
            classification = binding.classification
            current_type = binding.type_info
            for field in ast.parts[1:]:
                current_type, projected = self.type_checker.field_result(
                    current_type, field, ast.span
                )
                if projected:
                    expression = ProjectionExpr(
                        self._node_id("expr"), expression, field, current_type
                    )
                else:
                    expression = FieldExpr(
                        self._node_id("expr"), expression, field, current_type
                    )
            return expression, classification
        if isinstance(ast, AstCall):
            arguments = [self._lower_expr(item) for item in ast.arguments]
            signature = self.type_checker.builtin_result(
                ast.name,
                tuple(argument.type_info for argument, _ in arguments),
                ast.span,
            )
            argument, classification = arguments[0]
            return (
                CallExpr(
                    self._node_id("expr"),
                    signature.name,
                    tuple(item for item, _ in arguments),
                    signature.result_type,
                ),
                classification,
            )
        if isinstance(ast, AstBinary):
            left, left_classification = self._lower_expr(ast.left)
            right, right_classification = self._lower_expr(ast.right)
            result_type = self.type_checker.binary_result(
                ast.operator, left.type_info, right.type_info, ast.span
            )
            operator_map = {
                "+": "ADD",
                "-": "SUB",
                "*": "MUL",
                "/": "DIV",
                "<": "LT",
                "<=": "LE",
                ">": "GT",
                ">=": "GE",
                "==": "EQ",
                "!=": "NE",
            }
            classification = max(
                (left_classification, right_classification),
                key=lambda item: list(DataClassification).index(item),
            )
            return (
                BinaryExpr(
                    self._node_id("expr"),
                    operator_map[ast.operator],
                    left,
                    right,
                    result_type,
                ),
                classification,
            )
        if isinstance(ast, AstRead):
            try:
                requirement, contract = self.tools_by_id[ast.tool_id]
            except KeyError as error:
                raise self.diagnostics.error(
                    DiagnosticCode.SEM_UNDECLARED_TOOL,
                    f"tool not declared in requires: {ast.tool_id}",
                    ast.span,
                ) from error
            if contract.capability != ToolCapability.READ:
                raise self.diagnostics.error(
                    DiagnosticCode.SEM_WRITE_TOOL_FORBIDDEN,
                    f"READ expression requires a READ tool contract: {ast.tool_id}",
                    ast.span,
                )
            arguments = self._lower_tool_arguments(ast, contract)
            return (
                ReadExpr(
                    self._node_id("read"),
                    requirement.tool_ref,
                    tuple(arguments),
                    contract.output_type,
                    ResultPolicy(empty_is_valid=contract.empty_is_valid),
                ),
                contract.output_classification,
            )
        if isinstance(ast, AstWrite):
            requirement, contract = self.tools_by_id[ast.tool_id]
            arguments = self._lower_tool_arguments(ast, contract)
            return (
                WriteExpression(
                    self._node_id("write"),
                    requirement.tool_ref,
                    arguments,
                    ast.approval_id,
                    contract.output_type,
                ),
                contract.output_classification,
            )
        raise TypeError(ast)

    def _lower_tool_arguments(
        self, ast: AstRead | AstWrite, contract: ToolContract
    ) -> tuple[tuple[str, Any], ...]:
        provided_names = tuple(name for name, _ in ast.arguments)
        provided = set(provided_names)
        expected = {name for name, _ in contract.input_types}
        if provided != expected or len(provided_names) != len(provided):
            raise self.diagnostics.error(
                DiagnosticCode.SEM_TOOL_ARGUMENTS,
                f"tool arguments for {ast.tool_id} must be {sorted(expected)}",
                ast.span,
            )
        arguments: list[tuple[str, Any]] = []
        for name, ast_value in ast.arguments:
            value, _ = self._lower_expr(ast_value)
            self.type_checker.require_exact(
                value.type_info,
                contract.input_type(name),
                DiagnosticCode.SEM_TOOL_ARGUMENT_TYPE,
                f"tool argument type mismatch: {ast.tool_id}.{name}",
                ast_value.span,
            )
            arguments.append((name, value))
        return tuple(arguments)
