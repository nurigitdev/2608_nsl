from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypeAlias

from .core import (
    BOOL,
    CHECK_STATUS,
    INT,
    STRING,
    YEAR,
    DataClassification,
    TypeRef,
    domain,
    money_type,
    primitive,
)
from .diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticPhase,
    SourceLocation,
    compile_error,
    error_from_diagnostic,
)
from .source import SourceFile, SourcePosition, SourceSpan, coerce_source


class TokenCategory(StrEnum):
    KEYWORD = "KEYWORD"
    IDENTIFIER = "IDENTIFIER"
    LITERAL = "LITERAL"
    OPERATOR = "OPERATOR"
    DELIMITER = "DELIMITER"
    END = "END"


class TokenKind(StrEnum):
    KEYWORD = "KEYWORD"
    IDENTIFIER = "IDENTIFIER"
    STRING = "STRING"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    DURATION = "DURATION"

    LBRACE = "LBRACE"
    RBRACE = "RBRACE"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    COLON = "COLON"
    SEMICOLON = "SEMICOLON"
    COMMA = "COMMA"
    DOT = "DOT"
    ASSIGN = "ASSIGN"

    PLUS = "PLUS"
    MINUS = "MINUS"
    STAR = "STAR"
    SLASH = "SLASH"
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LE = "LE"
    GT = "GT"
    GE = "GE"
    EOF = "EOF"


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    value: Any
    lexeme: str
    span: SourceSpan

    @property
    def line(self) -> int:
        return self.span.start.line

    @property
    def column(self) -> int:
        return self.span.start.column

    @property
    def category(self) -> TokenCategory:
        if self.kind is TokenKind.KEYWORD:
            return TokenCategory.KEYWORD
        if self.kind is TokenKind.IDENTIFIER:
            return TokenCategory.IDENTIFIER
        if self.kind in {
            TokenKind.STRING,
            TokenKind.INTEGER,
            TokenKind.DECIMAL,
            TokenKind.BOOLEAN,
            TokenKind.DURATION,
        }:
            return TokenCategory.LITERAL
        if self.kind in {
            TokenKind.PLUS,
            TokenKind.MINUS,
            TokenKind.STAR,
            TokenKind.SLASH,
            TokenKind.EQ,
            TokenKind.NE,
            TokenKind.LT,
            TokenKind.LE,
            TokenKind.GT,
            TokenKind.GE,
        }:
            return TokenCategory.OPERATOR
        if self.kind is TokenKind.EOF:
            return TokenCategory.END
        return TokenCategory.DELIMITER


@dataclass(frozen=True, slots=True)
class LexResult:
    tokens: tuple[Token, ...]
    diagnostics: tuple[Diagnostic, ...]


