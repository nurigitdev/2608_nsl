from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from nsl.audit import InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.core import (
    DATE,
    DATETIME,
    Completeness,
    DataClassification,
    ExecutionStatus,
    INT,
    Money,
    ValueEnvelope,
    decode_value,
    encode_value,
    money_type,
)
from nsl.diagnostics import CompileError, DiagnosticCode
from nsl.ir import EmitStatement, LiteralExpr
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.runtime_models import EmitRecord, RuntimeErrorInfo
from nsl.tools import ToolExecutionError
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)
TYPED_OUTPUT_SOURCE = '''language NSL "0.1";

skill FINANCE.TYPED_OUTPUT {
    version "1.0.0";
    risk READ_ONLY;
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }
    input {
        amount: Money<KRW> classification INTERNAL;
        report_date: Date classification INTERNAL;
        captured_at: DateTime classification INTERNAL;
    }
    output {
        amount: Money<KRW> classification INTERNAL;
        report_date: Date classification INTERNAL;
        captured_at: DateTime classification INTERNAL;
    }
    emit {
        amount: amount;
        report_date: report_date;
        captured_at: captured_at;
    }
}
'''


def execution_request(execution_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )


def execute_vertical(execution_id: str = "exec-emit"):
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(execution_id),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )
    return skill, result


def execute_typed_output(inputs: dict, execution_id: str):
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(TYPED_OUTPUT_SOURCE).skill
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            ExecutionRequest(
                execution_id=execution_id,
                inputs=inputs,
                runtime_context={},
                principal=build_principal(),
            ),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )
    return result


def test_emt_001_emit_creates_typed_structured_record() -> None:
    skill, result = execute_vertical("exec-emt-001")

    assert result.status is ExecutionStatus.COMPLETED
    assert len(result.outputs) == 1
    record = result.outputs[0]
    output_types = {field.name: field.type_info for field in skill.outputs}
    assert tuple(record.fields) == tuple(output_types)
    assert all(isinstance(field, ValueEnvelope) for field in record.fields.values())
    assert {
        name: field.type_info for name, field in record.fields.items()
    } == output_types
    assert record.classifications["parent_project"] is DataClassification.CONFIDENTIAL
    assert record.values["parent_project"] == "PARENT-001"
    with pytest.raises(TypeError):
        record.fields["new_field"] = record.fields["status"]

    with pytest.raises(ValueError, match="map names to ValueEnvelope"):
        EmitRecord({"": record.fields["status"]})
    with pytest.raises(ValueError, match="map names to ValueEnvelope"):
        EmitRecord({"status": object()})


@pytest.mark.parametrize(
    "source",
    (
        SOURCE.replace("            status: BUDGET_LIMIT.status;\n", ""),
        SOURCE.replace(
            "            status: BUDGET_LIMIT.status;",
            "            status: BUDGET_LIMIT.status;\n"
            "            unexpected: BUDGET_LIMIT.status;",
        ),
        SOURCE.replace(
            "            status: BUDGET_LIMIT.status;",
            "            status: BUDGET_LIMIT.status;\n"
            "            status: BUDGET_LIMIT.status;",
        ),
    ),
)
def test_emt_002_compile_rejects_missing_extra_and_duplicate_emit_fields(
    source: str,
) -> None:
    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    error = captured.value
    assert error.code == DiagnosticCode.SEM_EMIT_SCHEMA
    assert error.location is not None
    assert error.snippet is not None


