from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import runpy
import sys
import tomllib

import pytest

import nsl.cli as cli_module
from nsl.adapters.filesystem import FileSystemIncludeResolver
from nsl.audit import InMemorySnapshotStore, value_hash
from nsl.cli import (
    CLIExecutionError,
    CLIExitCode,
    CLIReplayMismatchError,
    _safe_os_message,
    _to_jsonable,
    main,
)
from nsl.ir import NsoCodec
from nsl.source import SourceFile
from nsl.tools import ToolResultEnvelope


VALID_SOURCE = """language NSL "0.1";

skill TEST.CLI_PARSE {
    version "1.0.0";
    risk READ_VALIDATE;
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }
    output {
        status: String classification INTERNAL;
    }
    emit { status: "ok"; }
}
"""

TOOL_CONTRACT = {
    "tool_id": "TEST.GET_STATUS",
    "version": "1.0.0",
    "capability": "READ",
    "inputs": [
        {"name": "request_id", "type": {"kind": "primitive", "name": "String"}}
    ],
    "output": {"kind": "primitive", "name": "String"},
    "required_scope": "test:read",
    "output_classification": "INTERNAL",
}

PRINCIPAL = {
    "tenant_id": "tenant-cli",
    "subject_id": "user-cli",
    "actor_type": "USER",
    "roles": ["NSL_USER"],
    "scopes": ["nsl:skill:execute"],
    "auth_context_ref": "auth-cli-001",
    "verification": "VERIFIED",
}

INPUT_SOURCE = VALID_SOURCE.replace(
    "    output {",
    "    input { year: Year classification INTERNAL; }\n\n    output {",
).replace('status: "ok";', "status: year;").replace(
    "status: String classification INTERNAL;",
    "status: Year classification INTERNAL;",
)
CONTEXT_SOURCE = VALID_SOURCE.replace(
    "    output {",
    '    context { team: String from "user.team" classification INTERNAL; }\n\n'
    "    output {",
).replace('status: "ok";', "status: team;")
TOOL_SOURCE = VALID_SOURCE.replace(
    "    limits {",
    '    requires { tool TEST.GET_STATUS version "1.0.0"; }\n\n    limits {',
).replace(
    "    output {",
    "    input { request_id: String classification INTERNAL; }\n\n    output {",
).replace(
    '    emit { status: "ok"; }',
    "    let current_status = read TEST.GET_STATUS(request_id: request_id);\n"
    "    emit { status: current_status; }",
)


def invoke(*arguments: str):
    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, output, error


def write_json(path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_cli_001_parse_returns_structured_ast(tmp_path) -> None:
    source = tmp_path / "parse.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")

    code, output, error = invoke("parse", str(source))

    assert code == CLIExitCode.SUCCESS, error
    assert error is None
    assert output["command"] == "parse"
    assert output["source"] == "parse.ns"
    assert output["ast"]["kind"] == "AstSkill"
    assert output["ast"]["skill_id"] == "TEST.CLI_PARSE"
    assert output["ast"]["body"][0]["kind"] == "AstEmit"


def test_cli_001_parse_rejects_invalid_utf8_syntax_and_missing_file(tmp_path) -> None:
    invalid_utf8 = tmp_path / "invalid-utf8.ns"
    invalid_utf8.write_bytes(b"\xff")
    code, output, error = invoke("parse", str(invalid_utf8))
    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_VALIDATION_ERROR"

    invalid_syntax = tmp_path / "invalid-syntax.ns"
    invalid_syntax.write_text("skill", encoding="utf-8")
    code, output, error = invoke("parse", str(invalid_syntax))
    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["code"].startswith("NSL-E")

    code, output, error = invoke("parse", str(tmp_path / "missing.ns"))
    assert code == CLIExitCode.IO_ERROR
    assert output is None
    assert error == {
        "error": {"code": "CLI_IO_ERROR", "message": "file not found"}
    }


def test_cli_001_parse_usage_and_serialization_boundaries() -> None:
    code, output, error = invoke()
    assert code == CLIExitCode.USAGE_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_USAGE_ERROR"

    assert _to_jsonable({"values": frozenset({"second", "first"})}) == {
        "values": ["first", "second"]
    }
    assert _safe_os_message(PermissionError()) == "permission denied"
    assert _safe_os_message(OSError()) == "file operation failed"


