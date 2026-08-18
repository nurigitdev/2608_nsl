from __future__ import annotations

from decimal import Decimal
from traceback import format_exception
from typing import Any, Callable

from .audit import AuditRecorder, AuditSink, SnapshotStore, value_hash
from .core import (
    BOOL,
    CHECK_RESULT,
    CheckStatus,
    Completeness,
    DataClassification,
    ExecutionStatus,
    Money,
    Presence,
    TypeRef,
    ValueEnvelope,
    highest_classification,
)
from .diagnostics import DiagnosticCode
from .ir import (
    BinaryExpr,
    CallExpr,
    CheckStatement,
    EmitStatement,
    Expression,
    FieldExpr,
    ForeachStatement,
    LetStatement,
    LiteralExpr,
    ProjectionExpr,
    ReadExpr,
    SkillObject,
    Statement,
    SymbolRefExpr,
)
from .runtime_models import (
    CheckResult,
    EmitRecord,
    ExecutionRequest,
    ExecutionResult,
    LimitExceeded,
    ResourceUsage,
    RuntimeErrorInfo,
    _ExecutionContext,
)
from .security import AuthorizationError, StaticAuthorizer
from .tools import (
    ToolCallRequest,
    ToolContractCatalog,
    ToolExecutionError,
    ToolExecutionPort,
)


class RuntimeContractError(RuntimeError):
    """A controlled runtime validation error that is safe to return to callers."""


_UNEXPECTED_RUNTIME_MESSAGE = "An unexpected runtime error occurred."


