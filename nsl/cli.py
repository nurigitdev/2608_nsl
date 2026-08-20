from __future__ import annotations

import argparse
import asyncio
from dataclasses import fields, is_dataclass
from enum import IntEnum, StrEnum
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence, TextIO

from .adapters.filesystem import FileSystemIncludeResolver
from .cli_config import (
    load_mock_handlers,
    load_principal,
    load_tool_catalog,
    load_value_object,
)
from .cli_replay import (
    ReplayPackageSequenceError,
    decode_replay_package,
    encode_replay_package,
    execute_replay_package,
    validate_portable_replay_skill,
)
from .cli_runtime import execute_skill
from .compiler import CompilationResult, NslCompiler
from .core import ExecutionStatus, encode_value
from .diagnostics import CompileError
from .ir import NsoCodec, skill_to_data
from .source import SourceFile
from .syntax import Lexer, Parser
from .tools import ToolContractCatalog


class CLIExitCode(IntEnum):
    SUCCESS = 0
    USAGE_ERROR = 2
    IO_ERROR = 3
    VALIDATION_ERROR = 4
    EXECUTION_ERROR = 5
    REPLAY_MISMATCH = 6
    INTERNAL_ERROR = 70


class CLIUsageError(ValueError):
    pass


class CLIExecutionError(RuntimeError):
    def __init__(self, message: str, details: Mapping[str, Any] | None = None) -> None:
        self.details = details
        super().__init__(message)


class CLIReplayMismatchError(RuntimeError):
    def __init__(self, message: str, details: Mapping[str, Any] | None = None) -> None:
        self.details = details
        super().__init__(message)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIUsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="nsl")
    commands = parser.add_subparsers(dest="command", required=True)
    parse_command = commands.add_parser("parse")
    parse_command.add_argument("source", type=Path)
    parse_command.set_defaults(handler=_handle_parse)

    check_command = commands.add_parser("check")
    check_command.add_argument("source", type=Path)
    check_command.add_argument("--tool-contracts", type=Path)
    check_command.set_defaults(handler=_handle_check)

    compile_command = commands.add_parser("compile")
    compile_command.add_argument("source", type=Path)
    compile_command.add_argument("-o", "--output", type=Path, required=True)
    compile_command.add_argument("--tool-contracts", type=Path)
    compile_command.set_defaults(handler=_handle_compile)

    inspect_command = commands.add_parser("inspect")
    inspect_command.add_argument("artifact", type=Path)
    inspect_command.set_defaults(handler=_handle_inspect)

    run_command = commands.add_parser("run")
    run_command.add_argument("program", type=Path)
    run_command.add_argument("--principal", type=Path, required=True)
    run_command.add_argument("--tool-contracts", type=Path)
    run_command.add_argument("--input", type=Path)
    run_command.add_argument("--context", type=Path)
    run_command.add_argument("--fixture", type=Path)
    run_command.add_argument("--replay-out", type=Path)
    run_command.add_argument("--execution-id", default="cli-execution")
    run_command.set_defaults(handler=_handle_run)

    replay_command = commands.add_parser("replay")
    replay_command.add_argument("program", type=Path)
    replay_command.add_argument("--bundle", type=Path, required=True)
    replay_command.add_argument("--principal", type=Path, required=True)
    replay_command.add_argument("--tool-contracts", type=Path)
    replay_command.add_argument("--execution-id", default="cli-replay")
    replay_command.set_defaults(handler=_handle_replay)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    try:
        arguments = build_parser().parse_args(argv)
        payload = arguments.handler(arguments)
        _write_json(output, payload)
        return int(CLIExitCode.SUCCESS)
    except CLIUsageError as error:
        _write_error(error_output, "CLI_USAGE_ERROR", str(error))
        return int(CLIExitCode.USAGE_ERROR)
    except OSError as error:
        _write_error(error_output, "CLI_IO_ERROR", _safe_os_message(error))
        return int(CLIExitCode.IO_ERROR)
    except (CompileError, ValueError) as error:
        code = error.code if isinstance(error, CompileError) else "CLI_VALIDATION_ERROR"
        _write_error(error_output, code, str(error))
        return int(CLIExitCode.VALIDATION_ERROR)
    except CLIReplayMismatchError as error:
        _write_error(
            error_output,
            "CLI_REPLAY_MISMATCH",
            str(error),
            details=error.details,
        )
        return int(CLIExitCode.REPLAY_MISMATCH)
    except CLIExecutionError as error:
        _write_error(
            error_output,
            "CLI_EXECUTION_ERROR",
            str(error),
            details=error.details,
        )
        return int(CLIExitCode.EXECUTION_ERROR)
    except Exception:
        _write_error(
            error_output,
            "CLI_INTERNAL_ERROR",
            "internal command failure",
        )
        return int(CLIExitCode.INTERNAL_ERROR)


def _handle_parse(arguments: argparse.Namespace) -> dict[str, Any]:
    source = _read_source(arguments.source)
    ast = Parser(Lexer().tokenize(source), source).parse()
    return {
        "command": "parse",
        "source": source.logical_path,
        "ast": _to_jsonable(ast),
    }


