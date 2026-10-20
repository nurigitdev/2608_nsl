from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from nsl.core import (
    DataClassification,
    RiskLevel,
    STRING,
    ToolCapability,
    domain,
)
from nsl.ir import (
    ApprovalPolicy,
    ApprovalStatement,
    LiteralExpr,
    RequiredTool,
    WriteExpression,
)


WRITE_RESULT = domain("VoucherDraft")


def write_expression(**overrides: object) -> WriteExpression:
    values: dict[str, object] = {
        "node_id": "write0001",
        "tool_ref": "tool0001",
        "arguments": (
            ("description", LiteralExpr("expr0001", "memo", STRING)),
        ),
        "approval_ref": "VOUCHER_CREATE",
        "type_info": WRITE_RESULT,
    }
    values.update(overrides)
    return WriteExpression(**values)  # type: ignore[arg-type]


def required_write_tool(**overrides: object) -> RequiredTool:
    values: dict[str, object] = {
        "tool_ref": "tool0001",
        "tool_id": "FINANCE.CREATE_VOUCHER_DRAFT",
        "version": "1.0.0",
        "capability": ToolCapability.WRITE,
        "contract_hash": "sha256:" + "a" * 64,
        "required_scope": "finance:voucher:write",
        "output_classification": DataClassification.CONFIDENTIAL,
        "risk_level": RiskLevel.R3,
    }
    values.update(overrides)
    return RequiredTool(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("ttl_seconds", (None, 1, 2_147_483_647))
def test_ir_202_approval_statement_is_immutable_static_semantics(
    ttl_seconds: int | None,
) -> None:
    statement = ApprovalStatement(
        node_id="approval0001",
        approval_id="VOUCHER_CREATE",
        message="Create the voucher draft?",
        policy=ApprovalPolicy(ttl_seconds=ttl_seconds),
    )

    assert statement.policy.required is True
    assert statement.policy.ttl_seconds == ttl_seconds
    with pytest.raises(FrozenInstanceError):
        statement.approval_id = "CHANGED"  # type: ignore[misc]


@pytest.mark.parametrize("required", (False, 0, 1, None))
def test_ir_202_approval_policy_rejects_optional_or_implicit_bool(
    required,
) -> None:
    with pytest.raises(ValueError, match="must be required"):
        ApprovalPolicy(required=required)  # type: ignore[arg-type]


@pytest.mark.parametrize("ttl_seconds", (0, -1, True, "1"))
def test_ir_202_approval_policy_rejects_invalid_ttl(ttl_seconds) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        ApprovalPolicy(ttl_seconds=ttl_seconds)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"node_id": ""}, "node_id"),
        ({"approval_id": " "}, "approval_id"),
        ({"message": None}, "message"),
        ({"policy": object()}, "policy"),
    ),
)
def test_ir_202_approval_statement_rejects_malformed_static_fields(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "node_id": "approval0001",
        "approval_id": "VOUCHER_CREATE",
        "message": "Approve?",
        "policy": ApprovalPolicy(),
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        ApprovalStatement(**values)  # type: ignore[arg-type]


def test_ir_203_write_expression_has_immutable_argument_graph() -> None:
    expression = write_expression()

    assert expression.arguments[0][0] == "description"
    assert isinstance(expression.arguments[0][1], LiteralExpr)
    assert expression.type_info == WRITE_RESULT
    with pytest.raises(FrozenInstanceError):
        expression.tool_ref = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"node_id": ""}, "node_id"),
        ({"tool_ref": " "}, "tool_ref"),
        ({"arguments": []}, "immutable tuple"),
        ({"arguments": (("description",),)}, "name/expression pairs"),
        ({"arguments": (("", LiteralExpr("e", "v", STRING)),)}, "names"),
        ({"arguments": (("description", object()),)}, "IR expressions"),
        (
            {
                "arguments": (
                    ("description", LiteralExpr("e1", "a", STRING)),
                    ("description", LiteralExpr("e2", "b", STRING)),
                )
            },
            "unique",
        ),
        ({"type_info": "VoucherDraft"}, "TypeRef"),
    ),
)
def test_ir_203_write_expression_rejects_malformed_graph(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        write_expression(**overrides)


@pytest.mark.parametrize("approval_ref", (None, "", " "))
def test_ir_204_write_expression_requires_exact_static_approval_ref(
    approval_ref,
) -> None:
    with pytest.raises(ValueError, match="approval_ref"):
        write_expression(approval_ref=approval_ref)


def test_ir_205_required_write_tool_carries_static_contract_fingerprint() -> None:
    required = required_write_tool()

    assert required.capability is ToolCapability.WRITE
    assert required.risk_level is RiskLevel.R3
    assert required.contract_hash == "sha256:" + "a" * 64


@pytest.mark.parametrize("risk_level", (None, "", "R6", 3))
def test_ir_205_required_write_tool_rejects_missing_or_invalid_risk(
    risk_level,
) -> None:
    with pytest.raises(ValueError, match="risk_level"):
        required_write_tool(risk_level=risk_level)


def test_ir_205_legacy_read_required_tool_remains_additively_compatible() -> None:
    required = RequiredTool(
        tool_ref="tool0001",
        tool_id="PROJECT.LIST_PROJECTS",
        version="1.0.0",
        capability="READ",
        contract_hash="sha256:" + "b" * 64,
        required_scope="project:read",
        output_classification=DataClassification.INTERNAL,
    )

    assert required.capability is ToolCapability.READ
    assert required.risk_level is None


def test_ir_205_unknown_legacy_capability_remains_available_to_preflight() -> None:
    required = RequiredTool(
        tool_ref="tool0001",
        tool_id="PROJECT.RESERVED",
        version="1.0.0",
        capability="EXECUTE",
        contract_hash="sha256:" + "b" * 64,
        required_scope="project:write",
        output_classification=DataClassification.INTERNAL,
    )

    assert required.capability == "EXECUTE"


def test_ir_object_model_excludes_runtime_identity_state_and_tool_objects() -> None:
    model_fields = {
        field.name
        for model in (ApprovalStatement, WriteExpression, RequiredTool)
        for field in fields(model)
    }
    forbidden = {
        "tool_contract",
        "business_operation_id",
        "idempotency_key",
        "payload_hash",
        "approval_request_id",
        "policy_decision",
        "commit_state",
        "verification_status",
    }

    assert model_fields.isdisjoint(forbidden)
