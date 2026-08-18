from __future__ import annotations

from pathlib import Path

import pytest

from nsl import (
    CompileError,
    DiagnosticCode,
    DiagnosticPhase,
    NslCompiler,
    SourceFile,
    SourceId,
    SourcePosition,
    SourceSpan,
)
from nsl.core import BOOL
from nsl.syntax import (
    AstLiteral,
    AstPath,
    LexResult,
    Lexer,
    Parser,
    TokenCategory,
    TokenKind,
)
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE_TEXT = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def test_src_001_source_file_requires_ns_extension() -> None:
    source = SourceFile.from_text("finance/project_budget_check.ns", SOURCE_TEXT)
    assert source.logical_path.endswith(".ns")
    assert source.source_id.startswith("source:")

    explicit = SourceFile.from_text(
        "finance/project_budget_check.ns",
        SOURCE_TEXT,
        SourceId("source:explicit"),
    )
    assert explicit.source_id == "source:explicit"

    for invalid_path in ("finance/project_budget_check", "finance/skill.nsl", ".NS"):
        with pytest.raises(ValueError, match=r"\.ns"):
            SourceFile.from_text(invalid_path, SOURCE_TEXT)
    with pytest.raises(ValueError, match="source_id"):
        SourceFile(SourceId(""), "valid.ns", "")

    compiled = NslCompiler(build_tool_catalog()).compile(source)
    assert compiled.skill.skill_id == "FINANCE.PROJECT_BUDGET_CHECK"


def test_src_002_source_file_supports_strict_utf8() -> None:
    empty = SourceFile.from_bytes("empty.ns", b"")
    assert empty.text == ""

    korean = SourceFile.from_bytes(
        "korean.ns", b"\xef\xbb\xbf" + "예산 점검".encode("utf-8")
    )
    assert korean.text == "예산 점검"
    assert korean.encoding == "utf-8"

    with pytest.raises(ValueError, match="valid UTF-8"):
        SourceFile.from_bytes("invalid.ns", b"\xff")
    with pytest.raises(ValueError, match="encoding"):
        SourceFile(SourceId("source:latin"), "latin.ns", "text", "latin-1")


def test_src_003_korean_string_literal_round_trips() -> None:
    message = "예산 초과 여부를 확인했습니다."
    token = Lexer().tokenize(f'"{message}"')[0]
    assert token.value == message

    source = SourceFile.from_text(
        "finance/korean_message.ns",
        SOURCE_TEXT.replace(
            "자 프로젝트 지출 합계가 모 프로젝트 예산을 초과했습니다.",
            message,
        ),
    )
    compiled = NslCompiler(build_tool_catalog()).compile(source)
    check_statement = compiled.skill.body[1].body[3]
    assert check_statement.message == message


def test_src_004_source_positions_track_offset_line_and_column() -> None:
    start = SourcePosition(offset=0, line=1, column=1)
    assert SourceSpan(SourceId("source:test"), start, start).start == start
    with pytest.raises(ValueError, match="offset"):
        SourcePosition(offset=-1, line=1, column=1)
    with pytest.raises(ValueError, match="line and column"):
        SourcePosition(offset=0, line=0, column=1)
    with pytest.raises(ValueError, match="source_id"):
        SourceSpan(SourceId(""), start, start)
    with pytest.raises(ValueError, match="precede"):
        SourceSpan(
            SourceId("source:test"),
            SourcePosition(2, 1, 3),
            SourcePosition(1, 1, 2),
        )

    source = SourceFile.from_text(
        "positions.ns", "language\n  skill", SourceId("source:positions")
    )
    language, skill, eof = Lexer().tokenize(source)
    assert language.span == SourceSpan(
        source.source_id,
        SourcePosition(0, 1, 1),
        SourcePosition(8, 1, 9),
    )
    assert (skill.line, skill.column) == (2, 3)
    assert skill.span.start.offset == 11
    assert skill.span.end.offset == 16
    assert eof.span.start == eof.span.end == SourcePosition(16, 2, 8)


