from __future__ import annotations

import ast
from enum import StrEnum
from pathlib import Path

import pytest

from nsl.core import (
    ApprovalStatus,
    BusinessOperationId,
    CommitState,
    IdempotencyKey,
    PayloadHash,
    PreWriteEligibility,
    RiskLevel,
    ToolCapability,
    VerificationStatus,
)


ROOT = Path(__file__).resolve().parents[1]
NSL = ROOT / "nsl"
SHARED_SEMANTIC_TYPES = {
    "ApprovalStatus",
    "BusinessOperationId",
    "CommitState",
    "IdempotencyKey",
    "PayloadHash",
    "PreWriteEligibility",
    "RiskLevel",
    "ToolCapability",
    "VerificationStatus",
}


def _semantic_type_definitions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    definitions.update(
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "NewType"
        for target in node.targets
        if isinstance(target, ast.Name)
    )
    return definitions & SHARED_SEMANTIC_TYPES


def test_arc_201_shared_semantic_types_have_single_core_owner() -> None:
    owners = {
        name: []
        for name in SHARED_SEMANTIC_TYPES
    }
    for path in NSL.rglob("*.py"):
        for name in _semantic_type_definitions(path):
            owners[name].append(path.relative_to(ROOT).as_posix())

    assert owners == {
        name: ["nsl/core.py"]
        for name in SHARED_SEMANTIC_TYPES
    }


@pytest.mark.parametrize(
    ("enum_type", "values"),
    [
        (ToolCapability, ("READ", "WRITE", "TRANSACTION")),
        (RiskLevel, ("R0", "R1", "R2", "R3", "R4", "R5")),
        (
            ApprovalStatus,
            ("NOT_REQUESTED", "PENDING", "APPROVED", "REJECTED", "EXPIRED"),
        ),
        (
            PreWriteEligibility,
            ("ALLOWED", "BLOCKED", "REQUIRES_APPROVAL"),
        ),
        (
            CommitState,
            ("COMMITTED", "NOT_COMMITTED", "UNKNOWN_COMMIT_STATE"),
        ),
        (
            VerificationStatus,
            ("NOT_REQUESTED", "VERIFIED", "FAILED", "UNKNOWN"),
        ),
    ],
)
def test_arc_201_shared_state_domains_are_exact(
    enum_type: type[StrEnum], values: tuple[str, ...]
) -> None:
    assert tuple(member.value for member in enum_type) == values


@pytest.mark.parametrize(
    ("enum_type", "invalid"),
    [
        (ToolCapability, ""),
        (ToolCapability, "read"),
        (RiskLevel, "R-1"),
        (RiskLevel, "R6"),
        (ApprovalStatus, "WAITING"),
        (PreWriteEligibility, "RECONCILIATION_REQUIRED"),
        (CommitState, "UNKNOWN"),
        (VerificationStatus, "COMMITTED"),
    ],
)
def test_arc_201_shared_state_domains_reject_cross_domain_and_boundary_values(
    enum_type: type[StrEnum], invalid: str
) -> None:
    with pytest.raises(ValueError):
        enum_type(invalid)


def test_arc_201_runtime_identity_types_are_distinct_declarations() -> None:
    assert BusinessOperationId.__name__ == "BusinessOperationId"
    assert IdempotencyKey.__name__ == "IdempotencyKey"
    assert PayloadHash.__name__ == "PayloadHash"
    assert len({BusinessOperationId, IdempotencyKey, PayloadHash}) == 3
    assert BusinessOperationId("business-1") == "business-1"
    assert IdempotencyKey("idempotency-1") == "idempotency-1"
    assert PayloadHash("sha256:" + "0" * 64) == "sha256:" + "0" * 64
