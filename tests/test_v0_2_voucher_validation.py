from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from nsl.audit import InMemoryAuditSink
from nsl.cli import CLIExitCode, main
from nsl.cli_config import (
    load_json_document,
    load_mock_handlers_document,
    load_principal,
    load_tool_catalog,
    load_tool_catalog_document,
    load_value_object,
)
from nsl.compiler import NslCompiler
from nsl.core import (
    CheckStatus,
    Completeness,
    ExecutionStatus,
    RiskLevel,
    ToolCapability,
)
from nsl.ir import ApprovalPolicy, ApprovalStatement, LetStatement, WriteExpression
from nsl.ir_schema import NsoSchemaError
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.runtime_models import ValidationSummaryStatus
from nsl.tools import (
    MockToolExecutor,
    MockToolResult,
    ToolContractCatalog,
    ToolExecutionError,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "voucher_draft_validation"
SOURCE = (EXAMPLE / "voucher_draft_validation.ns").read_text(encoding="utf-8")


class _RecordingPort:
    def __init__(
        self,
        catalog,
        delegate,
        *,
        tenant_id: str | None = None,
        dependency_error: bool = False,
    ) -> None:
        self.catalog = catalog
        self.delegate = delegate
        self.tenant_id = tenant_id
        self.dependency_error = dependency_error
        self.read_calls = 0
        self.write_calls = 0

    async def execute(self, request):
        contract = self.catalog.resolve(request.tool_id, request.tool_version)
        if contract.capability is ToolCapability.WRITE:
            self.write_calls += 1
        else:
            self.read_calls += 1
        if self.tenant_id is not None and request.principal.tenant_id != self.tenant_id:
            raise ToolExecutionError("TENANT_MISMATCH", "tenant mismatch")
        if self.dependency_error:
            raise RuntimeError("provider detail must remain protected")
        return await self.delegate.execute(request)


def _compiled():
    catalog = load_tool_catalog(EXAMPLE / "tools.json")
    return catalog, NslCompiler(catalog).compile(SOURCE).skill


def _request(
    execution_id: str,
    *,
    principal_path: str = "principal.json",
    input_path: str = "input.json",
) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs=load_value_object(EXAMPLE / input_path, "input"),
        runtime_context=load_value_object(EXAMPLE / "context.json", "context"),
        principal=load_principal(EXAMPLE / principal_path),
    )


def _execute(
    fixture: str = "success.json",
    *,
    request: ExecutionRequest | None = None,
    port_options: dict | None = None,
):
    catalog, skill = _compiled()
    fixture_document = load_json_document(EXAMPLE / "fixtures" / fixture)
    mock = MockToolExecutor(
        catalog,
        load_mock_handlers_document(fixture_document, catalog),
    )
    port = _RecordingPort(catalog, mock, **(port_options or {}))
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            request or _request(f"exec-{fixture}"),
            port,
            InMemoryAuditSink(),
        )
    )
    return result, port, mock


def _invoke(*arguments: str):
    from io import StringIO

    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, output, error


def test_ac_w01_v0_2_read_contract_compiles_deterministically() -> None:
    catalog = load_tool_catalog(EXAMPLE / "tools.json")
    first = NslCompiler(catalog).compile(SOURCE)
    second = NslCompiler(catalog).compile(SOURCE)

    assert first.nso_bytes == second.nso_bytes
    assert first.skill.ir_version == "1.1"
    assert first.skill.language_version == "0.2"
    assert first.skill.semantics_profile == "NSL-0.2-STRICT"
    assert [item.tool_id for item in first.skill.required_tools] == [
        "ACCOUNT.GET_ACCOUNT",
        "PROJECT.GET_PROJECT",
        "FINANCE.GET_ACCOUNTING_PERIOD",
    ]
    assert all(
        item.capability is ToolCapability.READ
        and item.risk_level is RiskLevel.R1
        for item in first.skill.required_tools
    )


def test_ac_w01_legacy_read_contract_gets_bounded_ir_1_1_risk() -> None:
    catalog = load_tool_catalog(EXAMPLE / "tools.json")
    contracts = tuple(
        replace(catalog.get(tool_id, "1.0.0"), risk_level=None)
        for tool_id in (
            "ACCOUNT.GET_ACCOUNT",
            "PROJECT.GET_PROJECT",
            "FINANCE.GET_ACCOUNTING_PERIOD",
        )
    )

    skill = NslCompiler(ToolContractCatalog(contracts)).compile(SOURCE).skill

    assert all(item.risk_level is RiskLevel.R1 for item in skill.required_tools)


