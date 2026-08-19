from __future__ import annotations

from .diagnostics import (
    CompileError,
    DiagnosticCode,
    DiagnosticPhase,
    SourceLocation,
    compile_error,
)
from .source import SourceFile, SourceSpan


class SourceDiagnosticContext:
    def __init__(self, sources: tuple[SourceFile, ...]) -> None:
        self._sources = {source.source_id: source for source in sources}

    def error(
        self,
        code: DiagnosticCode,
        message: str,
        span: SourceSpan | None,
        phase: DiagnosticPhase = DiagnosticPhase.SEMANTIC,
    ) -> CompileError:
        if span is None:
            return compile_error(code, phase, message)
        source = self._sources.get(span.source_id)
        if source is None:
            return compile_error(code, phase, message)
        start = span.start
        lines = source.text.splitlines()
        snippet = lines[start.line - 1] if start.line <= len(lines) else None
        return compile_error(
            code,
            phase,
            message,
            SourceLocation(start.line, start.column),
            snippet,
            source.logical_path,
        )