def test_emt_002_compile_rejects_duplicate_output_schema_field() -> None:
    source = SOURCE.replace(
        "        status: CheckStatus classification INTERNAL;",
        "        status: CheckStatus classification INTERNAL;\n"
        "        status: CheckStatus classification INTERNAL;",
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == DiagnosticCode.SEM_EMIT_SCHEMA
    assert captured.value.location is not None
    assert captured.value.snippet is not None


@pytest.mark.parametrize(
    "mutation", ("field", "type", "value", "enum_value", "classification")
)
def test_emt_002_runtime_rejects_forged_emit_schema(mutation: str) -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    loop = base_skill.body[1]
    emit = loop.body[-1]
    assert isinstance(emit, EmitStatement)
    fields = emit.fields
    outputs = base_skill.outputs
    if mutation == "field":
        fields = (*fields[:-1], ("unexpected", fields[-1][1]))
    elif mutation == "type":
        name, expression = fields[0]
        fields = ((name, replace(expression, type_info=INT)), *fields[1:])
    elif mutation == "value":
        name, _ = fields[0]
        fields = (
            (name, LiteralExpr("forged-value", 1, outputs[0].type_info)),
            *fields[1:],
        )
    elif mutation == "enum_value":
        name, _ = fields[-1]
        fields = (
            *fields[:-1],
            (name, LiteralExpr("forged-enum", "MAYBE", outputs[-1].type_info)),
        )
    else:
        outputs = (
            replace(outputs[0], classification=DataClassification.PUBLIC),
            *outputs[1:],
        )
    forged = replace(
        base_skill,
        outputs=outputs,
        body=(
            base_skill.body[0],
            replace(loop, body=(*loop.body[:-1], replace(emit, fields=fields))),
        ),
    ).with_computed_hash()

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            forged,
            execution_request(f"exec-emt-002-{mutation}"),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.outputs == ()
    assert result.error is not None
    if mutation in {"value", "enum_value"}:
        assert result.error.message.startswith("runtime type mismatch for output")
    else:
        assert "output schema" in result.error.message


def test_emt_005_money_date_and_datetime_types_survive_emit() -> None:
    amount = Money(Decimal("1234.50"), "KRW")
    report_date = date(2026, 8, 19)
    captured_at = datetime(2026, 8, 19, 10, 30, tzinfo=timezone.utc)

    result = execute_typed_output(
        {
            "amount": amount,
            "report_date": report_date,
            "captured_at": captured_at,
        },
        "exec-emt-005",
    )

    assert result.status is ExecutionStatus.COMPLETED
    fields = result.outputs[0].fields
    assert fields["amount"].type_info == money_type("KRW")
    assert fields["amount"].value == amount
    assert fields["report_date"].type_info == DATE
    assert fields["report_date"].value == report_date
    assert fields["captured_at"].type_info == DATETIME
    assert fields["captured_at"].value == captured_at


def test_emt_005_date_and_datetime_value_codec_is_lossless() -> None:
    report_date = date(2026, 8, 19)
    captured_at = datetime(2026, 8, 19, 10, 30, tzinfo=timezone.utc)

    assert decode_value(encode_value(report_date)) == report_date
    assert decode_value(encode_value(captured_at)) == captured_at
    with pytest.raises(ValueError, match="Date must be an ISO string"):
        decode_value({"$type": "Date", "value": 20260819})
    with pytest.raises(ValueError, match="DateTime must be an ISO string"):
        decode_value({"$type": "DateTime", "value": "not-a-datetime"})


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("amount", Money(Decimal("1"), "USD")),
        ("amount", Decimal("1")),
        ("report_date", datetime(2026, 8, 19, 0, 0)),
        ("captured_at", date(2026, 8, 19)),
    ),
)
def test_emt_005_runtime_rejects_values_that_do_not_match_declared_type(
    field: str, invalid: object
) -> None:
    inputs = {
        "amount": Money(Decimal("1"), "KRW"),
        "report_date": date(2026, 8, 19),
        "captured_at": datetime(2026, 8, 19, 10, 30),
    }
    inputs[field] = invalid

    result = execute_typed_output(inputs, f"exec-emt-005-invalid-{field}")

    assert result.status is ExecutionStatus.FAILED
    assert result.outputs == ()
    assert result.error is not None
    assert result.error.message == f"runtime type mismatch for {field}"


