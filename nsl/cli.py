from __future__ import annotations

import argparse
import asyncio
from dataclasses import fields, is_dataclass
from enum import IntEnum, StrEnum
import json
from io import StringIO
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence, TextIO

from .adapters.filesystem import FileSystemIncludeResolver
from .cli_config import (
    load_json_document,
    load_mock_handlers_document,
    load_principal,
    load_tool_catalog,
    load_value_object,
)
from .cli_evidence import write_audit_jsonl, write_result_document
from .cli_isolation import (
    CLIIsolationError,
    encode_isolated_cli_request,
    run_isolated_cli,
)
from .cli_profile import resolve_cli_configuration
from .cli_replay import (
    ReplayPackageSequenceError,
    decode_replay_package,
    encode_replay_package,
    execute_replay_package,
    validate_portable_replay_skill,
)
from .cli_runtime import build_execution_request, execute_skill
from .cli_scenarios import (
    evaluate_scenario_suite,
    execute_scenario_suite,
    load_scenario_suite,
)
from .compiler import CompilationResult, NslCompiler
from .core import ExecutionStatus, encode_value
from .diagnostics import CompileError
from .ir import NsoCodec, skill_to_data
from .performance import SystemBenchmarkClock
from .runtime import RuntimeContractError, RuntimeEngine
from .runtime_models import LimitExceeded
from .security import AuthorizationError
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
    SCENARIO_MISMATCH = 7
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


