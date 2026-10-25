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
EXAMPLE = ROOT / "examples" / "project_operations"
CANONICAL_NAME = re.compile(
    r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$"
)
PROJECT_ID = "92000000-0000-4000-8000-000000000001"

CASES = (
    (
        "project_project_get.ns",
        "PROJECT.PROJECT.GET",
        {
            "tenant_id": "p8-project-read",
            "requester_subject_id": "employee-1001",
            "project_id": PROJECT_ID,
        },
    ),
    (
        "project_project_list.ns",
        "PROJECT.PROJECT.LIST",
        {
            "tenant_id": "p8-project-read",
            "requester_subject_id": "employee-1001",
            "limit": 100,
            "offset": 0,
        },
    ),
    (
        "project_membership_list.ns",
        "PROJECT.MEMBERSHIP.LIST",
        {
            "tenant_id": "p8-project-read",
            "requester_subject_id": "employee-1001",
            "project_id": PROJECT_ID,
            "limit": 100,
            "offset": 0,
        },
    ),
)


@pytest.mark.parametrize(("filename", "identity", "inputs"), CASES)
def test_project_read_sources_compile_and_execute_deterministically(
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
    assert first.skill.semantic_hash.startswith("sha256:")
    assert [tool.tool_id for tool in first.skill.required_tools] == [identity]
    assert first.skill.required_tools[0].capability is ToolCapability.READ
    assert CANONICAL_NAME.fullmatch(identity)

    handlers = load_mock_handlers_document(
        load_json_document(EXAMPLE / "fixtures" / "read_success.json"),
        catalog,
    )
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            first.skill,
            ExecutionRequest(
                execution_id=f"p8-s98-{identity.lower()}",
                inputs=inputs,
                runtime_context={},
                principal=load_principal(EXAMPLE / "principal_read.json"),
            ),
            MockToolExecutor(catalog, handlers),
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert len(result.outputs) == 1
    assert result.resources.tool_calls == 1


def test_project_read_catalog_matches_canonical_scope() -> None:
    catalog = load_tool_catalog(EXAMPLE / "tools.json")
    identities = {
        ("PROJECT.PROJECT.GET", "1.0.0"),
        ("PROJECT.PROJECT.LIST", "1.0.0"),
        ("PROJECT.MEMBERSHIP.LIST", "1.0.0"),
    }
    for tool_id, version in identities:
        contract = catalog.get(tool_id, version)
        assert contract.required_scope == "project:project:read"
        assert contract.capability is ToolCapability.READ
        assert CANONICAL_NAME.fullmatch(tool_id)


def test_project_read_artifact_manifest_matches_exact_sources() -> None:
    catalog = load_tool_catalog(EXAMPLE / "tools.json")
    manifest = load_json_document(EXAMPLE / "artifact_manifest.json")

    assert manifest["schema_version"] == "nsl_project_operation_artifact_set.v1"
    assert manifest["language_version"] == "0.2"
    assert manifest["runtime_package_version"] == "0.2.0"
    assert manifest["tool_catalog"]["sha256"] == _sha256(EXAMPLE / "tools.json")
    assert len(manifest["skills"]) == 3
    for artifact in manifest["skills"]:
        source_path = EXAMPLE / artifact["path"]
        compiled = NslCompiler(catalog).compile(
            source_path.read_text(encoding="utf-8")
        ).skill
        assert artifact["skill_id"] == compiled.skill_id
        assert artifact["skill_version"] == compiled.skill_version
        assert artifact["source_sha256"] == _sha256(source_path)
        assert artifact["semantic_hash"] == compiled.semantic_hash


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
