from __future__ import annotations

from dataclasses import fields, replace
from hashlib import sha256

import pytest

from nsl.adapters.mcp import MCPToolBinding
from nsl.core import (
    DataClassification,
    RiskLevel,
    STRING,
    ToolCapability,
    domain,
)
from nsl.ir import canonical_json
from nsl.tools import (
    Atomicity,
    CommitConfirmation,
    CommitSemantics,
    IdempotencySemantics,
    OperationKind,
    PostWriteVerification,
    ToolContract,
    WriteSemantics,
)


WRITE_RESULT = domain("VoucherDraft")


def write_contract() -> ToolContract:
    return ToolContract(
        tool_id="FINANCE.CREATE_VOUCHER_DRAFT",
        version="1.0.0",
        capability=ToolCapability.WRITE,
        input_types=(("description", STRING),),
        output_type=WRITE_RESULT,
        required_scope="finance:voucher:write",
        output_classification=DataClassification.CONFIDENTIAL,
        operation_kind=OperationKind.CREATE_DRAFT,
        risk_level=RiskLevel.R3,
        write_semantics=WriteSemantics(
            side_effect=True,
            atomicity=Atomicity.ATOMIC,
            idempotency=IdempotencySemantics(
                required=True,
                scope="TOOL_AND_TENANT",
                retention="PROVIDER_DEFINED",
                duplicate_behavior="RETURN_ORIGINAL_RESULT",
                payload_conflict_behavior="IDEMPOTENCY_CONFLICT",
            ),
            commit=CommitSemantics(CommitConfirmation.EXPLICIT),
            approval_required=True,
            preconditions_supported=True,
            verification=PostWriteVerification(
                tool_id="FINANCE.GET_VOUCHER",
                consistency_hint="READ_AFTER_WRITE",
            ),
        ),
    )


def test_tol_207_hash_uses_complete_canonical_write_semantics() -> None:
    contract = write_contract()
    canonical = contract.to_canonical_data()

    assert canonical == {
        "tool_id": "FINANCE.CREATE_VOUCHER_DRAFT",
        "version": "1.0.0",
        "capability": "WRITE",
        "risk": "READ_ONLY",
        "inputs": [
            {
                "name": "description",
                "type": {"kind": "primitive", "name": "String"},
            }
        ],
        "output": {"kind": "domain", "name": "VoucherDraft"},
        "required_scope": "finance:voucher:write",
        "output_classification": "CONFIDENTIAL",
        "timeout_ms": 30_000,
        "empty_is_valid": True,
        "operation_kind": "CREATE_DRAFT",
        "risk_level": "R3",
        "write_semantics": {
            "side_effect": True,
            "atomicity": "ATOMIC",
            "idempotency": {
                "required": True,
                "scope": "TOOL_AND_TENANT",
                "retention": "PROVIDER_DEFINED",
                "duplicate_behavior": "RETURN_ORIGINAL_RESULT",
                "payload_conflict_behavior": "IDEMPOTENCY_CONFLICT",
            },
            "commit": {"confirmation": "EXPLICIT"},
            "approval": {"required": True},
            "preconditions": {"supported": True},
            "verification": {
                "tool_id": "FINANCE.GET_VOUCHER",
                "consistency_hint": "READ_AFTER_WRITE",
            },
        },
    }
    assert contract.contract_hash == (
        "sha256:" + sha256(canonical_json(canonical)).hexdigest()
    )


@pytest.mark.parametrize(
    "changed",
    (
        lambda contract: replace(contract, operation_kind=OperationKind.CREATE),
        lambda contract: replace(contract, risk_level=RiskLevel.R4),
        lambda contract: replace(contract, required_scope="finance:write"),
        lambda contract: replace(
            contract,
            output_classification=DataClassification.INTERNAL,
        ),
        lambda contract: replace(contract, timeout_ms=29_999),
        lambda contract: replace(contract, empty_is_valid=False),
        lambda contract: replace(
            contract,
            write_semantics=replace(
                contract.write_semantics,
                idempotency=replace(
                    contract.write_semantics.idempotency,
                    scope="TOOL_TENANT_AND_SUBJECT",
                ),
            ),
        ),
        lambda contract: replace(
            contract,
            write_semantics=replace(
                contract.write_semantics,
                verification=replace(
                    contract.write_semantics.verification,
                    consistency_hint="EVENTUAL",
                ),
            ),
        ),
    ),
)
def test_tol_207_each_variable_semantic_changes_contract_hash(changed) -> None:
    contract = write_contract()

    assert changed(contract).contract_hash != contract.contract_hash


def test_tol_207_canonical_data_is_a_detached_deterministic_value() -> None:
    contract = write_contract()
    first = contract.to_canonical_data()
    second = contract.to_canonical_data()

    assert first == second
    assert first is not second
    first["required_scope"] = "mutated"
    first["write_semantics"]["idempotency"]["scope"] = "mutated"
    assert contract.to_canonical_data() == second
    assert contract.contract_hash == contract.contract_hash


def test_rsk_004_environment_policy_and_binding_fields_are_not_contract_fields() -> None:
    contract_fields = {field.name for field in fields(ToolContract)}
    forbidden = {
        "endpoint",
        "credential",
        "tenant_id",
        "user_allowlist",
        "tenant_entitlement",
        "mfa_state",
        "network_allowlist",
        "policy_decision",
        "approval_routing",
    }

    assert contract_fields.isdisjoint(forbidden)
    assert set(write_contract().to_canonical_data()).isdisjoint(forbidden)


def test_rsk_004_customer_bindings_cannot_change_canonical_contract_identity() -> None:
    contract = write_contract()
    original_hash = contract.contract_hash
    bindings = (
        MCPToolBinding(
            binding_id="binding-a",
            tenant_id="tenant-a",
            tool_id=contract.tool_id,
            tool_version=contract.version,
            server_ref="finance-mcp-a",
            tool_name="create_voucher_a",
        ),
        MCPToolBinding(
            binding_id="binding-b",
            tenant_id="tenant-b",
            tool_id=contract.tool_id,
            tool_version=contract.version,
            server_ref="finance-mcp-b",
            tool_name="create_voucher_b",
        ),
    )

    assert bindings[0] != bindings[1]
    canonical = canonical_json(contract.to_canonical_data()).decode("utf-8")
    assert all(
        value not in canonical
        for binding in bindings
        for value in (
            binding.binding_id,
            binding.tenant_id,
            binding.server_ref,
            binding.tool_name,
        )
    )
    assert contract.contract_hash == original_hash
