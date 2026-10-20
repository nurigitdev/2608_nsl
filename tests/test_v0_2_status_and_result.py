from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from nsl.core import (
    ApprovalStatus,
    CommitState,
    Completeness,
    ExecutionStatus,
    IdempotencyKey,
    PayloadHash,
    PreWriteEligibility,
    VerificationStatus,
)
from nsl.audit import InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.result_codec import RESULT_SCHEMA_VERSION, RESULT_V02_SCHEMA_VERSION
from nsl.runtime import RuntimeEngine
from nsl.runtime_models import (
    ApprovalSummary,
    ExecutionResult,
    ResourceUsage,
    SuspensionInfo,
    VerificationSummary,
    WriteSummary,
    ExecutionRequest,
)
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


LEGACY_STATUSES = {
    "CREATED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "TOOL_ERROR",
    "VALIDATION_ERROR",
    "LIMIT_EXCEEDED",
    "CANCELLED",
}
V02_STATUSES = {"WAITING_FOR_APPROVAL", "RECONCILIATION_REQUIRED"}
ROOT = Path(__file__).resolve().parents[1]


def _legacy_result(
    status: ExecutionStatus = ExecutionStatus.COMPLETED,
) -> ExecutionResult:
    return ExecutionResult(
        execution_id="execution-result",
        skill_id="RESULT.TEST",
        skill_version="1.0.0",
        semantic_hash="sha256:" + "a" * 64,
        status=status,
        completeness=Completeness.COMPLETE,
        checks=(),
        outputs=(),
        resources=ResourceUsage(0, 0, 0, 0),
    )


def _approval() -> ApprovalSummary:
    return ApprovalSummary(
        ApprovalStatus.APPROVED,
        "approval-create",
        "approval-request-0001",
    )


def _write(
    commit_state: CommitState = CommitState.COMMITTED,
) -> WriteSummary:
    return WriteSummary(
        commit_state=commit_state,
        tool_id="FINANCE.CREATE_DRAFT",
        node_id="write0001",
        idempotency_key=IdempotencyKey("idem-write0001"),
        payload_hash=PayloadHash("sha256:" + "b" * 64),
        provider_transaction_id="provider-transaction-0001",
        business_reference="DRAFT-0001",
    )


def _verification() -> VerificationSummary:
    return VerificationSummary(
        VerificationStatus.VERIFIED,
        reason_code="MATCHED",
        evidence_ref="evidence-verify-0001",
    )


def _suspension(version: int = 1) -> SuspensionInfo:
    return SuspensionInfo(
        reason="APPROVAL_REQUIRED",
        checkpoint_version=version,
        approval_id="approval-create",
        approval_request_id="approval-request-0001",
    )


def test_sts_201_preserves_every_v01_status_and_adds_only_durable_states() -> None:
    values = {status.value for status in ExecutionStatus}

    assert LEGACY_STATUSES <= values
    assert values == LEGACY_STATUSES | V02_STATUSES


def test_sts_202_validation_error_keeps_legacy_identity_and_result_meaning() -> None:
    result = _legacy_result(ExecutionStatus.VALIDATION_ERROR)

    assert ExecutionStatus.VALIDATION_ERROR.value == "VALIDATION_ERROR"
    assert result.to_data()["status"] == "VALIDATION_ERROR"
    assert result.to_data()["schema_version"] == RESULT_SCHEMA_VERSION


@pytest.mark.parametrize("forbidden", ["RESUME_QUEUED", "WRITE_IN_PROGRESS"])
def test_sts_203_and_204_platform_and_attempt_states_are_not_public_statuses(
    forbidden,
) -> None:
    assert forbidden not in ExecutionStatus.__members__
    with pytest.raises(ValueError):
        ExecutionStatus(forbidden)