class Lexer:
    _KEYWORDS = {
        "language",
        "skill",
        "version",
        "description",
        "risk",
        "include",
        "requires",
        "limits",
        "input",
        "context",
        "output",
        "tool",
        "let",
        "read",
        "foreach",
        "in",
        "max",
        "check",
        "assert",
        "severity",
        "on_fail",
        "message",
        "emit",
    }
    _TWO_CHAR = {
        "<=": TokenKind.LE,
        ">=": TokenKind.GE,
        "==": TokenKind.EQ,
        "!=": TokenKind.NE,
    }
    _SINGLE = {
        "{": TokenKind.LBRACE,
        "}": TokenKind.RBRACE,
        "(": TokenKind.LPAREN,
        ")": TokenKind.RPAREN,
        ":": TokenKind.COLON,
        ";": TokenKind.SEMICOLON,
        ",": TokenKind.COMMA,
        ".": TokenKind.DOT,
        "=": TokenKind.ASSIGN,
        "+": TokenKind.PLUS,
        "-": TokenKind.MINUS,
        "*": TokenKind.STAR,
        "/": TokenKind.SLASH,
        "<": TokenKind.LT,
        ">": TokenKind.GT,
    }

    def tokenize(self, source: str | SourceFile) -> tuple[Token, ...]:
        result = self.scan(source)
        if result.diagnostics:
            raise error_from_diagnostic(result.diagnostics[0])
        return result.tokens

    def scan(self, source: str | SourceFile) -> LexResult:
        source_file = coerce_source(source)
        text = source_file.text
        tokens: list[Token] = []
        diagnostics: list[Diagnostic] = []
        source_lines = text.splitlines()
        index = 0
        line = 1
        column = 1
        while index < len(text):
            char = text[index]
            if char in " \t\r":
                index += 1
                column += 1
                continue
            if char == "\n":
                index += 1
                line += 1
                column = 1
                continue
            if text.startswith("//", index):
                while index < len(text) and text[index] != "\n":
                    index += 1
                    column += 1
                continue
            if char == '"':
                start_index, start_line, start_column = index, line, column
                index += 1
                column += 1
                value: list[str] = []
                unterminated_escape = False
                while index < len(text) and text[index] != '"':
                    if text[index] == "\\":
                        index += 1
                        column += 1
                        if index >= len(text):
                            diagnostics.append(
                                compile_error(
                                    DiagnosticCode.LEX_UNTERMINATED_ESCAPE,
                                    DiagnosticPhase.LEXER,
                                    "unterminated string escape",
                                    SourceLocation(line, column),
                                    source_lines[line - 1],
                                ).diagnostic
                            )
                            unterminated_escape = True
                            break
                        escapes = {
                            "n": "\n",
                            "r": "\r",
                            "t": "\t",
                            '"': '"',
                            "\\": "\\",
                        }
                        value.append(escapes.get(text[index], text[index]))
                    else:
                        value.append(text[index])
                    if text[index] == "\n":
                        line += 1
                        column = 1
                    else:
                        column += 1
                    index += 1
                if unterminated_escape:
                    break
                if index >= len(text):
                    diagnostics.append(
                        compile_error(
                            DiagnosticCode.LEX_UNTERMINATED_STRING,
                            DiagnosticPhase.LEXER,
                            "unterminated string",
                            SourceLocation(start_line, start_column),
                            source_lines[start_line - 1],
                        ).diagnostic
                    )
                    break
                index += 1
                column += 1
                tokens.append(
                    Token(
                        TokenKind.STRING,
                        "".join(value),
                        text[start_index:index],
                        SourceSpan(
                            source_file.source_id,
                            SourcePosition(start_index, start_line, start_column),
                            SourcePosition(index, line, column),
                        ),
                    )
                )
                continue
            if char.isdigit():
                start = index
                start_column = column
                while index < len(text) and text[index].isdigit():
                    index += 1
                    column += 1
                is_decimal = (
                    index + 1 < len(text)
                    and text[index] == "."
                    and text[index + 1].isdigit()
                )
                if is_decimal:
                    index += 1
                    column += 1
                    while index < len(text) and text[index].isdigit():
                        index += 1
                        column += 1
                lexeme = text[start:index]
                duration_unit = None
                if not is_decimal:
                    for unit in ("ms", "s", "m"):
                        unit_end = index + len(unit)
                        if text.startswith(unit, index) and (
                            unit_end == len(text)
                            or not (
                                text[unit_end].isascii()
                                and (
                                    text[unit_end].isalnum()
                                    or text[unit_end] == "_"
                                )
                            )
                        ):
                            duration_unit = unit
                            index = unit_end
                            column += len(unit)
                            lexeme = text[start:index]
                            break
                if duration_unit is not None:
                    multipliers = {"ms": 1, "s": 1_000, "m": 60_000}
                    value = int(text[start : index - len(duration_unit)])
                    value *= multipliers[duration_unit]
                    kind = TokenKind.DURATION
                else:
                    value = lexeme
                    kind = TokenKind.DECIMAL if is_decimal else TokenKind.INTEGER
                tokens.append(
                    Token(
                        kind,
                        value,
                        lexeme,
                        SourceSpan(
                            source_file.source_id,
                            SourcePosition(start, line, start_column),
                            SourcePosition(index, line, column),
                        ),
                    )
                )
                continue
            if (char.isascii() and char.isalpha()) or char == "_":
                start = index
                start_column = column
                while index < len(text) and (
                    (text[index].isascii() and text[index].isalnum())
                    or text[index] == "_"
                ):
                    index += 1
                    column += 1
                lexeme = text[start:index]
                if lexeme in {"true", "false"}:
                    kind = TokenKind.BOOLEAN
                    value: Any = lexeme == "true"
                elif lexeme in self._KEYWORDS:
                    kind = TokenKind.KEYWORD
                    value = lexeme
                else:
                    kind = TokenKind.IDENTIFIER
                    value = lexeme
                tokens.append(
                    Token(
                        kind,
                        value,
                        lexeme,
                        SourceSpan(
                            source_file.source_id,
                            SourcePosition(start, line, start_column),
                            SourcePosition(index, line, column),
                        ),
                    )
                )
                continue
            two = text[index : index + 2]
            if two in self._TWO_CHAR:
                tokens.append(
                    Token(
                        self._TWO_CHAR[two],
                        two,
                        two,
                        SourceSpan(
                            source_file.source_id,
                            SourcePosition(index, line, column),
                            SourcePosition(index + 2, line, column + 2),
                        ),
                    )
                )
                index += 2
                column += 2
                continue
            if char in self._SINGLE:
                tokens.append(
                    Token(
                        self._SINGLE[char],
                        char,
                        char,
                        SourceSpan(
                            source_file.source_id,
                            SourcePosition(index, line, column),
                            SourcePosition(index + 1, line, column + 1),
                        ),
                    )
                )
                index += 1
                column += 1
                continue
            diagnostics.append(
                compile_error(
                    DiagnosticCode.LEX_UNEXPECTED_CHARACTER,
                    DiagnosticPhase.LEXER,
                    f"unexpected character {char!r}",
                    SourceLocation(line, column),
                    source_lines[line - 1],
                ).diagnostic
            )
            index += 1
            column += 1
        position = SourcePosition(index, line, column)
        tokens.append(
            Token(
                TokenKind.EOF,
                "<eof>",
                "",
                SourceSpan(source_file.source_id, position, position),
            )
        )
        return LexResult(tuple(tokens), tuple(diagnostics))


