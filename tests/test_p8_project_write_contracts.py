from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from nsl.cli_config import load_json_document, load_tool_catalog
from nsl.compiler import NslCompiler
from nsl.core import RiskLevel, ToolCapability
from nsl.runtime import ControlledExecutionPlan
from nsl.tools import Atomicity, OperationKind

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "project_operations"
CANONICAL_NAME = re.compile(
    r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$"
)

CASES = (
    (
        "project_nvt_project_create.ns",
        "PROJECT.NVT_PROJECT.CREATE",
        OperationKind.CREATE,
    ),
    (
        "project_nvt_project_update.ns",
        "PROJECT.NVT_PROJECT.UPDATE",
        OperationKind.UPDATE,
    ),
    ("project_project_create.ns", "PROJECT.PROJECT.CREATE", OperationKind.CREATE),
    (
        "project_project_deactivate.ns",
        "PROJECT.PROJECT.DEACTIVATE",
        OperationKind.UPDATE,
    ),
    ("project_membership_add.ns", "PROJECT.MEMBERSHIP.ADD", OperationKind.CREATE),
    (
        "project_membership_remove.ns",
        "PROJECT.MEMBERSHIP.REMOVE",
        OperationKind.UPDATE,
    ),
)


@pytest.mark.parametrize(("filename", "identity", "operation_kind"), CASES)
def test_project_write_sources_compile_with_strict_controlled_write_semantics(
    filename: str,
    identity: str,
    operation_kind: OperationKind,
) -> None:
    catalog = load_tool_catalog(EXAMPLE / "tools_write.json")
    source = (EXAMPLE / filename).read_text(encoding="utf-8")
    first = NslCompiler(catalog).compile(source)
    second = NslCompiler(catalog).compile(source)
    contract = catalog.get(identity, "1.0.0")

    assert first.nso_bytes == second.nso_bytes
    assert first.skill.skill_id == identity
    assert first.skill.semantic_hash == second.skill.semantic_hash
    assert CANONICAL_NAME.fullmatch(identity)
    assert contract.capability is ToolCapability.WRITE
    assert contract.operation_kind is operation_kind
    assert contract.risk_level is RiskLevel.R3
    assert contract.write_semantics is not None
    assert contract.write_semantics.atomicity is Atomicity.ATOMIC
    assert contract.write_semantics.idempotency.required is True
    assert contract.write_semantics.approval_required is True
    assert contract.write_semantics.preconditions_supported is True
    assert contract.write_semantics.verification is not None
    assert contract.write_semantics.verification.reference_schema == contract.output_type

    plan = ControlledExecutionPlan.from_skill(first.skill)
    required = first.skill.required_tools[0]
    assert required.tool_id == identity
    assert plan.write_expression.tool_ref == required.tool_ref
    assert plan.write_expression.approval_ref == plan.approval.approval_id


def test_project_write_catalog_has_exact_operation_and_scope_map() -> None:
    catalog = load_tool_catalog(EXAMPLE / "tools_write.json")
    expected = {
        "PROJECT.NVT_PROJECT.CREATE": (
            OperationKind.CREATE,
            "project:nvt_project:create",
        ),
        "PROJECT.NVT_PROJECT.UPDATE": (
            OperationKind.UPDATE,
            "project:nvt_project:admin",
        ),
        "PROJECT.PROJECT.CREATE": (OperationKind.CREATE, "project:project:create"),
        "PROJECT.PROJECT.DEACTIVATE": (
            OperationKind.UPDATE,
            "project:project:admin",
        ),
        "PROJECT.MEMBERSHIP.ADD": (
            OperationKind.CREATE,
            "project:membership:manage",
        ),
        "PROJECT.MEMBERSHIP.REMOVE": (
            OperationKind.UPDATE,
            "project:membership:manage",
        ),
    }

    for tool_id, (operation_kind, required_scope) in expected.items():
        contract = catalog.get(tool_id, "1.0.0")
        assert contract.operation_kind is operation_kind
        assert contract.required_scope == required_scope
        assert CANONICAL_NAME.fullmatch(tool_id)


def test_project_write_artifact_manifest_matches_exact_sources() -> None:
    catalog = load_tool_catalog(EXAMPLE / "tools_write.json")
    manifest = load_json_document(EXAMPLE / "write_artifact_manifest.json")

    assert manifest["schema_version"] == "nsl_project_write_artifact_set.v1"
    assert manifest["language_version"] == "0.2"
    assert manifest["runtime_package_version"] == "0.2.1"
    assert manifest["tool_catalog"]["sha256"] == _sha256(
        EXAMPLE / "tools_write.json"
    )
    assert len(manifest["skills"]) == 6
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
