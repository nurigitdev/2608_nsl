from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .core import (
    BOOL,
    CHECK_RESULT,
    DataClassification,
    TypeRef,
    classification_allows,
    list_type,
)
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
from .syntax import (
    AstBinary,
    AstCall,
    AstCheck,
    AstEmit,
    AstExpression,
    AstForeach,
    AstLet,
    AstLiteral,
    AstPath,
    AstRead,
    AstSkill,
    AstStatement,
    CompileError,
    Lexer,
    Parser,
)
from .tools import ToolContract, ToolContractCatalog


@dataclass(frozen=True, slots=True)
class _Binding:
    symbol_id: str
    type_info: TypeRef
    classification: DataClassification


@dataclass(frozen=True, slots=True)
class CompilationResult:
    skill: SkillObject
    nso_bytes: bytes
    semantic_hash: str
    source_bundle_hash: str


class NslCompiler:
    def __init__(self, tool_catalog: ToolContractCatalog) -> None:
        self.tool_catalog = tool_catalog

    def compile(self, source: str) -> CompilationResult:
        ast = Parser(Lexer().tokenize(source)).parse()
        lowerer = _Lowerer(self.tool_catalog, ast)
        skill = lowerer.lower().with_computed_hash()
        nso_bytes = NsoCodec.encode(skill)
        return CompilationResult(
            skill=skill,
            nso_bytes=nso_bytes,
            semantic_hash=skill.semantic_hash,
            source_bundle_hash="sha256:" + sha256(source.encode("utf-8")).hexdigest(),
        )


