from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from nsl.audit import InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.core import CheckStatus, Completeness, ExecutionStatus, Money, MoneyError
from nsl.diagnostics import CompileError, DiagnosticCode
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.tools import ToolCallRequest, ToolExecutionError
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
    run_vertical_slice,
)
from tools.requirements_traceability import DEFAULT_SOURCE
from tools.srs_certification import (
    CertificationError,
    CertificationStatus,
    DEFAULT_NEGATIVE_BASELINE,
    certify_srs,
    extract_negative_acceptance,
    main as certification_main,
    validate_negative_acceptance,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")


def execution_request(execution_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )


def execute(execution_id: str, tools):
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    return asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            execution_request(execution_id),
            tools,
            InMemoryAuditSink(),
        )
    )


def test_ac_n01_syntax_error_is_rejected_before_execution() -> None:
    with pytest.raises(CompileError):
        NslCompiler(build_tool_catalog()).compile('language NSL "0.1";')


def test_ac_n02_undeclared_tool_is_a_compile_error() -> None:
    changed = SOURCE.replace(
        "read PROJECT.LIST_CHILD_PROJECTS(",
        "read PROJECT.UNDECLARED(",
    )
    with pytest.raises(CompileError, match="tool not declared"):
        NslCompiler(build_tool_catalog()).compile(changed)


def test_ac_n03_write_tool_is_a_compile_error() -> None:
    catalog = build_tool_catalog()
    write_contracts = tuple(
        replace(contract, capability="WRITE")
        if contract.tool_id == "PROJECT.LIST_CHILD_PROJECTS"
        else contract
        for contract in catalog._contracts.values()
    )
    with pytest.raises(CompileError, match="WRITE"):
        NslCompiler(type(catalog)(write_contracts)).compile(SOURCE)


def test_ac_n04_unbounded_foreach_is_a_compile_error() -> None:
    changed = SOURCE.replace("foreach parent in parents max 10", "foreach parent in parents")
    with pytest.raises(CompileError):
        NslCompiler(build_tool_catalog()).compile(changed)


def test_ac_n05_currency_mismatch_fails_closed() -> None:
    with pytest.raises(MoneyError, match="currency mismatch"):
        Money(Decimal("1"), "KRW") + Money(Decimal("1"), "USD")


def test_ac_n06_tool_failure_is_not_converted_to_empty_result() -> None:
    catalog = build_tool_catalog()
    delegate = build_mock_executor(catalog)

    class FailingTool:
        async def execute(self, request: ToolCallRequest):
            if request.tool_id == "PROJECT.LIST_CHILD_PROJECTS":
                raise ToolExecutionError("UPSTREAM_TIMEOUT", "provider timeout")
            return await delegate.execute(request)

    result = execute("exec-ac-n06", FailingTool())

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.error is not None
    assert result.error.detail_code == "UPSTREAM_TIMEOUT"
    assert result.outputs == ()


def test_ac_n07_partial_result_cannot_produce_pass() -> None:
    catalog = build_tool_catalog()
    delegate = build_mock_executor(catalog)

    class PartialTool:
        async def execute(self, request: ToolCallRequest):
            result = await delegate.execute(request)
            if request.tool_id == "PROJECT.LIST_CHILD_PROJECTS":
                return replace(result, completeness=Completeness.PARTIAL)
            return result

    result = execute("exec-ac-n07", PartialTool())

    assert result.status is ExecutionStatus.COMPLETED
    assert result.checks[0].status is CheckStatus.UNKNOWN
    assert result.checks[0].status is not CheckStatus.PASS


def test_ac_n08_budget_exceeded_produces_check_fail() -> None:
    catalog = build_tool_catalog()
    tools = build_mock_executor(catalog)
    tools.handlers["PROJECT.LIST_CHILD_PROJECTS"] = lambda arguments: [
        {
            "project_code": "CHILD-OVER",
            "parent_project": arguments["parent_project"],
            "expense_amount": Money(Decimal("100000001"), "KRW"),
        }
    ]

    result = execute("exec-ac-n08", tools)

    assert result.status is ExecutionStatus.COMPLETED
    assert result.checks[0].status is CheckStatus.FAIL


def test_ac_n09_limit_exceeded_returns_explicit_status() -> None:
    catalog = build_tool_catalog()
    tools = build_mock_executor(catalog)
    original = tools.handlers["PROJECT.LIST_PARENT_PROJECTS"]
    tools.handlers["PROJECT.LIST_PARENT_PROJECTS"] = (
        lambda arguments: original(arguments) * 11
    )

    result = execute("exec-ac-n09", tools)

    assert result.status is ExecutionStatus.LIMIT_EXCEEDED
    assert result.error is not None
    assert result.error.code == DiagnosticCode.RESOURCE_LIMIT_EXCEEDED


