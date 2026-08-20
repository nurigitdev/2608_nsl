from __future__ import annotations

import json
from pathlib import Path

import pytest

import nsl.cli as cli_module
from nsl.cli import CLIExitCode, main
from nsl.cli_evidence import write_audit_jsonl, write_result_document
from nsl.cli_profile import (
    MAX_PROFILE_BYTES,
    LocalExecutionProfile,
    LocalExecutionProfileDocument,
    ProfileExecutionOptions,
    ResolvedCLIConfiguration,
    load_local_execution_profile,
    load_profile_document,
    resolve_cli_configuration,
)
from nsl.ir_schema import NsoSchemaError
from nsl.process_isolation import MAX_ISOLATED_TIMEOUT_MS


VALID_PROFILE = {
    "schema_version": "1.0",
    "program": "skill.ns",
    "principal": "principal.json",
    "tool_contracts": "tools.json",
    "input": "input.json",
    "context": "context.json",
    "fixture": "fixture.json",
    "execution": {"isolate": True, "timeout_ms": 12_345},
}

ROOT = Path(__file__).resolve().parents[1]


def _write_json(path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _invoke(*arguments: str):
    from io import StringIO

    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, output, error


def test_clx_001_profile_schema_is_strict_versioned_and_bounded(tmp_path) -> None:
    profile_path = tmp_path / "local.profile.json"
    _write_json(profile_path, VALID_PROFILE)

    assert load_profile_document(profile_path) == LocalExecutionProfileDocument(
        profile_path=profile_path,
        program="skill.ns",
        principal="principal.json",
        tool_contracts="tools.json",
        input="input.json",
        context="context.json",
        fixture="fixture.json",
        execution=ProfileExecutionOptions(isolate=True, timeout_ms=12_345),
    )

    minimal = {
        "schema_version": "1.0",
        "program": "skill.ns",
        "principal": "principal.json",
    }
    _write_json(profile_path, minimal)
    assert load_profile_document(profile_path).execution == ProfileExecutionOptions()

    invalid_documents = [
        [],
        {},
        {**minimal, "schema_version": "2.0"},
        {**minimal, "program": ""},
        {**minimal, "principal": None},
        {**minimal, "extra": True},
        {**minimal, "execution": []},
        {**minimal, "execution": {"extra": True}},
        {**minimal, "execution": {"isolate": 1}},
        {**minimal, "execution": {"timeout_ms": True}},
        {**minimal, "execution": {"timeout_ms": 0}},
        {**minimal, "execution": {"timeout_ms": MAX_ISOLATED_TIMEOUT_MS + 1}},
        {**minimal, "api_key": "secret"},
    ]
    for document in invalid_documents:
        _write_json(profile_path, document)
        with pytest.raises((NsoSchemaError, ValueError)):
            load_profile_document(profile_path)

    profile_path.write_bytes(
        b'{"schema_version":"1.0","program":"a.ns",'
        b'"program":"b.ns","principal":"principal.json"}'
    )
    with pytest.raises(NsoSchemaError, match="duplicate object field"):
        load_profile_document(profile_path)

    profile_path.write_bytes(b"\xff")
    with pytest.raises(NsoSchemaError, match="UTF-8"):
        load_profile_document(profile_path)

    profile_path.write_bytes(b" " * (MAX_PROFILE_BYTES + 1))
    with pytest.raises(NsoSchemaError, match="1 MiB"):
        load_profile_document(profile_path)


def test_clx_002_profile_paths_are_canonical_contained_files(
    tmp_path, monkeypatch
) -> None:
    profile_path = tmp_path / "local.profile.json"
    for name in (
        "skill.ns",
        "principal.json",
        "tools.json",
        "input.json",
        "context.json",
        "fixture.json",
    ):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    _write_json(profile_path, VALID_PROFILE)
    resolved = load_local_execution_profile(profile_path)
    assert resolved == LocalExecutionProfile(
        profile_path=profile_path.resolve(),
        root=tmp_path.resolve(),
        program=(tmp_path / "skill.ns").resolve(),
        principal=(tmp_path / "principal.json").resolve(),
        tool_contracts=(tmp_path / "tools.json").resolve(),
        input=(tmp_path / "input.json").resolve(),
        context=(tmp_path / "context.json").resolve(),
        fixture=(tmp_path / "fixture.json").resolve(),
        execution=ProfileExecutionOptions(isolate=True, timeout_ms=12_345),
    )

    outside = tmp_path.parent / "outside.ns"
    outside.write_text("outside", encoding="utf-8")
    for invalid_path in (str(outside.resolve()), "../outside.ns"):
        _write_json(profile_path, {**VALID_PROFILE, "program": invalid_path})
        with pytest.raises(NsoSchemaError, match="relative|escapes"):
            load_local_execution_profile(profile_path)

    directory = tmp_path / "directory.ns"
    directory.mkdir()
    _write_json(profile_path, {**VALID_PROFILE, "program": "directory.ns"})
    with pytest.raises(NsoSchemaError, match="reference a file"):
        load_local_execution_profile(profile_path)

    _write_json(profile_path, {**VALID_PROFILE, "program": "missing.ns"})
    with pytest.raises(FileNotFoundError):
        load_local_execution_profile(profile_path)

    linked = tmp_path / "linked.ns"
    linked.write_text("placeholder", encoding="utf-8")
    original_resolve = type(tmp_path).resolve

    def resolve_outside(path, strict=False):
        if path.name == "linked.ns":
            return outside.resolve()
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(type(tmp_path), "resolve", resolve_outside)
    _write_json(profile_path, {**VALID_PROFILE, "program": "linked.ns"})
    with pytest.raises(NsoSchemaError, match="escapes"):
        load_local_execution_profile(profile_path)


def test_clx_003_cli_options_deterministically_override_profile(tmp_path) -> None:
    profile_path = tmp_path / "local.profile.json"
    for name in (
        "skill.ns",
        "principal.json",
        "tools.json",
        "input.json",
        "context.json",
        "fixture.json",
    ):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    _write_json(profile_path, VALID_PROFILE)

    explicit_program = tmp_path.parent / "explicit.nso"
    explicit_principal = tmp_path.parent / "explicit-principal.json"
    resolved = resolve_cli_configuration(
        profile_path=profile_path,
        program=explicit_program,
        principal=explicit_principal,
        isolate=False,
        timeout_ms=1,
    )
    assert resolved == ResolvedCLIConfiguration(
        profile_path=profile_path.resolve(),
        program=explicit_program,
        principal=explicit_principal,
        tool_contracts=(tmp_path / "tools.json").resolve(),
        input=(tmp_path / "input.json").resolve(),
        context=(tmp_path / "context.json").resolve(),
        fixture=(tmp_path / "fixture.json").resolve(),
        isolate=False,
        timeout_ms=1,
    )

    direct = resolve_cli_configuration(
        program=explicit_program,
        principal=explicit_principal,
    )
    assert direct == ResolvedCLIConfiguration(
        profile_path=None,
        program=explicit_program,
        principal=explicit_principal,
        tool_contracts=None,
        input=None,
        context=None,
        fixture=None,
        isolate=False,
        timeout_ms=30_000,
    )


def test_clx_004_profile_drives_check_compile_and_source_or_nso_run(tmp_path) -> None:
    source = tmp_path / "skill.ns"
    source.write_text(
        '''language NSL "0.1";
skill TEST.PROFILE {
    version "1.0.0";
    risk READ_VALIDATE;
    limits { tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1; }
    output { status: String classification INTERNAL; }
    emit { status: "ok"; }
}
''',
        encoding="utf-8",
    )
    principal = tmp_path / "principal.json"
    _write_json(
        principal,
        {
            "tenant_id": "tenant-profile",
            "subject_id": "user-profile",
            "actor_type": "USER",
            "roles": ["NSL_USER"],
            "scopes": ["nsl:skill:execute"],
            "auth_context_ref": "auth-profile-001",
            "verification": "VERIFIED",
        },
    )
    profile = tmp_path / "local.profile.json"
    _write_json(
        profile,
        {
            "schema_version": "1.0",
            "program": "skill.ns",
            "principal": "principal.json",
        },
    )

    code, output, error = _invoke("check", "--profile", str(profile))
    assert code == CLIExitCode.SUCCESS, error
    assert output["skill_id"] == "TEST.PROFILE"

    artifact = tmp_path / "skill.nso"
    code, output, error = _invoke(
        "compile", "--profile", str(profile), "-o", str(artifact)
    )
    assert code == CLIExitCode.SUCCESS, error
    assert output["status"] == "compiled"

    code, output, error = _invoke("run", "--profile", str(profile))
    assert code == CLIExitCode.SUCCESS, error
    assert output["result"]["status"] == "COMPLETED"

    code, output, error = _invoke(
        "run", str(artifact), "--profile", str(profile)
    )
    assert code == CLIExitCode.SUCCESS, error
    assert output["result"]["skill_id"] == "TEST.PROFILE"

    for arguments in (("check",), ("run",)):
        code, output, error = _invoke(*arguments)
        assert code == CLIExitCode.USAGE_ERROR
        assert output is None
        assert error["error"]["code"] == "CLI_USAGE_ERROR"


def test_clx_005_project_budget_example_is_complete_and_executable() -> None:
    profile = ROOT / "examples" / "project_budget_check.profile.json"

    code, output, error = _invoke("check", "--profile", str(profile))
    assert code == CLIExitCode.SUCCESS, error
    assert output["skill_id"] == "FINANCE.PROJECT_BUDGET_CHECK"

    code, output, error = _invoke(
        "run", "--profile", str(profile), "--execution-id", "example-success"
    )
    assert code == CLIExitCode.SUCCESS, error
    result = output["result"]
    assert result["execution_id"] == "example-success"
    assert result["status"] == "COMPLETED"
    assert result["completeness"] == "COMPLETE"
    assert result["checks"][0]["status"] == "PASS"
    fields = {item["name"]: item["value"] for item in result["outputs"][0]["fields"]}
    assert fields["spent"] == {
        "$type": "Money",
        "amount": "87500000",
        "currency": "KRW",
    }
    assert fields["remaining"] == {
        "$type": "Money",
        "amount": "12500000",
        "currency": "KRW",
    }


def test_clx_006_dry_run_validates_without_tool_execution(
    tmp_path, monkeypatch
) -> None:
    profile = ROOT / "examples" / "project_budget_check.profile.json"

    async def forbidden_execute(*_args, **_kwargs):
        raise AssertionError("dry-run must not execute the Skill")

    monkeypatch.setattr(cli_module, "execute_skill", forbidden_execute)
    code, output, error = _invoke(
        "run", "--profile", str(profile), "--dry-run"
    )
    assert code == CLIExitCode.SUCCESS, error
    assert output["command"] == "run"
    assert output["mode"] == "dry-run"
    assert output["status"] == "ready"

    code, output, error = _invoke(
        "run",
        "--profile",
        str(profile),
        "--dry-run",
        "--replay-out",
        str(tmp_path / "execution.nsr"),
    )
    assert code == CLIExitCode.USAGE_ERROR
    assert output is None
    assert "cannot be combined" in error["error"]["message"]

    principal = tmp_path / "principal.json"
    source_principal = json.loads(
        (ROOT / "examples/project_budget_check_cli/principal.json").read_text(
            encoding="utf-8"
        )
    )
    _write_json(
        principal,
        {
            **source_principal,
            "scopes": ["nsl:skill:execute"],
        },
    )
    code, output, error = _invoke(
        "run",
        "--profile",
        str(profile),
        "--principal",
        str(principal),
        "--dry-run",
    )
    assert code == CLIExitCode.EXECUTION_ERROR
    assert output is None
    assert "project:budget:read" in error["error"]["message"]

    invalid_input = tmp_path / "input.json"
    _write_json(invalid_input, {"year": True})
    code, output, error = _invoke(
        "run",
        "--profile",
        str(profile),
        "--input",
        str(invalid_input),
        "--dry-run",
    )
    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None
    assert "runtime type mismatch" in error["error"]["message"]


def test_clx_007_dry_run_report_is_deterministic_safe_and_complete() -> None:
    profile = ROOT / "examples" / "project_budget_check.profile.json"

    first = _invoke("run", "--profile", str(profile), "--dry-run")
    second = _invoke("run", "--profile", str(profile), "--dry-run")
    assert first == second
    code, output, error = first
    assert code == CLIExitCode.SUCCESS, error
    assert output["program_kind"] == "ns"
    assert output["profile"] == "project_budget_check.profile.json"
    assert output["skill"]["id"] == "FINANCE.PROJECT_BUDGET_CHECK"
    assert output["skill"]["version"] == "1.0.0"
    assert output["skill"]["semantic_hash"].startswith("sha256:")
    assert output["skill"]["source_bundle_hash"].startswith("sha256:")
    assert output["skill"]["sources"] == ["project_budget_check.ns"]
    assert output["required_scopes"] == [
        "nsl:skill:execute",
        "project:budget:read",
    ]
    assert [item["tool_id"] for item in output["required_tools"]] == [
        "PROJECT.LIST_PARENT_PROJECTS",
        "PROJECT.LIST_CHILD_PROJECTS",
    ]
    assert output["inputs"] == [
        {
            "name": "year",
            "type": {"kind": "primitive", "name": "Year"},
            "required": True,
            "classification": "INTERNAL",
        }
    ]
    assert output["contexts"][0]["path"] == "user.team_id"
    assert output["limits"] == {
        "tool_calls": 11,
        "loop_iterations": 10,
        "emitted_rows": 10,
        "collection_size": 1000,
        "duration_ms": 60000,
    }
    serialized = json.dumps(output)
    assert "auth-context-demo-001" not in serialized
    assert str(ROOT) not in serialized


def test_clx_008_result_and_redacted_audit_evidence_are_atomic(tmp_path) -> None:
    profile = ROOT / "examples" / "project_budget_check.profile.json"
    result_path = tmp_path / "result.json"
    audit_path = tmp_path / "audit.jsonl"

    code, output, error = _invoke(
        "run",
        "--profile",
        str(profile),
        "--result-out",
        str(result_path),
        "--audit-out",
        str(audit_path),
    )
    assert code == CLIExitCode.SUCCESS, error
    assert json.loads(result_path.read_text(encoding="utf-8")) == output
    audit = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert audit
    assert [item["sequence"] for item in audit] == list(range(1, len(audit) + 1))
    assert all(item["event_hash"].startswith("sha256:") for item in audit)
    serialized_audit = json.dumps(audit)
    assert "100000000" not in serialized_audit
    assert not list(tmp_path.glob(".*.nsl-tmp"))

    dry_result = tmp_path / "dry-result.json"
    code, output, error = _invoke(
        "run",
        "--profile",
        str(profile),
        "--dry-run",
        "--result-out",
        str(dry_result),
    )
    assert code == CLIExitCode.SUCCESS, error
    assert json.loads(dry_result.read_text(encoding="utf-8")) == output

    for option, path in (
        ("--result-out", tmp_path / "result.txt"),
        ("--audit-out", tmp_path / "audit.json"),
    ):
        code, output, error = _invoke(
            "run", "--profile", str(profile), option, str(path)
        )
        assert code == CLIExitCode.VALIDATION_ERROR
        assert output is None

    code, output, error = _invoke(
        "run",
        "--profile",
        str(profile),
        "--dry-run",
        "--audit-out",
        str(audit_path),
    )
    assert code == CLIExitCode.USAGE_ERROR
    assert output is None

    with pytest.raises(ValueError, match="credential material"):
        write_result_document(result_path, {"api_key": "forbidden"})

    class UnsafeEvent:
        def to_data(self):
            return {"authorization": "Bearer forbidden"}

    with pytest.raises(ValueError, match="credential material"):
        write_audit_jsonl(audit_path, [UnsafeEvent()])


def test_clx_009_timing_is_separate_consistent_observability(tmp_path) -> None:
    profile = ROOT / "examples" / "project_budget_check.profile.json"
    artifact = tmp_path / "project_budget_check.nso"
    code, _, error = _invoke(
        "compile", "--profile", str(profile), "-o", str(artifact)
    )
    assert code == CLIExitCode.SUCCESS, error

    code, source_output, error = _invoke(
        "run",
        "--profile",
        str(profile),
        "--timing",
        "--execution-id",
        "timed-source",
    )
    assert code == CLIExitCode.SUCCESS, error
    timing = source_output["timing"]
    assert type(timing["source_compile_ns"]) is int
    assert timing["source_compile_ns"] >= 0
    assert timing["nso_load_ns"] is None
    assert timing["runtime_total_ns"] >= 0
    assert timing["tool_total_ns"] >= 0
    assert timing["runtime_overhead_ns"] == max(
        0, timing["runtime_total_ns"] - timing["tool_total_ns"]
    )
    assert timing["tool_call_count"] == 2
    assert "timing" not in source_output["result"]

    audit = tmp_path / "timed-audit.jsonl"
    code, artifact_output, error = _invoke(
        "run",
        str(artifact),
        "--profile",
        str(profile),
        "--timing",
        "--audit-out",
        str(audit),
    )
    assert code == CLIExitCode.SUCCESS, error
    assert artifact_output["timing"]["source_compile_ns"] is None
    assert type(artifact_output["timing"]["nso_load_ns"]) is int
    assert "duration_ns" not in audit.read_text(encoding="utf-8")

    code, dry_output, error = _invoke(
        "run", "--profile", str(profile), "--dry-run", "--timing"
    )
    assert code == CLIExitCode.SUCCESS, error
    assert set(dry_output["timing"]) == {"source_compile_ns", "nso_load_ns"}


def test_clx_010_process_isolation_success_timeout_and_override(tmp_path) -> None:
    profile = ROOT / "examples" / "project_budget_check.profile.json"

    code, isolated, error = _invoke(
        "run", "--profile", str(profile), "--execution-id", "isolated-same"
    )
    assert code == CLIExitCode.SUCCESS, error
    assert isolated["isolation"] == {"status": "COMPLETED", "exit_code": 0}

    code, direct, error = _invoke(
        "run",
        "--profile",
        str(profile),
        "--no-isolate",
        "--execution-id",
        "isolated-same",
    )
    assert code == CLIExitCode.SUCCESS, error
    assert "isolation" not in direct
    assert isolated["result"] == direct["result"]

    code, output, error = _invoke(
        "run",
        "--profile",
        str(profile),
        "--isolate",
        "--timeout-ms",
        "1",
    )
    assert code == CLIExitCode.EXECUTION_ERROR
    assert output is None
    assert error["error"]["details"]["isolation"]["code"] == (
        "ISOLATED_RUNTIME_TIMEOUT"
    )

    code, output, error = _invoke(
        "run",
        "--profile",
        str(profile),
        "--isolate",
        "--replay-out",
        str(tmp_path / "execution.nsr"),
    )
    assert code == CLIExitCode.USAGE_ERROR
    assert output is None
    assert "cannot be combined" in error["error"]["message"]

    for timeout in ("0", str(2_147_483_648)):
        code, output, error = _invoke(
            "run",
            "--profile",
            str(profile),
            "--isolate",
            "--timeout-ms",
            timeout,
        )
        assert code == CLIExitCode.VALIDATION_ERROR
        assert output is None
        assert "timeout_ms" in error["error"]["message"]


def test_clx_010_isolated_wire_rejects_malformed_requests_and_responses(
    monkeypatch,
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from nsl import cli_isolation as isolation_module
    from nsl.cli_isolation import (
        CLIIsolationError,
        ISOLATED_CLI_FORMAT,
        ISOLATED_CLI_SCHEMA_VERSION,
        encode_isolated_cli_request,
        execute_isolated_cli_request,
        run_isolated_cli,
    )
    from nsl.compiler import NslCompiler
    from nsl.ir import canonical_json
    from nsl.ir_schema import NsoSchemaError, load_nso_json
    from nsl.process_isolation import (
        MAX_ISOLATED_PAYLOAD_BYTES,
        IsolatedProcessResult,
        IsolatedProcessStatus,
    )
    from nsl.vertical_slice import build_principal, build_tool_catalog

    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(
        (ROOT / "examples/project_budget_check.ns").read_text(encoding="utf-8")
    ).skill
    principal = build_principal()
    fixture = json.loads(
        (ROOT / "examples/project_budget_check_cli/fixtures/success.json").read_text(
            encoding="utf-8"
        )
    )
    payload = encode_isolated_cli_request(
        skill,
        catalog,
        principal,
        "wire-valid",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        fixture_document=fixture,
        measure_timing=False,
    )
    response_payload = execute_isolated_cli_request(payload)

    request_document = load_nso_json(payload)

    def request_with(field, value):
        document = dict(request_document)
        if field is None:
            document.pop("fixture")
        else:
            document[field] = value
        return canonical_json(document)

    invalid_requests = [
        request_with(None, None),
        request_with("format", "INVALID"),
        request_with("schema_version", "2.0"),
        request_with("execution_id", ""),
        request_with("nso_base64", 1),
        request_with("nso_base64", "%%%"),
        request_with("measure_timing", 1),
        request_with("inputs", []),
    ]
    for invalid in invalid_requests:
        with pytest.raises((ValueError, NsoSchemaError)):
            execute_isolated_cli_request(invalid)

    with pytest.raises(NsoSchemaError, match="canonical JSON"):
        execute_isolated_cli_request(payload + b" ")
    with pytest.raises(ValueError, match="payload limit"):
        execute_isolated_cli_request(b" " * (MAX_ISOLATED_PAYLOAD_BYTES + 1))

    empty_payload = encode_isolated_cli_request(
        skill,
        catalog,
        replace(principal, on_behalf_of="requester-001"),
        "wire-empty",
        inputs=None,
        runtime_context=None,
        fixture_document={"schema_version": "1.0", "tools": []},
        measure_timing=False,
    )
    empty_document = load_nso_json(empty_payload)
    assert empty_document["inputs"] == {}
    assert empty_document["runtime_context"] == {}
    assert empty_document["principal"]["on_behalf_of"] == "requester-001"

    with pytest.raises(ValueError, match="payload limit"):
        encode_isolated_cli_request(
            skill,
            catalog,
            principal,
            "wire-large",
            inputs={},
            runtime_context={},
            fixture_document={"padding": "x" * MAX_ISOLATED_PAYLOAD_BYTES},
            measure_timing=False,
        )

    class FakeBoundary:
        result = IsolatedProcessResult(
            IsolatedProcessStatus.COMPLETED,
            response_payload,
            0,
            None,
        )

        def __init__(self, _target, *, timeout_ms):
            assert timeout_ms == 100

        def execute(self, _payload):
            return self.result

    monkeypatch.setattr(isolation_module, "ProcessIsolatedRuntime", FakeBoundary)
    valid = run_isolated_cli(payload, timeout_ms=100)
    assert valid.result["status"] == "COMPLETED"
    assert valid.timing is None

    response_document = load_nso_json(response_payload)

    def response_with(field, value):
        document = dict(response_document)
        if field is None:
            document.pop("audit")
        else:
            document[field] = value
        return canonical_json(document)

    invalid_responses = [
        response_with(None, None),
        response_with("format", "INVALID"),
        response_with("schema_version", "2.0"),
        response_with("result", []),
        response_with("result", {**response_document["result"], "status": "BAD"}),
        response_with("audit", {}),
        response_with("audit", [{}]),
        response_with(
            "timing",
            {
                "runtime_total_ns": -1,
                "tool_total_ns": 0,
                "runtime_overhead_ns": 0,
                "tool_call_count": 0,
            },
        ),
        response_with(
            "timing",
            {
                "runtime_total_ns": 10,
                "tool_total_ns": 1,
                "runtime_overhead_ns": 1,
                "tool_call_count": 1,
            },
        ),
    ]
    for invalid in invalid_responses:
        FakeBoundary.result = IsolatedProcessResult(
            IsolatedProcessStatus.COMPLETED,
            invalid,
            0,
            None,
        )
        with pytest.raises(CLIIsolationError, match="response is invalid"):
            run_isolated_cli(payload, timeout_ms=100)

    statuses = [
        (
            IsolatedProcessStatus.TIMED_OUT,
            -15,
            "ISOLATED_RUNTIME_TIMEOUT",
            "timed out",
        ),
        (IsolatedProcessStatus.CRASHED, 23, "ISOLATED_RUNTIME_CRASH", "crashed"),
        (
            IsolatedProcessStatus.TARGET_ERROR,
            0,
            "ISOLATED_RUNTIME_TARGET_ERROR",
            "execution failed",
        ),
        (
            IsolatedProcessStatus.PROTOCOL_ERROR,
            0,
            "ISOLATED_RUNTIME_PROTOCOL_ERROR",
            "protocol failed",
        ),
    ]
    for status, exit_code, error_code, message in statuses:
        FakeBoundary.result = IsolatedProcessResult(
            status, None, exit_code, error_code
        )
        with pytest.raises(CLIIsolationError, match=message) as captured:
            run_isolated_cli(payload, timeout_ms=100)
        assert captured.value.code == error_code

    FakeBoundary.result = SimpleNamespace(
        status=IsolatedProcessStatus.PROTOCOL_ERROR,
        payload=None,
        exit_code=0,
        error_code=None,
    )
    with pytest.raises(CLIIsolationError) as captured:
        run_isolated_cli(payload, timeout_ms=100)
    assert captured.value.code == "ISOLATED_RUNTIME_FAILED"

    FakeBoundary.result = IsolatedProcessResult(
        IsolatedProcessStatus.COMPLETED,
        response_payload + b" ",
        0,
        None,
    )
    with pytest.raises(CLIIsolationError, match="response is invalid"):
        run_isolated_cli(payload, timeout_ms=100)

    assert ISOLATED_CLI_FORMAT == "NSL-CLI-ISOLATED-EXECUTION"
    assert ISOLATED_CLI_SCHEMA_VERSION == "1.0"


def test_clx_011_scenario_suite_schema_and_command_are_strict(tmp_path) -> None:
    profile = ROOT / "examples" / "project_budget_check.profile.json"
    local_profile = tmp_path / "local.profile.json"
    local_profile.write_bytes(profile.read_bytes())
    suite = tmp_path / "scenarios.json"
    valid = {
        "schema_version": "1.0",
        "profile": "local.profile.json",
        "cases": [
            {
                "id": "success-001",
                "isolate": False,
                "timeout_ms": 100,
                "expect": {
                    "exit_code": 0,
                    "status": "COMPLETED",
                    "completeness": "COMPLETE",
                    "checks": {"BUDGET_LIMIT": "PASS"},
                    "outputs": [{"status": "PASS"}],
                    "resources": {"tool_calls": 2},
                },
            }
        ],
    }
    _write_json(suite, valid)

    code, output, error = _invoke("test", "--suite", str(suite))
    assert code == CLIExitCode.SCENARIO_MISMATCH
    assert output is None
    assert error["error"]["code"] == "CLI_SCENARIO_MISMATCH"
    assert error["error"]["details"] == {
        "command": "test",
        "status": "failed",
        "suite": "scenarios.json",
        "profile": "local.profile.json",
        "case_count": 1,
        "passed": 0,
        "failed": 1,
        "cases": [
            {
                "id": "success-001",
                "exit_code": 3,
                "execution_id": None,
                "passed": False,
                "mismatches": [
                    {"path": "exit_code", "expected": 0, "actual": 3},
                    {
                        "path": "result.status",
                        "expected": "COMPLETED",
                        "actual": None,
                    },
                    {
                        "path": "result.completeness",
                        "expected": "COMPLETE",
                        "actual": None,
                    },
                    {
                        "path": "checks.BUDGET_LIMIT",
                        "expected": "PASS",
                        "actual": None,
                    },
                    {
                        "path": "result.outputs",
                        "expected": [{"status": "PASS"}],
                        "actual": [],
                    },
                    {
                        "path": "resources.tool_calls",
                        "expected": 2,
                        "actual": None,
                    },
                ],
            }
        ],
    }

    invalid_documents = [
        [],
        {},
        {**valid, "schema_version": "2.0"},
        {**valid, "extra": True},
        {**valid, "cases": []},
        {**valid, "cases": valid["cases"] * 1001},
        {**valid, "cases": valid["cases"] * 2},
        {**valid, "cases": [{"id": "bad id", "expect": {"exit_code": 0}}]},
        {**valid, "cases": [{"id": "case", "expect": []}]},
        {**valid, "cases": [{"id": "case", "expect": {"exit_code": -1}}]},
        {**valid, "cases": [{"id": "case", "expect": {"exit_code": 256}}]},
        {**valid, "cases": [{"id": "case", "expect": {"exit_code": 0, "status": "BAD"}}]},
        {**valid, "cases": [{"id": "case", "expect": {"exit_code": 0, "completeness": "BAD"}}]},
        {**valid, "cases": [{"id": "case", "expect": {"exit_code": 0, "checks": []}}]},
        {**valid, "cases": [{"id": "case", "expect": {"exit_code": 0, "checks": {"C": "BAD"}}}]},
        {**valid, "cases": [{"id": "case", "expect": {"exit_code": 0, "outputs": [1]}}]},
        {**valid, "cases": [{"id": "case", "expect": {"exit_code": 0, "resources": {"bad": 1}}}]},
        {**valid, "cases": [{"id": "case", "expect": {"exit_code": 0, "resources": {"tool_calls": -1}}}]},
        {**valid, "cases": [{"id": "case", "isolate": 1, "expect": {"exit_code": 0}}]},
        {**valid, "cases": [{"id": "case", "timeout_ms": 0, "expect": {"exit_code": 0}}]},
        {**valid, "api_key": "forbidden"},
    ]
    for document in invalid_documents:
        _write_json(suite, document)
        code, output, error = _invoke("test", "--suite", str(suite))
        assert code == CLIExitCode.VALIDATION_ERROR
        assert output is None

    suite.write_bytes(b" " * (8 * 1024 * 1024 + 1))
    code, output, error = _invoke("test", "--suite", str(suite))
    assert code == CLIExitCode.VALIDATION_ERROR
    assert output is None


def test_clx_012_scenario_cases_execute_with_fresh_runtime_state(tmp_path) -> None:
    source = tmp_path / "skill.ns"
    source.write_text(
        '''language NSL "0.1";
skill TEST.SCENARIO {
    version "1.0.0";
    risk READ_VALIDATE;
    limits { tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1; }
    output { status: String classification INTERNAL; }
    emit { status: "ok"; }
}
''',
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "principal.json",
        {
            "tenant_id": "tenant-scenario",
            "subject_id": "user-scenario",
            "actor_type": "USER",
            "roles": ["NSL_USER"],
            "scopes": ["nsl:skill:execute"],
            "auth_context_ref": "auth-scenario-001",
            "verification": "VERIFIED",
        },
    )
    _write_json(
        tmp_path / "profile.json",
        {
            "schema_version": "1.0",
            "program": "skill.ns",
            "principal": "principal.json",
        },
    )
    _write_json(
        tmp_path / "suite.json",
        {
            "schema_version": "1.0",
            "profile": "profile.json",
            "cases": [
                {"id": "first", "expect": {"exit_code": 0}},
                {"id": "second", "expect": {"exit_code": 0}},
            ],
        },
    )

    code, output, error = _invoke(
        "test", "--suite", str(tmp_path / "suite.json")
    )
    assert code == CLIExitCode.SUCCESS, error
    assert output["status"] == "passed"
    assert output["passed"] == 2
    assert output["failed"] == 0
    assert [item["execution_id"] for item in output["cases"]] == [
        "scenario-first",
        "scenario-second",
    ]
    assert all(item["passed"] for item in output["cases"])


def test_clx_013_scenario_assertions_cover_semantic_result_dimensions() -> None:
    from pathlib import Path

    from nsl.cli_scenarios import (
        ScenarioCase,
        ScenarioExpectation,
        ScenarioInvocation,
        ScenarioSuite,
        evaluate_scenario_suite,
    )

    expectation = ScenarioExpectation(
        exit_code=5,
        status="TOOL_ERROR",
        completeness="UNKNOWN",
        checks=(("CHECK_A", "UNKNOWN"),),
        outputs=({"status": "UNKNOWN"},),
        error_code="NSL-E4101",
        resources=(("tool_calls", 1),),
    )
    case = ScenarioCase(
        "failure",
        None,
        None,
        None,
        None,
        None,
        None,
        expectation,
    )
    suite = ScenarioSuite(Path("suite.json"), Path("."), Path("profile.json"), (case,))
    result = {
        "execution_id": "scenario-failure",
        "status": "TOOL_ERROR",
        "completeness": "UNKNOWN",
        "checks": [{"check_id": "CHECK_A", "status": "UNKNOWN"}],
        "outputs": [
            {"fields": [{"name": "status", "value": "UNKNOWN"}]}
        ],
        "resources": {"tool_calls": 1},
        "error": {"code": "NSL-E4101"},
    }
    matching = ScenarioInvocation(
        "failure",
        5,
        None,
        {"error": {"code": "CLI_EXECUTION_ERROR", "details": result}},
    )
    evaluation = evaluate_scenario_suite(suite, (matching,))[0]
    assert evaluation.passed
    assert evaluation.mismatches == ()

    mismatching = ScenarioInvocation(
        "failure",
        0,
        {"result": {**result, "status": "COMPLETED", "checks": [], "outputs": []}},
        None,
    )
    evaluation = evaluate_scenario_suite(suite, (mismatching,))[0]
    assert not evaluation.passed
    assert {item.path for item in evaluation.mismatches} == {
        "exit_code",
        "result.status",
        "checks.CHECK_A",
        "result.outputs",
    }


def test_clx_014_scenario_summary_order_and_exit_code_are_deterministic(
    tmp_path,
) -> None:
    source = tmp_path / "skill.ns"
    source.write_text(
        '''language NSL "0.1";
skill TEST.SCENARIO_SUMMARY {
    version "1.0.0";
    risk READ_VALIDATE;
    limits { tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1; }
    output { status: String classification INTERNAL; }
    emit { status: "ok"; }
}
''',
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "principal.json",
        {
            "tenant_id": "tenant-summary",
            "subject_id": "user-summary",
            "actor_type": "USER",
            "roles": ["NSL_USER"],
            "scopes": ["nsl:skill:execute"],
            "auth_context_ref": "auth-summary-001",
            "verification": "VERIFIED",
        },
    )
    _write_json(
        tmp_path / "profile.json",
        {
            "schema_version": "1.0",
            "program": "skill.ns",
            "principal": "principal.json",
        },
    )
    suite = tmp_path / "suite.json"
    _write_json(
        suite,
        {
            "schema_version": "1.0",
            "profile": "profile.json",
            "cases": [
                {"id": "pass-first", "expect": {"exit_code": 0}},
                {
                    "id": "fail-second",
                    "expect": {"exit_code": 0, "status": "TOOL_ERROR"},
                },
            ],
        },
    )

    first = _invoke("test", "--suite", str(suite))
    second = _invoke("test", "--suite", str(suite))
    assert first == second
    code, output, error = first
    assert code == CLIExitCode.SCENARIO_MISMATCH
    assert output is None
    assert error["error"]["code"] == "CLI_SCENARIO_MISMATCH"
    summary = error["error"]["details"]
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert [item["id"] for item in summary["cases"]] == [
        "pass-first",
        "fail-second",
    ]
    assert summary["cases"][1]["mismatches"] == [
        {
            "path": "result.status",
            "expected": "TOOL_ERROR",
            "actual": "COMPLETED",
        }
    ]


def test_clx_015_project_budget_scenarios_and_runner_boundaries(tmp_path) -> None:
    from nsl.cli_scenarios import (
        ScenarioCase,
        ScenarioExpectation,
        ScenarioInvocation,
        ScenarioSuite,
        evaluate_scenario_suite,
        execute_scenario_suite,
        load_scenario_suite,
    )

    code, output, error = _invoke(
        "test", "--suite", str(ROOT / "examples/project_budget_check.scenarios.json")
    )
    assert code == CLIExitCode.SUCCESS, error
    assert output["status"] == "passed"
    assert output["passed"] == 7
    assert output["failed"] == 0
    assert [item["id"] for item in output["cases"]] == [
        "success",
        "budget-overrun",
        "tool-fixture-missing",
        "authorization-denied",
        "invalid-input",
        "resource-limit",
        "deterministic-repeat",
    ]

    profile = tmp_path / "profile.json"
    principal = tmp_path / "principal.json"
    input_path = tmp_path / "input.json"
    context = tmp_path / "context.json"
    fixture = tmp_path / "fixture.json"
    for path in (profile, principal, input_path, context, fixture):
        _write_json(path, {})
    suite_path = tmp_path / "suite.json"
    valid_document = {
        "schema_version": "1.0",
        "profile": "profile.json",
        "cases": [
            {
                "id": "boundary",
                "principal": "principal.json",
                "input": "input.json",
                "context": "context.json",
                "fixture": "fixture.json",
                "isolate": False,
                "timeout_ms": 1,
                "expect": {
                    "exit_code": 5,
                    "status": "FAILED",
                    "completeness": "UNKNOWN",
                    "checks": {"CHECK_A": "UNKNOWN"},
                    "outputs": [{"status": "UNKNOWN"}],
                    "error_code": "NSL-E8001",
                    "resources": {"tool_calls": 0},
                },
            }
        ],
    }
    _write_json(suite_path, valid_document)
    loaded = load_scenario_suite(suite_path)

    captured_arguments = []

    def successful_invoker(arguments):
        captured_arguments.append(arguments)
        return 0, None, None

    invocations = execute_scenario_suite(loaded, successful_invoker)
    assert len(invocations) == 1
    assert captured_arguments[0] == (
        "run",
        "--profile",
        str(profile),
        "--execution-id",
        "scenario-boundary",
        "--principal",
        str(principal),
        "--input",
        str(input_path),
        "--context",
        str(context),
        "--fixture",
        str(fixture),
        "--no-isolate",
        "--timeout-ms",
        "1",
    )

    with pytest.raises(TypeError, match="ScenarioSuite"):
        execute_scenario_suite(object(), successful_invoker)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="invoker"):
        execute_scenario_suite(loaded, None)  # type: ignore[arg-type]
    for result, message in (
        ((True, None, None), "exit code"),
        ((0, [], None), "output"),
        ((0, None, []), "error"),
    ):
        with pytest.raises(TypeError, match=message):
            execute_scenario_suite(loaded, lambda _arguments, value=result: value)

    with pytest.raises(TypeError, match="ScenarioSuite"):
        evaluate_scenario_suite(object(), ())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exactly cover"):
        evaluate_scenario_suite(loaded, [])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exactly cover"):
        evaluate_scenario_suite(loaded, ())
    with pytest.raises(TypeError, match="ScenarioInvocation"):
        evaluate_scenario_suite(loaded, (object(),))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="order differs"):
        evaluate_scenario_suite(
            loaded, (ScenarioInvocation("different", 0, None, None),)
        )

    expectation = ScenarioExpectation(
        0, "COMPLETED", None, (), ({"status": "ok"},), None, ()
    )
    case = ScenarioCase("case", None, None, None, None, None, None, expectation)
    direct_suite = ScenarioSuite(
        suite_path, tmp_path, profile, (case,)
    )
    missing_result = evaluate_scenario_suite(
        direct_suite, (ScenarioInvocation("case", 0, None, None),)
    )[0]
    malformed_error = evaluate_scenario_suite(
        direct_suite, (ScenarioInvocation("case", 0, None, {"error": []}),)
    )[0]
    malformed_outputs = evaluate_scenario_suite(
        direct_suite,
        (
            ScenarioInvocation(
                "case",
                0,
                {
                    "result": {
                        "execution_id": "scenario-case",
                        "status": "COMPLETED",
                        "outputs": [{"fields": {}}],
                    }
                },
                None,
            ),
        ),
    )[0]
    assert not missing_result.passed
    assert not malformed_error.passed
    assert not malformed_outputs.passed

    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    _write_json(outside, {})
    directory = tmp_path / "directory"
    directory.mkdir()
    invalid_documents = [
        {**valid_document, "cases": {}},
        {**valid_document, "profile": ""},
        {**valid_document, "profile": str(outside.resolve())},
        {**valid_document, "profile": f"../{outside.name}"},
        {**valid_document, "profile": "directory"},
        {
            **valid_document,
            "cases": [{"id": "case", "expect": {"exit_code": 0, "checks": {"": "PASS"}}}],
        },
        {
            **valid_document,
            "cases": [{"id": "case", "expect": {"exit_code": 0, "outputs": {}}}],
        },
        {
            **valid_document,
            "cases": [{"id": "case", "expect": {"exit_code": 0, "error_code": ""}}],
        },
    ]
    for document in invalid_documents:
        _write_json(suite_path, document)
        with pytest.raises(NsoSchemaError):
            load_scenario_suite(suite_path)