@dataclass(frozen=True, slots=True)
class AstLiteral:
    value: Any
    type_info: TypeRef


@dataclass(frozen=True, slots=True)
class AstPath:
    parts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AstCall:
    name: str
    arguments: tuple[AstExpression, ...]


@dataclass(frozen=True, slots=True)
class AstRead:
    tool_id: str
    arguments: tuple[tuple[str, AstExpression], ...]


@dataclass(frozen=True, slots=True)
class AstBinary:
    operator: str
    left: AstExpression
    right: AstExpression


AstExpression: TypeAlias = AstLiteral | AstPath | AstCall | AstRead | AstBinary


@dataclass(frozen=True, slots=True)
class AstLet:
    name: str
    value: AstExpression


@dataclass(frozen=True, slots=True)
class AstForeach:
    iterator: str
    collection: AstExpression
    max_iterations: int
    body: tuple[AstStatement, ...]


@dataclass(frozen=True, slots=True)
class AstCheck:
    check_id: str
    condition: AstExpression
    severity: str
    on_fail: str
    message: str


@dataclass(frozen=True, slots=True)
class AstEmit:
    fields: tuple[tuple[str, AstExpression], ...]


AstStatement: TypeAlias = AstLet | AstForeach | AstCheck | AstEmit


@dataclass(frozen=True, slots=True)
class AstFieldSpec:
    name: str
    type_info: TypeRef
    classification: DataClassification
    path: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AstLimits:
    tool_calls: int
    loop_iterations: int
    emitted_rows: int
    collection_size: int