def test_cli_001_module_entry_point(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "module.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["nsl", "parse", str(source)])

    with pytest.raises(SystemExit) as raised:
        runpy.run_module("nsl", run_name="__main__")

    captured = capsys.readouterr()
    assert raised.value.code == CLIExitCode.SUCCESS
    assert json.loads(captured.out)["command"] == "parse"
    assert captured.err == ""


def test_cli_001_console_script_is_registered() -> None:
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    assert project["project"]["scripts"]["nsl"] == "nsl.cli:main"


def test_cli_002_check_compiles_source_and_secure_includes(tmp_path) -> None:
    source = tmp_path / "check.ns"
    source.write_text(
        VALID_SOURCE.replace(
            "    limits {", '    include "context.ns";\n\n    limits {'
        ),
        encoding="utf-8",
    )
    (tmp_path / "context.ns").write_text(
        'context { team: String from "user.team" classification INTERNAL; }',
        encoding="utf-8",
    )

    code, output, error = invoke("check", str(source))

    assert code == CLIExitCode.SUCCESS
    assert error is None
    assert output["command"] == "check"
    assert output["status"] == "valid"
    assert output["skill_id"] == "TEST.CLI_PARSE"
    assert output["skill_version"] == "1.0.0"
    assert output["semantic_hash"].startswith("sha256:")
    assert output["source_bundle_hash"].startswith("sha256:")
    assert output["sources"] == ["check.ns", "context.ns"]


def test_cli_002_check_resolves_explicit_tool_contract_catalog(tmp_path) -> None:
    source = tmp_path / "requires.ns"
    source.write_text(
        VALID_SOURCE.replace(
            "    limits {",
            '    requires { tool TEST.GET_STATUS version "1.0.0"; }\n\n'
            "    limits {",
        ),
        encoding="utf-8",
    )
    catalog = tmp_path / "tools.json"
    catalog.write_text(json.dumps({"tools": [TOOL_CONTRACT]}), encoding="utf-8")

    code, output, error = invoke(
        "check", str(source), "--tool-contracts", str(catalog)
    )

    assert code == CLIExitCode.SUCCESS, error
    assert error is None
    assert output["status"] == "valid"


@pytest.mark.parametrize(
    "catalog_data",
    [
        [],
        {},
        {"tools": [], "extra": True},
        {"tools": {}},
        {"tools": [{}]},
        {
            "tools": [
                {
                    **TOOL_CONTRACT,
                    "output_classification": "SECRET",
                }
            ]
        },
        {"tools": [{**TOOL_CONTRACT, "timeout_ms": True}]},
        {"tools": [{**TOOL_CONTRACT, "empty_is_valid": 1}]},
        {"tools": [{**TOOL_CONTRACT, "inputs": [None]}]},
        {"tools": [{**TOOL_CONTRACT, "tool_id": ""}]},
        {
            "tools": [
                {
                    **TOOL_CONTRACT,
                    "output": {"kind": "unknown"},
                }
            ]
        },
    ],
)
def test_cli_002_check_rejects_malformed_tool_catalogs(
    tmp_path, catalog_data
) -> None:
    source = tmp_path / "check.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")
    catalog = tmp_path / "tools.json"
    catalog.write_text(json.dumps(catalog_data), encoding="utf-8")

    code, output, error = invoke(
        "check", str(source), "--tool-contracts", str(catalog)
    )

    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_VALIDATION_ERROR"


def test_cli_002_filesystem_include_resolver_rejects_boundary_targets(
    tmp_path, monkeypatch
) -> None:
    non_directory = tmp_path / "not-a-directory"
    non_directory.write_text("content", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        FileSystemIncludeResolver(non_directory)

    resolver = FileSystemIncludeResolver(tmp_path)
    root = SourceFile.from_text("root.ns", "")
    (tmp_path / "directory.ns").mkdir()
    with pytest.raises(FileNotFoundError):
        resolver.resolve(root, "directory.ns")

    original_resolve = type(tmp_path).resolve
    outside = tmp_path.parent / "outside.ns"

    def resolve_outside(path, strict=False):
        if path.name == "link.ns":
            return outside
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(type(tmp_path), "resolve", resolve_outside)
    with pytest.raises(FileNotFoundError):
        resolver.resolve(root, "link.ns")


def test_cli_003_compile_writes_verified_nso_artifact(tmp_path) -> None:
    source = tmp_path / "compile.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")
    output_path = tmp_path / "compile.nso"

    code, output, error = invoke(
        "compile", str(source), "-o", str(output_path)
    )

    assert code == CLIExitCode.SUCCESS
    assert error is None
    assert output["command"] == "compile"
    assert output["status"] == "compiled"
    assert output["output"] == str(output_path)
    assert output["size_bytes"] == output_path.stat().st_size
    artifact = NsoCodec.decode(output_path.read_bytes())
    assert artifact.skill_id == "TEST.CLI_PARSE"
    assert artifact.semantic_hash == output["semantic_hash"]
    assert artifact.build.source_bundle_sha256 == output["source_bundle_hash"]