def test_src_005_skill_id_and_version_are_declared_in_source() -> None:
    compiled = NslCompiler(build_tool_catalog()).compile(SOURCE_TEXT)
    assert compiled.skill.skill_id == "FINANCE.PROJECT_BUDGET_CHECK"
    assert compiled.skill.skill_version == "1.0.0"

    missing_id = SOURCE_TEXT.replace(
        "skill FINANCE.PROJECT_BUDGET_CHECK", "skill", 1
    )
    with pytest.raises(CompileError, match="expected IDENT"):
        NslCompiler(build_tool_catalog()).compile(missing_id)

    missing_version = SOURCE_TEXT.replace('version "1.0.0";', "", 1)
    with pytest.raises(CompileError, match="version, risk, and limits"):
        NslCompiler(build_tool_catalog()).compile(missing_version)


def test_src_006_language_version_is_declared_in_source() -> None:
    compiled = NslCompiler(build_tool_catalog()).compile(SOURCE_TEXT)
    assert compiled.skill.language_version == "0.1"

    missing = SOURCE_TEXT.replace('language NSL "0.1";', "", 1)
    with pytest.raises(CompileError, match="expected 'language'"):
        NslCompiler(build_tool_catalog()).compile(missing)

    empty = SOURCE_TEXT.replace('language NSL "0.1";', 'language NSL "";', 1)
    with pytest.raises(CompileError, match="unsupported NSL version"):
        NslCompiler(build_tool_catalog()).compile(empty)


@pytest.mark.parametrize("version", ["0.0", "0.0.9", "0.2", "1.0"])
def test_src_007_unsupported_language_version_is_compile_error(version) -> None:
    source = SOURCE_TEXT.replace(
        'language NSL "0.1";', f'language NSL "{version}";', 1
    )
    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)
    assert captured.value.code == DiagnosticCode.SEM_UNSUPPORTED_LANGUAGE_VERSION
    assert version in captured.value.public_message


def test_src_009_true_and_false_are_bool_literals() -> None:
    true_literal = Parser(Lexer().tokenize("true"))._expression()
    false_literal = Parser(Lexer().tokenize("false"))._expression()
    assert true_literal == AstLiteral(True, BOOL)
    assert false_literal == AstLiteral(False, BOOL)

    assert isinstance(Parser(Lexer().tokenize("True"))._expression(), AstPath)
    assert isinstance(Parser(Lexer().tokenize("False"))._expression(), AstPath)


def test_lex_001_source_is_converted_to_immutable_token_stream() -> None:
    empty = Lexer().tokenize("")
    assert isinstance(empty, tuple)
    assert len(empty) == 1
    assert empty[0].kind == "EOF"

    tokens = Lexer().tokenize("alpha + 1")
    assert tuple(token.value for token in tokens) == (
        "alpha",
        "+",
        "1",
        "<eof>",
    )
    assert tokens[-1].kind == "EOF"


def test_lex_002_token_categories_are_distinct() -> None:
    tokens = Lexer().tokenize('language account 42 1.5 "text" + { ;')
    assert tuple((token.kind, token.category) for token in tokens) == (
        (TokenKind.KEYWORD, TokenCategory.KEYWORD),
        (TokenKind.IDENTIFIER, TokenCategory.IDENTIFIER),
        (TokenKind.INTEGER, TokenCategory.LITERAL),
        (TokenKind.DECIMAL, TokenCategory.LITERAL),
        (TokenKind.STRING, TokenCategory.LITERAL),
        (TokenKind.PLUS, TokenCategory.OPERATOR),
        (TokenKind.LBRACE, TokenCategory.DELIMITER),
        (TokenKind.SEMICOLON, TokenCategory.DELIMITER),
        (TokenKind.EOF, TokenCategory.END),
    )
    assert tokens[4].lexeme == '"text"'
    assert tokens[4].value == "text"


def test_lex_003_every_token_has_source_line_and_column() -> None:
    source = SourceFile.from_text(
        "token_positions.ns",
        'a\n  <=\t"한글"',
        SourceId("source:token-positions"),
    )
    identifier, operator, string, eof = Lexer().tokenize(source)

    assert (identifier.line, identifier.column) == (1, 1)
    assert identifier.span.end == SourcePosition(1, 1, 2)
    assert (operator.line, operator.column) == (2, 3)
    assert operator.span.end == SourcePosition(6, 2, 5)
    assert (string.line, string.column) == (2, 6)
    assert string.span.end == SourcePosition(11, 2, 10)
    assert eof.span.start == SourcePosition(11, 2, 10)
    assert all(token.span.source_id == source.source_id for token in (identifier, operator, string, eof))