@pytest.mark.parametrize("parent_count", (0, 1, 3))
def test_emt_003_multiple_emit_records_are_returned_as_an_ordered_list(
    parent_count: int,
) -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    executor = build_mock_executor(catalog)
    parent_fixture = executor.handlers["PROJECT.LIST_PARENT_PROJECTS"]

    def parents(arguments):
        template = parent_fixture(arguments)[0]
        return [
            {**template, "project_code": f"PARENT-{index:03d}"}
            for index in range(parent_count)
        ]

    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = parents
    executor.handlers["PROJECT.LIST_CHILD_PROJECTS"] = lambda arguments: []
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(f"exec-emt-003-{parent_count}"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert isinstance(result.outputs, tuple)
    serialized_outputs = result.semantic_view()["outputs"]
    assert isinstance(serialized_outputs, list)
    assert len(serialized_outputs) == parent_count
    assert [record["parent_project"] for record in serialized_outputs] == [
        f"PARENT-{index:03d}" for index in range(parent_count)
    ]


@pytest.mark.parametrize(
    ("limit", "parent_count", "expected_status", "expected_outputs"),
    (
        (0, 1, ExecutionStatus.LIMIT_EXCEEDED, 0),
        (1, 1, ExecutionStatus.COMPLETED, 1),
        (1, 2, ExecutionStatus.LIMIT_EXCEEDED, 1),
    ),
)
def test_emt_004_emitted_rows_limit_blocks_the_next_record(
    limit: int,
    parent_count: int,
    expected_status: ExecutionStatus,
    expected_outputs: int,
) -> None:
    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    skill = replace(
        base_skill,
        limits=replace(base_skill.limits, emitted_rows=limit),
    ).with_computed_hash()
    executor = build_mock_executor(catalog)
    parent_fixture = executor.handlers["PROJECT.LIST_PARENT_PROJECTS"]

    def parents(arguments):
        template = parent_fixture(arguments)[0]
        return [
            {**template, "project_code": f"PARENT-{index:03d}"}
            for index in range(parent_count)
        ]

    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = parents
    executor.handlers["PROJECT.LIST_CHILD_PROJECTS"] = lambda arguments: []
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(f"exec-emt-004-{limit}-{parent_count}"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is expected_status
    assert len(result.outputs) == expected_outputs
    assert result.resources.emitted_rows == expected_outputs
    if expected_status is ExecutionStatus.LIMIT_EXCEEDED:
        assert result.error is not None
        assert result.error.message == "emitted row limit exceeded"


def test_emt_006_execution_result_has_lossless_deterministic_json() -> None:
    amount = Money(Decimal("1234.50"), "KRW")
    report_date = date(2026, 8, 19)
    captured_at = datetime(2026, 8, 19, 10, 30, tzinfo=timezone.utc)
    result = execute_typed_output(
        {
            "amount": amount,
            "report_date": report_date,
            "captured_at": captured_at,
        },
        "exec-emt-006",
    )

    payload = result.to_data()
    serialized = result.to_json()

    assert json.loads(serialized) == payload
    assert result.to_json() == serialized
    assert payload["schema_version"] == "1.0"
    assert payload["execution_id"] == "exec-emt-006"
    assert isinstance(payload["outputs"], list)
    fields = {
        field["name"]: field for field in payload["outputs"][0]["fields"]
    }
    assert fields["amount"]["type"] == money_type("KRW").to_data()
    assert fields["amount"]["value"] == {
        "$type": "Money",
        "amount": "1234.50",
        "currency": "KRW",
    }
    assert fields["report_date"]["value"] == {
        "$type": "Date",
        "value": "2026-08-19",
    }
    assert fields["captured_at"]["value"] == {
        "$type": "DateTime",
        "value": "2026-08-19T10:30:00+00:00",
    }


def test_emt_006_failed_result_serializes_structured_error_and_empty_outputs() -> None:
    result = execute_typed_output(
        {
            "amount": Decimal("1"),
            "report_date": date(2026, 8, 19),
            "captured_at": datetime(2026, 8, 19, 10, 30),
        },
        "exec-emt-006-failure",
    )

    payload = json.loads(result.to_json())

    assert payload["status"] == "FAILED"
    assert payload["checks"] == []
    assert payload["outputs"] == []
    assert payload["error"] == {
        "code": "NSL-E8001",
        "category": "RUNTIME",
        "message": "runtime type mismatch for amount",
        "node_id": None,
        "detail_code": None,
    }


def test_rel_003_runtime_error_is_returned_as_structured_result() -> None:
    result = execute_typed_output(
        {
            "amount": Decimal("1"),
            "report_date": date(2026, 8, 19),
            "captured_at": datetime(2026, 8, 19, 10, 30),
        },
        "exec-rel-003",
    )

    assert result.execution_id == "exec-rel-003"
    assert result.status is ExecutionStatus.FAILED
    assert isinstance(result.error, RuntimeErrorInfo)
    assert result.error == RuntimeErrorInfo(
        code="NSL-E8001",
        category="RUNTIME",
        message="runtime type mismatch for amount",
    )
    assert result.checks == ()
    assert result.outputs == ()
    assert result.to_data()["error"]["code"] == "NSL-E8001"


def test_rel_004_tool_failure_has_explicit_status_and_detail_code() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    delegate = build_mock_executor(catalog)

    class FailingToolExecutor:
        async def execute(self, request):
            if request.tool_id == "PROJECT.LIST_CHILD_PROJECTS":
                raise ToolExecutionError("UPSTREAM_TIMEOUT", "ERP timed out")
            return await delegate.execute(request)

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request("exec-rel-004"),
            FailingToolExecutor(),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.error == RuntimeErrorInfo(
        code="NSL-E4101",
        category="TOOL",
        message="ERP timed out",
        detail_code="UPSTREAM_TIMEOUT",
    )
    assert result.checks == ()
    assert result.outputs == ()
    assert result.to_data()["error"]["detail_code"] == "UPSTREAM_TIMEOUT"


@pytest.mark.parametrize(
    "completeness", (Completeness.PARTIAL, Completeness.UNKNOWN)
)
def test_rel_005_incomplete_data_is_not_reported_as_complete_execution_result(
    completeness: Completeness,
) -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    delegate = build_mock_executor(catalog)

    class IncompleteToolExecutor:
        async def execute(self, request):
            result = await delegate.execute(request)
            if request.tool_id == "PROJECT.LIST_CHILD_PROJECTS":
                return replace(result, completeness=completeness)
            return result

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(f"exec-rel-005-{completeness.value.lower()}"),
            IncompleteToolExecutor(),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert result.completeness is completeness
    assert result.completeness is not Completeness.COMPLETE
    assert result.to_data()["completeness"] == completeness.value
    assert result.semantic_view()["completeness"] == completeness.value


def test_rel_005_complete_and_interrupted_result_boundaries() -> None:
    _, complete = execute_vertical("exec-rel-005-complete")
    assert complete.completeness is Completeness.COMPLETE

    catalog = build_tool_catalog()
    base_skill = NslCompiler(catalog).compile(SOURCE).skill
    limited = replace(
        base_skill,
        limits=replace(base_skill.limits, emitted_rows=1),
    ).with_computed_hash()
    executor = build_mock_executor(catalog)
    parent_fixture = executor.handlers["PROJECT.LIST_PARENT_PROJECTS"]

    def two_parents(arguments):
        template = parent_fixture(arguments)[0]
        return [
            {**template, "project_code": "PARENT-001"},
            {**template, "project_code": "PARENT-002"},
        ]

    executor.handlers["PROJECT.LIST_PARENT_PROJECTS"] = two_parents
    executor.handlers["PROJECT.LIST_CHILD_PROJECTS"] = lambda arguments: []
    interrupted = asyncio.run(
        RuntimeEngine(catalog).execute(
            limited,
            execution_request("exec-rel-005-interrupted"),
            executor,
            InMemoryAuditSink(),
        )
    )
    failed_before_output = execute_typed_output(
        {
            "amount": Decimal("1"),
            "report_date": date(2026, 8, 19),
            "captured_at": datetime(2026, 8, 19, 10, 30),
        },
        "exec-rel-005-no-output",
    )

    assert interrupted.status is ExecutionStatus.LIMIT_EXCEEDED
    assert len(interrupted.outputs) == 1
    assert interrupted.completeness is Completeness.PARTIAL
    assert failed_before_output.status is ExecutionStatus.FAILED
    assert failed_before_output.completeness is Completeness.UNKNOWN
