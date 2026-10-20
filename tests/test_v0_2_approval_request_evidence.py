from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nsl.approval import ApprovalEvidence, ApprovalRequest
from nsl.core import (
    ApprovalDecision,
    ApprovalRequestId,
    BusinessOperationId,
    DataClassification,
    PayloadHash,
    RiskLevel,
)
from nsl.data_protection import CredentialMaterialError


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
NOW = datetime(2026, 10, 20, 1, 2, 3, tzinfo=UTC)


def _request(**changes: object) -> ApprovalRequest:
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "execution_id": "exec-0047",
        "business_operation_id": BusinessOperationId("business-0047"),
        "approval_id": "APPROVE_DRAFT",
        "approval_request_id": ApprovalRequestId("approval-request-0047"),
        "skill_id": "FINANCE.DRAFT",
        "skill_version": "0.2.0",
        "skill_semantic_hash": HASH_A,
        "write_node_id": "write0001",
        "tool_id": "finance.create_draft",
        "payload_hash": PayloadHash(HASH_B),
        "write_intent_hash": "write-intent-0047",
        "risk_level": RiskLevel.R3,
        "message": "Create finance draft",
        "expires_at": NOW + timedelta(hours=1),
        "display_safe_summary": {
            "document_type": "expense",
            "line_count": 2,
            "flags": ["reviewed", True],
        },
        "protected_payload_ref": "snapshot://tenant-a/payload-0047",
        "data_classification": DataClassification.CONFIDENTIAL,
    }
    values.update(changes)
    return ApprovalRequest(**values)  # type: ignore[arg-type]


def _evidence(**changes: object) -> ApprovalEvidence:
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "execution_id": "exec-0047",
        "business_operation_id": BusinessOperationId("business-0047"),
        "approval_id": "APPROVE_DRAFT",
        "approval_request_id": ApprovalRequestId("approval-request-0047"),
        "skill_semantic_hash": HASH_A,
        "decision": ApprovalDecision.APPROVED,
        "approver_id": "user-approver",
        "decided_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
        "payload_hash": PayloadHash(HASH_B),
        "write_intent_hash": "write-intent-0047",
        "evidence_integrity": "opaque-signature-0047",
    }
    values.update(changes)
    return ApprovalEvidence(**values)  # type: ignore[arg-type]


def test_apr_002_static_and_execution_approval_identities_are_distinct() -> None:
    request = _request()

    assert request.approval_id == "APPROVE_DRAFT"
    assert request.approval_request_id == "approval-request-0047"
    assert request.approval_id != request.approval_request_id
    assert "approval_id" in {item.name for item in fields(request)}
    assert "approval_request_id" in {item.name for item in fields(request)}


def test_apr_003_request_and_evidence_preserve_all_required_bindings() -> None:
    request = _request()
    evidence = _evidence()

    for name in (
        "tenant_id",
        "execution_id",
        "business_operation_id",
        "approval_id",
        "approval_request_id",
        "skill_semantic_hash",
        "payload_hash",
        "write_intent_hash",
    ):
        assert getattr(request, name) == getattr(evidence, name)
    assert evidence.decision is ApprovalDecision.APPROVED
    assert set(ApprovalDecision) == {
        ApprovalDecision.APPROVED,
        ApprovalDecision.REJECTED,
    }


def test_apr_014_request_data_contains_protected_metadata_not_raw_payload() -> None:
    data = _request().to_data()

    assert data["display_safe_summary"] == {
        "document_type": "expense",
        "line_count": 2,
        "flags": ["reviewed", True],
    }
    assert data["protected_payload_ref"] == (
        "snapshot://tenant-a/payload-0047"
    )
    assert data["payload_hash"] == HASH_B
    assert data["data_classification"] == "CONFIDENTIAL"
    assert "payload" not in data
    assert "arguments" not in data


