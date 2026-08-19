from __future__ import annotations

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
    Statement,
    StaticAnalysis,
    SymbolRefExpr,
)


class UnboundedStructureError(ValueError):
    pass


class StaticBoundAnalyzer:
    def analyze(self, statements: tuple[Statement, ...]) -> StaticAnalysis:
        tool_calls, loop_iterations, emit_records = self._analyze_block(statements)
        return StaticAnalysis(
            max_tool_calls=tool_calls,
            max_loop_iterations=loop_iterations,
            max_emit_records=emit_records,
            bounded=True,
        )

    def _analyze_block(
        self, statements: tuple[Statement, ...]
    ) -> tuple[int, int, int]:
        tool_calls = 0
        loop_iterations = 0
        emit_records = 0
        for statement in statements:
            if isinstance(statement, LetStatement):
                tool_calls += self._expression_tool_calls(statement.value)
            elif isinstance(statement, ForeachStatement):
                if (
                    type(statement.max_iterations) is not int
                    or statement.max_iterations <= 0
                ):
                    raise UnboundedStructureError(
                        f"foreach {statement.node_id} requires a positive static max"
                    )
                collection_calls = self._expression_tool_calls(statement.collection)
                body = self._analyze_block(statement.body)
                tool_calls += collection_calls + statement.max_iterations * body[0]
                loop_iterations += statement.max_iterations * (1 + body[1])
                emit_records += statement.max_iterations * body[2]
            elif isinstance(statement, CheckStatement):
                tool_calls += self._expression_tool_calls(statement.condition)
            elif isinstance(statement, EmitStatement):
                emit_records += 1
                tool_calls += sum(
                    self._expression_tool_calls(value)
                    for _, value in statement.fields
                )
            else:
                raise UnboundedStructureError(
                    f"unsupported statement in static bound analysis: "
                    f"{type(statement).__name__}"
                )
        return tool_calls, loop_iterations, emit_records

    def _expression_tool_calls(self, expression: Expression) -> int:
        if isinstance(expression, ReadExpr):
            return 1 + sum(
                self._expression_tool_calls(value)
                for _, value in expression.arguments
            )
        if isinstance(expression, (FieldExpr, ProjectionExpr)):
            return self._expression_tool_calls(expression.source)
        if isinstance(expression, CallExpr):
            return sum(
                self._expression_tool_calls(item) for item in expression.arguments
            )
        if isinstance(expression, BinaryExpr):
            return self._expression_tool_calls(
                expression.left
            ) + self._expression_tool_calls(expression.right)
        if isinstance(expression, (LiteralExpr, SymbolRefExpr)):
            return 0
        raise UnboundedStructureError(
            f"unsupported expression in static bound analysis: "
            f"{type(expression).__name__}"
        )