class RuntimeEngine:
    def __init__(
        self,
        tool_catalog: ToolContractCatalog,
        authorizer: StaticAuthorizer | None = None,
        debug_mode: bool = False,
        debug_trace_sink: Callable[[str], None] | None = None,
    ) -> None:
        self.tool_catalog = tool_catalog
        self.authorizer = authorizer or StaticAuthorizer()
        self.debug_mode = debug_mode
        self.debug_trace_sink = debug_trace_sink

    async def execute(
        self,
        skill: SkillObject,
        request: ExecutionRequest,
        tools: ToolExecutionPort,
        audit_sink: AuditSink,
        snapshot_store: SnapshotStore | None = None,
    ) -> ExecutionResult:
        audit = AuditRecorder(audit_sink, request.data_policy)
        ctx = _ExecutionContext(skill, request, audit)
        try:
            self._preflight(skill)
            skill_scopes = frozenset({"nsl:skill:execute"})
            skill_decision = self.authorizer.authorize(
                request.principal,
                f"skill:{skill.skill_id}:execute",
                skill_scopes,
            )
            input_classification = DataClassification.PUBLIC
            for spec in skill.inputs:
                input_classification = highest_classification(
                    input_classification, spec.classification
                )
            context_classification = DataClassification.PUBLIC
            for spec in skill.contexts:
                context_classification = highest_classification(
                    context_classification, spec.classification
                )
            input_ref = None
            context_ref = None
            if snapshot_store is not None:
                input_ref = snapshot_store.put(
                    request.principal.tenant_id,
                    dict(request.inputs),
                    input_classification,
                    request.data_policy.snapshot_retention_days,
                )
                context_ref = snapshot_store.put(
                    request.principal.tenant_id,
                    dict(request.runtime_context),
                    context_classification,
                    request.data_policy.snapshot_retention_days,
                )
            audit.emit(
                "EXECUTION_STARTED",
                {
                    "execution_id": request.execution_id,
                    "skill_id": skill.skill_id,
                    "skill_version": skill.skill_version,
                    "semantic_hash": skill.semantic_hash,
                    "tenant_id": request.principal.tenant_id,
                    "subject_id": request.principal.subject_id,
                    "authorization_decision_ref": skill_decision.decision_id,
                    "input_snapshot_ref": None
                    if input_ref is None
                    else input_ref.snapshot_id,
                    "input_hash": value_hash(dict(request.inputs)),
                    "context_snapshot_ref": None
                    if context_ref is None
                    else context_ref.snapshot_id,
                    "context_hash": value_hash(dict(request.runtime_context)),
                },
            )
            ctx.frames.append({})
            self._bind_inputs_and_contexts(ctx)
            await self._execute_block(ctx, skill.body, tools)
            result = self._result(ctx, ExecutionStatus.COMPLETED)
            audit.emit(
                "EXECUTION_COMPLETED",
                {
                    "execution_id": request.execution_id,
                    "status": result.status.value,
                    "output_count": len(result.outputs),
                    "check_count": len(result.checks),
                },
            )
            return result
        except AuthorizationError as error:
            return self._failed(
                ctx,
                DiagnosticCode.AUTHORIZATION_DENIED,
                "AUTHORIZATION",
                str(error),
            )
        except PermissionError as error:
            return self._failed(
                ctx,
                DiagnosticCode.REPLAY_ACCESS_DENIED,
                "AUTHORIZATION",
                str(error),
            )
        except ToolExecutionError as error:
            code = (
                DiagnosticCode.SEM_UNKNOWN_TOOL_CONTRACT
                if error.code == "TOOL_CONTRACT_MISMATCH"
                else DiagnosticCode.TOOL_EXECUTION_FAILURE
            )
            return self._failed(
                ctx,
                code,
                "TOOL",
                str(error),
                ExecutionStatus.TOOL_ERROR,
                detail_code=error.code,
            )
        except LimitExceeded as error:
            return self._failed(
                ctx,
                DiagnosticCode.RESOURCE_LIMIT_EXCEEDED,
                "RESOURCE",
                str(error),
                ExecutionStatus.LIMIT_EXCEEDED,
            )
        except RuntimeContractError as error:
            return self._failed(
                ctx, DiagnosticCode.RUNTIME_EVALUATION, "RUNTIME", str(error)
            )
        except Exception as error:
            self._write_debug_trace(error)
            return self._failed(
                ctx,
                DiagnosticCode.RUNTIME_UNEXPECTED,
                "RUNTIME",
                _UNEXPECTED_RUNTIME_MESSAGE,
            )

    def _write_debug_trace(self, error: Exception) -> None:
        if not self.debug_mode or self.debug_trace_sink is None:
            return
        trace = "".join(
            format_exception(type(error), error, error.__traceback__)
        )
        try:
            self.debug_trace_sink(trace)
        except Exception:
            return

    def _preflight(self, skill: SkillObject) -> None:
        if skill.ir_version != "1.0" or skill.language_version != "0.1":
            raise RuntimeContractError("unsupported IR or language version")
        if skill.semantics_profile != "NSL-0.1-STRICT":
            raise RuntimeContractError("unsupported semantics profile")
        if skill.with_computed_hash().semantic_hash != skill.semantic_hash:
            raise RuntimeContractError("semantic hash mismatch")
        if not skill.analysis.bounded:
            raise RuntimeContractError("unbounded skill")
        for required in skill.required_tools:
            contract = self.tool_catalog.get(required.tool_id, required.version)
            if contract.contract_hash != required.contract_hash:
                raise RuntimeContractError(
                    f"tool contract mismatch: {required.tool_id}"
                )
            if required.capability != "READ":
                raise RuntimeContractError("WRITE capability is forbidden")

    def _bind_inputs_and_contexts(self, ctx: _ExecutionContext) -> None:
        for spec in ctx.skill.inputs:
            if spec.required and spec.name not in ctx.request.inputs:
                raise RuntimeContractError(f"missing required input: {spec.name}")
            value = ctx.request.inputs[spec.name]
            self._validate_runtime_type(value, spec.type_info, spec.name)
            ctx.bind(
                spec.symbol_id,
                ValueEnvelope.complete(value, spec.type_info, spec.classification),
            )
        for spec in ctx.skill.contexts:
            value: Any = ctx.request.runtime_context
            for part in spec.path:
                try:
                    value = value[part]
                except (KeyError, TypeError) as error:
                    raise RuntimeContractError(
                        f"missing runtime context path: {'.'.join(spec.path)}"
                    ) from error
            self._validate_runtime_type(value, spec.type_info, spec.name)
            ctx.bind(
                spec.symbol_id,
                ValueEnvelope.complete(value, spec.type_info, spec.classification),
            )

    def _validate_runtime_type(self, value: Any, type_info: TypeRef, name: str) -> None:
        valid = True
        if type_info.name in {"Year", "Int"}:
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif type_info.kind == "domain" or type_info.name == "String":
            valid = isinstance(value, str)
        elif type_info.name == "Bool":
            valid = isinstance(value, bool)
        if not valid:
            raise RuntimeContractError(f"runtime type mismatch for {name}")

    async def _execute_block(
        self,
        ctx: _ExecutionContext,
        statements: tuple[Statement, ...],
        tools: ToolExecutionPort,
    ) -> None:
        for statement in statements:
            ctx.audit.emit(
                "STATEMENT_STARTED",
                {"node_id": statement.node_id, "kind": type(statement).__name__},
            )
            if isinstance(statement, LetStatement):
                ctx.bind(statement.target_symbol_id, await self._evaluate(ctx, statement.value, tools))
            elif isinstance(statement, ForeachStatement):
                collection = await self._evaluate(ctx, statement.collection, tools)
                values = collection.value
                if len(values) > statement.max_iterations:
                    raise LimitExceeded(
                        f"foreach {statement.node_id} exceeds max {statement.max_iterations}"
                    )
                for value in values:
                    ctx.resources.loop_iterations += 1
                    if ctx.resources.loop_iterations > ctx.skill.limits.loop_iterations:
                        raise LimitExceeded("loop iteration limit exceeded")
                    ctx.frames.append({})
                    ctx.bind(
                        statement.iterator_symbol_id,
                        ValueEnvelope(
                            value,
                            collection.type_info.item,
                            Presence.PRESENT,
                            collection.completeness,
                            collection.classification,
                            collection.provenance_refs,
                        ),
                    )
                    await self._execute_block(ctx, statement.body, tools)
                    ctx.frames.pop()
            elif isinstance(statement, CheckStatement):
                predicate = await self._evaluate(ctx, statement.condition, tools)
                if predicate.type_info != BOOL:
                    raise RuntimeContractError("CHECK predicate is not Bool")
                if predicate.completeness == Completeness.COMPLETE:
                    status = CheckStatus.PASS if predicate.value else CheckStatus.FAIL
                else:
                    status = CheckStatus.UNKNOWN
                check = CheckResult(
                    statement.check_id,
                    status,
                    statement.severity,
                    statement.message,
                    predicate.completeness,
                    predicate.provenance_refs,
                )
                ctx.checks.append(check)
                ctx.bind(
                    statement.result_symbol_id,
                    ValueEnvelope.complete(
                        {"status": status.value},
                        CHECK_RESULT,
                        DataClassification.INTERNAL,
                        predicate.provenance_refs,
                    ),
                )
                ctx.audit.emit(
                    "CHECK_COMPLETED",
                    {
                        "node_id": statement.node_id,
                        "check_id": statement.check_id,
                        "status": status.value,
                        "completeness": predicate.completeness.value,
                        "provenance_refs": list(predicate.provenance_refs),
                    },
                )
            elif isinstance(statement, EmitStatement):
                if ctx.resources.emitted_rows >= ctx.skill.limits.emitted_rows:
                    raise LimitExceeded("emitted row limit exceeded")
                values: dict[str, Any] = {}
                classifications: dict[str, DataClassification] = {}
                maximum = DataClassification.PUBLIC
                for name, expression in statement.fields:
                    result = await self._evaluate(ctx, expression, tools)
                    values[name] = result.value
                    classifications[name] = result.classification
                    maximum = highest_classification(maximum, result.classification)
                ctx.outputs.append(EmitRecord(values, classifications))
                ctx.resources.emitted_rows += 1
                ctx.audit.emit(
                    "EMIT_COMPLETED",
                    {"node_id": statement.node_id, "values": values},
                    maximum,
                )
            else:
                raise TypeError(statement)
            ctx.audit.emit(
                "STATEMENT_COMPLETED",
                {"node_id": statement.node_id, "kind": type(statement).__name__},
            )

    async def _evaluate(
        self,
        ctx: _ExecutionContext,
        expression: Expression,
        tools: ToolExecutionPort,
    ) -> ValueEnvelope:
        if isinstance(expression, LiteralExpr):
            return ValueEnvelope.complete(
                expression.value, expression.type_info, DataClassification.PUBLIC
            )
        if isinstance(expression, SymbolRefExpr):
            return ctx.resolve(expression.symbol_id)
        if isinstance(expression, FieldExpr):
            source = await self._evaluate(ctx, expression.source, tools)
            value = source.value[expression.field]
            return ValueEnvelope(
                value,
                expression.type_info,
                Presence.PRESENT,
                source.completeness,
                source.classification,
                source.provenance_refs,
            )
        if isinstance(expression, ProjectionExpr):
            source = await self._evaluate(ctx, expression.source, tools)
            value = [item[expression.field] for item in source.value]
            return ValueEnvelope(
                value,
                expression.type_info,
                Presence.EMPTY if not value else Presence.PRESENT,
                source.completeness,
                source.classification,
                source.provenance_refs,
            )
        if isinstance(expression, CallExpr):
            argument = await self._evaluate(ctx, expression.arguments[0], tools)
            if expression.function != "sum":
                raise RuntimeContractError(
                    f"unsupported built-in: {expression.function}"
                )
            if expression.type_info.kind == "money":
                currency = expression.type_info.currency
                value = Money(Decimal("0"), currency)
                for item in argument.value:
                    value = value + item
            else:
                value = sum(argument.value)
            return ValueEnvelope(
                value,
                expression.type_info,
                Presence.PRESENT,
                argument.completeness,
                argument.classification,
                argument.provenance_refs,
            )
        if isinstance(expression, BinaryExpr):
            left = await self._evaluate(ctx, expression.left, tools)
            right = await self._evaluate(ctx, expression.right, tools)
            operations = {
                "ADD": lambda: left.value + right.value,
                "SUB": lambda: left.value - right.value,
                "MUL": lambda: left.value * right.value,
                "DIV": lambda: left.value / right.value,
                "LT": lambda: left.value < right.value,
                "LE": lambda: left.value <= right.value,
                "GT": lambda: left.value > right.value,
                "GE": lambda: left.value >= right.value,
                "EQ": lambda: left.value == right.value,
                "NE": lambda: left.value != right.value,
            }
            completeness = self._combine_completeness(
                left.completeness, right.completeness
            )
            return ValueEnvelope(
                operations[expression.operator](),
                expression.type_info,
                Presence.PRESENT,
                completeness,
                highest_classification(left.classification, right.classification),
                tuple(dict.fromkeys(left.provenance_refs + right.provenance_refs)),
            )
        if isinstance(expression, ReadExpr):
            required = next(
                item
                for item in ctx.skill.required_tools
                if item.tool_ref == expression.tool_ref
            )
            decision = self.authorizer.authorize(
                ctx.request.principal,
                f"tool:{required.tool_id}:execute",
                frozenset({required.required_scope}),
            )
            arguments = {
                name: await self._evaluate(ctx, value, tools)
                for name, value in expression.arguments
            }
            ctx.resources.tool_calls += 1
            if ctx.resources.tool_calls > ctx.skill.limits.tool_calls:
                raise LimitExceeded("tool call limit exceeded")
            ctx.invocation_counter += 1
            invocation_id = f"inv{ctx.invocation_counter:04d}"
            ctx.audit.emit(
                "TOOL_STARTED",
                {
                    "node_id": expression.node_id,
                    "invocation_id": invocation_id,
                    "tool_id": required.tool_id,
                    "argument_hash": value_hash(
                        {name: envelope.value for name, envelope in arguments.items()}
                    ),
                    "authorization_decision_ref": decision.decision_id,
                },
            )
            result = await tools.execute(
                ToolCallRequest(
                    execution_id=ctx.request.execution_id,
                    invocation_id=invocation_id,
                    node_id=expression.node_id,
                    tool_id=required.tool_id,
                    tool_version=required.version,
                    contract_hash=required.contract_hash,
                    arguments=arguments,
                    principal=ctx.request.principal,
                    authorization_decision_ref=decision.decision_id,
                )
            )
            if (
                result.completeness != Completeness.COMPLETE
                and not expression.result_policy.accept_partial
            ):
                # Preserve the partial value so strict CHECK semantics can yield UNKNOWN.
                pass
            if isinstance(result.value, list):
                ctx.resources.max_collection_size_seen = max(
                    ctx.resources.max_collection_size_seen, len(result.value)
                )
                if len(result.value) > ctx.skill.limits.collection_size:
                    raise LimitExceeded("collection size limit exceeded")
            provenance_ref = f"prov:{invocation_id}:{result.result_hash[7:19]}"
            ctx.audit.emit(
                "TOOL_COMPLETED",
                {
                    "node_id": expression.node_id,
                    "invocation_id": invocation_id,
                    "tool_id": required.tool_id,
                    "result_hash": result.result_hash,
                    "snapshot_ref": result.snapshot_ref,
                    "presence": result.presence.value,
                    "completeness": result.completeness.value,
                },
            )
            return result.to_value(provenance_ref)
        raise TypeError(expression)

    def _combine_completeness(
        self, left: Completeness, right: Completeness
    ) -> Completeness:
        if Completeness.UNKNOWN in {left, right}:
            return Completeness.UNKNOWN
        if Completeness.PARTIAL in {left, right}:
            return Completeness.PARTIAL
        return Completeness.COMPLETE

    def _result(
        self,
        ctx: _ExecutionContext,
        status: ExecutionStatus,
        error: RuntimeErrorInfo | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            execution_id=ctx.request.execution_id,
            skill_id=ctx.skill.skill_id,
            skill_version=ctx.skill.skill_version,
            semantic_hash=ctx.skill.semantic_hash,
            status=status,
            checks=tuple(ctx.checks),
            outputs=tuple(ctx.outputs),
            resources=ctx.usage(),
            error=error,
        )

    def _failed(
        self,
        ctx: _ExecutionContext,
        code: str | DiagnosticCode,
        category: str,
        message: str,
        status: ExecutionStatus = ExecutionStatus.FAILED,
        detail_code: str | None = None,
    ) -> ExecutionResult:
        normalized_code = str(code)
        ctx.audit.emit(
            "EXECUTION_FAILED",
            {
                "execution_id": ctx.request.execution_id,
                "status": status.value,
                "error_code": normalized_code,
                "category": category,
                "message": message,
                "detail_code": detail_code,
            },
        )
        return self._result(
            ctx,
            status,
            RuntimeErrorInfo(
                normalized_code,
                category,
                message,
                detail_code=detail_code,
            ),
        )
