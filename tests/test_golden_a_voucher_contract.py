from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/golden_a_voucher_draft"
SEMANTIC_HASH = (
    "sha256:b6ba60f6ae681dc754b6d304fcf7c99794a48e88429c698afd9d12afcb90a8fc"
)
PAYLOAD_HASH = "sha256:240a7e83a1b3355ba98b5473cc239f1aea6a318b17e53e385c01837666fb8eb9"


def _tool(tool_id: str, version: str) -> dict:
    catalog = json.loads((EXAMPLE / "tools.json").read_text(encoding="utf-8"))
    return next(
        item
        for item in catalog["tools"]
        if item["tool_id"] == tool_id and item["version"] == version
    )


def test_golden_a_v11_write_contract_contains_only_canonical_business_payload() -> None:
    resolver = _tool("FINANCE.RESOLVE_VOUCHER_DRAFT_COMMAND", "1.0.0")
    write = _tool("FINANCE.CREATE_VOUCHER_DRAFT", "1.1.0")
    expected = [
        "schema_version",
        "tenant_id",
        "requester_ref",
        "project_ref",
        "voucher_type",
        "transaction_date",
        "currency",
        "description_text",
        "lines",
        "attachments",
    ]

    assert [item["name"] for item in write["inputs"]] == expected
    assert [item["name"] for item in resolver["output"]["fields"]] == expected
    assert write["write_semantics"]["verification"]["tool_id"] == (
        "FINANCE.GET_VOUCHER_DRAFT"
    )
    assert not {
        "nsl_approval_request_id",
        "nsl_approval_evidence_ref",
        "business_approval_proof_ref",
    } & set(expected)

    fixture = json.loads(
        (EXAMPLE / "fixtures/success.json").read_text(encoding="utf-8")
    )
    command = next(
        item
        for item in fixture["tools"]
        if item["tool_id"] == "FINANCE.RESOLVE_VOUCHER_DRAFT_COMMAND"
    )["cases"][0]["result"]
    assert command["project_ref"] == {
        "type": "eos.project",
        "id": "82000000-0000-4000-8000-000000000001",
    }
    assert [line["signed_amount"] for line in command["lines"]] == [
        "84000.00",
        "-84000.00",
    ]


def test_golden_a_v11_controlled_prefix_binds_expected_payload_hash(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    approval = tmp_path / "approval.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "nsl",
            "run",
            "--profile",
            str(EXAMPLE / "controlled.profile.json"),
            "--business-operation-id",
            "86000000-0000-4000-8000-000000000001",
            "--checkpoint-out",
            str(checkpoint),
            "--approval-request-out",
            str(approval),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    request = json.loads(approval.read_text(encoding="utf-8"))["approval_request"]

    assert result["result"]["status"] == "WAITING_FOR_APPROVAL"
    assert result["result"]["semantic_hash"] == SEMANTIC_HASH
    assert request["skill_version"] == "1.1.0"
    assert request["skill_semantic_hash"] == SEMANTIC_HASH
    assert request["payload_hash"] == PAYLOAD_HASH
    assert request["approval_request_id"] not in PAYLOAD_HASH