class _Lowerer:
    def __init__(self, catalog: ToolContractCatalog, ast: AstSkill) -> None:
        self.catalog = catalog
        self.ast = ast
        self.symbol_counter = 0
        self.node_counters: dict[str, int] = {}
        self.symbols: list[SymbolSpec] = []
        self.bindings: dict[str, _Binding] = {}
        self.tools_by_id: dict[str, tuple[RequiredTool, ToolContract]] = {}

    def _symbol_id(self) -> str:
        self.symbol_counter += 1
        return f"s{self.symbol_counter:04d}"

    def _node_id(self, kind: str) -> str:
        self.node_counters[kind] = self.node_counters.get(kind, 0) + 1
        return f"{kind}{self.node_counters[kind]:04d}"

    def _add_binding(
        self,
        name: str,
        category: str,
        type_info: TypeRef,
        classification: DataClassification,
    ) -> _Binding:
        if name in self.bindings:
            raise CompileError(f"duplicate symbol or shadowing is forbidden: {name}")
        binding = _Binding(self._symbol_id(), type_info, classification)
        self.bindings[name] = binding
        self.symbols.append(
            SymbolSpec(binding.symbol_id, name, category, type_info, classification)
        )
        return binding

    def lower(self) -> SkillObject:
        if self.ast.language_version != "0.1":
            raise CompileError(f"unsupported NSL version: {self.ast.language_version}")
        if self.ast.risk not in {"READ_ONLY", "READ_VALIDATE"}:
            raise CompileError(f"unsupported risk profile: {self.ast.risk}")

        required_tools: list[RequiredTool] = []
        for index, (tool_id, version) in enumerate(self.ast.requires, start=1):
            if tool_id in self.tools_by_id:
                raise CompileError(f"duplicate required tool: {tool_id}")
            try:
                contract = self.catalog.get(tool_id, version)
            except KeyError as error:
                raise CompileError(str(error)) from error
            if contract.capability != "READ":
                raise CompileError(f"WRITE tool is forbidden in v0.1: {tool_id}")
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
                item.name, "INPUT", item.type_info, item.classification
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
                item.name, "CONTEXT", item.type_info, item.classification
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
            raise CompileError("static tool call bound exceeds declared limit")
        if analysis.max_loop_iterations > limits.loop_iterations:
            raise CompileError("static loop bound exceeds declared limit")
        if analysis.max_emit_records > limits.emitted_rows:
            raise CompileError("static emit bound exceeds declared limit")

        return SkillObject(
            ir_version="1.0",
            language_version=self.ast.language_version,
            skill_id=self.ast.skill_id,
            skill_version=self.ast.skill_version,
            risk=self.ast.risk,
            semantics_profile="NSL-0.1-STRICT",
            semantic_hash="",
            features=frozenset({"READ", "FOREACH", "LET", "CHECK", "EMIT", "LIMITS"}),
            symbols=tuple(self.symbols),
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
                    statement.name, "VARIABLE", expression.type_info, classification
                )
                lowered.append(
                    LetStatement(self._node_id("stmt"), binding.symbol_id, expression)
                )
            elif isinstance(statement, AstForeach):
                collection, classification = self._lower_expr(statement.collection)
                if collection.type_info.kind != "list" or collection.type_info.item is None:
                    raise CompileError("foreach collection must be List<T>")
                outer_bindings = dict(self.bindings)
                iterator = self._add_binding(
                    statement.iterator,
                    "ITERATOR",
                    collection.type_info.item,
                    classification,
                )
                body = self._lower_block(statement.body, outputs)
                self.bindings = outer_bindings
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
                if condition.type_info != BOOL:
                    raise CompileError("CHECK assert must have Bool type")
                binding = self._add_binding(
                    statement.check_id,
                    "CHECK",
                    CHECK_RESULT,
                    DataClassification.INTERNAL,
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
                    raise CompileError("EMIT fields must exactly match output schema")
                fields: list[tuple[str, Any]] = []
                for name, ast_expression in statement.fields:
                    expression, classification = self._lower_expr(ast_expression)
                    output = declared[name]
                    if expression.type_info != output.type_info:
                        raise CompileError(f"output type mismatch for {name}")
                    if not classification_allows(classification, output.classification):
                        raise CompileError(
                            f"output classification for {name} is too weak"
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
            try:
                binding = self.bindings[ast.parts[0]]
            except KeyError as error:
                raise CompileError(f"unknown identifier: {ast.parts[0]}") from error
            expression: Any = SymbolRefExpr(
                self._node_id("expr"), binding.symbol_id, binding.type_info
            )
            classification = binding.classification
            current_type = binding.type_info
            for field in ast.parts[1:]:
                try:
                    if current_type.kind == "list" and current_type.item is not None:
                        field_type = current_type.item.field(field)
                        current_type = list_type(field_type)
                        expression = ProjectionExpr(
                            self._node_id("expr"), expression, field, current_type
                        )
                    elif current_type.kind == "record":
                        current_type = current_type.field(field)
                        expression = FieldExpr(
                            self._node_id("expr"), expression, field, current_type
                        )
                    else:
                        raise KeyError(field)
                except KeyError as error:
                    raise CompileError(
                        f"type {current_type.kind} has no field {field}"
                    ) from error
            return expression, classification
        if isinstance(ast, AstCall):
            arguments = [self._lower_expr(item) for item in ast.arguments]
            if ast.name != "sum" or len(arguments) != 1:
                raise CompileError(f"unsupported built-in call: {ast.name}")
            argument, classification = arguments[0]
            if argument.type_info.kind != "list" or argument.type_info.item is None:
                raise CompileError("sum requires List<Int|Decimal|Money>")
            if argument.type_info.item.kind not in {"primitive", "money"}:
                raise CompileError("sum requires List<Int|Decimal|Money>")
            return (
                CallExpr(
                    self._node_id("expr"), "sum", (argument,), argument.type_info.item
                ),
                classification,
            )
        if isinstance(ast, AstBinary):
            left, left_classification = self._lower_expr(ast.left)
            right, right_classification = self._lower_expr(ast.right)
            if left.type_info != right.type_info:
                raise CompileError(f"binary type mismatch for {ast.operator}")
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
            result_type = BOOL if ast.operator in {"<", "<=", ">", ">=", "==", "!="} else left.type_info
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
                raise CompileError(f"tool not declared in requires: {ast.tool_id}") from error
            provided = {name for name, _ in ast.arguments}
            expected = {name for name, _ in contract.input_types}
            if provided != expected:
                raise CompileError(
                    f"tool arguments for {ast.tool_id} must be {sorted(expected)}"
                )
            arguments: list[tuple[str, Any]] = []
            for name, ast_value in ast.arguments:
                value, _ = self._lower_expr(ast_value)
                if value.type_info != contract.input_type(name):
                    raise CompileError(f"tool argument type mismatch: {ast.tool_id}.{name}")
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