def test_cli_003_compile_requires_output_and_nso_extension(tmp_path) -> None:
    source = tmp_path / "compile.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")

    code, output, error = invoke("compile", str(source))
    assert code == CLIExitCode.USAGE_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_USAGE_ERROR"

    code, output, error = invoke(
        "compile", str(source), "-o", str(tmp_path / "compile.json")
    )
    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["message"] == "compiled output path must end with .nso"


def test_cli_003_compile_reports_output_io_failure(tmp_path) -> None:
    source = tmp_path / "compile.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")
    output_directory = tmp_path / "directory.nso"
    output_directory.mkdir()

    code, output, error = invoke(
        "compile", str(source), "-o", str(output_directory)
    )

    assert code == CLIExitCode.IO_ERROR
    assert output is None
    assert error == {
        "error": {"code": "CLI_IO_ERROR", "message": "permission denied"}
    }


def test_cli_004_inspect_verifies_and_returns_canonical_ir(tmp_path) -> None:
    source = tmp_path / "inspect.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")
    artifact_path = tmp_path / "inspect.nso"
    compile_code, _, _ = invoke(
        "compile", str(source), "-o", str(artifact_path)
    )
    assert compile_code == CLIExitCode.SUCCESS

    code, output, error = invoke("inspect", str(artifact_path))

    assert code == CLIExitCode.SUCCESS
    assert error is None
    assert output["command"] == "inspect"
    assert output["status"] == "valid"
    assert output["artifact"]["format"] == "NSO"
    assert output["artifact"]["skill"] == {
        "id": "TEST.CLI_PARSE",
        "version": "1.0.0",
        "risk": "READ_VALIDATE",
    }
    assert output["artifact"]["hashes"]["semantic_sha256"].startswith("sha256:")


def test_cli_004_inspect_rejects_invalid_or_missing_artifact(tmp_path) -> None:
    invalid = tmp_path / "invalid.nso"
    invalid.write_bytes(b'{"format":"NSO"}')

    code, output, error = invoke("inspect", str(invalid))
    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_VALIDATION_ERROR"

    code, output, error = invoke("inspect", str(tmp_path / "missing.nso"))
    assert code == CLIExitCode.IO_ERROR
    assert output is None
    assert error["error"]["message"] == "file not found"


def test_cli_011_return_codes_are_stable() -> None:
    assert {item.name: item.value for item in CLIExitCode} == {
        "SUCCESS": 0,
        "USAGE_ERROR": 2,
        "IO_ERROR": 3,
        "VALIDATION_ERROR": 4,
        "EXECUTION_ERROR": 5,
        "REPLAY_MISMATCH": 6,
        "INTERNAL_ERROR": 70,
    }


@pytest.mark.parametrize(
    ("raised_error", "expected_code", "expected_error_code"),
    [
        (
            CLIExecutionError("execution stopped"),
            CLIExitCode.EXECUTION_ERROR,
            "CLI_EXECUTION_ERROR",
        ),
        (
            CLIReplayMismatchError("result differs"),
            CLIExitCode.REPLAY_MISMATCH,
            "CLI_REPLAY_MISMATCH",
        ),
        (
            RuntimeError("sensitive internal detail"),
            CLIExitCode.INTERNAL_ERROR,
            "CLI_INTERNAL_ERROR",
        ),
    ],
)
def test_cli_011_expected_and_internal_failures_use_consistent_json(
    monkeypatch, raised_error, expected_code, expected_error_code
) -> None:
    class RaisingParser:
        def parse_args(self, _arguments):
            return cli_module.argparse.Namespace(
                handler=lambda _parsed: (_ for _ in ()).throw(raised_error)
            )

    monkeypatch.setattr(cli_module, "build_parser", RaisingParser)

    code, output, error = invoke("command")

    assert code == expected_code
    assert output is None
    assert error["error"]["code"] == expected_error_code
    if expected_code == CLIExitCode.INTERNAL_ERROR:
        assert error["error"]["message"] == "internal command failure"
        assert "sensitive" not in json.dumps(error)


def test_cli_005_run_source_returns_structured_execution_result(tmp_path) -> None:
    source = tmp_path / "run.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    write_json(principal, PRINCIPAL)

    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--execution-id",
        "exec-cli-source",
    )

    assert code == CLIExitCode.SUCCESS
    assert error is None
    assert output["command"] == "run"
    assert output["result"]["execution_id"] == "exec-cli-source"
    assert output["result"]["status"] == "COMPLETED"
    assert output["result"]["outputs"][0]["fields"][0]["value"] == "ok"
    assert output["result"]["resources"] == {
        "tool_calls": 0,
        "loop_iterations": 0,
        "emitted_rows": 1,
        "max_collection_size_seen": 0,
    }


