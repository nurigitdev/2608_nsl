from __future__ import annotations

import pytest

from nsl import CompileError, DiagnosticCode, DiagnosticPhase, SourceFile, SourceId
from nsl.syntax import (
    LanguageHeaderScanner,
    Lexer,
    SourceLanguageVersion,
    TokenKind,
)


def test_rd_003_header_scan_preserves_version_and_source_locations() -> None:
    source = SourceFile.from_text(
        "compiler/header.ns",
        '// leading\n  language // name\n NSL // version\n "0.2" // end\n ;\nskill',
        SourceId("source:header"),
    )

    header = LanguageHeaderScanner().scan(source)

    assert header.version is SourceLanguageVersion.V0_2
    assert header.span.source_id == source.source_id
    assert (header.span.start.line, header.span.start.column) == (2, 3)
    assert (header.span.end.line, header.span.end.column) == (5, 3)
    assert source.text[
        header.version_span.start.offset : header.version_span.end.offset
    ] == '"0.2"'


@pytest.mark.parametrize(
    ("source", "code", "message"),
    [
        ("", DiagnosticCode.LEX_LANGUAGE_HEADER_REQUIRED, "is required"),
        (
            'skill X {}\nlanguage NSL "0.1";',
            DiagnosticCode.LEX_NON_LEADING_LANGUAGE_HEADER,
            "first non-comment",
        ),
        (
            'language NSL 0.1;',
            DiagnosticCode.LEX_MALFORMED_LANGUAGE_HEADER,
            "expected '\"'",
        ),
        (
            'language NXL "0.1";',
            DiagnosticCode.LEX_MALFORMED_LANGUAGE_HEADER,
            "expected 'NSL'",
        ),
        (
            'language NSL "0.1;',
            DiagnosticCode.LEX_MALFORMED_LANGUAGE_HEADER,
            "unterminated version",
        ),
        (
            'language NSL "0.\\1";',
            DiagnosticCode.LEX_MALFORMED_LANGUAGE_HEADER,
            "version string",
        ),
        (
            'language NSL "0.\n1";',
            DiagnosticCode.LEX_MALFORMED_LANGUAGE_HEADER,
            "version string",
        ),
        (
            'language NSL "0.1"; language NSL "0.1";',
            DiagnosticCode.LEX_DUPLICATE_LANGUAGE_HEADER,
            "duplicate",
        ),
    ],
)
def test_rd_003_invalid_headers_fail_before_full_tokenization(
    source: str, code: DiagnosticCode, message: str
) -> None:
    with pytest.raises(CompileError, match=message) as captured:
        Lexer().tokenize_root(source + " @")

    assert captured.value.code == code
    assert captured.value.diagnostic.phase is DiagnosticPhase.LEXER


@pytest.mark.parametrize("version", ["", "0.0", "0.3", "1.0"])
def test_rd_003_unsupported_version_fails_at_the_header_boundary(
    version: str,
) -> None:
    source = f'language NSL "{version}"; @'

    with pytest.raises(CompileError) as captured:
        Lexer().tokenize_root(source)

    assert captured.value.code == DiagnosticCode.LEX_UNSUPPORTED_LANGUAGE_VERSION
    assert captured.value.location is not None
    assert (captured.value.location.line, captured.value.location.column) == (1, 14)
    assert version in captured.value.public_message


def test_rd_003_header_scan_enforces_exact_byte_boundary() -> None:
    declaration = 'language NSL "0.1";'
    at_limit = " " * (
        LanguageHeaderScanner.MAX_HEADER_BYTES - len(declaration.encode("utf-8"))
    ) + declaration

    assert LanguageHeaderScanner().scan(at_limit).version is SourceLanguageVersion.V0_1

    with pytest.raises(CompileError) as captured:
        LanguageHeaderScanner().scan(" " + at_limit)
    assert captured.value.code == DiagnosticCode.LEX_LANGUAGE_HEADER_LIMIT


@pytest.mark.parametrize(
    "tail",
    [
        " " * LanguageHeaderScanner.MAX_HEADER_BYTES + "skill",
        "//" + "x" * LanguageHeaderScanner.MAX_HEADER_BYTES,
    ],
)
def test_rd_003_valid_header_does_not_scan_an_unbounded_tail(tail: str) -> None:
    header = LanguageHeaderScanner().scan('language NSL "0.2";' + tail)

    assert header.version is SourceLanguageVersion.V0_2


@pytest.mark.parametrize("keyword", ["write", "approve", "approved_by"])
def test_lex_201_new_words_are_reserved_only_for_language_0_2(
    keyword: str,
) -> None:
    legacy, _ = Lexer().tokenize(keyword, SourceLanguageVersion.V0_1)
    current, _ = Lexer().tokenize(keyword, SourceLanguageVersion.V0_2)

    assert legacy.kind is TokenKind.IDENTIFIER
    assert current.kind is TokenKind.KEYWORD
    assert (current.lexeme, current.value) == (keyword, keyword)
    assert current.span.start.offset == 0
    assert current.span.end.offset == len(keyword)


