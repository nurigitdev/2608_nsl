from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from nsl import CompileError, DiagnosticCode, SourceFile, SourceId
from nsl.syntax import (
    AstApprove,
    AstLet,
    AstPath,
    AstSkill,
    AstWrite,
    Lexer,
    Parser,
)


SOURCE = '''language NSL "0.2";
skill FINANCE.CREATE_VOUCHER {
    version "1.0.0";
    risk WRITE;
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }
    input {
        account_code: String;
        amount: Decimal;
    }
    output {
        result: String;
    }

    approve VOUCHER_CREATE {
        message "Create the voucher draft?";
    }

    let voucher = write FINANCE.CREATE_VOUCHER_DRAFT(
        account_code = account_code,
        amount = amount
    ) approved_by VOUCHER_CREATE;
}
'''


def _parse(source_text: str = SOURCE) -> tuple[SourceFile, AstSkill]:
    source = SourceFile.from_text(
        "compiler/write_parser.ns",
        source_text,
        SourceId("source:write-parser"),
    )
    ast = Parser(Lexer().tokenize_root(source), source).parse()
    assert isinstance(ast, AstSkill)
    return source, ast


def _write(ast: AstSkill) -> AstWrite:
    statement = ast.body[-1]
    assert isinstance(statement, AstLet)
    assert isinstance(statement.value, AstWrite)
    return statement.value


def test_par_201_write_expression_ast_preserves_tool_and_arguments() -> None:
    _, ast = _parse()
    write = _write(ast)

    assert write.tool_id == "FINANCE.CREATE_VOUCHER_DRAFT"
    assert tuple(name for name, _ in write.arguments) == (
        "account_code",
        "amount",
    )
    assert all(isinstance(value, AstPath) for _, value in write.arguments)
    with pytest.raises(FrozenInstanceError):
        write.tool_id = "OTHER"  # type: ignore[misc]


def test_par_201_write_expression_accepts_empty_argument_list() -> None:
    source_text = SOURCE.replace(
        "(\n        account_code = account_code,\n        amount = amount\n    )",
        "()",
    )

    _, ast = _parse(source_text)

    assert _write(ast).arguments == ()


def test_par_202_approve_statement_ast_is_immutable() -> None:
    _, ast = _parse()
    approval = ast.body[0]

    assert isinstance(approval, AstApprove)
    assert approval.approval_id == "VOUCHER_CREATE"
    assert approval.message == "Create the voucher draft?"
    with pytest.raises(FrozenInstanceError):
        approval.message = "changed"  # type: ignore[misc]


def test_par_203_write_preserves_the_exact_approval_reference() -> None:
    _, ast = _parse()
    write = _write(ast)

    assert write.approval_id == "VOUCHER_CREATE"
    assert write.approval_span is not None

    missing_reference = SOURCE.replace(
        ") approved_by VOUCHER_CREATE;",
        ");",
    )
    with pytest.raises(CompileError, match="expected 'approved_by'"):
        _parse(missing_reference)


def test_par_204_new_ast_nodes_retain_complete_source_spans() -> None:
    source, ast = _parse()
    approval = ast.body[0]
    write = _write(ast)
    assert isinstance(approval, AstApprove)
    assert approval.span is not None
    assert approval.approval_id_span is not None
    assert write.span is not None
    assert write.approval_span is not None

    approval_text = source.text[
        approval.span.start.offset : approval.span.end.offset
    ]
    write_text = source.text[write.span.start.offset : write.span.end.offset]
    approval_id_text = source.text[
        approval.approval_id_span.start.offset : approval.approval_id_span.end.offset
    ]
    write_ref_text = source.text[
        write.approval_span.start.offset : write.approval_span.end.offset
    ]

    assert approval_text.startswith("approve VOUCHER_CREATE {")
    assert approval_text.endswith("}")
    assert write_text.startswith("write FINANCE.CREATE_VOUCHER_DRAFT(")
    assert write_text.endswith("approved_by VOUCHER_CREATE")
    assert approval_id_text == write_ref_text == "VOUCHER_CREATE"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "approve VOUCHER_CREATE {",
            "approve {",
            "expected IDENTIFIER",
        ),
        (
            'message "Create the voucher draft?";',
            'unknown "Create the voucher draft?";',
            "expected 'message'",
        ),
        (
            "account_code = account_code",
            "account_code: account_code",
            "expected '='",
        ),
        (
            ") approved_by VOUCHER_CREATE;",
            ") approved_by;",
            "expected IDENTIFIER",
        ),
    ],
)
def test_parser_rejects_malformed_approval_and_write_forms(
    old: str, new: str, message: str
) -> None:
    with pytest.raises(CompileError, match=message):
        _parse(SOURCE.replace(old, new))


def test_v0_1_keeps_new_v0_2_words_available_as_identifiers() -> None:
    source_text = '''language NSL "0.1";
skill TEST.LEGACY_IDENTIFIERS {
    version "1.0.0";
    risk READ_ONLY;
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }
    input {
        write: Int;
        approve: Int;
        approved_by: Int;
    }
}
'''

    _, ast = _parse(source_text)

    assert tuple(item.name for item in ast.inputs) == (
        "write",
        "approve",
        "approved_by",
    )
