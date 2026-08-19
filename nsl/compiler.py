from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bounds import StaticBoundAnalyzer, UnboundedStructureError
from .core import (
    CHECK_RESULT,
    DataClassification,
    TypeRef,
    classification_allows,
)
from .diagnostics import CompileError, DiagnosticCode, DiagnosticPhase
from .ir import (
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
from .source import SourceFile, coerce_source
from .symbols import ScopeKind, SymbolBinding, SymbolNamespace, SymbolTable
from .syntax import (
    AstBinary,
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
    Lexer,
    Parser,
)
from .tools import (
    IncompatibleToolVersionError,
    ToolContract,
    ToolContractCatalog,
    UnknownToolContractError,
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
        ast = Parser(Lexer().tokenize(source_file), source_file).parse()
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
        lowerer = _Lowerer(self.tool_catalog, ast, source_files)
        skill = lowerer.lower(build).with_computed_hash()
        nso_bytes = NsoCodec.encode(skill)
        return CompilationResult(
            skill=skill,
            nso_bytes=nso_bytes,
            semantic_hash=skill.semantic_hash,
            source_bundle_hash=source_bundle_hash,
            source_manifest=source_manifest,
            include_edges=include_edges,
        )


class _Lowerer:
    def __init__(
        self,
        catalog: ToolContractCatalog,
        ast: AstSkill,
        sources: tuple[SourceFile, ...],
    ) -> None:
        self.catalog = catalog
        self.ast = ast
        self.node_counters: dict[str, int] = {}
        self.symbol_table = SymbolTable(sources)
        self.type_checker = StaticTypeChecker(sources)
        self.diagnostics = SourceDiagnosticContext(sources)
        self.tools_by_id: dict[str, tuple[RequiredTool, ToolContract]] = {}

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
        if self.ast.language_version != "0.1":
            raise self.diagnostics.error(
                DiagnosticCode.SEM_UNSUPPORTED_LANGUAGE_VERSION,
                f"unsupported NSL version: {self.ast.language_version}",
                self.ast.language_version_span,
            )
        if self.ast.risk not in {"READ_ONLY", "READ_VALIDATE"}:
            raise self.diagnostics.error(
                DiagnosticCode.SEM_UNSUPPORTED_RISK,
                f"unsupported risk profile: {self.ast.risk}",
                self.ast.risk_span,
            )

        required_tools: list[RequiredTool] = []
        for index, declaration in enumerate(self.ast.requires, start=1):
            tool_id = declaration.tool_id
            version = declaration.version
            if tool_id in self.tools_by_id:
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
            if contract.capability != "READ":
                raise self.diagnostics.error(
                    DiagnosticCode.SEM_WRITE_TOOL_FORBIDDEN,
                    f"{contract.capability} tool capability is forbidden "
                    f"in v0.1: {tool_id}",
                    declaration.span,
                )
            requirement = RequiredTool(
                tool_ref=f"tool{index:04d}",
                tool_id=tool_id,
                version=version,
                capability=contract.capability,
                contract_hash=contract.contract_hash,
                required_scope=contract.required_scope,
                output_classification=contract.output_classification,
            )
            self.tools_by_id[tool_id] = (requirement, contract)
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
            ir_version="1.0",
            language_version=self.ast.language_version,
            skill_id=self.ast.skill_id,
            skill_version=self.ast.skill_version,
            risk=self.ast.risk,
            semantics_profile="NSL-0.1-STRICT",
            semantic_hash="",
            features=frozenset({"READ", "FOREACH", "LET", "CHECK", "EMIT", "LIMITS"}),
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
        raise TypeError(ast)