def test_sts_205_unknown_commit_can_produce_reconciliation_result() -> None:
    result = replace(
        _legacy_result(ExecutionStatus.RECONCILIATION_REQUIRED),
        write=_write(CommitState.UNKNOWN_COMMIT_STATE),
    )

    assert result.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert result.write is not None
    assert result.write.commit_state is CommitState.UNKNOWN_COMMIT_STATE
    assert result.to_data()["status"] == "RECONCILIATION_REQUIRED"
    assert result.to_data()["write"]["commit_state"] == "UNKNOWN_COMMIT_STATE"


def test_waiting_for_approval_is_a_structured_durable_suspension() -> None:
    result = replace(
        _legacy_result(ExecutionStatus.WAITING_FOR_APPROVAL),
        pre_write_eligibility=PreWriteEligibility.REQUIRES_APPROVAL,
        approval=ApprovalSummary(
            ApprovalStatus.PENDING,
            "approval-create",
            "approval-request-0001",
        ),
        suspension=_suspension(),
    )

    assert result.to_data()["status"] == "WAITING_FOR_APPROVAL"
    assert result.to_data()["suspension"]["checkpoint_version"] == 1


def test_rsl_201_v01_result_data_and_json_are_byte_compatible() -> None:
    result = _legacy_result()
    expected = {
        "schema_version": "1.0",
        "execution_id": "execution-result",
        "skill_id": "RESULT.TEST",
        "skill_version": "1.0.0",
        "semantic_hash": "sha256:" + "a" * 64,
        "status": "COMPLETED",
        "completeness": "COMPLETE",
        "checks": [],
        "outputs": [],
        "resources": {
            "tool_calls": 0,
            "loop_iterations": 0,
            "emitted_rows": 0,
            "max_collection_size_seen": 0,
        },
        "error": None,
    }

    assert result.to_data() == expected
    assert result.to_json() == json.dumps(
        expected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert set(result.semantic_view()) == set(expected) - {
        "schema_version",
        "execution_id",
    }


def test_rsl_202_serializes_all_v02_optional_summary_fields_additively() -> None:
    result = replace(
        _legacy_result(),
        pre_write_eligibility=PreWriteEligibility.ALLOWED,
        approval=_approval(),
        write=_write(),
        verification=_verification(),
        suspension=_suspension(),
    )

    data = result.to_data()

    assert data["schema_version"] == RESULT_V02_SCHEMA_VERSION
    assert data["pre_write_eligibility"] == "ALLOWED"
    assert data["approval"] == _approval().to_data()
    assert data["write"] == _write().to_data()
    assert data["verification"] == _verification().to_data()
    assert data["suspension"] == _suspension().to_data()
    assert json.loads(result.to_json()) == data
    for name in (
        "pre_write_eligibility",
        "approval",
        "write",
        "verification",
        "suspension",
    ):
        assert result.semantic_view()[name] == data[name]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pre_write_eligibility", PreWriteEligibility.BLOCKED),
        ("approval", _approval()),
        ("write", _write()),
        ("verification", _verification()),
        ("suspension", _suspension()),
    ],
)
def test_rsl_202_each_optional_field_is_independently_serializable(
    field, value
) -> None:
    data = replace(_legacy_result(), **{field: value}).to_data()

    assert data["schema_version"] == RESULT_V02_SCHEMA_VERSION
    assert data[field] is not None
    assert {
        "pre_write_eligibility",
        "approval",
        "write",
        "verification",
        "suspension",
    } <= set(data)


def test_rsl_203_read_only_result_does_not_invent_write_semantics() -> None:
    result = _legacy_result()

    assert result.pre_write_eligibility is None
    assert result.approval is None
    assert result.write is None
    assert result.verification is None
    assert result.suspension is None
    assert not {
        "pre_write_eligibility",
        "approval",
        "write",
        "verification",
        "suspension",
    } & set(result.to_data())