def test_cli_005_run_requires_verified_principal_and_source_path(tmp_path) -> None:
    source = tmp_path / "run.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")

    code, output, error = invoke("run", str(source))
    assert code == CLIExitCode.USAGE_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_USAGE_ERROR"

    principal = tmp_path / "principal.json"
    write_json(principal, {**PRINCIPAL, "verification": "UNVERIFIED"})
    code, output, error = invoke(
        "run", str(source), "--principal", str(principal)
    )
    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert "verified execution principal is required" in error["error"]["message"]

    write_json(principal, PRINCIPAL)
    code, output, error = invoke(
        "run", str(tmp_path / "run.txt"), "--principal", str(principal)
    )
    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["message"] == "program path must end with .ns or .nso"


def test_cli_005_run_authorization_failure_uses_execution_return_code(tmp_path) -> None:
    source = tmp_path / "run.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    write_json(principal, {**PRINCIPAL, "scopes": []})

    code, output, error = invoke(
        "run", str(source), "--principal", str(principal)
    )

    assert code == CLIExitCode.EXECUTION_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_EXECUTION_ERROR"
    assert error["error"]["details"]["status"] == "FAILED"
    assert error["error"]["details"]["error"]["category"] == "AUTHORIZATION"


@pytest.mark.parametrize(
    "principal_data",
    [
        {**PRINCIPAL, "verification": "UNKNOWN"},
        {**PRINCIPAL, "roles": ["NSL_USER", "NSL_USER"]},
        {**PRINCIPAL, "roles": [""]},
        {**PRINCIPAL, "on_behalf_of": ""},
        {**PRINCIPAL, "actor_type": "ROBOT"},
        {**PRINCIPAL, "api_key": "must-not-be-accepted"},
    ],
)
def test_cli_005_run_rejects_malformed_principal(tmp_path, principal_data) -> None:
    source = tmp_path / "run.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    write_json(principal, principal_data)

    code, output, error = invoke(
        "run", str(source), "--principal", str(principal)
    )

    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_VALIDATION_ERROR"


def test_cli_006_run_nso_executes_verified_artifact(tmp_path) -> None:
    source = tmp_path / "run.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")
    artifact = tmp_path / "run.nso"
    compile_code, _, _ = invoke("compile", str(source), "-o", str(artifact))
    assert compile_code == CLIExitCode.SUCCESS
    principal = tmp_path / "principal.json"
    write_json(principal, PRINCIPAL)

    code, output, error = invoke(
        "run",
        str(artifact),
        "--principal",
        str(principal),
        "--execution-id",
        "exec-cli-nso",
    )

    assert code == CLIExitCode.SUCCESS
    assert error is None
    assert output["result"]["execution_id"] == "exec-cli-nso"
    assert output["result"]["status"] == "COMPLETED"
    assert output["result"]["skill_id"] == "TEST.CLI_PARSE"


def test_cli_006_run_nso_rejects_untrusted_artifact(tmp_path) -> None:
    artifact = tmp_path / "run.nso"
    artifact.write_bytes(b'{"format":"NSO"}')
    principal = tmp_path / "principal.json"
    write_json(principal, PRINCIPAL)

    code, output, error = invoke(
        "run", str(artifact), "--principal", str(principal)
    )

    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_VALIDATION_ERROR"


def test_cli_007_run_loads_input_json_object(tmp_path) -> None:
    source = tmp_path / "input.ns"
    source.write_text(INPUT_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    inputs = tmp_path / "input.json"
    write_json(principal, PRINCIPAL)
    write_json(inputs, {"year": 2026})

    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--input",
        str(inputs),
    )

    assert code == CLIExitCode.SUCCESS
    assert error is None
    field = output["result"]["outputs"][0]["fields"][0]
    assert field["name"] == "status"
    assert field["value"] == 2026