def test_ac_n10_replay_matches_original_semantic_result() -> None:
    result = asyncio.run(run_vertical_slice())

    assert result.live.status is ExecutionStatus.COMPLETED
    assert result.live.semantic_view() == result.replay.semantic_view()


def test_srs_certification_audits_all_requirements_and_negative_cases() -> None:
    report = certify_srs()

    assert report.status is CertificationStatus.CONDITIONAL
    assert report.requirement_count == 325
    assert report.negative_acceptance_count == 10
    assert sum(report.status_counts.values()) == 325
    assert report.gap_requirement_ids == (
        "NSL-SEC-013",
        "NSL-AE-004",
        "NSL-AE-008",
    )
    assert report.to_data()["status"] == "CONDITIONAL"
    assert tuple(validate_negative_acceptance()) == tuple(
        f"AC-N{number:02d}" for number in range(1, 11)
    )


def test_srs_certification_cli_writes_machine_readable_report(tmp_path, capsys) -> None:
    output = tmp_path / "certification.json"

    assert certification_main(["--output", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["requirement_count"] == 325
    assert payload["negative_acceptance_count"] == 10
    assert json.loads(capsys.readouterr().out) == payload


def test_negative_acceptance_extraction_rejects_missing_duplicate_or_drift(tmp_path) -> None:
    with pytest.raises(CertificationError, match="cannot load SRS"):
        extract_negative_acceptance(tmp_path / "missing.md")

    original = DEFAULT_SOURCE.read_text(encoding="utf-8")
    duplicate = tmp_path / "duplicate.md"
    duplicate.write_text(
        original.replace("# 41. 개발 단계", "## AC-N10 Replay\n\n# 41. 개발 단계"),
        encoding="utf-8",
    )
    with pytest.raises(CertificationError, match="duplicate"):
        extract_negative_acceptance(duplicate)

    drifted = tmp_path / "drifted.md"
    drifted.write_text(
        original.replace("## AC-N10 Replay", "## AC-N11 Replay"),
        encoding="utf-8",
    )
    with pytest.raises(CertificationError, match="exactly"):
        extract_negative_acceptance(drifted)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: {**data, "schema_version": 2}, "schema_version"),
        (lambda data: {**data, "srs_section": "39"}, "srs_section"),
        (lambda data: {**data, "expected_case_count": 9}, "expected_case_count"),
        (lambda data: {**data, "cases": {}}, "cases must be a list"),
        (lambda data: {**data, "cases": [None]}, "malformed"),
    ],
)
def test_negative_acceptance_baseline_rejects_invalid_document(
    tmp_path, change, message
) -> None:
    data = json.loads(DEFAULT_NEGATIVE_BASELINE.read_text(encoding="utf-8"))
    path = tmp_path / "negative.json"
    path.write_text(json.dumps(change(data)), encoding="utf-8")

    with pytest.raises(CertificationError, match=message):
        validate_negative_acceptance(baseline_path=path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "AC-N01", "duplicate"),
        ("title", "Changed", "differs"),
        ("evidence", [], "evidence is invalid"),
        ("evidence", ["missing.py::test_missing"], "path is missing"),
        ("evidence", ["tests/test_srs_certification.py"], "symbol is required"),
        ("evidence", ["tests/test_srs_certification.py::missing_symbol"], "symbol is missing"),
    ],
)
def test_negative_acceptance_case_rejects_drifted_evidence(
    tmp_path, field, value, message
) -> None:
    data = json.loads(DEFAULT_NEGATIVE_BASELINE.read_text(encoding="utf-8"))
    data["cases"][1][field] = value
    path = tmp_path / "negative.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CertificationError, match=message):
        validate_negative_acceptance(baseline_path=path)


def test_negative_acceptance_requires_all_cases_in_order(tmp_path) -> None:
    data = json.loads(DEFAULT_NEGATIVE_BASELINE.read_text(encoding="utf-8"))
    data["cases"].reverse()
    path = tmp_path / "negative.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CertificationError, match="in order"):
        validate_negative_acceptance(baseline_path=path)


def test_certification_wraps_traceability_errors_and_cli_failure(tmp_path, capsys) -> None:
    invalid = tmp_path / "traceability.json"
    invalid.write_text("{}", encoding="utf-8")

    with pytest.raises(CertificationError, match="traceability validation failed"):
        certify_srs(baseline_path=invalid)
    assert certification_main(["--baseline", str(invalid)]) == 2
    assert "traceability validation failed" in capsys.readouterr().err