@pytest.mark.parametrize(
    ("fixture", "failed_check"),
    (
        ("invalid_account.json", "ACCOUNT_VALID"),
        ("invalid_project.json", "PROJECT_VALID"),
        ("closed_period.json", "PERIOD_OPEN"),
    ),
)
def test_ac_w01_invalid_reference_data_fails_only_its_check(
    fixture: str, failed_check: str
) -> None:
    result, port, mock = _execute(fixture)
    statuses = {check.check_id: check.status for check in result.checks}

    assert result.status is ExecutionStatus.COMPLETED
    assert result.completeness is Completeness.COMPLETE
    assert result.validation is not None
    assert result.validation.status is ValidationSummaryStatus.HAS_FAIL
    assert result.validation.failed_check_ids == (failed_check,)
    assert statuses[failed_check] is CheckStatus.FAIL
    assert sum(status is CheckStatus.PASS for status in statuses.values()) == 3
    assert (port.read_calls, port.write_calls, mock.call_count) == (3, 0, 3)


@pytest.mark.parametrize(
    ("input_path", "expected_status", "summary_status"),
    (
        ("input.json", CheckStatus.PASS, ValidationSummaryStatus.ALL_PASS),
        (
            "input_zero_amount.json",
            CheckStatus.FAIL,
            ValidationSummaryStatus.HAS_FAIL,
        ),
    ),
)
def test_ac_w01_amount_boundary_zero_and_one(
    input_path: str,
    expected_status: CheckStatus,
    summary_status: ValidationSummaryStatus,
) -> None:
    result, port, mock = _execute(
        request=_request(f"exec-{input_path}", input_path=input_path)
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert {check.check_id: check.status for check in result.checks}[
        "AMOUNT_VALID"
    ] is expected_status
    assert result.validation is not None
    assert result.validation.status is summary_status
    assert result.to_data()["schema_version"] == "1.1"
    assert (port.read_calls, port.write_calls, mock.call_count) == (3, 0, 3)


@pytest.mark.parametrize(
    ("fixture", "expected_completeness"),
    (
        ("partial_account.json", Completeness.PARTIAL),
        ("unknown_account.json", Completeness.UNKNOWN),
    ),
)
def test_ac_w01_incomplete_read_cannot_false_pass(
    fixture: str, expected_completeness: Completeness
) -> None:
    result, port, mock = _execute(fixture)
    statuses = {check.check_id: check.status for check in result.checks}

    assert result.status is ExecutionStatus.COMPLETED
    assert result.completeness is expected_completeness
    assert statuses["ACCOUNT_VALID"] is CheckStatus.UNKNOWN
    assert result.validation is not None
    assert result.validation.status is ValidationSummaryStatus.HAS_UNKNOWN
    assert result.validation.unknown_check_ids == ("ACCOUNT_VALID",)
    assert result.outputs[0].values["account_status"] == "UNKNOWN"
    assert (port.read_calls, port.write_calls, mock.call_count) == (3, 0, 3)


@pytest.mark.parametrize(
    "fixture",
    ("missing_account.json", "malformed_account.json", "missing_tools.json"),
)
def test_ac_w01_missing_malformed_and_dependency_data_fail_closed(
    fixture: str,
) -> None:
    result, port, mock = _execute(fixture)

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.completeness is Completeness.UNKNOWN
    assert result.error is not None
    assert result.error.code == "NSL-E4101"
    assert result.validation is not None
    assert result.validation.status is ValidationSummaryStatus.NO_CHECK
    assert port.write_calls == 0
    assert port.read_calls == 1
    assert mock.call_count in {0, 1}


def test_ac_w01_authorization_and_missing_input_stop_before_tools() -> None:
    denied, denied_port, denied_mock = _execute(
        request=_request(
            "exec-auth-denied", principal_path="principal_no_tool_scope.json"
        )
    )
    missing, missing_port, missing_mock = _execute(
        request=_request("exec-missing-input", input_path="input_missing_amount.json")
    )

    assert denied.status is ExecutionStatus.FAILED
    assert denied.error is not None and denied.error.code == "NSL-E5201"
    assert missing.status is ExecutionStatus.FAILED
    assert missing.error is not None and missing.error.code == "NSL-E8001"
    assert denied_port.write_calls == missing_port.write_calls == 0
    assert denied_mock.call_count == missing_mock.call_count == 0


def test_ac_w01_tenant_mismatch_and_dependency_crash_are_isolated() -> None:
    mismatched, mismatch_port, mismatch_mock = _execute(
        request=_request("exec-wrong-tenant"),
        port_options={"tenant_id": "another-tenant"},
    )
    crashed, crash_port, crash_mock = _execute(
        request=_request("exec-dependency-crash"),
        port_options={"dependency_error": True},
    )

    assert mismatched.status is ExecutionStatus.TOOL_ERROR
    assert mismatched.error is not None
    assert mismatched.error.detail_code == "TENANT_MISMATCH"
    assert mismatch_mock.call_count == 0
    assert crashed.status is ExecutionStatus.FAILED
    assert crashed.error is not None
    assert crashed.error.message == "An unexpected runtime error occurred."
    assert "provider detail" not in crashed.error.message
    assert crash_mock.call_count == 0
    assert mismatch_port.write_calls == crash_port.write_calls == 0


@pytest.mark.parametrize("controlled_kind", ("approval", "write"))
def test_ac_w01_direct_runtime_rejects_controlled_nodes_before_tool_call(
    controlled_kind: str,
) -> None:
    catalog, skill = _compiled()
    if controlled_kind == "approval":
        body = (
            ApprovalStatement(
                node_id="stmt-forged-approval",
                approval_id="FORGED_APPROVAL",
                message="forged",
                policy=ApprovalPolicy(),
            ),
            *skill.body,
        )
    else:
        first = skill.body[0]
        assert isinstance(first, LetStatement)
        body = (
            replace(
                first,
                value=WriteExpression(
                    node_id="expr-forged-write",
                    tool_ref="tool0001",
                    arguments=(),
                    approval_ref="FORGED_APPROVAL",
                    type_info=first.value.type_info,
                ),
            ),
            *skill.body[1:],
        )
    forged = replace(skill, body=body).with_computed_hash()
    mock = MockToolExecutor(catalog, {})

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            forged,
            _request(f"exec-forged-{controlled_kind}"),
            mock,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert "controlled v0.2 execution pipeline" in result.error.message
    assert mock.call_count == 0


def test_ac_w01_mock_fixture_completeness_and_risk_schema_are_strict() -> None:
    with pytest.raises(TypeError, match="Completeness"):
        MockToolResult({}, "PARTIAL")  # type: ignore[arg-type]

    tools = load_json_document(EXAMPLE / "tools.json")
    tools["tools"][0]["risk_level"] = "R6"
    with pytest.raises(NsoSchemaError, match="unknown risk level"):
        load_tool_catalog_document(tools)

    fixture = load_json_document(EXAMPLE / "fixtures" / "success.json")
    fixture["tools"][0]["cases"][0]["completeness"] = "INCOMPLETE"
    catalog = load_tool_catalog(EXAMPLE / "tools.json")
    with pytest.raises(NsoSchemaError, match="unknown completeness"):
        load_mock_handlers_document(fixture, catalog)


def test_ac_w01_cli_scenario_suite_is_deterministic_and_no_write() -> None:
    arguments = (
        "test",
        "--suite",
        str(EXAMPLE / "scenarios.json"),
    )
    first = _invoke(*arguments)
    second = _invoke(*arguments)

    assert first == second
    code, output, error = first
    assert code == CLIExitCode.SUCCESS, error
    assert output["status"] == "passed"
    assert output["passed"] == 12
    assert output["failed"] == 0
    assert [item["id"] for item in output["cases"]] == [
        "valid-boundary-amount",
        "invalid-account",
        "invalid-project",
        "closed-period",
        "invalid-zero-amount",
        "partial-account",
        "unknown-account",
        "missing-account-data",
        "malformed-account-data",
        "dependency-fixture-missing",
        "authorization-denied",
        "missing-required-input",
    ]