def test_cli_007_run_missing_or_wrong_input_is_execution_failure(tmp_path) -> None:
    source = tmp_path / "input.ns"
    source.write_text(INPUT_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    wrong_input = tmp_path / "wrong.json"
    write_json(principal, PRINCIPAL)
    write_json(wrong_input, {"year": "2026"})

    for arguments in (
        ("run", str(source), "--principal", str(principal)),
        (
            "run",
            str(source),
            "--principal",
            str(principal),
            "--input",
            str(wrong_input),
        ),
    ):
        code, output, error = invoke(*arguments)
        assert code == CLIExitCode.EXECUTION_ERROR
        assert output is None
        assert error["error"]["details"]["status"] == "FAILED"


@pytest.mark.parametrize("input_data", [[], {"api_key": "forbidden"}])
def test_cli_007_run_rejects_unsafe_input_document(tmp_path, input_data) -> None:
    source = tmp_path / "input.ns"
    source.write_text(INPUT_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    inputs = tmp_path / "input.json"
    write_json(principal, PRINCIPAL)
    write_json(inputs, input_data)

    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--input",
        str(inputs),
    )

    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_VALIDATION_ERROR"


def test_cli_008_run_loads_nested_context_json_object(tmp_path) -> None:
    source = tmp_path / "context.ns"
    source.write_text(CONTEXT_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    context = tmp_path / "context.json"
    write_json(principal, PRINCIPAL)
    write_json(context, {"user": {"team": "FINANCE"}})

    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--context",
        str(context),
    )

    assert code == CLIExitCode.SUCCESS
    assert error is None
    field = output["result"]["outputs"][0]["fields"][0]
    assert field["name"] == "status"
    assert field["value"] == "FINANCE"


def test_cli_008_run_missing_or_wrong_context_is_execution_failure(tmp_path) -> None:
    source = tmp_path / "context.ns"
    source.write_text(CONTEXT_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    wrong_context = tmp_path / "wrong-context.json"
    write_json(principal, PRINCIPAL)
    write_json(wrong_context, {"user": {"team": 7}})

    for arguments in (
        ("run", str(source), "--principal", str(principal)),
        (
            "run",
            str(source),
            "--principal",
            str(principal),
            "--context",
            str(wrong_context),
        ),
    ):
        code, output, error = invoke(*arguments)
        assert code == CLIExitCode.EXECUTION_ERROR
        assert output is None
        assert error["error"]["details"]["status"] == "FAILED"


def test_cli_008_run_rejects_non_object_context(tmp_path) -> None:
    source = tmp_path / "context.ns"
    source.write_text(CONTEXT_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    context = tmp_path / "context.json"
    write_json(principal, PRINCIPAL)
    write_json(context, ["not", "an", "object"])

    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--context",
        str(context),
    )

    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert "context must be a JSON object" in error["error"]["message"]


def test_cli_009_run_executes_exact_mock_fixture_case(tmp_path) -> None:
    source = tmp_path / "tool.ns"
    source.write_text(TOOL_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    catalog = tmp_path / "tools.json"
    inputs = tmp_path / "input.json"
    fixture = tmp_path / "fixture.json"
    write_json(principal, {**PRINCIPAL, "scopes": ["nsl:skill:execute", "test:read"]})
    write_json(catalog, {"tools": [TOOL_CONTRACT]})
    write_json(inputs, {"request_id": "REQ-001"})
    write_json(
        fixture,
        {
            "schema_version": "1.0",
            "tools": [
                {
                    "tool_id": "TEST.GET_STATUS",
                    "version": "1.0.0",
                    "cases": [
                        {
                            "arguments": {"request_id": "REQ-001"},
                            "result": "READY",
                        }
                    ],
                }
            ],
        },
    )

    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--tool-contracts",
        str(catalog),
        "--input",
        str(inputs),
        "--fixture",
        str(fixture),
    )

    assert code == CLIExitCode.SUCCESS
    assert error is None
    assert output["result"]["outputs"][0]["fields"][0]["value"] == "READY"
    assert output["result"]["resources"]["tool_calls"] == 1


def test_cli_009_run_missing_or_nonmatching_fixture_fails_execution(tmp_path) -> None:
    source = tmp_path / "tool.ns"
    source.write_text(TOOL_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    catalog = tmp_path / "tools.json"
    inputs = tmp_path / "input.json"
    fixture = tmp_path / "fixture.json"
    write_json(principal, {**PRINCIPAL, "scopes": ["nsl:skill:execute", "test:read"]})
    write_json(catalog, {"tools": [TOOL_CONTRACT]})
    write_json(inputs, {"request_id": "REQ-001"})
    write_json(
        fixture,
        {
            "schema_version": "1.0",
            "tools": [
                {
                    "tool_id": "TEST.GET_STATUS",
                    "version": "1.0.0",
                    "cases": [
                        {"arguments": {"request_id": "OTHER"}, "result": "READY"}
                    ],
                }
            ],
        },
    )

    common = (
        "run",
        str(source),
        "--principal",
        str(principal),
        "--tool-contracts",
        str(catalog),
        "--input",
        str(inputs),
    )
    for arguments in (common, common + ("--fixture", str(fixture))):
        code, output, error = invoke(*arguments)
        assert code == CLIExitCode.EXECUTION_ERROR
        assert output is None
        assert error["error"]["details"]["status"] == "TOOL_ERROR"


@pytest.mark.parametrize(
    "fixture_data",
    [
        [],
        {"schema_version": "2.0", "tools": []},
        {
            "schema_version": "1.0",
            "tools": [
                {"tool_id": "UNKNOWN", "version": "1.0.0", "cases": [{}]}
            ],
        },
        {
            "schema_version": "1.0",
            "tools": [
                {"tool_id": "TEST.GET_STATUS", "version": "1.0.0", "cases": []}
            ],
        },
        {
            "schema_version": "1.0",
            "tools": [
                {
                    "tool_id": "TEST.GET_STATUS",
                    "version": "1.0.0",
                    "cases": [{"arguments": {}, "result": "READY"}],
                },
                {
                    "tool_id": "TEST.GET_STATUS",
                    "version": "1.0.0",
                    "cases": [{"arguments": {}, "result": "DONE"}],
                },
            ],
        },
        {
            "schema_version": "1.0",
            "tools": [
                {
                    "tool_id": "TEST.GET_STATUS",
                    "version": "1.0.0",
                    "cases": [{"arguments": [], "result": "READY"}],
                }
            ],
        },
        {
            "schema_version": "1.0",
            "tools": [
                {
                    "tool_id": "TEST.GET_STATUS",
                    "version": "1.0.0",
                    "cases": [
                        {"arguments": {}, "result": "READY"},
                        {"arguments": {}, "result": "DONE"},
                    ],
                }
            ],
        },
        {
            "schema_version": "1.0",
            "tools": [
                {
                    "tool_id": "TEST.GET_STATUS",
                    "version": "1.0.0",
                    "cases": [{"arguments": {"api_key": "secret"}, "result": "READY"}],
                }
            ],
        },
    ],
)
def test_cli_009_run_rejects_malformed_fixture(tmp_path, fixture_data) -> None:
    source = tmp_path / "tool.ns"
    source.write_text(TOOL_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    catalog = tmp_path / "tools.json"
    fixture = tmp_path / "fixture.json"
    write_json(principal, PRINCIPAL)
    write_json(catalog, {"tools": [TOOL_CONTRACT]})
    write_json(fixture, fixture_data)

    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--tool-contracts",
        str(catalog),
        "--fixture",
        str(fixture),
    )

    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["code"] == "CLI_VALIDATION_ERROR"


def prepare_replay_files(tmp_path):
    source = tmp_path / "replay.ns"
    artifact = tmp_path / "replay.nso"
    principal = tmp_path / "principal.json"
    catalog = tmp_path / "tools.json"
    inputs = tmp_path / "input.json"
    fixture = tmp_path / "fixture.json"
    package = tmp_path / "execution.nsr"
    source.write_text(TOOL_SOURCE, encoding="utf-8")
    write_json(
        principal,
        {
            **PRINCIPAL,
            "scopes": ["nsl:skill:execute", "nsl:replay:read", "test:read"],
        },
    )
    write_json(catalog, {"tools": [TOOL_CONTRACT]})
    write_json(inputs, {"request_id": "REQ-REPLAY"})
    write_json(
        fixture,
        {
            "schema_version": "1.0",
            "tools": [
                {
                    "tool_id": "TEST.GET_STATUS",
                    "version": "1.0.0",
                    "cases": [
                        {
                            "arguments": {"request_id": "REQ-REPLAY"},
                            "result": "READY",
                        }
                    ],
                }
            ],
        },
    )
    compile_code, _, _ = invoke("compile", str(source), "-o", str(artifact), "--tool-contracts", str(catalog))
    assert compile_code == CLIExitCode.SUCCESS
    run_code, run_output, run_error = invoke(
        "run",
        str(artifact),
        "--principal",
        str(principal),
        "--tool-contracts",
        str(catalog),
        "--input",
        str(inputs),
        "--fixture",
        str(fixture),
        "--replay-out",
        str(package),
        "--execution-id",
        "exec-original-cli",
    )
    assert run_code == CLIExitCode.SUCCESS, run_error
    assert run_output["replay_package"] == str(package)
    return source, artifact, principal, catalog, package


def test_cli_010_replay_package_matches_recorded_execution(tmp_path) -> None:
    _, artifact, principal, catalog, package = prepare_replay_files(tmp_path)

    code, output, error = invoke(
        "replay",
        str(artifact),
        "--bundle",
        str(package),
        "--principal",
        str(principal),
        "--tool-contracts",
        str(catalog),
        "--execution-id",
        "exec-replayed-cli",
    )

    assert code == CLIExitCode.SUCCESS
    assert error is None
    assert output["command"] == "replay"
    assert output["matches"] is True
    assert output["differences"] == []
    assert output["tool_call_count"] == 1
    assert output["result"]["execution_id"] == "exec-replayed-cli"
    assert output["result"]["outputs"][0]["fields"][0]["value"] == "READY"


def test_cli_010_replay_reports_semantic_mismatch(tmp_path) -> None:
    _, artifact, principal, catalog, package = prepare_replay_files(tmp_path)
    document = json.loads(package.read_text(encoding="utf-8"))
    document["original_result"]["status"] = "FAILED"
    payload = {
        key: value for key, value in document.items() if key != "integrity_sha256"
    }
    document["integrity_sha256"] = value_hash(payload)
    write_json(package, document)

    code, output, error = invoke(
        "replay",
        str(artifact),
        "--bundle",
        str(package),
        "--principal",
        str(principal),
        "--tool-contracts",
        str(catalog),
    )

    assert code == CLIExitCode.REPLAY_MISMATCH
    assert output is None
    assert error["error"]["code"] == "CLI_REPLAY_MISMATCH"
    assert error["error"]["details"]["matches"] is False
    assert error["error"]["details"]["differences"] == [
        {"path": "/status", "original": "FAILED", "replayed": "COMPLETED"}
    ]


def test_cli_010_replay_rejects_tampered_package(tmp_path) -> None:
    _, artifact, principal, catalog, package = prepare_replay_files(tmp_path)
    document = json.loads(package.read_text(encoding="utf-8"))
    document["original_execution_id"] = "tampered"
    write_json(package, document)

    code, output, error = invoke(
        "replay",
        str(artifact),
        "--bundle",
        str(package),
        "--principal",
        str(principal),
        "--tool-contracts",
        str(catalog),
    )

    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert "integrity mismatch" in error["error"]["message"]


def test_cli_010_replay_requires_scope_and_nsr_extension(tmp_path) -> None:
    source = tmp_path / "replay.ns"
    source.write_text(VALID_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    write_json(principal, PRINCIPAL)

    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--replay-out",
        str(tmp_path / "execution.nsr"),
    )
    assert code == CLIExitCode.EXECUTION_ERROR
    assert output is None
    assert error["error"]["message"] == "nsl:replay:read scope is required"

    write_json(
        principal,
        {**PRINCIPAL, "scopes": ["nsl:skill:execute", "nsl:replay:read"]},
    )
    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--replay-out",
        str(tmp_path / "execution.json"),
    )
    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["message"] == "replay package output path must end with .nsr"


def test_cli_010_portable_package_rejects_high_classification_data(tmp_path) -> None:
    source = tmp_path / "protected.ns"
    source.write_text(
        INPUT_SOURCE.replace(
            "year: Year classification INTERNAL",
            "year: Year classification CONFIDENTIAL",
        ).replace(
            "status: Year classification INTERNAL",
            "status: Year classification CONFIDENTIAL",
        ),
        encoding="utf-8",
    )
    principal = tmp_path / "principal.json"
    inputs = tmp_path / "input.json"
    write_json(
        principal,
        {**PRINCIPAL, "scopes": ["nsl:skill:execute", "nsl:replay:read"]},
    )
    write_json(inputs, {"year": 2026})

    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--input",
        str(inputs),
        "--replay-out",
        str(tmp_path / "protected.nsr"),
    )

    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert "protected snapshot backend" in error["error"]["message"]
    assert not (tmp_path / "protected.nsr").exists()


def test_cli_010_replay_command_requires_replay_scope(tmp_path) -> None:
    _, artifact, principal, catalog, package = prepare_replay_files(tmp_path)
    write_json(principal, PRINCIPAL)

    code, output, error = invoke(
        "replay",
        str(artifact),
        "--bundle",
        str(package),
        "--principal",
        str(principal),
        "--tool-contracts",
        str(catalog),
    )

    assert code == CLIExitCode.EXECUTION_ERROR
    assert output is None
    assert error["error"]["message"] == "nsl:replay:read scope is required"


def test_cli_010_replay_rejects_malformed_package_boundaries(tmp_path) -> None:
    _, artifact, principal, catalog, package = prepare_replay_files(tmp_path)
    original = json.loads(package.read_text(encoding="utf-8"))

    def replace_value(path, value):
        def mutate(document):
            target = document
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value

        return mutate

    mutations = [
        replace_value(("format",), "BAD"),
        replace_value(("schema_version",), "2.0"),
        replace_value(("semantic_hash",), "sha256:" + "0" * 64),
        replace_value(("tenant_id",), "tenant-other"),
        replace_value(("classifications", "inputs"), "PUBLIC"),
        replace_value(("classifications", "inputs"), "SECRET"),
        replace_value(("inputs",), []),
        replace_value(("tool_calls",), {}),
        replace_value(("tool_calls", 0, "tool_id"), "UNKNOWN"),
        replace_value(("tool_calls", 0, "tool_version"), "9.0.0"),
        replace_value(("tool_calls", 0, "classification"), "PUBLIC"),
        replace_value(("tool_calls", 0, "result"), 7),
        replace_value(("tool_calls", 0, "argument_hash"), "bad"),
        replace_value(("tool_calls", 0, "argument_hash"), "sha256:bad"),
        replace_value(("tool_calls", 0, "argument_hash"), "sha256:" + "g" * 64),
        replace_value(("original_execution_id",), ""),
        replace_value(("runtime_version",), 1),
        lambda document: document.pop("runtime_version"),
        lambda document: document.update({"unexpected": True}),
        lambda document: document.clear(),
    ]

    for index, mutate in enumerate(mutations):
        document = json.loads(json.dumps(original))
        mutate(document)
        if document:
            payload = {
                key: value
                for key, value in document.items()
                if key != "integrity_sha256"
            }
            document["integrity_sha256"] = value_hash(payload)
        candidate = tmp_path / f"malformed-{index}.nsr"
        write_json(candidate, document if document else [])

        code, output, error = invoke(
            "replay",
            str(artifact),
            "--bundle",
            str(candidate),
            "--principal",
            str(principal),
            "--tool-contracts",
            str(catalog),
        )

        assert code == CLIExitCode.VALIDATION_ERROR, (index, error)
        assert output is None
        assert error["error"]["code"] == "CLI_VALIDATION_ERROR"


def test_cli_010_export_rejects_corrupt_recorded_tool_snapshot(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "corrupt.ns"
    source.write_text(TOOL_SOURCE, encoding="utf-8")
    principal = tmp_path / "principal.json"
    catalog = tmp_path / "tools.json"
    inputs = tmp_path / "input.json"
    fixture = tmp_path / "fixture.json"
    write_json(
        principal,
        {
            **PRINCIPAL,
            "scopes": ["nsl:skill:execute", "nsl:replay:read", "test:read"],
        },
    )
    write_json(catalog, {"tools": [TOOL_CONTRACT]})
    write_json(inputs, {"request_id": "REQ-CORRUPT"})
    write_json(
        fixture,
        {
            "schema_version": "1.0",
            "tools": [
                {
                    "tool_id": "TEST.GET_STATUS",
                    "version": "1.0.0",
                    "cases": [
                        {
                            "arguments": {"request_id": "REQ-CORRUPT"},
                            "result": "READY",
                        }
                    ],
                }
            ],
        },
    )
    original_get = InMemorySnapshotStore.get

    def corrupt_tool_result(store, reference, execution_principal):
        value = original_get(store, reference, execution_principal)
        return "invalid" if isinstance(value, ToolResultEnvelope) else value

    monkeypatch.setattr(InMemorySnapshotStore, "get", corrupt_tool_result)

    code, output, error = invoke(
        "run",
        str(source),
        "--principal",
        str(principal),
        "--tool-contracts",
        str(catalog),
        "--input",
        str(inputs),
        "--fixture",
        str(fixture),
        "--replay-out",
        str(tmp_path / "corrupt.nsr"),
    )

    assert code == CLIExitCode.INTERNAL_ERROR
    assert output is None
    assert error["error"]["message"] == "internal command failure"


def test_cli_010_replay_extra_recorded_call_is_mismatch(tmp_path) -> None:
    _, artifact, principal, catalog, package = prepare_replay_files(tmp_path)
    document = json.loads(package.read_text(encoding="utf-8"))
    document["tool_calls"].append(dict(document["tool_calls"][0]))
    payload = {
        key: value for key, value in document.items() if key != "integrity_sha256"
    }
    document["integrity_sha256"] = value_hash(payload)
    write_json(package, document)

    code, output, error = invoke(
        "replay",
        str(artifact),
        "--bundle",
        str(package),
        "--principal",
        str(principal),
        "--tool-contracts",
        str(catalog),
    )

    assert code == CLIExitCode.REPLAY_MISMATCH
    assert output is None
    assert error["error"]["message"] == (
        "recorded tool sequence differs from replay execution"
    )


def test_cli_010_nso_export_requires_compile_time_catalog(tmp_path) -> None:
    _, artifact, principal, _, _ = prepare_replay_files(tmp_path)

    code, output, error = invoke(
        "run",
        str(artifact),
        "--principal",
        str(principal),
        "--replay-out",
        str(tmp_path / "missing-catalog.nsr"),
    )

    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert error["error"]["message"] == "portable replay requires every tool contract"
