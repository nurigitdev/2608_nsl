from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

from nsl.cli import CLIExitCode, main


ROOT = Path(__file__).resolve().parents[1]
CARD_ROOT = ROOT / "examples/nsl_showcase_pack/corporate_card"


def _invoke(*arguments: str):
    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    output = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    error = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return code, output, error


def _output_values(result: dict) -> list[dict]:
    return [
        {field["name"]: field["value"] for field in record["fields"]}
        for record in result["outputs"]
    ]


def test_showcase_card_001_monthly_summary_emits_totals_without_checks() -> None:
    profile = CARD_ROOT / "summary.profile.json"

    check_code, check, check_error = _invoke(
        "check", "--profile", str(profile)
    )
    assert check_code == CLIExitCode.SUCCESS, check_error
    assert check["skill_id"] == "FINANCE.CORPORATE_CARD_MONTHLY_SUMMARY"

    code, output, error = _invoke(
        "run", "--profile", str(profile), "--no-isolate"
    )
    assert code == CLIExitCode.SUCCESS, error
    result = output["result"]
    assert result["status"] == "COMPLETED"
    assert result["checks"] == []
    assert _output_values(result) == [
        {
            "team_id": "TEAM-FINANCE",
            "team_name": "Finance",
            "total_amount": {
                "$type": "Money",
                "amount": "900000",
                "currency": "KRW",
            },
            "transaction_count": 9,
        },
        {
            "team_id": "TEAM-DEVELOPMENT",
            "team_name": "Development",
            "total_amount": {
                "$type": "Money",
                "amount": "1000000",
                "currency": "KRW",
            },
            "transaction_count": 10,
        },
        {
            "team_id": "TEAM-HR",
            "team_name": "Human Resources",
            "total_amount": {
                "$type": "Money",
                "amount": "0",
                "currency": "KRW",
            },
            "transaction_count": 0,
        },
        {
            "team_id": "TEAM-SALES",
            "team_name": "Sales",
            "total_amount": {
                "$type": "Money",
                "amount": "1000001",
                "currency": "KRW",
            },
            "transaction_count": 2,
        },
    ]
    assert result["resources"] == {
        "tool_calls": 5,
        "loop_iterations": 4,
        "emitted_rows": 4,
        "max_collection_size_seen": 4,
    }


def test_showcase_card_002_basic_policy_checks_enforce_exact_boundaries() -> None:
    profile = CARD_ROOT / "policy.profile.json"

    check_code, check, check_error = _invoke(
        "check", "--profile", str(profile)
    )
    assert check_code == CLIExitCode.SUCCESS, check_error
    assert check["skill_id"] == "FINANCE.CORPORATE_CARD_MONTHLY_POLICY_CHECK"

    code, output, error = _invoke(
        "run", "--profile", str(profile), "--no-isolate"
    )
    assert code == CLIExitCode.SUCCESS, error
    result = output["result"]
    values = _output_values(result)

    assert [
        (
            item["team_id"],
            item["amount_limit_status"],
            item["usage_presence_status"],
            item["count_limit_status"],
        )
        for item in values
    ] == [
        ("TEAM-FINANCE", "PASS", "PASS", "PASS"),
        ("TEAM-DEVELOPMENT", "PASS", "PASS", "FAIL"),
        ("TEAM-HR", "PASS", "FAIL", "PASS"),
        ("TEAM-SALES", "FAIL", "PASS", "PASS"),
    ]
    assert [item["total_amount"]["amount"] for item in values] == [
        "900000",
        "1000000",
        "0",
        "1000001",
    ]
    assert [item["transaction_count"] for item in values] == [9, 10, 0, 2]
    assert len(result["checks"]) == 24
    assert result["resources"] == {
        "tool_calls": 5,
        "loop_iterations": 4,
        "emitted_rows": 4,
        "max_collection_size_seen": 4,
    }


def test_showcase_card_003_receipt_single_amount_and_merchant_checks() -> None:
    code, output, error = _invoke(
        "run",
        "--profile",
        str(CARD_ROOT / "policy.profile.json"),
        "--no-isolate",
    )
    assert code == CLIExitCode.SUCCESS, error
    values = _output_values(output["result"])

    assert [
        (
            item["team_id"],
            item["missing_receipt_count"],
            item["maximum_transaction_amount"]["amount"],
            item["restricted_merchant_count"],
            item["receipt_status"],
            item["single_transaction_status"],
            item["merchant_status"],
        )
        for item in values
    ] == [
        ("TEAM-FINANCE", 0, "100000", 0, "PASS", "PASS", "PASS"),
        ("TEAM-DEVELOPMENT", 0, "500000", 0, "PASS", "PASS", "PASS"),
        ("TEAM-HR", 0, "0", 0, "PASS", "PASS", "PASS"),
        ("TEAM-SALES", 1, "600001", 1, "FAIL", "FAIL", "FAIL"),
    ]
    sales_fields = {
        field["name"]: field
        for field in output["result"]["outputs"][-1]["fields"]
    }
    for name in (
        "total_amount",
        "transaction_count",
        "missing_receipt_count",
        "restricted_merchant_count",
        "maximum_transaction_amount",
    ):
        assert sales_fields[name]["classification"] == "CONFIDENTIAL"
    for name in (
        "amount_limit_status",
        "usage_presence_status",
        "count_limit_status",
        "receipt_status",
        "single_transaction_status",
        "merchant_status",
    ):
        assert sales_fields[name]["classification"] == "INTERNAL"


def test_showcase_card_004_summary_and_policy_scenarios_pass() -> None:
    for suite_name in ("summary.scenarios.json", "policy.scenarios.json"):
        code, output, error = _invoke(
            "test", "--suite", str(CARD_ROOT / suite_name)
        )
        assert code == CLIExitCode.SUCCESS, error
        assert output["status"] == "passed"
        assert output["case_count"] == 5
        assert output["passed"] == 5
        assert output["failed"] == 0
        assert [item["id"] for item in output["cases"]] == [
            "boundary-matrix",
            "tool-fixture-missing",
            "authorization-denied",
            "resource-limit",
            "deterministic-repeat",
        ]


def test_showcase_card_005_pack_inventory_tracks_implemented_and_planned_modules() -> None:
    inventory = json.loads(
        (CARD_ROOT.parent / "pack.json").read_text(encoding="utf-8")
    )

    assert inventory["format"] == "NSL-BUSINESS-CONTROLS-SHOWCASE"
    assert inventory["schema_version"] == "1.0"
    assert inventory["pack_id"] == "NEX.NSL.BUSINESS_CONTROLS"
    assert inventory["version"] == "0.1.0"
    assert [module["module_id"] for module in inventory["modules"]] == [
        "CORPORATE_CARD_CONTROL",
        "EMPLOYEE_CONTRACT_EXPIRY",
        "ACCESS_SEGREGATION",
        "VENDOR_CONCENTRATION",
        "DUPLICATE_INVOICE",
    ]
    assert [module["status"] for module in inventory["modules"]] == [
        "IMPLEMENTED",
        "PLANNED",
        "PLANNED",
        "PLANNED",
        "PLANNED",
    ]
    assert inventory["modules"][0]["skills"] == [
        "FINANCE.CORPORATE_CARD_MONTHLY_SUMMARY",
        "FINANCE.CORPORATE_CARD_MONTHLY_POLICY_CHECK",
    ]
    for source_name in (
        "corporate_card_monthly_summary.ns",
        "corporate_card_monthly_policy_check.ns",
    ):
        assert (CARD_ROOT / source_name).is_file()