def test_lex_201_keyword_matching_is_ascii_case_sensitive_and_exact() -> None:
    tokens = Lexer().tokenize(
        "Write writer approve_ approved_by2",
        SourceLanguageVersion.V0_2,
    )

    assert all(token.kind is TokenKind.IDENTIFIER for token in tokens[:-1])


def test_lex_201_root_header_selects_the_keyword_table() -> None:
    legacy = Lexer().tokenize_root('language NSL "0.1"; write')
    current = Lexer().tokenize_root('language NSL "0.2"; write')

    assert legacy[-2].kind is TokenKind.IDENTIFIER
    assert current[-2].kind is TokenKind.KEYWORD


@pytest.mark.parametrize(
    "version", [SourceLanguageVersion.V0_1, SourceLanguageVersion.V0_2]
)
def test_lex_202_include_and_bool_tokens_are_preserved(
    version: SourceLanguageVersion,
) -> None:
    include, true, false, include_suffix, title_true, title_false, eof = (
        Lexer().tokenize(
            "include true false include_ True False",
            version,
        )
    )

    assert (include.kind, include.lexeme, include.value) == (
        TokenKind.KEYWORD,
        "include",
        "include",
    )
    assert (true.kind, true.lexeme, true.value) == (
        TokenKind.BOOLEAN,
        "true",
        True,
    )
    assert (false.kind, false.lexeme, false.value) == (
        TokenKind.BOOLEAN,
        "false",
        False,
    )
    assert all(
        token.kind is TokenKind.IDENTIFIER
        for token in (include_suffix, title_true, title_false)
    )
    assert eof.kind is TokenKind.EOF


@pytest.mark.parametrize(
    "version", [SourceLanguageVersion.V0_1, SourceLanguageVersion.V0_2]
)
def test_lex_203_identifier_decimal_and_money_tokens_are_preserved(
    version: SourceLanguageVersion,
) -> None:
    tokens = Lexer().tokenize(
        "_value value2 0 0.0 1234567890.0001 Money<KRW>",
        version,
    )

    assert tuple((token.kind, token.lexeme, token.value) for token in tokens) == (
        (TokenKind.IDENTIFIER, "_value", "_value"),
        (TokenKind.IDENTIFIER, "value2", "value2"),
        (TokenKind.INTEGER, "0", "0"),
        (TokenKind.DECIMAL, "0.0", "0.0"),
        (TokenKind.DECIMAL, "1234567890.0001", "1234567890.0001"),
        (TokenKind.IDENTIFIER, "Money", "Money"),
        (TokenKind.LT, "<", "<"),
        (TokenKind.IDENTIFIER, "KRW", "KRW"),
        (TokenKind.GT, ">", ">"),
        (TokenKind.EOF, "", "<eof>"),
    )
    assert tokens[4].span.end.offset - tokens[4].span.start.offset == len(
        tokens[4].lexeme
    )


@pytest.mark.parametrize(
    "version", [SourceLanguageVersion.V0_1, SourceLanguageVersion.V0_2]
)
def test_lex_203_incomplete_decimal_forms_keep_legacy_token_boundaries(
    version: SourceLanguageVersion,
) -> None:
    integer, trailing_dot, leading_dot, fraction, eof = Lexer().tokenize(
        "1. .5",
        version,
    )

    assert (integer.kind, integer.value) == (TokenKind.INTEGER, "1")
    assert trailing_dot.kind is TokenKind.DOT
    assert leading_dot.kind is TokenKind.DOT
    assert (fraction.kind, fraction.value) == (TokenKind.INTEGER, "5")
    assert eof.kind is TokenKind.EOF


@pytest.mark.parametrize(
    "version", [SourceLanguageVersion.V0_1, SourceLanguageVersion.V0_2]
)
def test_lex_203_worst_case_numeric_token_is_not_truncated(
    version: SourceLanguageVersion,
) -> None:
    literal = "9" * 4_096 + "." + "0" * 4_096

    decimal, eof = Lexer().tokenize(literal, version)

    assert decimal.kind is TokenKind.DECIMAL
    assert decimal.lexeme == decimal.value == literal
    assert decimal.span.end.offset == len(literal)
    assert eof.span.start.offset == len(literal)


@pytest.mark.parametrize(
    "version", [SourceLanguageVersion.V0_1, SourceLanguageVersion.V0_2]
)
def test_lex_203_non_ascii_identifier_still_fails_closed(
    version: SourceLanguageVersion,
) -> None:
    with pytest.raises(CompileError) as captured:
        Lexer().tokenize("한글", version)

    assert captured.value.code == DiagnosticCode.LEX_UNEXPECTED_CHARACTER
    assert captured.value.location is not None
    assert (captured.value.location.line, captured.value.location.column) == (1, 1)


def test_lexer_rejects_an_unrecognized_explicit_version() -> None:
    with pytest.raises(ValueError, match="unsupported lexer language version"):
        Lexer().tokenize("write", "9.9")
