from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .core import DataClassification, TypeRef
from .diagnostics import (
    CompileError,
    DiagnosticCode,
)
from .semantic_diagnostics import SourceDiagnosticContext
from .source import SourceFile, SourceSpan


class SymbolNamespace(StrEnum):
    VALUE = "VALUE"
    CHECK = "CHECK"
    APPROVAL = "APPROVAL"


class ScopeKind(StrEnum):
    SKILL = "SKILL"
    FOREACH = "FOREACH"


@dataclass(frozen=True, slots=True)
class SymbolBinding:
    symbol_id: str
    name: str
    namespace: SymbolNamespace
    category: str
    type_info: TypeRef
    classification: DataClassification
    scope_id: str
    declaration_span: SourceSpan | None


@dataclass(slots=True)
class _ScopeFrame:
    scope_id: str
    kind: ScopeKind
    bindings: dict[str, SymbolBinding] = field(default_factory=dict)


class SymbolTable:
    def __init__(self, sources: tuple[SourceFile, ...]) -> None:
        self._diagnostics = SourceDiagnosticContext(sources)
        self._scope_counter = 1
        self._symbol_counter = 0
        self._frames = [_ScopeFrame("scope0001", ScopeKind.SKILL)]
        self._bindings: list[SymbolBinding] = []

    @property
    def bindings(self) -> tuple[SymbolBinding, ...]:
        return tuple(self._bindings)

    @property
    def current_scope_id(self) -> str:
        return self._frames[-1].scope_id

    def enter_scope(self, kind: ScopeKind) -> str:
        self._scope_counter += 1
        scope_id = f"scope{self._scope_counter:04d}"
        self._frames.append(_ScopeFrame(scope_id, kind))
        return scope_id

    def leave_scope(self, scope_id: str) -> None:
        if len(self._frames) == 1:
            raise ValueError("cannot leave the root symbol scope")
        if self._frames[-1].scope_id != scope_id:
            raise ValueError("symbol scopes must be left in stack order")
        self._frames.pop()

    def declare(
        self,
        name: str,
        namespace: SymbolNamespace,
        category: str,
        type_info: TypeRef,
        classification: DataClassification,
        span: SourceSpan | None,
    ) -> SymbolBinding:
        if any(name in frame.bindings for frame in self._frames):
            raise self._diagnostics.error(
                DiagnosticCode.SEM_DUPLICATE_SYMBOL,
                f"duplicate symbol or shadowing is forbidden: {name}",
                span,
            )
        self._symbol_counter += 1
        binding = SymbolBinding(
            f"s{self._symbol_counter:04d}",
            name,
            namespace,
            category,
            type_info,
            classification,
            self.current_scope_id,
            span,
        )
        self._frames[-1].bindings[name] = binding
        self._bindings.append(binding)
        return binding

    def resolve(self, name: str, span: SourceSpan | None) -> SymbolBinding:
        return self._resolve(
            name,
            span,
            namespace=None,
            code=DiagnosticCode.SEM_UNKNOWN_IDENTIFIER,
            message=f"unknown identifier: {name}",
        )

    def resolve_in_namespace(
        self,
        name: str,
        namespace: SymbolNamespace,
        span: SourceSpan | None,
        *,
        code: DiagnosticCode = DiagnosticCode.SEM_UNKNOWN_IDENTIFIER,
        message: str | None = None,
    ) -> SymbolBinding:
        return self._resolve(
            name,
            span,
            namespace=namespace,
            code=code,
            message=message or f"unknown {namespace.value.lower()} symbol: {name}",
        )

    def _resolve(
        self,
        name: str,
        span: SourceSpan | None,
        *,
        namespace: SymbolNamespace | None,
        code: DiagnosticCode,
        message: str,
    ) -> SymbolBinding:
        for frame in reversed(self._frames):
            binding = frame.bindings.get(name)
            if binding is not None and (
                namespace is None or binding.namespace is namespace
            ):
                return binding
        raise self._diagnostics.error(
            code,
            message,
            span,
        )
