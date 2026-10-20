from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest

from nsl.audit import InMemoryAuditSink
from nsl.cli import CLIExitCode, main
from nsl.cli_config import (
    load_json_document,
    load_mock_handlers_document,
    load_principal,
    load_tool_catalog,
    load_value_object,
)
from nsl.compiler import NslCompiler
from nsl.core import (
    BusinessOperationId,
    CheckStatus,
    Completeness,
    DataClassification,
    ExecutionStatus,
    INT,
    RiskLevel,
    STRING,
    ToolCapability,
    domain,
)
from nsl.ir import CheckStatement
from nsl.runtime import ControlledExecutionPlan, ExecutionRequest, RuntimeEngine
from nsl.runtime_models import ValidationSummaryStatus
from nsl.tools import (
    Atomicity,
    CommitConfirmation,
    CommitSemantics,
    IdempotencySemantics,
    MockToolExecutor,
    OperationKind,
    PostWriteVerification,
    ToolContract,
    ToolContractCatalog,
    ToolExecutionError,
    WriteSemantics,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "project_draft_validation"
VALIDATION_SOURCE = (EXAMPLE / "project_draft_validation.ns").read_text(
    encoding="utf-8"
)
WRITE_SOURCE = (EXAMPLE / "project_draft_write.ns").read_text(encoding="utf-8")


class _RecordingPort:
    def __init__(
        self,
        catalog: ToolContractCatalog,
        delegate: MockToolExecutor,
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
            raise RuntimeError("project provider detail must remain protected")
        return await self.delegate.execute(request)


def _catalog() -> tuple[ToolContractCatalog, ToolContract]:
    reads = load_tool_catalog(EXAMPLE / "tools.json")
    get_project = reads.get("PROJECT.GET_PROJECT", "1.0.0")
    duplicate = reads.get("PROJECT.CHECK_DUPLICATE", "1.0.0")
    write = ToolContract(
        tool_id="PROJECT.CREATE_PROJECT_DRAFT",
        version="1.0.0",
        capability=ToolCapability.WRITE,
        input_types=(
            ("project_code", domain("ProjectCode")),
            ("project_name", STRING),
            ("parent_project_code", domain("ProjectCode")),
            ("start_day", INT),
            ("end_day", INT),
            ("team_id", domain("TeamId")),
        ),
        output_type=get_project.output_type,
        required_scope="project:draft:write",
        output_classification=DataClassification.INTERNAL,
        operation_kind=OperationKind.CREATE_DRAFT,
        risk_level=RiskLevel.R3,
        write_semantics=WriteSemantics(
            side_effect=True,
            atomicity=Atomicity.ATOMIC,
            idempotency=IdempotencySemantics(
                required=True,
                scope="TOOL_AND_TENANT",
                retention="PROVIDER_DEFINED",
                duplicate_behavior="RETURN_ORIGINAL_RESULT",
                payload_conflict_behavior="IDEMPOTENCY_CONFLICT",
            ),
            commit=CommitSemantics(CommitConfirmation.EXPLICIT),
            approval_required=True,
            preconditions_supported=True,
            verification=PostWriteVerification(
                tool_id="PROJECT.GET_PROJECT",
                reference_schema=get_project.output_type,
                consistency_hint="READ_AFTER_WRITE_STRONG",
            ),
        ),
    )
    return ToolContractCatalog((get_project, duplicate, write)), write


def _compiled():
    catalog, write = _catalog()
    return catalog, write, NslCompiler(catalog).compile(WRITE_SOURCE).skill


def _request(
    execution_id: str,
    *,
    input_path: str = "input.json",
    principal_path: str = "principal_write.json",
) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs=load_value_object(EXAMPLE / input_path, "input"),
        runtime_context=load_value_object(EXAMPLE / "context.json", "context"),
        principal=load_principal(EXAMPLE / principal_path),
        business_operation_id=BusinessOperationId("business-project-0064"),
    )


def _execute_prefix(
    fixture: str = "success.json",
    *,
    request: ExecutionRequest | None = None,
    port_options: dict | None = None,
):
    catalog, _, skill = _compiled()
    fixture_document = load_json_document(EXAMPLE / "fixtures" / fixture)
    mock = MockToolExecutor(
        catalog,
        load_mock_handlers_document(fixture_document, catalog),
    )
    port = _RecordingPort(catalog, mock, **(port_options or {}))
    execution = asyncio.run(
        RuntimeEngine(catalog).execute_controlled_prefix(
            skill,
            request or _request(f"exec-project-{fixture}"),
            port,
            InMemoryAuditSink(),
        )
    )
    return execution, port, mock


def _invoke(*arguments: str):
    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, output, error


def test_ac_w02_sources_and_write_contract_are_deterministic() -> None:
    read_catalog = load_tool_catalog(EXAMPLE / "tools.json")
    validation_first = NslCompiler(read_catalog).compile(VALIDATION_SOURCE)
    validation_second = NslCompiler(read_catalog).compile(VALIDATION_SOURCE)
    catalog, write, full_first = _compiled()
    full_second = NslCompiler(catalog).compile(WRITE_SOURCE).skill
    plan = ControlledExecutionPlan.from_skill(full_first)

    assert validation_first.nso_bytes == validation_second.nso_bytes
    assert full_first.semantic_hash == full_second.semantic_hash
    assert full_first.ir_version == "1.1"
    assert full_first.language_version == "0.2"
    assert full_first.semantics_profile == "NSL-0.2-STRICT"
    assert [item.tool_id for item in full_first.required_tools] == [
        "PROJECT.GET_PROJECT",
        "PROJECT.CHECK_DUPLICATE",
        "PROJECT.CREATE_PROJECT_DRAFT",
    ]
    assert [item.capability for item in full_first.required_tools] == [
        ToolCapability.READ,
        ToolCapability.READ,
        ToolCapability.WRITE,
    ]
    assert write.operation_kind is OperationKind.CREATE_DRAFT
    assert write.risk_level is RiskLevel.R3
    assert write.write_semantics is not None
    assert write.write_semantics.verification is not None
    assert write.write_semantics.verification.tool_id == "PROJECT.GET_PROJECT"
    assert write.write_semantics.verification.reference_schema == write.output_type
    assert tuple(
        item.check_id for item in plan.prefix if isinstance(item, CheckStatement)
    ) == tuple(
        item.check_id
        for item in validation_first.skill.body
        if isinstance(item, CheckStatement)
    )


def test_ac_w02_valid_controlled_prefix_is_deterministic_and_never_writes() -> None:
    first, first_port, first_mock = _execute_prefix()
    second, second_port, second_mock = _execute_prefix()

    assert first.result.semantic_view() == second.result.semantic_view()
    assert first.result.status is ExecutionStatus.COMPLETED
    assert first.result.completeness is Completeness.COMPLETE
    assert first.result.validation is not None
    assert first.result.validation.status is ValidationSummaryStatus.ALL_PASS
    assert all(check.status is CheckStatus.PASS for check in first.result.checks)
    assert first.result.outputs == ()
    assert first.checkpoint is not None
    assert first.checkpoint == second.checkpoint
    assert (first_port.read_calls, first_port.write_calls, first_mock.call_count) == (
        2,
        0,
        2,
    )
    assert (second_port.read_calls, second_port.write_calls, second_mock.call_count) == (
        2,
        0,
        2,
    )


@pytest.mark.parametrize(
    ("fixture", "input_path", "failed_check"),
    (
        ("inactive_parent.json", "input.json", "PARENT_ACTIVE"),
        ("success.json", "input_start_before_period.json", "START_IN_PERIOD"),
        ("success.json", "input_end_after_period.json", "END_IN_PERIOD"),
        ("success.json", "input_reversed_range.json", "DATE_RANGE_VALID"),
        ("success.json", "input_unauthorized_team.json", "TEAM_AUTHORIZED"),
    ),
)
def test_ac_w02_each_business_rule_fails_closed_without_write(
    fixture: str, input_path: str, failed_check: str
) -> None:
    execution, port, mock = _execute_prefix(
        fixture,
        request=_request(
            f"exec-project-{failed_check.lower()}",
            input_path=input_path,
        ),
    )
    statuses = {check.check_id: check.status for check in execution.result.checks}

    assert execution.result.status is ExecutionStatus.COMPLETED
    assert execution.result.validation is not None
    assert execution.result.validation.status is ValidationSummaryStatus.HAS_FAIL
    assert execution.result.validation.failed_check_ids == (failed_check,)
    assert statuses[failed_check] is CheckStatus.FAIL
    assert sum(status is CheckStatus.PASS for status in statuses.values()) == 5
    assert execution.result.outputs == ()
    assert (port.read_calls, port.write_calls, mock.call_count) == (2, 0, 2)


@pytest.mark.parametrize(
    ("fixture", "size", "status", "duplicate_status"),
    (
        ("success.json", 0, ExecutionStatus.COMPLETED, CheckStatus.PASS),
        ("duplicate_single.json", 1, ExecutionStatus.COMPLETED, CheckStatus.FAIL),
        ("duplicate_max.json", 100, ExecutionStatus.COMPLETED, CheckStatus.FAIL),
        ("duplicate_over_limit.json", 101, ExecutionStatus.LIMIT_EXCEEDED, None),
    ),
)
def test_ac_w02_duplicate_collection_zero_one_max_and_over_limit(
    fixture: str,
    size: int,
    status: ExecutionStatus,
    duplicate_status: CheckStatus | None,
) -> None:
    execution, port, mock = _execute_prefix(fixture)
    result = execution.result

    assert result.status is status
    assert result.resources.max_collection_size_seen == size
    assert result.outputs == ()
    assert port.write_calls == 0
    assert port.read_calls == 2
    assert mock.call_count == 2
    if duplicate_status is None:
        assert execution.checkpoint is None
        assert result.error is not None
        assert result.error.code == "NSL-E6001"
        assert result.checks == ()
    else:
        assert execution.checkpoint is not None
        statuses = {check.check_id: check.status for check in result.checks}
        assert statuses["DUPLICATE_ABSENT"] is duplicate_status


@pytest.mark.parametrize(
    ("fixture", "completeness", "unknown_check"),
    (
        ("partial_duplicates.json", Completeness.PARTIAL, "DUPLICATE_ABSENT"),
        ("unknown_parent.json", Completeness.UNKNOWN, "PARENT_ACTIVE"),
    ),
)
def test_ac_w02_partial_and_unknown_evidence_cannot_false_pass(
    fixture: str, completeness: Completeness, unknown_check: str
) -> None:
    execution, port, mock = _execute_prefix(fixture)
    result = execution.result
    statuses = {check.check_id: check.status for check in result.checks}

    assert result.status is ExecutionStatus.COMPLETED
    assert result.completeness is completeness
    assert result.validation is not None
    assert result.validation.status is ValidationSummaryStatus.HAS_UNKNOWN
    assert result.validation.unknown_check_ids == (unknown_check,)
    assert statuses[unknown_check] is CheckStatus.UNKNOWN
    assert result.outputs == ()
    assert (port.read_calls, port.write_calls, mock.call_count) == (2, 0, 2)


@pytest.mark.parametrize(
    ("fixture", "read_calls", "mock_calls"),
    (
        ("missing_parent.json", 1, 1),
        ("malformed_parent.json", 1, 1),
        ("missing_duplicate_tool.json", 2, 1),
    ),
)
def test_ac_w02_missing_malformed_and_dependency_data_fail_closed(
    fixture: str, read_calls: int, mock_calls: int
) -> None:
    execution, port, mock = _execute_prefix(fixture)

    assert execution.result.status is ExecutionStatus.TOOL_ERROR
    assert execution.result.completeness is Completeness.UNKNOWN
    assert execution.result.error is not None
    assert execution.result.error.code == "NSL-E4101"
    assert execution.checkpoint is None
    assert port.read_calls == read_calls
    assert port.write_calls == 0
    assert mock.call_count == mock_calls


def test_ac_w02_authorization_and_missing_input_stop_before_tools() -> None:
    denied, denied_port, denied_mock = _execute_prefix(
        request=_request(
            "exec-project-auth-denied",
            principal_path="principal_no_tool_scope.json",
        )
    )
    missing, missing_port, missing_mock = _execute_prefix(
        request=_request(
            "exec-project-missing-input",
            input_path="input_missing_name.json",
        )
    )

    assert denied.result.status is ExecutionStatus.FAILED
    assert denied.result.error is not None
    assert denied.result.error.code == "NSL-E5201"
    assert missing.result.status is ExecutionStatus.FAILED
    assert missing.result.error is not None
    assert missing.result.error.code == "NSL-E8001"
    assert denied.checkpoint is missing.checkpoint is None
    assert denied_port.read_calls == missing_port.read_calls == 0
    assert denied_port.write_calls == missing_port.write_calls == 0
    assert denied_mock.call_count == missing_mock.call_count == 0


def test_ac_w02_tenant_mismatch_and_dependency_crash_are_isolated() -> None:
    mismatched, mismatch_port, mismatch_mock = _execute_prefix(
        request=_request("exec-project-wrong-tenant"),
        port_options={"tenant_id": "another-tenant"},
    )
    crashed, crash_port, crash_mock = _execute_prefix(
        request=_request("exec-project-provider-crash"),
        port_options={"dependency_error": True},
    )

    assert mismatched.result.status is ExecutionStatus.TOOL_ERROR
    assert mismatched.result.error is not None
    assert mismatched.result.error.detail_code == "TENANT_MISMATCH"
    assert mismatch_mock.call_count == 0
    assert crashed.result.status is ExecutionStatus.FAILED
    assert crashed.result.error is not None
    assert crashed.result.error.message == "An unexpected runtime error occurred."
    assert "provider detail" not in crashed.result.error.message
    assert crash_mock.call_count == 0
    assert mismatch_port.read_calls == crash_port.read_calls == 1
    assert mismatch_port.write_calls == crash_port.write_calls == 0


def test_ac_w02_direct_runtime_rejects_controlled_source_without_tool_calls() -> None:
    catalog, _, skill = _compiled()
    mock = MockToolExecutor(catalog, {})

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            _request("exec-project-direct-runtime"),
            mock,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert "controlled v0.2 execution pipeline" in result.error.message
    assert mock.call_count == 0


def test_ac_w02_project_rules_do_not_enter_common_runtime() -> None:
    common_runtime = (ROOT / "nsl" / "runtime.py").read_text(encoding="utf-8")
    common_write = (ROOT / "nsl" / "write_execution.py").read_text(
        encoding="utf-8"
    )

    assert "PROJECT.GET_PROJECT" not in common_runtime
    assert "PROJECT.CHECK_DUPLICATE" not in common_runtime
    assert "PROJECT.CREATE_PROJECT_DRAFT" not in common_runtime
    assert "PROJECT.GET_PROJECT" not in common_write
    assert "PROJECT.CREATE_PROJECT_DRAFT" not in common_write


def test_ac_w02_cli_scenario_suite_is_deterministic_and_no_write() -> None:
    arguments = ("test", "--suite", str(EXAMPLE / "scenarios.json"))
    first = _invoke(*arguments)
    second = _invoke(*arguments)

    assert first == second
    code, output, error = first
    assert code == CLIExitCode.SUCCESS, error
    assert output["status"] == "passed"
    assert output["passed"] == 16
    assert output["failed"] == 0
    assert [item["id"] for item in output["cases"]] == [
        "valid-empty-duplicates",
        "duplicate-single",
        "duplicate-maximum",
        "duplicate-over-limit",
        "inactive-parent",
        "start-before-period",
        "end-after-period",
        "reversed-date-range",
        "unauthorized-team",
        "partial-duplicates",
        "unknown-parent",
        "missing-parent-data",
        "malformed-parent-data",
        "missing-duplicate-fixture",
        "authorization-denied",
        "missing-required-input",
    ]
