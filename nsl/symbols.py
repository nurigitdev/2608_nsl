from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .core import DataClassification, TypeRef
from .diagnostics import (
    CompileError,
    DiagnosticCode,
    DiagnosticPhase,
    SourceLocation,
    compile_error,
)
from .source import SourceFile, SourceSpan


class SymbolNamespace(StrEnum):
    VALUE = "VALUE"
    CHECK = "CHECK"


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
        self._sources = {source.source_id: source for source in sources}
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
            raise self._error(
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
        for frame in reversed(self._frames):
            binding = frame.bindings.get(name)
            if binding is not None:
                return binding
        raise self._error(
            DiagnosticCode.SEM_UNKNOWN_IDENTIFIER,
            f"unknown identifier: {name}",
            span,
        )

    def _error(
        self,
        code: DiagnosticCode,
        message: str,
        span: SourceSpan | None,
    ) -> CompileError:
        if span is None:
            return compile_error(code, DiagnosticPhase.SEMANTIC, message)
        source = self._sources.get(span.source_id)
        if source is None:
            return compile_error(code, DiagnosticPhase.SEMANTIC, message)
        start = span.start
        lines = source.text.splitlines()
        snippet = lines[start.line - 1] if start.line <= len(lines) else None
        return compile_error(
            code,
            DiagnosticPhase.SEMANTIC,
            message,
            SourceLocation(start.line, start.column),
            snippet,
            source.logical_path,
        )
