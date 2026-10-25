from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

import pytest

from nsl.audit import InMemoryAuditSink
from nsl.cli_config import (
    load_json_document,
    load_mock_handlers_document,
    load_principal,
    load_tool_catalog,
)
from nsl.compiler import NslCompiler
from nsl.core import ExecutionStatus
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.tools import MockToolExecutor, ToolCapability

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "voucher_operations"
CANONICAL_NAME = re.compile(
    r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$"
)

CASES = (
    (
        "finance_voucher_draft_get.ns",
        "FINANCE.VOUCHER_DRAFT.GET",
        {
            "tenant_id": "p8-voucher-read",
            "requester_subject_id": "employee-1001",
            "voucher_id": "94000000-0000-4000-8000-000000000001",
        },
    ),
    (
        "finance_voucher_draft_list.ns",
        "FINANCE.VOUCHER_DRAFT.LIST",
        {
            "tenant_id": "p8-voucher-read",
            "requester_subject_id": "employee-1001",
            "project_id": "92000000-0000-4000-8000-000000000001",
            "limit": 100,
            "offset": 0,
        },
    ),
)


@pytest.mark.parametrize(("filename", "identity", "inputs"), CASES)
def test_voucher_read_sources_compile_and_execute_deterministically(
    filename: str,
    identity: str,
    inputs: dict[str, object],
) -> None:
    catalog = load_tool_catalog(EXAMPLE / "tools.json")
    source = (EXAMPLE / filename).read_text(encoding="utf-8")
    first = NslCompiler(catalog).compile(source)
    second = NslCompiler(catalog).compile(source)

    assert first.nso_bytes == second.nso_bytes
    assert first.skill.skill_id == identity
    assert first.skill.semantic_hash == second.skill.semantic_hash
    assert CANONICAL_NAME.fullmatch(identity)
    assert first.skill.required_tools[0].capability is ToolCapability.READ

    handlers = load_mock_handlers_document(
        load_json_document(EXAMPLE / "fixtures" / "read_success.json"),
        catalog,
    )
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            first.skill,
            ExecutionRequest(
                execution_id=f"p8-s101-{identity.lower()}",
                inputs=inputs,
                runtime_context={},
                principal=load_principal(EXAMPLE / "principal_read.json"),
            ),
            MockToolExecutor(catalog, handlers),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert result.resources.tool_calls == 1
    assert len(result.outputs) == 1


def test_voucher_read_catalog_and_manifest_match_exact_sources() -> None:
    catalog = load_tool_catalog(EXAMPLE / "tools.json")
    manifest = load_json_document(EXAMPLE / "artifact_manifest.json")

    assert manifest["schema_version"] == "nsl_voucher_read_artifact_set.v1"
    assert manifest["runtime_package_version"] == "0.2.1"
    assert manifest["tool_catalog"]["sha256"] == _sha256(EXAMPLE / "tools.json")
    assert len(manifest["skills"]) == 2
    for artifact in manifest["skills"]:
        contract = catalog.get(artifact["skill_id"], artifact["skill_version"])
        source_path = EXAMPLE / artifact["path"]
        compiled = NslCompiler(catalog).compile(
            source_path.read_text(encoding="utf-8")
        ).skill
        assert contract.required_scope == "finance:voucher-draft:read"
        assert contract.capability is ToolCapability.READ
        assert artifact["source_sha256"] == _sha256(source_path)
        assert artifact["semantic_hash"] == compiled.semantic_hash


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
