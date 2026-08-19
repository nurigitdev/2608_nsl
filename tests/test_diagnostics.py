from __future__ import annotations

import asyncio
import ast
import re
from pathlib import Path

import pytest

from nsl import CompileError, DiagnosticCode, DiagnosticPhase, NslCompiler, SourceLocation
from nsl.audit import InMemoryAuditSink
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.security import DataHandlingPolicy
from nsl.syntax import Lexer
from nsl.vertical_slice import build_principal, build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")


class _ExplodingToolExecutor:
    async def execute(self, request):
        raise RuntimeError("Traceback: secret internal failure")


def test_err_001_all_compile_errors_have_unique_stable_codes() -> None:
    codes = [item.value for item in DiagnosticCode]
    assert len(codes) == len(set(codes))
    assert all(re.fullmatch(r"NSL-E[1-9]\d{3}", code) for code in codes)

    for relative_path in ("nsl/syntax.py", "nsl/compiler.py"):
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        direct_constructions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CompileError"
        ]
        assert direct_constructions == []

    with pytest.raises(CompileError) as captured:
        Lexer().tokenize("@")
    assert captured.value.code == DiagnosticCode.LEX_UNEXPECTED_CHARACTER
    assert captured.value.diagnostic.phase is DiagnosticPhase.LEXER
    assert str(captured.value).startswith("[NSL-E1003]")


def test_err_002_source_errors_expose_structured_line_and_column() -> None:
    assert SourceLocation(1, 1) == SourceLocation(line=1, column=1)
    with pytest.raises(ValueError, match="positive"):
        SourceLocation(0, 1)
    with pytest.raises(ValueError, match="positive"):
        SourceLocation(1, 0)

    with pytest.raises(CompileError) as captured:
        Lexer().tokenize("\n  @")
    assert captured.value.location == SourceLocation(2, 3)
    assert "at 2:3" in str(captured.value)


def test_err_003_source_snippet_is_included_when_available() -> None:
    with pytest.raises(CompileError) as captured:
        Lexer().tokenize("valid\n  @ trailing")
    assert captured.value.snippet == "  @ trailing"
    assert str(captured.value).endswith("\n  @ trailing")


def test_err_004_public_message_is_separate_from_internal_exception() -> None:
    source = SOURCE.replace(
        'version "1.0.0";\n    }', 'version "9.0.0";\n    }', 1
    )
    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    error = captured.value
    assert error.public_message.startswith("incompatible tool version")
    assert isinstance(error.__cause__, KeyError)
    assert repr(error.__cause__) not in error.public_message
    assert not hasattr(error.diagnostic, "internal_exception")


def test_err_005_stack_trace_is_not_exposed_to_normal_users() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    request = ExecutionRequest(
        execution_id="exec-safe-error",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
        data_policy=DataHandlingPolicy(),
    )
    audit = InMemoryAuditSink()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(skill, request, _ExplodingToolExecutor(), audit)
    )

    assert result.error is not None
    assert result.error.message == "An unexpected runtime error occurred."
    exposed = repr(result) + repr(audit.events)
    assert "Traceback" not in exposed
    assert "secret internal failure" not in exposed


def test_err_006_debug_mode_can_send_detailed_trace_to_protected_sink() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    request = ExecutionRequest(
        execution_id="exec-debug-error",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
        data_policy=DataHandlingPolicy(),
    )
    traces: list[str] = []

    result = asyncio.run(
        RuntimeEngine(
            catalog, debug_mode=True, debug_trace_sink=traces.append
        ).execute(
            skill,
            request,
            _ExplodingToolExecutor(),
            InMemoryAuditSink(),
        )
    )

    assert result.error is not None
    assert result.error.message == "An unexpected runtime error occurred."
    assert len(traces) == 1
    assert "Traceback (most recent call last)" in traces[0]
    assert "RuntimeError: Traceback: secret internal failure" in traces[0]

    def broken_sink(trace: str) -> None:
        raise OSError("debug storage unavailable")

    safe_result = asyncio.run(
        RuntimeEngine(
            catalog, debug_mode=True, debug_trace_sink=broken_sink
        ).execute(
            skill,
            request,
            _ExplodingToolExecutor(),
            InMemoryAuditSink(),
        )
    )
    assert safe_result.error is not None
    assert safe_result.error.message == "An unexpected runtime error occurred."
