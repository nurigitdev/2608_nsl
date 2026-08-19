from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .core import (
    CHECK_RESULT,
    DataClassification,
    TypeRef,
    classification_allows,
)
from .diagnostics import CompileError, DiagnosticCode, DiagnosticPhase, compile_error
from .ir import (
    BinaryExpr,
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
    StaticAnalysis,
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
        source_bundle_hash = "sha256:" + sha256(
            source_file.text.encode("utf-8")
        ).hexdigest()
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
            source_bundle_hash = bundle.bundle_hash
            source_manifest = bundle.manifest
            include_edges = bundle.edges
            source_files = bundle.sources
        lowerer = _Lowerer(self.tool_catalog, ast, source_files)
        skill = lowerer.lower().with_computed_hash()
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

    def lower(self) -> SkillObject:
        if self.ast.includes:
            raise compile_error(
                DiagnosticCode.SEM_INCLUDE_REQUIRES_COMPOSITION,
                DiagnosticPhase.SEMANTIC,
                "include declarations require Source composition before lowering",
            )
        if self.ast.limits is None:
            raise compile_error(
                DiagnosticCode.INC_REQUIRED_LIMITS_MISSING,
                DiagnosticPhase.INCLUDE,
                "composed skill must define exactly one limits block",
            )
        if self.ast.language_version != "0.1":
            raise compile_error(
                DiagnosticCode.SEM_UNSUPPORTED_LANGUAGE_VERSION,
                DiagnosticPhase.SEMANTIC,
                f"unsupported NSL version: {self.ast.language_version}",
            )
        if self.ast.risk not in {"READ_ONLY", "READ_VALIDATE"}:
            raise compile_error(
                DiagnosticCode.SEM_UNSUPPORTED_RISK,
                DiagnosticPhase.SEMANTIC,
                f"unsupported risk profile: {self.ast.risk}",
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

        outputs = tuple(
            OutputField(item.name, item.type_info, item.classification)
            for item in self.ast.outputs
        )
        limits = ResourceLimits(
            tool_calls=self.ast.limits.tool_calls,
            loop_iterations=self.ast.limits.loop_iterations,
            emitted_rows=self.ast.limits.emitted_rows,
            collection_size=self.ast.limits.collection_size,
        )
        body = self._lower_block(self.ast.body, outputs)
        bounds = self._bounds(body)
        analysis = StaticAnalysis(*bounds, bounded=True)
        if analysis.max_tool_calls > limits.tool_calls:
            raise compile_error(
                DiagnosticCode.SEM_TOOL_CALL_BOUND,
                DiagnosticPhase.SEMANTIC,
                "static tool call bound exceeds declared limit",
            )
        if analysis.max_loop_iterations > limits.loop_iterations:
            raise compile_error(
                DiagnosticCode.SEM_LOOP_BOUND,
                DiagnosticPhase.SEMANTIC,
                "static loop bound exceeds declared limit",
            )
        if analysis.max_emit_records > limits.emitted_rows:
            raise compile_error(
                DiagnosticCode.SEM_EMIT_BOUND,
                DiagnosticPhase.SEMANTIC,
                "static emit bound exceeds declared limit",
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
                if set(name for name, _ in statement.fields) != set(declared):
                    raise compile_error(
                        DiagnosticCode.SEM_EMIT_SCHEMA,
                        DiagnosticPhase.SEMANTIC,
                        "EMIT fields must exactly match output schema",
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
                        raise compile_error(
                            DiagnosticCode.SEM_OUTPUT_CLASSIFICATION,
                            DiagnosticPhase.SEMANTIC,
                            f"output classification for {name} is too weak",
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
            provided = {name for name, _ in ast.arguments}
            expected = {name for name, _ in contract.input_types}
            if provided != expected:
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

    def _bounds(self, statements: tuple[Statement, ...]) -> tuple[int, int, int]:
        tool_calls = 0
        loop_iterations = 0
        emits = 0
        for statement in statements:
            if isinstance(statement, LetStatement):
                tool_calls += self._expr_tool_calls(statement.value)
            elif isinstance(statement, ForeachStatement):
                collection_calls = self._expr_tool_calls(statement.collection)
                body_calls, body_loops, body_emits = self._bounds(statement.body)
                tool_calls += collection_calls + statement.max_iterations * body_calls
                loop_iterations += statement.max_iterations * (1 + body_loops)
                emits += statement.max_iterations * body_emits
            elif isinstance(statement, CheckStatement):
                tool_calls += self._expr_tool_calls(statement.condition)
            elif isinstance(statement, EmitStatement):
                emits += 1
                tool_calls += sum(
                    self._expr_tool_calls(value) for _, value in statement.fields
                )
        return tool_calls, loop_iterations, emits

    def _expr_tool_calls(self, expression: Any) -> int:
        if isinstance(expression, ReadExpr):
            return 1 + sum(
                self._expr_tool_calls(value) for _, value in expression.arguments
            )
        if isinstance(expression, (FieldExpr, ProjectionExpr)):
            return self._expr_tool_calls(expression.source)
        if isinstance(expression, CallExpr):
            return sum(self._expr_tool_calls(item) for item in expression.arguments)
        if isinstance(expression, BinaryExpr):
            return self._expr_tool_calls(expression.left) + self._expr_tool_calls(
                expression.right
            )
        return 0
