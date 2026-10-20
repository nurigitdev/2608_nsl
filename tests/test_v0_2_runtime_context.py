from __future__ import annotations

from dataclasses import fields
from inspect import signature
from pathlib import Path

import pytest

from nsl.compiler import NslCompiler
from nsl.core import (
    ApprovalStatus,
    BusinessOperationId,
    IdempotencyKey,
    PayloadHash,
)
from nsl.data_protection import CredentialMaterialError
from nsl.runtime import RuntimeEngine
from nsl.runtime_models import (
    ApprovalState,
    CheckpointState,
    ExecutionContext,
    ExecutionIdentity,
    ExecutionRequest,
    ExecutionSecurityContext,
    IdempotencyState,
    WriteJournalState,
)
from nsl.security import DataHandlingPolicy
from nsl.vertical_slice import build_principal, build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def _request(
    execution_id: str = "exec-v02-context",
    *,
    business_operation_id: BusinessOperationId | None = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
        data_policy=DataHandlingPolicy(
            snapshot_retention_days=7,
            audit_retention_days=30,
        ),
        business_operation_id=business_operation_id,
    )


def _context(execution_id: str = "exec-v02-context") -> ExecutionContext:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    return ExecutionContext(skill, _request(execution_id))


def test_ctx_201_context_preserves_isolated_per_execution_approval_state() -> None:
    first = _context("exec-ctx-201-a")
    second = _context("exec-ctx-201-b")

    first.approvals.records["CREATE_DRAFT"] = ApprovalStatus.PENDING

    assert isinstance(first.approvals, ApprovalState)
    assert first.approvals.records == {
        "CREATE_DRAFT": ApprovalStatus.PENDING,
    }
    assert second.approvals.records == {}
    assert first.approvals is not second.approvals


def test_ctx_202_context_preserves_isolated_per_execution_write_journal() -> None:
    first = _context("exec-ctx-202-a")
    second = _context("exec-ctx-202-b")
    event = {"event_type": "WRITE_REQUESTED", "node_id": "write0001"}

    first.writes.entries.append(event)

    assert isinstance(first.writes, WriteJournalState)
    assert first.writes.entries == [event]
    assert second.writes.entries == []
    assert first.writes is not second.writes


def test_ctx_203_context_preserves_isolated_per_execution_idempotency_state() -> None:
    first = _context("exec-ctx-203-a")
    second = _context("exec-ctx-203-b")

    first.idempotency.keys_by_write_node["write0001"] = IdempotencyKey(
        "idem-ctx-203"
    )
    first.idempotency.payload_hashes_by_write_node["write0001"] = PayloadHash(
        "sha256:" + "a" * 64
    )

    assert isinstance(first.idempotency, IdempotencyState)
    assert first.idempotency.keys_by_write_node == {
        "write0001": "idem-ctx-203"
    }
    assert first.idempotency.payload_hashes_by_write_node == {
        "write0001": "sha256:" + "a" * 64
    }
    assert second.idempotency.keys_by_write_node == {}
    assert second.idempotency.payload_hashes_by_write_node == {}


def test_ctx_204_context_preserves_checkpoint_state_without_persisting_it() -> None:
    context = _context("exec-ctx-204")

    context.checkpoint.checkpoint_version = 1
    context.checkpoint.last_completed_node_id = "let0001"
    context.checkpoint.suspended_at_node_id = "approval0001"
    context.checkpoint.trace_sequence = 2

    assert isinstance(context.checkpoint, CheckpointState)
    assert context.checkpoint.checkpoint_version == 1
    assert context.checkpoint.last_completed_node_id == "let0001"
    assert context.checkpoint.suspended_at_node_id == "approval0001"
    assert context.checkpoint.trace_sequence == 2
    assert not hasattr(context.checkpoint, "save")
    assert not hasattr(context.checkpoint, "load")


def test_ctx_205_named_state_objects_are_mutable_containers_not_services() -> None:
    context = _context("exec-ctx-205")
    state_objects = (
        context.approvals,
        context.writes,
        context.idempotency,
        context.checkpoint,
    )

    assert all(not callable(item) for item in state_objects)
    assert all(not hasattr(item, "connect") for item in state_objects)
    assert all(not hasattr(item, "execute") for item in state_objects)
    assert all(not hasattr(item, "persist") for item in state_objects)


def test_ctx_206_external_services_are_not_execution_context_fields() -> None:
    context_fields = {item.name for item in fields(ExecutionContext)}
    forbidden = {
        "audit",
        "audit_sink",
        "snapshot_store",
        "checkpoint_port",
        "approval_service",
        "tool_executor",
        "database",
        "mcp_client",
        "trace_sink",
        "trust_verifier",
    }
    execute_parameters = signature(RuntimeEngine.execute).parameters

    assert not context_fields & forbidden
    assert {"tools", "audit_sink", "snapshot_store"}.issubset(execute_parameters)
    runtime_models_source = (ROOT / "nsl" / "runtime_models.py").read_text(
        encoding="utf-8"
    )
    assert "from .audit import" not in runtime_models_source


def test_ctx_210_v02_request_exposes_distinct_runtime_and_business_identity() -> None:
    request = _request(
        "exec-ctx-210",
        business_operation_id=BusinessOperationId("business-operation-210"),
    )

    assert request.identity == ExecutionIdentity(
        "exec-ctx-210",
        BusinessOperationId("business-operation-210"),
    )
    assert request.identity.execution_id != request.identity.business_operation_id
    assert request.security_context.principal is request.principal
    assert request.security_context.data_policy is request.data_policy
    assert request.security_context.tenant_id == request.principal.tenant_id


def test_arc_211_v01_principal_and_data_policy_contract_is_additively_preserved() -> None:
    principal = build_principal(tenant_id="tenant-arc-211")
    policy = DataHandlingPolicy(
        snapshot_retention_days=1,
        audit_retention_days=1,
    )
    request = ExecutionRequest("exec-arc-211", {}, {}, principal, policy)

    assert request.principal is principal
    assert request.data_policy is policy
    assert request.business_operation_id is None
    assert request.identity == ExecutionIdentity("exec-arc-211")
    assert request.security_context == ExecutionSecurityContext(principal, policy)


@pytest.mark.parametrize("value", ["", " ", 0, 1, False])
def test_execution_request_rejects_invalid_business_operation_identity(value) -> None:
    with pytest.raises(
        ValueError,
        match="ExecutionRequest.business_operation_id must be a non-empty string",
    ):
        _request(business_operation_id=value)


def test_execution_request_rejects_credentials_in_business_operation_id() -> None:
    with pytest.raises(
        CredentialMaterialError,
        match="ExecutionRequest.business_operation_id",
    ):
        _request(
            business_operation_id=BusinessOperationId(
                "password=not-an-operation-id"
            )
        )


@pytest.mark.parametrize(
    ("principal", "policy", "message"),
    [
        (object(), DataHandlingPolicy(), "principal must be"),
        (build_principal(), object(), "data_policy must be"),
    ],
)
def test_execution_security_context_rejects_malformed_values(
    principal, policy, message
) -> None:
    with pytest.raises(TypeError, match=message):
        ExecutionSecurityContext(principal, policy)