def _handle_check(arguments: argparse.Namespace) -> dict[str, Any]:
    compilation = _compile_source(arguments.source, arguments.tool_contracts)
    return {
        "command": "check",
        "status": "valid",
        "skill_id": compilation.skill.skill_id,
        "skill_version": compilation.skill.skill_version,
        "semantic_hash": compilation.semantic_hash,
        "source_bundle_hash": compilation.source_bundle_hash,
        "sources": [
            item.logical_path for item in compilation.source_manifest
        ],
    }


def _handle_compile(arguments: argparse.Namespace) -> dict[str, Any]:
    if arguments.output.suffix.lower() != ".nso":
        raise ValueError("compiled output path must end with .nso")
    compilation = _compile_source(arguments.source, arguments.tool_contracts)
    arguments.output.write_bytes(compilation.nso_bytes)
    return {
        "command": "compile",
        "status": "compiled",
        "output": str(arguments.output),
        "size_bytes": len(compilation.nso_bytes),
        "semantic_hash": compilation.semantic_hash,
        "source_bundle_hash": compilation.source_bundle_hash,
    }


def _handle_inspect(arguments: argparse.Namespace) -> dict[str, Any]:
    skill = NsoCodec.decode(arguments.artifact.read_bytes())
    return {
        "command": "inspect",
        "status": "valid",
        "artifact": skill_to_data(skill),
    }


def _handle_run(arguments: argparse.Namespace) -> dict[str, Any]:
    catalog = load_tool_catalog(arguments.tool_contracts)
    skill = _load_program(arguments.program, catalog)
    principal = load_principal(arguments.principal)
    if arguments.replay_out is not None:
        if arguments.replay_out.suffix.lower() != ".nsr":
            raise ValueError("replay package output path must end with .nsr")
        if "nsl:replay:read" not in principal.scopes:
            raise CLIExecutionError("nsl:replay:read scope is required")
        validate_portable_replay_skill(skill, catalog)
    session = asyncio.run(
        execute_skill(
            skill,
            catalog,
            principal,
            arguments.execution_id,
            inputs=(
                None
                if arguments.input is None
                else load_value_object(arguments.input, "input")
            ),
            runtime_context=(
                None
                if arguments.context is None
                else load_value_object(arguments.context, "context")
            ),
            handlers=load_mock_handlers(arguments.fixture, catalog),
        )
    )
    result = session.result.to_data()
    if session.result.status is not ExecutionStatus.COMPLETED:
        message = (
            "execution failed"
            if session.result.error is None
            else session.result.error.message
        )
        raise CLIExecutionError(message, result)
    response = {"command": "run", "result": result}
    if arguments.replay_out is not None:
        arguments.replay_out.write_bytes(
            encode_replay_package(session, skill, principal)
        )
        response["replay_package"] = str(arguments.replay_out)
    return response


def _handle_replay(arguments: argparse.Namespace) -> dict[str, Any]:
    catalog = load_tool_catalog(arguments.tool_contracts)
    skill = _load_program(arguments.program, catalog)
    principal = load_principal(arguments.principal)
    if "nsl:replay:read" not in principal.scopes:
        raise CLIExecutionError("nsl:replay:read scope is required")
    bundle, store = decode_replay_package(
        arguments.bundle.read_bytes(), skill, catalog, principal
    )
    try:
        report = asyncio.run(
            execute_replay_package(
                bundle,
                store,
                skill,
                catalog,
                principal,
                arguments.execution_id,
            )
        )
    except ReplayPackageSequenceError as error:
        raise CLIReplayMismatchError(str(error)) from error
    details = {
        "matches": report.matches,
        "original_runtime_version": report.original_runtime_version,
        "replay_runtime_version": report.replay_runtime_version,
        "runtime_version_changed": report.runtime_version_changed,
        "tool_call_count": report.execution.tool_call_count,
        "result": report.execution.result.to_data(),
        "differences": [
            {
                "path": item.path,
                "original": encode_value(item.original),
                "replayed": encode_value(item.replayed),
            }
            for item in report.differences
        ],
    }
    if not report.matches:
        raise CLIReplayMismatchError("replay result differs", details)
    return {"command": "replay", **details}


def _load_program(path: Path, catalog: ToolContractCatalog):
    suffix = path.suffix.lower()
    if suffix == ".ns":
        return _compile_source_with_catalog(path, catalog).skill
    if suffix == ".nso":
        return NsoCodec.decode(path.read_bytes())
    raise ValueError("program path must end with .ns or .nso")


def _compile_source(source_path: Path, catalog_path: Path | None) -> CompilationResult:
    return _compile_source_with_catalog(
        source_path, load_tool_catalog(catalog_path)
    )


def _compile_source_with_catalog(
    source_path: Path, catalog: ToolContractCatalog
) -> CompilationResult:
    source = _read_source(source_path)
    resolver = FileSystemIncludeResolver(source_path.parent)
    return NslCompiler(catalog, resolver).compile(source)


def _read_source(path: Path) -> SourceFile:
    return SourceFile.from_bytes(path.name, path.read_bytes())


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "kind": type(value).__name__,
            **{
                field.name: _to_jsonable(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, frozenset):
        return [_to_jsonable(item) for item in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return encode_value(value)


def _write_json(stream: TextIO, payload: Mapping[str, Any]) -> None:
    stream.write(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def _write_error(
    stream: TextIO,
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    _write_json(stream, {"error": error})


def _safe_os_message(error: OSError) -> str:
    if isinstance(error, FileNotFoundError):
        return "file not found"
    if isinstance(error, PermissionError):
        return "permission denied"
    return "file operation failed"
