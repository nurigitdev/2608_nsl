from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from nsl import CompileError, DiagnosticCode, NslCompiler, SourceFile, SourceId
from nsl.compiler import ApprovalBindingAnalyzer
from nsl.core import DataClassification, INT
from nsl.symbols import SymbolNamespace, SymbolTable
from nsl.syntax import AstSkill, Lexer, Parser
from nsl.tools import ToolContractCatalog


def _source(body: str, *, language_version: str = "0.2") -> str:
    risk = "WRITE" if language_version == "0.2" else "READ_ONLY"
    return f'''language NSL "{language_version}";
skill FINANCE.APPROVAL_BINDING {{
    version "1.0.0";
    risk {risk};
    limits {{
        tool_calls 2;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }}
    input {{
        amount: Int;
        items: List<Int>;
    }}
    output {{
        result: Int;
    }}
{body}
}}
'''


def _parse(body: str, *, language_version: str = "0.2") -> tuple[SourceFile, AstSkill]:
    source = SourceFile.from_text(
        "compiler/approval_binding.ns",
        _source(body, language_version=language_version),
        SourceId("source:approval-binding"),
    )
    ast = Parser(Lexer().tokenize_root(source), source).parse()
    assert isinstance(ast, AstSkill)
    return source, ast


def _analyze(body: str):
    source, ast = _parse(body)
    return ApprovalBindingAnalyzer((source,)).analyze(ast)


def _write(name: str, approval_id: str) -> str:
    return (
        f"    let {name} = write FINANCE.CREATE_DRAFT("
        f"amount = amount) approved_by {approval_id};"
    )


def test_sem_201_approval_namespace_and_analysis_are_explicit_and_immutable() -> None:
    analysis = _analyze(
        '''    approve CREATE_DRAFT {
        message "Create draft?";
    }
'''
        + _write("draft", "CREATE_DRAFT")
    )

    assert SymbolNamespace.APPROVAL.value == "APPROVAL"
    assert len(analysis.approvals) == len(analysis.bindings) == 1
    assert analysis.approvals[0].approval_id == "CREATE_DRAFT"
    assert analysis.approvals[0].symbol_id == "s0001"
    assert analysis.bindings[0].approval is analysis.approvals[0]
    assert analysis.bindings[0].inside_foreach is False
    with pytest.raises(FrozenInstanceError):
        analysis.approvals[0].approval_id = "CHANGED"  # type: ignore[misc]


def test_sem_201_symbol_table_resolves_only_the_requested_namespace() -> None:
    table = SymbolTable(())
    value = table.declare(
        "CREATE_DRAFT",
        SymbolNamespace.VALUE,
        "VARIABLE",
        INT,
        DataClassification.INTERNAL,
        None,
    )
    assert table.resolve("CREATE_DRAFT", None) is value
    with pytest.raises(CompileError) as captured:
        table.resolve_in_namespace(
            "CREATE_DRAFT",
            SymbolNamespace.APPROVAL,
            None,
            code=DiagnosticCode.SEM_UNKNOWN_APPROVAL,
        )
    assert captured.value.code == DiagnosticCode.SEM_UNKNOWN_APPROVAL


def test_sem_202_duplicate_approval_id_is_rejected_at_second_declaration() -> None:
    body = '''    approve CREATE_DRAFT {
        message "First";
    }
    approve CREATE_DRAFT {
        message "Second";
    }
''' + _write("draft", "CREATE_DRAFT")

    source_text = _source(body)
    declaration_lines = [
        line_number
        for line_number, line in enumerate(source_text.splitlines(), start=1)
        if line.strip() == "approve CREATE_DRAFT {"
    ]
    with pytest.raises(CompileError) as captured:
        _analyze(body)

    assert captured.value.code == DiagnosticCode.SEM_DUPLICATE_SYMBOL
    assert captured.value.location is not None
    assert captured.value.location.line == declaration_lines[1]
    assert "duplicate approval ID" in captured.value.public_message


def test_sem_203_approved_by_must_resolve_an_approval_symbol() -> None:
    with pytest.raises(CompileError) as captured:
        _analyze(_write("draft", "MISSING"))

    assert captured.value.code == DiagnosticCode.SEM_UNKNOWN_APPROVAL
    assert captured.value.logical_path == "compiler/approval_binding.ns"
    assert captured.value.snippet is not None
    assert "approved_by MISSING" in captured.value.snippet


def test_sem_203_compiler_pipeline_runs_approval_resolution_before_lowering() -> None:
    source = SourceFile.from_text(
        "compiler/integrated_approval.ns",
        _source(_write("draft", "MISSING")),
    )
    with pytest.raises(CompileError) as captured:
        NslCompiler(ToolContractCatalog(())).compile(source)
    assert captured.value.code == DiagnosticCode.SEM_UNKNOWN_APPROVAL


def test_sem_204_approval_must_precede_the_linked_write() -> None:
    body = _write("draft", "CREATE_DRAFT") + '''
    approve CREATE_DRAFT {
        message "Too late";
    }
'''
    with pytest.raises(CompileError) as captured:
        _analyze(body)

    assert captured.value.code == DiagnosticCode.SEM_APPROVAL_ORDER
    assert "declared before WRITE" in captured.value.public_message


def test_sem_205_and_apr_001_approval_cannot_be_shared_by_writes() -> None:
    body = '''    approve CREATE_DRAFT {
        message "Create draft?";
    }
''' + _write("first", "CREATE_DRAFT") + "\n" + _write("second", "CREATE_DRAFT")
    with pytest.raises(CompileError) as captured:
        _analyze(body)

    assert captured.value.code == DiagnosticCode.SEM_APPROVAL_WRITE_NOT_ONE_TO_ONE
    assert "exactly one WRITE" in captured.value.public_message


def test_sem_206_unused_approval_is_rejected() -> None:
    body = '''    approve USED {
        message "Used";
    }
    approve UNUSED {
        message "Unused";
    }
''' + _write("draft", "USED")
    with pytest.raises(CompileError) as captured:
        _analyze(body)

    assert captured.value.code == DiagnosticCode.SEM_UNUSED_APPROVAL
    assert "UNUSED" in captured.value.public_message


def test_sem_201_approval_declaration_inside_foreach_is_rejected() -> None:
    body = '''    foreach item in items max 1 {
        approve NESTED {
            message "Nested";
        }
    }
''' + _write("draft", "NESTED")
    with pytest.raises(CompileError) as captured:
        _analyze(body)

    assert captured.value.code == DiagnosticCode.SEM_APPROVAL_SCOPE
    assert "skill scope" in captured.value.public_message


def test_v0_1_analysis_remains_empty_and_does_not_change_symbol_behavior() -> None:
    source, ast = _parse("", language_version="0.1")
    analysis = ApprovalBindingAnalyzer((source,)).analyze(ast)
    assert analysis.approvals == ()
    assert analysis.bindings == ()
