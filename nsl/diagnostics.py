from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DiagnosticPhase(StrEnum):
    LEXER = "LEXER"
    PARSER = "PARSER"
    INCLUDE = "INCLUDE"
    SEMANTIC = "SEMANTIC"


class DiagnosticCode(StrEnum):
    LEX_UNTERMINATED_ESCAPE = "NSL-E1001"
    LEX_UNTERMINATED_STRING = "NSL-E1002"
    LEX_UNEXPECTED_CHARACTER = "NSL-E1003"

    PAR_EXPECTED_TOKEN = "NSL-E1101"
    PAR_EXPECTED_TOKEN_KIND = "NSL-E1102"
    PAR_UNKNOWN_TYPE = "NSL-E1103"
    PAR_UNKNOWN_CLASSIFICATION = "NSL-E1104"
    PAR_REQUIRED_METADATA = "NSL-E1105"
    PAR_INVALID_LIMIT_FIELDS = "NSL-E1106"
    PAR_NON_POSITIVE_LIMIT = "NSL-E1107"
    PAR_NON_POSITIVE_FOREACH_LIMIT = "NSL-E1108"
    PAR_UNEXPECTED_STATEMENT = "NSL-E1109"
    PAR_EXPECTED_EXPRESSION = "NSL-E1110"
    PAR_UNEXPECTED_FRAGMENT_DECLARATION = "NSL-E1111"

    SEM_UNKNOWN_IDENTIFIER = "NSL-E2001"
    SEM_DUPLICATE_SYMBOL = "NSL-E2002"
    SEM_UNSUPPORTED_LANGUAGE_VERSION = "NSL-E2003"
    SEM_UNSUPPORTED_RISK = "NSL-E2004"
    SEM_UNKNOWN_FIELD = "NSL-E2005"
    SEM_UNSUPPORTED_BUILTIN = "NSL-E2006"
    SEM_UNDECLARED_TOOL = "NSL-E2201"
    SEM_DUPLICATE_TOOL = "NSL-E2202"
    SEM_INCLUDE_REQUIRES_COMPOSITION = "NSL-E2300"
    INC_CYCLE = "NSL-E2301"
    INC_PATH_OUTSIDE_ROOT = "NSL-E2302"
    INC_DEPTH_LIMIT = "NSL-E2303"
    INC_FILE_LIMIT = "NSL-E2304"
    INC_BUNDLE_SIZE_LIMIT = "NSL-E2305"
    INC_TOOL_VERSION_CONFLICT = "NSL-E2306"
    INC_DUPLICATE_CONTEXT = "NSL-E2307"
    INC_DUPLICATE_LIMIT = "NSL-E2308"
    INC_RESOLUTION_FAILED = "NSL-E2309"
    INC_REQUIRED_LIMITS_MISSING = "NSL-E2310"

    SEM_BINARY_TYPE = "NSL-E3001"
    SEM_FOREACH_COLLECTION_TYPE = "NSL-E3002"
    SEM_CHECK_CONDITION_TYPE = "NSL-E3003"
    SEM_SUM_ARGUMENT_TYPE = "NSL-E3004"
    SEM_TOOL_ARGUMENT_TYPE = "NSL-E3005"

    SEM_UNKNOWN_TOOL_CONTRACT = "NSL-E4001"
    SEM_INCOMPATIBLE_TOOL_VERSION = "NSL-E4002"
    TOOL_EXECUTION_FAILURE = "NSL-E4101"
    SEM_TOOL_ARGUMENTS = "NSL-E4201"

    SEM_WRITE_TOOL_FORBIDDEN = "NSL-E5001"
    AUTHORIZATION_DENIED = "NSL-E5201"
    REPLAY_ACCESS_DENIED = "NSL-E5202"

    RESOURCE_LIMIT_EXCEEDED = "NSL-E6001"
    SEM_TOOL_CALL_BOUND = "NSL-E6101"
    SEM_LOOP_BOUND = "NSL-E6102"
    SEM_EMIT_BOUND = "NSL-E6103"
    SEM_UNBOUNDED_STRUCTURE = "NSL-E6104"

    SEM_EMIT_SCHEMA = "NSL-E7001"
    SEM_OUTPUT_TYPE = "NSL-E7002"
    SEM_OUTPUT_CLASSIFICATION = "NSL-E7003"

    RUNTIME_EVALUATION = "NSL-E8001"
    RUNTIME_UNEXPECTED = "NSL-E8002"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    line: int
    column: int

    def __post_init__(self) -> None:
        if self.line < 1 or self.column < 1:
            raise ValueError("source line and column must be positive")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: DiagnosticCode
    phase: DiagnosticPhase
    message: str
    location: SourceLocation | None = None
    snippet: str | None = None
    logical_path: str | None = None


class CompileError(ValueError):
    def __init__(self, diagnostic: Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(str(self))

    @property
    def code(self) -> str:
        return self.diagnostic.code.value

    @property
    def public_message(self) -> str:
        return self.diagnostic.message

    @property
    def location(self) -> SourceLocation | None:
        return self.diagnostic.location

    @property
    def snippet(self) -> str | None:
        return self.diagnostic.snippet

    @property
    def logical_path(self) -> str | None:
        return self.diagnostic.logical_path

    def __str__(self) -> str:
        rendered = f"[{self.code}] {self.public_message}"
        if self.location is not None:
            rendered += f" at {self.location.line}:{self.location.column}"
        if self.logical_path is not None:
            rendered += f" in {self.logical_path}"
        if self.snippet is not None:
            rendered += f"\n{self.snippet}"
        return rendered


def compile_error(
    code: DiagnosticCode,
    phase: DiagnosticPhase,
    message: str,
    location: SourceLocation | None = None,
    snippet: str | None = None,
    logical_path: str | None = None,
) -> CompileError:
    return CompileError(
        Diagnostic(code, phase, message, location, snippet, logical_path)
    )


def error_from_diagnostic(diagnostic: Diagnostic) -> CompileError:
    return CompileError(diagnostic)