def test_rsl_203_ir_1_0_runtime_execution_keeps_v01_result_contract() -> None:
    catalog = build_tool_catalog()
    source = (ROOT / "examples" / "project_budget_check.ns").read_text(
        encoding="utf-8"
    )
    skill = NslCompiler(catalog).compile(source).skill
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            ExecutionRequest(
                "execution-read-only",
                {"year": 2026},
                {"user": {"team_id": "TEAM-FINANCE"}},
                build_principal(),
            ),
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )
    )

    assert skill.ir_version == "1.0"
    assert result.status is ExecutionStatus.COMPLETED
    assert result.pre_write_eligibility is None
    assert result.approval is None
    assert result.write is None
    assert result.verification is None
    assert result.suspension is None
    assert result.to_data()["schema_version"] == RESULT_SCHEMA_VERSION


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("pre_write_eligibility", "ALLOWED", PreWriteEligibility),
        ("approval", object(), ApprovalSummary),
        ("write", object(), WriteSummary),
        ("verification", object(), VerificationSummary),
        ("suspension", object(), SuspensionInfo),
    ],
)
def test_execution_result_rejects_invalid_optional_summary_types(
    field, value, expected
) -> None:
    with pytest.raises(TypeError, match=expected.__name__):
        replace(_legacy_result(), **{field: value})


def test_result_summaries_are_immutable() -> None:
    summaries = (
        (_approval(), "status"),
        (_write(), "commit_state"),
        (_verification(), "status"),
        (_suspension(), "reason"),
    )

    for summary, field in summaries:
        with pytest.raises(FrozenInstanceError):
            setattr(summary, field, "changed")


@pytest.mark.parametrize("status", ["APPROVED", object()])
def test_approval_summary_rejects_untyped_status(status) -> None:
    with pytest.raises(TypeError, match="ApprovalStatus"):
        ApprovalSummary(status, "approval")


@pytest.mark.parametrize("status", ["COMMITTED", object()])
def test_write_summary_rejects_untyped_commit_state(status) -> None:
    with pytest.raises(TypeError, match="CommitState"):
        WriteSummary(status, "tool", "node", "key", "hash")


@pytest.mark.parametrize("status", ["VERIFIED", object()])
def test_verification_summary_rejects_untyped_status(status) -> None:
    with pytest.raises(TypeError, match="VerificationStatus"):
        VerificationSummary(status)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ApprovalSummary(ApprovalStatus.PENDING, ""),
        lambda: ApprovalSummary(ApprovalStatus.PENDING, "approval", " "),
        lambda: WriteSummary(CommitState.COMMITTED, "", "node", "key", "hash"),
        lambda: WriteSummary(CommitState.COMMITTED, "tool", "node", "", "hash"),
        lambda: VerificationSummary(VerificationStatus.UNKNOWN, reason_code=""),
        lambda: SuspensionInfo("", 1, "approval", "request"),
    ],
)
def test_result_summaries_reject_empty_required_identities(factory) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        factory()


@pytest.mark.parametrize("version", [-1, 0, False])
def test_suspension_rejects_non_positive_checkpoint_version(version) -> None:
    with pytest.raises(ValueError, match="checkpoint_version must be positive"):
        _suspension(version)


@pytest.mark.parametrize("version", [1, 2_147_483_647])
def test_suspension_accepts_checkpoint_version_boundaries(version) -> None:
    assert _suspension(version).checkpoint_version == version


def test_optional_summary_references_accept_none() -> None:
    approval = ApprovalSummary(ApprovalStatus.NOT_REQUESTED, "approval")
    write = WriteSummary(
        CommitState.NOT_COMMITTED,
        "tool",
        "node",
        "key",
        "sha256:" + "c" * 64,
    )
    verification = VerificationSummary(VerificationStatus.NOT_REQUESTED)

    assert approval.approval_request_id is None
    assert write.provider_transaction_id is None
    assert write.business_reference is None
    assert verification.reason_code is None
    assert verification.evidence_ref is None