@dataclass(frozen=True, slots=True)
class AstSkill:
    language_version: str
    skill_id: str
    skill_version: str
    risk: str
    requires: tuple[tuple[str, str], ...]
    limits: AstLimits
    inputs: tuple[AstFieldSpec, ...]
    contexts: tuple[AstFieldSpec, ...]
    outputs: tuple[AstFieldSpec, ...]
    body: tuple[AstStatement, ...]


class Parser:
    _TYPE_NAMES = {
        "String": STRING,
        "Int": INT,
        "Bool": BOOL,
        "Year": YEAR,
        "TeamId": domain("TeamId"),
        "ProjectCode": domain("ProjectCode"),
        "CheckStatus": CHECK_STATUS,
        "Decimal": primitive("Decimal"),
    }
    _PRECEDENCE = {
        "==": 10,
        "!=": 10,
        "<": 20,
        "<=": 20,
        ">": 20,
        ">=": 20,
        "+": 30,
        "-": 30,
        "*": 40,
        "/": 40,
    }

    def __init__(self, tokens: tuple[Token, ...]) -> None:
        self.tokens = tokens
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def _advance(self) -> Token:
        token = self.current
        self.index += 1
        return token

    def _accept(self, value: str) -> bool:
        if self.current.value == value:
            self._advance()
            return True
        return False

    def _expect(self, value: str) -> Token:
        if self.current.value != value:
            token = self.current
            raise compile_error(
                DiagnosticCode.PAR_EXPECTED_TOKEN,
                DiagnosticPhase.PARSER,
                f"expected {value!r}, got {token.value!r}",
                SourceLocation(token.line, token.column),
            )
        return self._advance()

    def _expect_kind(self, kind: TokenKind) -> Token:
        if self.current.kind != kind:
            token = self.current
            raise compile_error(
                DiagnosticCode.PAR_EXPECTED_TOKEN_KIND,
                DiagnosticPhase.PARSER,
                f"expected {kind}, got {token.value!r}",
                SourceLocation(token.line, token.column),
            )
        return self._advance()

    def _qualified_name(self) -> str:
        parts = [self._expect_kind(TokenKind.IDENTIFIER).value]
        while self._accept("."):
            parts.append(self._expect_kind(TokenKind.IDENTIFIER).value)
        return ".".join(parts)

    def _type(self) -> TypeRef:
        token = self._expect_kind(TokenKind.IDENTIFIER)
        name = token.value
        if name == "Money":
            self._expect("<")
            currency = self._expect_kind(TokenKind.IDENTIFIER).value
            self._expect(">")
            return money_type(currency)
        try:
            return self._TYPE_NAMES[name]
        except KeyError as error:
            raise compile_error(
                DiagnosticCode.PAR_UNKNOWN_TYPE,
                DiagnosticPhase.PARSER,
                f"unknown type: {name}",
                SourceLocation(token.line, token.column),
            ) from error

    def _classification(self) -> DataClassification:
        if not self._accept("classification"):
            return DataClassification.INTERNAL
        token = self._expect_kind(TokenKind.IDENTIFIER)
        value = token.value
        try:
            return DataClassification(value)
        except ValueError as error:
            raise compile_error(
                DiagnosticCode.PAR_UNKNOWN_CLASSIFICATION,
                DiagnosticPhase.PARSER,
                f"unknown data classification: {value}",
                SourceLocation(token.line, token.column),
            ) from error

    def parse(self) -> AstSkill:
        self._expect("language")
        self._expect("NSL")
        language_version = self._expect_kind(TokenKind.STRING).value
        self._expect(";")
        self._expect("skill")
        skill_id = self._qualified_name()
        self._expect("{")

        skill_version = ""
        risk = ""
        requires: tuple[tuple[str, str], ...] = ()
        limits: AstLimits | None = None
        inputs: tuple[AstFieldSpec, ...] = ()
        contexts: tuple[AstFieldSpec, ...] = ()
        outputs: tuple[AstFieldSpec, ...] = ()
        body: list[AstStatement] = []

        while not self._accept("}"):
            keyword = self.current.value
            if keyword == "version":
                self._advance()
                skill_version = self._expect_kind(TokenKind.STRING).value
                self._expect(";")
            elif keyword == "risk":
                self._advance()
                risk = self._expect_kind(TokenKind.IDENTIFIER).value
                self._expect(";")
            elif keyword == "requires":
                requires = self._requires()
            elif keyword == "limits":
                limits = self._limits()
            elif keyword == "input":
                inputs = self._fields("input")
            elif keyword == "context":
                contexts = self._fields("context", with_path=True)
            elif keyword == "output":
                outputs = self._fields("output")
            else:
                body.append(self._statement())
        self._expect("<eof>")

        if not skill_version or not risk or limits is None:
            raise compile_error(
                DiagnosticCode.PAR_REQUIRED_METADATA,
                DiagnosticPhase.PARSER,
                "version, risk, and limits are required",
                SourceLocation(self.tokens[-1].line, self.tokens[-1].column),
            )
        return AstSkill(
            language_version,
            skill_id,
            skill_version,
            risk,
            requires,
            limits,
            inputs,
            contexts,
            outputs,
            tuple(body),
        )

    def _requires(self) -> tuple[tuple[str, str], ...]:
        self._expect("requires")
        self._expect("{")
        items: list[tuple[str, str]] = []
        while not self._accept("}"):
            self._expect("tool")
            tool_id = self._qualified_name()
            self._expect("version")
            version = self._expect_kind(TokenKind.STRING).value
            self._expect(";")
            items.append((tool_id, version))
        return tuple(items)

    def _limits(self) -> AstLimits:
        self._expect("limits")
        self._expect("{")
        values: dict[str, int] = {}
        while not self._accept("}"):
            name = self._expect_kind(TokenKind.IDENTIFIER).value
            values[name] = int(self._expect_kind(TokenKind.INTEGER).value)
            self._expect(";")
        required = {"tool_calls", "loop_iterations", "emitted_rows", "collection_size"}
        if set(values) != required:
            raise compile_error(
                DiagnosticCode.PAR_INVALID_LIMIT_FIELDS,
                DiagnosticPhase.PARSER,
                f"limits must define exactly: {', '.join(sorted(required))}",
                SourceLocation(self.current.line, self.current.column),
            )
        if any(value <= 0 for value in values.values()):
            raise compile_error(
                DiagnosticCode.PAR_NON_POSITIVE_LIMIT,
                DiagnosticPhase.PARSER,
                "all limits must be positive",
                SourceLocation(self.current.line, self.current.column),
            )
        return AstLimits(**values)

    def _fields(
        self, keyword: str, with_path: bool = False
    ) -> tuple[AstFieldSpec, ...]:
        self._expect(keyword)
        self._expect("{")
        fields: list[AstFieldSpec] = []
        while not self._accept("}"):
            name = self._expect_kind(TokenKind.IDENTIFIER).value
            self._expect(":")
            type_info = self._type()
            path: tuple[str, ...] = ()
            if with_path:
                self._expect("from")
                path = tuple(self._expect_kind(TokenKind.STRING).value.split("."))
            classification = self._classification()
            self._expect(";")
            fields.append(AstFieldSpec(name, type_info, classification, path))
        return tuple(fields)

    def _statement(self) -> AstStatement:
        keyword = self.current.value
        if keyword == "let":
            self._advance()
            name = self._expect_kind(TokenKind.IDENTIFIER).value
            self._expect("=")
            value = self._expression()
            self._expect(";")
            return AstLet(name, value)
        if keyword == "foreach":
            self._advance()
            iterator = self._expect_kind(TokenKind.IDENTIFIER).value
            self._expect("in")
            collection = self._expression()
            self._expect("max")
            max_token = self._expect_kind(TokenKind.INTEGER)
            max_iterations = int(max_token.value)
            if max_iterations <= 0:
                raise compile_error(
                    DiagnosticCode.PAR_NON_POSITIVE_FOREACH_LIMIT,
                    DiagnosticPhase.PARSER,
                    "foreach max must be positive",
                    SourceLocation(max_token.line, max_token.column),
                )
            self._expect("{")
            body: list[AstStatement] = []
            while not self._accept("}"):
                body.append(self._statement())
            return AstForeach(iterator, collection, max_iterations, tuple(body))
        if keyword == "check":
            self._advance()
            check_id = self._expect_kind(TokenKind.IDENTIFIER).value
            self._expect("{")
            self._expect("assert")
            condition = self._expression()
            self._expect(";")
            self._expect("severity")
            severity = self._expect_kind(TokenKind.IDENTIFIER).value
            self._expect(";")
            self._expect("on_fail")
            on_fail = self._expect_kind(TokenKind.IDENTIFIER).value
            self._expect(";")
            self._expect("message")
            message = self._expect_kind(TokenKind.STRING).value
            self._expect(";")
            self._expect("}")
            return AstCheck(check_id, condition, severity, on_fail, message)
        if keyword == "emit":
            self._advance()
            self._expect("{")
            fields: list[tuple[str, AstExpression]] = []
            while not self._accept("}"):
                name = self._expect_kind(TokenKind.IDENTIFIER).value
                self._expect(":")
                fields.append((name, self._expression()))
                self._expect(";")
            return AstEmit(tuple(fields))
        token = self.current
        raise compile_error(
            DiagnosticCode.PAR_UNEXPECTED_STATEMENT,
            DiagnosticPhase.PARSER,
            f"unexpected statement {token.value!r}",
            SourceLocation(token.line, token.column),
        )

    def _expression(self, minimum_precedence: int = 0) -> AstExpression:
        left = self._primary()
        while True:
            operator = self.current.value
            precedence = self._PRECEDENCE.get(operator)
            if precedence is None or precedence < minimum_precedence:
                break
            self._advance()
            right = self._expression(precedence + 1)
            left = AstBinary(operator, left, right)
        return left

    def _primary(self) -> AstExpression:
        token = self.current
        if token.kind in {TokenKind.INTEGER, TokenKind.DECIMAL}:
            self._advance()
            if "." in token.value:
                return AstLiteral(Decimal(token.value), primitive("Decimal"))
            return AstLiteral(int(token.value), INT)
        if token.kind is TokenKind.STRING:
            self._advance()
            return AstLiteral(token.value, STRING)
        if token.kind is TokenKind.BOOLEAN:
            self._advance()
            return AstLiteral(token.value, BOOL)
        if token.value == "read":
            self._advance()
            tool_id = self._qualified_name()
            self._expect("(")
            arguments: list[tuple[str, AstExpression]] = []
            if not self._accept(")"):
                while True:
                    name = self._expect_kind(TokenKind.IDENTIFIER).value
                    self._expect(":")
                    arguments.append((name, self._expression()))
                    if self._accept(")"):
                        break
                    self._expect(",")
            return AstRead(tool_id, tuple(arguments))
        if token.kind is TokenKind.IDENTIFIER:
            name = self._advance().value
            if self._accept("("):
                arguments: list[AstExpression] = []
                if not self._accept(")"):
                    while True:
                        arguments.append(self._expression())
                        if self._accept(")"):
                            break
                        self._expect(",")
                return AstCall(name, tuple(arguments))
            parts = [name]
            while self._accept("."):
                parts.append(self._expect_kind(TokenKind.IDENTIFIER).value)
            return AstPath(tuple(parts))
        if self._accept("("):
            expression = self._expression()
            self._expect(")")
            return expression
        raise compile_error(
            DiagnosticCode.PAR_EXPECTED_EXPRESSION,
            DiagnosticPhase.PARSER,
            f"expected expression, got {token.value!r}",
            SourceLocation(token.line, token.column),
        )