class CLIScenarioMismatchError(RuntimeError):
    def __init__(self, details: Mapping[str, Any]) -> None:
        self.details = details
        super().__init__("scenario expectations differ")


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
    check_command.add_argument("source", type=Path, nargs="?")
    check_command.add_argument("--profile", type=Path)
    check_command.add_argument("--tool-contracts", type=Path)
    check_command.set_defaults(handler=_handle_check)

    compile_command = commands.add_parser("compile")
    compile_command.add_argument("source", type=Path, nargs="?")
    compile_command.add_argument("--profile", type=Path)
    compile_command.add_argument("-o", "--output", type=Path, required=True)
    compile_command.add_argument("--tool-contracts", type=Path)
    compile_command.set_defaults(handler=_handle_compile)

    inspect_command = commands.add_parser("inspect")
    inspect_command.add_argument("artifact", type=Path)
    inspect_command.set_defaults(handler=_handle_inspect)

    run_command = commands.add_parser("run")
    run_command.add_argument("program", type=Path, nargs="?")
    run_command.add_argument("--profile", type=Path)
    run_command.add_argument("--principal", type=Path)
    run_command.add_argument("--tool-contracts", type=Path)
    run_command.add_argument("--input", type=Path)
    run_command.add_argument("--context", type=Path)
    run_command.add_argument("--fixture", type=Path)
    run_command.add_argument("--dry-run", action="store_true")
    run_command.add_argument("--result-out", type=Path)
    run_command.add_argument("--audit-out", type=Path)
    run_command.add_argument("--timing", action="store_true")
    isolation = run_command.add_mutually_exclusive_group()
    isolation.add_argument("--isolate", dest="isolate", action="store_true")
    isolation.add_argument("--no-isolate", dest="isolate", action="store_false")
    run_command.set_defaults(isolate=None)
    run_command.add_argument("--timeout-ms", type=int)
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

    test_command = commands.add_parser("test")
    test_command.add_argument("--suite", type=Path, required=True)
    test_command.set_defaults(handler=_handle_test)
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
    except CLIScenarioMismatchError as error:
        _write_error(
            error_output,
            "CLI_SCENARIO_MISMATCH",
            str(error),
            details=error.details,
        )
        return int(CLIExitCode.SCENARIO_MISMATCH)
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
    configuration = _resolve_configuration(arguments)
    source = _required_path(configuration.program, "source or --profile is required")
    compilation = _compile_source(source, configuration.tool_contracts)
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
    configuration = _resolve_configuration(arguments)
    source = _required_path(configuration.program, "source or --profile is required")
    compilation = _compile_source(source, configuration.tool_contracts)
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
    configuration = _resolve_configuration(arguments)
    program = _required_path(configuration.program, "program or --profile is required")
    principal_path = _required_path(
        configuration.principal, "--principal or --profile is required"
    )
    catalog = load_tool_catalog(configuration.tool_contracts)
    program_clock = SystemBenchmarkClock()
    program_started_ns = program_clock.monotonic_ns()
    skill = _load_program(program, catalog)
    program_duration_ns = max(
        0, program_clock.monotonic_ns() - program_started_ns
    )
    principal = load_principal(principal_path)
    inputs = (
        None
        if configuration.input is None
        else load_value_object(configuration.input, "input")
    )
    runtime_context = (
        None
        if configuration.context is None
        else load_value_object(configuration.context, "context")
    )
    fixture_document = (
        {"schema_version": "1.0", "tools": []}
        if configuration.fixture is None
        else load_json_document(configuration.fixture)
    )
    handlers = load_mock_handlers_document(fixture_document, catalog)
    if arguments.dry_run:
        if arguments.replay_out is not None:
            raise CLIUsageError("--dry-run cannot be combined with --replay-out")
        if arguments.audit_out is not None:
            raise CLIUsageError("--dry-run cannot be combined with --audit-out")
        request = build_execution_request(
            principal,
            arguments.execution_id,
            inputs=inputs,
            runtime_context=runtime_context,
        )
        try:
            RuntimeEngine(catalog).validate_execution_request(skill, request)
        except AuthorizationError as error:
            raise CLIExecutionError(str(error)) from error
        except (RuntimeContractError, LimitExceeded) as error:
            raise ValueError(str(error)) from error
        response = _dry_run_response(skill, program, configuration.profile_path)
        if arguments.timing:
            response["timing"] = _program_timing(program, program_duration_ns)
        if arguments.result_out is not None:
            response["result_output"] = str(arguments.result_out)
            write_result_document(arguments.result_out, response)
        return response
    if arguments.replay_out is not None:
        if configuration.isolate:
            raise CLIUsageError("--isolate cannot be combined with --replay-out")
        if arguments.replay_out.suffix.lower() != ".nsr":
            raise ValueError("replay package output path must end with .nsr")
        if "nsl:replay:read" not in principal.scopes:
            raise CLIExecutionError("nsl:replay:read scope is required")
        validate_portable_replay_skill(skill, catalog)
    session = None
    isolation_evidence = None
    if configuration.isolate:
        payload = encode_isolated_cli_request(
            skill,
            catalog,
            principal,
            arguments.execution_id,
            inputs=inputs,
            runtime_context=runtime_context,
            fixture_document=fixture_document,
            measure_timing=arguments.timing,
        )
        try:
            isolated = run_isolated_cli(
                payload,
                timeout_ms=configuration.timeout_ms,
            )
        except CLIIsolationError as error:
            raise CLIExecutionError(
                str(error), {"isolation": {"code": error.code}}
            ) from error
        result = isolated.result
        audit_events = isolated.audit_events
        runtime_timing = isolated.timing
        isolation_evidence = {
            "status": isolated.process_status.value,
            "exit_code": isolated.exit_code,
        }
    else:
        session = asyncio.run(
            execute_skill(
                skill,
                catalog,
                principal,
                arguments.execution_id,
                inputs=inputs,
                runtime_context=runtime_context,
                handlers=handlers,
                measure_timing=arguments.timing,
            )
        )
        result = session.result.to_data()
        audit_events = tuple(session.audit.events)
        runtime_timing = (
            None
            if session.timing is None
            else {
                "runtime_total_ns": session.timing.total_duration_ns,
                "tool_total_ns": session.timing.tool_duration_ns,
                "runtime_overhead_ns": session.timing.runtime_overhead_ns,
                "tool_call_count": session.timing.tool_call_count,
            }
        )
    if arguments.audit_out is not None:
        write_audit_jsonl(arguments.audit_out, audit_events)
    if result["status"] != ExecutionStatus.COMPLETED.value:
        error_data = result.get("error")
        message = (
            "execution failed"
            if not isinstance(error_data, dict)
            else error_data.get("message", "execution failed")
        )
        raise CLIExecutionError(message, result)
    response = {"command": "run", "result": result}
    if isolation_evidence is not None:
        response["isolation"] = isolation_evidence
    if arguments.timing:
        response["timing"] = {
            **_program_timing(program, program_duration_ns),
            **runtime_timing,
        }
    if arguments.replay_out is not None:
        arguments.replay_out.write_bytes(
            encode_replay_package(session, skill, principal)
        )
        response["replay_package"] = str(arguments.replay_out)
    if arguments.audit_out is not None:
        response["audit_output"] = str(arguments.audit_out)
    if arguments.result_out is not None:
        response["result_output"] = str(arguments.result_out)
        write_result_document(arguments.result_out, response)
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


