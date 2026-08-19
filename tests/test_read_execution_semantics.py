from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from nsl import CompileError, DiagnosticCode, SourceFile
from nsl.audit import InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.core import DataClassification, ExecutionStatus, STRING
from nsl.ir import LetStatement, ReadExpr
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.tools import (
    MAX_TOOL_TIMEOUT_MS,
    MockToolExecutor,
    ToolContractCatalog,
    ToolExecutionError,
    tool_result_hash,
)
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def execution_request(execution_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )


def test_read_001_runtime_rejects_unregistered_tool_reference_before_call() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    statement = skill.body[0]
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, ReadExpr)
    forged_read = replace(statement.value, tool_ref="tool:unregistered")
    forged_skill = replace(
        skill,
        body=(replace(statement, value=forged_read), *skill.body[1:]),
    ).with_computed_hash()
    executor = build_mock_executor(catalog)

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            forged_skill,
            execution_request("exec-read-001"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.category == "RUNTIME"
    assert result.error.message == (
        "read references unregistered tool: tool:unregistered"
    )
    assert executor.call_count == 0


def test_read_002_compiler_rejects_duplicate_tool_parameter_names() -> None:
    duplicate_source = SourceFile.from_text(
        "skills/duplicate_read_parameter.ns",
        SOURCE.replace(
            "        year: year,\n        team_id: team_id",
            "        year: year,\n        year: year,\n        team_id: team_id",
            1,
        ),
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(duplicate_source)

    assert captured.value.code == DiagnosticCode.SEM_TOOL_ARGUMENTS
    assert captured.value.logical_path == "skills/duplicate_read_parameter.ns"


def test_read_002_runtime_rejects_duplicate_parameter_before_call() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    statement = skill.body[0]
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, ReadExpr)
    duplicate_read = replace(
        statement.value,
        arguments=(*statement.value.arguments, statement.value.arguments[0]),
    )
    forged_skill = replace(
        skill,
        body=(replace(statement, value=duplicate_read), *skill.body[1:]),
    ).with_computed_hash()
    executor = build_mock_executor(catalog)

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            forged_skill,
            execution_request("exec-read-002"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.error is not None
    assert result.error.detail_code == "TOOL_ARGUMENT_MISMATCH"
    assert executor.call_count == 0


@pytest.mark.parametrize(
    ("mutation", "detail_code"),
    (
        (lambda result: {"value": result.value}, "MALFORMED_TOOL_RESULT"),
        (
            lambda result: replace(result, invocation_id="inv9999"),
            "TOOL_RESULT_IDENTITY_MISMATCH",
        ),
        (
            lambda result: replace(result, presence="PRESENT"),
            "MALFORMED_TOOL_RESULT",
        ),
        (
            lambda result: replace(result, snapshot_ref=0),
            "MALFORMED_TOOL_RESULT",
        ),
    ),
)
def test_read_003_runtime_requires_structured_tool_result(
    mutation, detail_code: str
) -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    canonical = build_mock_executor(catalog)

    class MalformedExecutor:
        async def execute(self, request):
            result = await canonical.execute(request)
            return mutation(result)

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(f"exec-read-003-{detail_code}"),
            MalformedExecutor(),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.error is not None
    assert result.error.detail_code == detail_code
    assert canonical.call_count == 1


def test_read_003_structured_envelope_supports_non_collection_schema() -> None:
    base_catalog = build_tool_catalog()
    parent = replace(
        base_catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0"),
        output_type=STRING,
    )
    child = base_catalog.get("PROJECT.LIST_CHILD_PROJECTS", "1.0.0")
    catalog = ToolContractCatalog((parent, child))
    base_skill = NslCompiler(base_catalog).compile(SOURCE).skill
    statement = base_skill.body[0]
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, ReadExpr)
    required = replace(
        base_skill.required_tools[0], contract_hash=parent.contract_hash
    )
    read = replace(statement.value, type_info=STRING)
    skill = replace(
        base_skill,
        required_tools=(required, *base_skill.required_tools[1:]),
        body=(replace(statement, value=read),),
    ).with_computed_hash()
    executor = MockToolExecutor(
        catalog,
        {
            "PROJECT.LIST_PARENT_PROJECTS": lambda arguments: "structured",
            "PROJECT.LIST_CHILD_PROJECTS": lambda arguments: [],
        },
    )

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-read-003-string"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert executor.call_count == 1


@pytest.mark.parametrize(
    "mutation",
    (
        lambda result: replace(result, value="not-a-record-list"),
        lambda result: replace(result, type_info=STRING),
        lambda result: replace(
            result, classification=DataClassification.PUBLIC
        ),
    ),
)
def test_read_004_runtime_validates_result_against_tool_contract(mutation) -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    canonical = build_mock_executor(catalog)

    class ContractViolatingExecutor:
        async def execute(self, request):
            return mutation(await canonical.execute(request))

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-read-004"),
            ContractViolatingExecutor(),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.error is not None
    assert result.error.detail_code == "OUTPUT_CONTRACT_VIOLATION"
    assert canonical.call_count == 1


@pytest.mark.parametrize(
    "timeout_ms", (0, -1, True, 1.0, MAX_TOOL_TIMEOUT_MS + 1)
)
def test_read_006_tool_contract_rejects_invalid_timeout(timeout_ms) -> None:
    contract = build_tool_catalog().get(
        "PROJECT.LIST_PARENT_PROJECTS", "1.0.0"
    )

    with pytest.raises(ValueError, match="timeout_ms"):
        replace(contract, timeout_ms=timeout_ms)


@pytest.mark.parametrize("timeout_ms", (1, MAX_TOOL_TIMEOUT_MS))
def test_read_006_tool_contract_accepts_timeout_boundaries(
    timeout_ms: int,
) -> None:
    contract = build_tool_catalog().get(
        "PROJECT.LIST_PARENT_PROJECTS", "1.0.0"
    )

    assert replace(contract, timeout_ms=timeout_ms).timeout_ms == timeout_ms


def test_read_006_timeout_is_an_explicit_tool_error() -> None:
    base_catalog = build_tool_catalog()
    parent = replace(
        base_catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0"),
        timeout_ms=1,
    )
    child = base_catalog.get("PROJECT.LIST_CHILD_PROJECTS", "1.0.0")
    catalog = ToolContractCatalog((parent, child))
    skill = NslCompiler(catalog).compile(SOURCE).skill

    class HangingExecutor:
        def __init__(self) -> None:
            self.call_count = 0
            self.cancelled = False

        async def execute(self, request):
            self.call_count += 1
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    executor = HangingExecutor()
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-read-006"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.error is not None
    assert result.error.detail_code == "TOOL_TIMEOUT"
    assert result.error.message == (
        "tool timed out after 1 ms: PROJECT.LIST_PARENT_PROJECTS"
    )
    assert executor.call_count == 1
    assert executor.cancelled is True


def test_read_007_audit_records_each_invocation_input_and_output() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    audit = InMemoryAuditSink()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-read-007-success"),
            build_mock_executor(catalog),
            audit,
        )
    )

    started = [event for event in audit.events if event.event_type == "TOOL_STARTED"]
    completed = [
        event for event in audit.events if event.event_type == "TOOL_COMPLETED"
    ]
    assert result.status is ExecutionStatus.COMPLETED
    assert len(started) == len(completed) == 2
    assert [event.payload["invocation_id"] for event in started] == [
        event.payload["invocation_id"] for event in completed
    ]
    for event in started:
        assert event.payload["input"]["argument_names"]
        assert event.payload["input"]["argument_hash"] == event.payload[
            "argument_hash"
        ]
        assert event.payload["input"]["argument_hash"].startswith("sha256:")
        assert "TEAM-FINANCE" not in repr(event.payload)
    for event in completed:
        output = event.payload["output"]
        assert output["result_hash"] == event.payload["result_hash"]
        assert output["result_hash"].startswith("sha256:")
        assert output["type"]["kind"] == "list"
        assert output["presence"] in {"EMPTY", "PRESENT"}
        assert output["completeness"] == "COMPLETE"


