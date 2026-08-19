from __future__ import annotations

from .builtins import (
    BuiltinSignature,
    BuiltinSignatureError,
    UnknownBuiltinError,
    V0_1_BUILTINS,
)
from .core import BOOL, DECIMAL, INT, TypeRef, list_type
from .diagnostics import DiagnosticCode
from .semantic_diagnostics import SourceDiagnosticContext
from .source import SourceFile, SourceSpan


class StaticTypeChecker:
    def __init__(self, sources: tuple[SourceFile, ...]) -> None:
        self._diagnostics = SourceDiagnosticContext(sources)

    def require_exact(
        self,
        actual: TypeRef,
        expected: TypeRef,
        code: DiagnosticCode,
        message: str,
        span: SourceSpan | None,
    ) -> None:
        if actual != expected:
            raise self._diagnostics.error(code, message, span)

    def require_bool(self, actual: TypeRef, span: SourceSpan | None) -> None:
        self.require_exact(
            actual,
            BOOL,
            DiagnosticCode.SEM_CHECK_CONDITION_TYPE,
            "CHECK assert must have Bool type",
            span,
        )

    def require_list(
        self,
        actual: TypeRef,
        code: DiagnosticCode,
        message: str,
        span: SourceSpan | None,
    ) -> TypeRef:
        if actual.kind != "list" or actual.item is None:
            raise self._diagnostics.error(code, message, span)
        return actual.item

    def field_result(
        self,
        source_type: TypeRef,
        field: str,
        span: SourceSpan | None,
    ) -> tuple[TypeRef, bool]:
        try:
            if source_type.kind == "list" and source_type.item is not None:
                return list_type(source_type.item.field(field)), True
            if source_type.kind == "record":
                return source_type.field(field), False
        except KeyError:
            pass
        raise self._diagnostics.error(
            DiagnosticCode.SEM_UNKNOWN_FIELD,
            f"type {source_type.kind} has no field {field}",
            span,
        )

    def sum_result(
        self, argument_type: TypeRef, span: SourceSpan | None
    ) -> TypeRef:
        return self.builtin_result("sum", (argument_type,), span).result_type

    def builtin_result(
        self,
        name: str,
        argument_types: tuple[TypeRef, ...],
        span: SourceSpan | None,
    ) -> BuiltinSignature:
        try:
            return V0_1_BUILTINS.resolve(name, argument_types)
        except UnknownBuiltinError as error:
            raise self._diagnostics.error(
                DiagnosticCode.SEM_UNSUPPORTED_BUILTIN,
                str(error),
                span,
            ) from error
        except BuiltinSignatureError as error:
            code = (
                DiagnosticCode.SEM_SUM_ARGUMENT_TYPE
                if name == "sum"
                else DiagnosticCode.SEM_UNSUPPORTED_BUILTIN
            )
            raise self._diagnostics.error(code, str(error), span) from error

    def binary_result(
        self,
        operator: str,
        left: TypeRef,
        right: TypeRef,
        span: SourceSpan | None,
    ) -> TypeRef:
        self.require_exact(
            left,
            right,
            DiagnosticCode.SEM_BINARY_TYPE,
            f"binary type mismatch for {operator}",
            span,
        )
        if operator in {"==", "!="}:
            return BOOL
        if operator in {"<", "<=", ">", ">="}:
            if left in {INT, DECIMAL} or left.kind == "money":
                return BOOL
        elif operator in {"+", "-", "*", "/"}:
            if left == INT:
                return DECIMAL if operator == "/" else INT
            if left == DECIMAL:
                return DECIMAL
            if left.kind == "money" and operator in {"+", "-"}:
                return left
        raise self._diagnostics.error(
            DiagnosticCode.SEM_BINARY_TYPE,
            f"operator {operator} is not defined for {left.name or left.kind}",
            span,
        )