def _handle_test(arguments: argparse.Namespace) -> dict[str, Any]:
    suite = load_scenario_suite(arguments.suite)
    invocations = execute_scenario_suite(suite, _invoke_scenario)
    evaluations = evaluate_scenario_suite(suite, invocations)
    response = {
        "command": "test",
        "status": "passed" if all(item.passed for item in evaluations) else "failed",
        "suite": suite.suite_path.name,
        "profile": suite.profile.name,
        "case_count": len(suite.cases),
        "passed": sum(item.passed for item in evaluations),
        "failed": sum(not item.passed for item in evaluations),
        "cases": [
            {
                "id": item.case_id,
                "exit_code": item.exit_code,
                "execution_id": item.execution_id,
                "passed": item.passed,
                "mismatches": [
                    {
                        "path": mismatch.path,
                        "expected": mismatch.expected,
                        "actual": mismatch.actual,
                    }
                    for mismatch in item.mismatches
                ],
            }
            for item in evaluations
        ],
    }
    if response["failed"]:
        raise CLIScenarioMismatchError(response)
    return response


def _invoke_scenario(
    arguments: Sequence[str],
) -> tuple[int, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, output, error


def _load_program(path: Path, catalog: ToolContractCatalog):
    suffix = path.suffix.lower()
    if suffix == ".ns":
        return _compile_source_with_catalog(path, catalog).skill
    if suffix == ".nso":
        return NsoCodec.decode(path.read_bytes())
    raise ValueError("program path must end with .ns or .nso")


def _dry_run_response(skill, program: Path, profile_path: Path | None) -> dict[str, Any]:
    required_scopes = {
        "nsl:skill:execute",
        *(required.required_scope for required in skill.required_tools),
    }
    return {
        "command": "run",
        "mode": "dry-run",
        "status": "ready",
        "program_kind": program.suffix.lower()[1:],
        "profile": None if profile_path is None else profile_path.name,
        "skill": {
            "id": skill.skill_id,
            "version": skill.skill_version,
            "semantic_hash": skill.semantic_hash,
            "source_bundle_hash": skill.build.source_bundle_sha256,
            "sources": [item.logical_path for item in skill.build.sources],
        },
        "required_scopes": sorted(required_scopes),
        "required_tools": [
            {
                "tool_id": item.tool_id,
                "version": item.version,
                "required_scope": item.required_scope,
                "contract_hash": item.contract_hash,
            }
            for item in skill.required_tools
        ],
        "inputs": [
            {
                "name": item.name,
                "type": item.type_info.to_data(),
                "required": item.required,
                "classification": item.classification.value,
            }
            for item in skill.inputs
        ],
        "contexts": [
            {
                "name": item.name,
                "path": ".".join(item.path),
                "type": item.type_info.to_data(),
                "classification": item.classification.value,
            }
            for item in skill.contexts
        ],
        "limits": {
            "tool_calls": skill.limits.tool_calls,
            "loop_iterations": skill.limits.loop_iterations,
            "emitted_rows": skill.limits.emitted_rows,
            "collection_size": skill.limits.collection_size,
            "duration_ms": skill.limits.duration_ms,
        },
    }


def _program_timing(program: Path, duration_ns: int) -> dict[str, Any]:
    source = program.suffix.lower() == ".ns"
    return {
        "source_compile_ns": duration_ns if source else None,
        "nso_load_ns": None if source else duration_ns,
    }


def _compile_source(source_path: Path, catalog_path: Path | None) -> CompilationResult:
    return _compile_source_with_catalog(
        source_path, load_tool_catalog(catalog_path)
    )


def _resolve_configuration(arguments: argparse.Namespace):
    return resolve_cli_configuration(
        profile_path=arguments.profile,
        program=getattr(arguments, "program", getattr(arguments, "source", None)),
        principal=getattr(arguments, "principal", None),
        tool_contracts=arguments.tool_contracts,
        input_path=getattr(arguments, "input", None),
        context=getattr(arguments, "context", None),
        fixture=getattr(arguments, "fixture", None),
        isolate=getattr(arguments, "isolate", None),
        timeout_ms=getattr(arguments, "timeout_ms", None),
    )


def _required_path(path: Path | None, message: str) -> Path:
    if path is None:
        raise CLIUsageError(message)
    return path


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
            indent=2,
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