@pytest.mark.parametrize("invalid", ["@", "$", "\x00", "한글식별자"])
def test_lex_004_invalid_character_reports_clear_lexical_error(invalid) -> None:
    source = SourceFile.from_text("invalid_character.ns", f"ok\n  {invalid}")
    with pytest.raises(CompileError) as captured:
        Lexer().tokenize(source)

    error = captured.value
    assert error.code == DiagnosticCode.LEX_UNEXPECTED_CHARACTER
    assert error.diagnostic.phase is DiagnosticPhase.LEXER
    assert error.location is not None
    assert (error.location.line, error.location.column) == (2, 3)
    assert error.snippet == f"  {invalid}"


def test_lex_005_single_line_comments_preserve_code_and_strings() -> None:
    tokens = Lexer().tokenize(
        '// full line\na/b // trailing\n"// string" // eof comment'
    )
    assert tuple((token.kind, token.value) for token in tokens) == (
        (TokenKind.IDENTIFIER, "a"),
        (TokenKind.SLASH, "/"),
        (TokenKind.IDENTIFIER, "b"),
        (TokenKind.STRING, "// string"),
        (TokenKind.EOF, "<eof>"),
    )
    assert (tokens[0].line, tokens[0].column) == (2, 1)
    assert (tokens[3].line, tokens[3].column) == (3, 1)
    assert Lexer().tokenize("//")[0].kind is TokenKind.EOF


def test_lex_006_scan_recovers_and_finds_additional_errors() -> None:
    result = Lexer().scan("@ first\n$ second")
    assert isinstance(result, LexResult)
    assert tuple(token.value for token in result.tokens) == (
        "first",
        "second",
        "<eof>",
    )
    assert tuple(diagnostic.code for diagnostic in result.diagnostics) == (
        DiagnosticCode.LEX_UNEXPECTED_CHARACTER,
        DiagnosticCode.LEX_UNEXPECTED_CHARACTER,
    )
    assert tuple(
        (diagnostic.location.line, diagnostic.location.column)
        for diagnostic in result.diagnostics
        if diagnostic.location is not None
    ) == ((1, 1), (2, 1))

    with pytest.raises(CompileError) as captured:
        Lexer().tokenize("@ first\n$ second")
    assert captured.value.diagnostic == result.diagnostics[0]

    unrecoverable = Lexer().scan('ok "unterminated')
    assert len(unrecoverable.diagnostics) == 1
    assert unrecoverable.diagnostics[0].code is DiagnosticCode.LEX_UNTERMINATED_STRING
    assert unrecoverable.tokens[-1].kind is TokenKind.EOF


def test_lex_007_true_and_false_are_boolean_tokens() -> None:
    true, false, upper_true, upper_false, eof = Lexer().tokenize(
        "true false True False"
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
    assert upper_true.kind is TokenKind.IDENTIFIER
    assert upper_false.kind is TokenKind.IDENTIFIER
    assert eof.kind is TokenKind.EOF


def test_lex_008_include_is_reserved_keyword() -> None:
    include, title_case, suffix, underscore, eof = Lexer().tokenize(
        "include Include included include_"
    )
    assert include.kind is TokenKind.KEYWORD
    assert include.value == "include"
    assert title_case.kind is TokenKind.IDENTIFIER
    assert suffix.kind is TokenKind.IDENTIFIER
    assert underscore.kind is TokenKind.IDENTIFIER
    assert eof.kind is TokenKind.EOF


@pytest.mark.parametrize(
    ("literal", "milliseconds"),
    [("0ms", 0), ("1ms", 1), ("500ms", 500), ("30s", 30_000), ("2m", 120_000)],
)
def test_lex_009_duration_is_independent_normalized_token(
    literal, milliseconds
) -> None:
    duration, eof = Lexer().tokenize(literal)
    assert duration.kind is TokenKind.DURATION
    assert duration.category is TokenCategory.LITERAL
    assert duration.lexeme == literal
    assert duration.value == milliseconds
    assert duration.span.end.offset == len(literal)
    assert eof.kind is TokenKind.EOF

    integer, suffix, _ = Lexer().tokenize(f"{literal}x")
    assert integer.kind is TokenKind.INTEGER
    assert suffix.kind is TokenKind.IDENTIFIER