def test_read_007_audit_records_failed_invocation_output() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    audit = InMemoryAuditSink()

    class FailingExecutor:
        async def execute(self, request):
            raise ToolExecutionError("UPSTREAM_FAILURE", "upstream failed")

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-read-007-failure"),
            FailingExecutor(),
            audit,
        )
    )

    started = next(
        event for event in audit.events if event.event_type == "TOOL_STARTED"
    )
    failed = next(
        event for event in audit.events if event.event_type == "TOOL_FAILED"
    )
    assert result.status is ExecutionStatus.TOOL_ERROR
    assert failed.payload["invocation_id"] == started.payload["invocation_id"]
    assert failed.payload["output"] == {
        "status": "TOOL_ERROR",
        "error_code": "UPSTREAM_FAILURE",
    }
    assert not any(
        event.event_type == "TOOL_COMPLETED" for event in audit.events
    )


def test_read_007_completed_output_is_audited_before_collection_limit() -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    skill = replace(
        base_skill,
        limits=replace(base_skill.limits, collection_size=0),
    ).with_computed_hash()
    audit = InMemoryAuditSink()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-read-007-limit"),
            build_mock_executor(catalog),
            audit,
        )
    )

    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    completed = next(
        event for event in audit.events if event.event_type == "TOOL_COMPLETED"
    )
    assert completed.payload["output"]["result_hash"].startswith("sha256:")


def test_read_008_result_hash_is_canonical_and_deterministic() -> None:
    first = {"b": [2, 1], "a": {"value": "x"}}
    reordered = {"a": {"value": "x"}, "b": [2, 1]}

    assert tool_result_hash(first) == tool_result_hash(reordered)
    assert tool_result_hash(first) != tool_result_hash(
        {"b": [1, 2], "a": {"value": "x"}}
    )
    assert len(tool_result_hash([])) == len("sha256:") + 64


@pytest.mark.parametrize(
    "forged_hash", ("", "sha256:invalid", "sha256:" + "0" * 64)
)
def test_read_008_runtime_rejects_unverified_result_hash(
    forged_hash: str,
) -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    canonical = build_mock_executor(catalog)

    class ForgedHashExecutor:
        async def execute(self, request):
            result = await canonical.execute(request)
            return replace(result, result_hash=forged_hash)

    audit = InMemoryAuditSink()
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-read-008"),
            ForgedHashExecutor(),
            audit,
        )
    )

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.error is not None
    assert result.error.detail_code == "TOOL_RESULT_HASH_MISMATCH"
    failed = next(
        event for event in audit.events if event.event_type == "TOOL_FAILED"
    )
    assert failed.payload["error_code"] == "TOOL_RESULT_HASH_MISMATCH"
    assert not any(
        event.event_type == "TOOL_COMPLETED" for event in audit.events
    )