def test_request_and_evidence_are_immutable_with_deep_frozen_summary() -> None:
    source = {"nested": {"items": [1]}}
    request = _request(display_safe_summary=source)
    source["nested"] = {"items": [2]}

    assert request.to_data()["display_safe_summary"] == {
        "nested": {"items": [1]}
    }
    with pytest.raises(TypeError):
        request.display_safe_summary["new"] = "value"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        request.approval_id = "CHANGED"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        _evidence().decision = ApprovalDecision.REJECTED  # type: ignore[misc]


@pytest.mark.parametrize(
    ("changes", "error_type", "message"),
    [
        ({"tenant_id": ""}, ValueError, "tenant_id"),
        ({"approval_request_id": None}, ValueError, "approval_request_id"),
        ({"skill_semantic_hash": "sha256:" + "A" * 64}, ValueError, "SHA-256"),
        ({"payload_hash": "sha256:123"}, ValueError, "SHA-256"),
        ({"risk_level": "R3"}, TypeError, "RiskLevel"),
        ({"data_classification": "CONFIDENTIAL"}, TypeError, "DataClassification"),
        ({"expires_at": datetime(2026, 1, 1)}, ValueError, "UTC"),
        (
            {"expires_at": NOW.astimezone(timezone(timedelta(hours=9)))},
            ValueError,
            "UTC",
        ),
        ({"display_safe_summary": []}, TypeError, "mapping"),
        ({"display_safe_summary": {"": "value"}}, ValueError, "keys"),
        ({"display_safe_summary": {"value": object()}}, TypeError, "JSON-safe"),
        ({"display_safe_summary": {"value": float("inf")}}, ValueError, "finite"),
        ({"protected_payload_ref": ""}, ValueError, "protected_payload_ref"),
    ],
)
def test_approval_request_rejects_malformed_or_unsafe_values(
    changes: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        _request(**changes)


def test_approval_request_accepts_no_expiry_or_protected_reference() -> None:
    request = _request(
        expires_at=None,
        protected_payload_ref=None,
        display_safe_summary={"ratio": 0.5, "optional": None},
    )

    assert request.to_data()["expires_at"] is None
    assert request.to_data()["protected_payload_ref"] is None
    assert request.to_data()["display_safe_summary"] == {
        "ratio": 0.5,
        "optional": None,
    }


def test_approval_request_rejects_credentials_in_summary_and_identity() -> None:
    with pytest.raises(CredentialMaterialError, match="display_safe_summary"):
        _request(display_safe_summary={"api_key": "secret-value"})
    with pytest.raises(CredentialMaterialError, match="tenant_id"):
        _request(tenant_id="token=secret-value")


@pytest.mark.parametrize(
    ("changes", "error_type", "message"),
    [
        ({"execution_id": ""}, ValueError, "execution_id"),
        ({"decision": "APPROVED"}, TypeError, "ApprovalDecision"),
        ({"decided_at": "2026-01-01"}, TypeError, "datetime"),
        ({"decided_at": datetime(2026, 1, 1)}, ValueError, "UTC"),
        (
            {"expires_at": NOW - timedelta(microseconds=1)},
            ValueError,
            "precedes",
        ),
        ({"evidence_integrity": ""}, ValueError, "evidence_integrity"),
        ({"payload_hash": "sha256:" + "g" * 64}, ValueError, "SHA-256"),
    ],
)
def test_approval_evidence_rejects_malformed_values(
    changes: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        _evidence(**changes)


def test_approval_evidence_serializes_utc_instants_and_optional_expiry() -> None:
    evidence = _evidence(decision=ApprovalDecision.REJECTED, expires_at=None)

    assert evidence.to_data() == {
        "tenant_id": "tenant-a",
        "execution_id": "exec-0047",
        "business_operation_id": "business-0047",
        "approval_id": "APPROVE_DRAFT",
        "approval_request_id": "approval-request-0047",
        "skill_semantic_hash": HASH_A,
        "decision": "REJECTED",
        "approver_id": "user-approver",
        "decided_at": "2026-10-20T01:02:03+00:00",
        "expires_at": None,
        "payload_hash": HASH_B,
        "write_intent_hash": "write-intent-0047",
        "evidence_integrity": "opaque-signature-0047",
    }
