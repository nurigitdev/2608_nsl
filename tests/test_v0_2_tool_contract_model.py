from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from nsl import CompileError, DiagnosticCode, NslCompiler
from nsl.core import (
    DataClassification,
    RiskLevel,
    STRING,
    ToolCapability,
    domain,
)
from nsl.tools import (
    Atomicity,
    CommitConfirmation,
    CommitSemantics,
    IdempotencySemantics,
    OperationKind,
    PostWriteVerification,
    ToolContract,
    ToolContractCatalog,
    WriteSemantics,
)
from nsl.vertical_slice import build_tool_catalog


WRITE_RESULT = domain("VoucherDraft")


def _idempotency(**overrides: object) -> IdempotencySemantics:
    values: dict[str, object] = {
        "required": True,
        "scope": "TOOL_AND_TENANT",
        "retention": "PROVIDER_DEFINED",
        "duplicate_behavior": "RETURN_ORIGINAL_RESULT",
        "payload_conflict_behavior": "IDEMPOTENCY_CONFLICT",
    }
    values.update(overrides)
    return IdempotencySemantics(**values)  # type: ignore[arg-type]


def _write_semantics(**overrides: object) -> WriteSemantics:
    values: dict[str, object] = {
        "side_effect": True,
        "atomicity": Atomicity.ATOMIC,
        "idempotency": _idempotency(),
        "commit": CommitSemantics(CommitConfirmation.EXPLICIT),
        "approval_required": True,
        "preconditions_supported": True,
        "verification": None,
    }
    values.update(overrides)
    return WriteSemantics(**values)  # type: ignore[arg-type]


def _write_contract(**overrides: object) -> ToolContract:
    values: dict[str, object] = {
        "tool_id": "FINANCE.CREATE_VOUCHER_DRAFT",
        "version": "1.0.0",
        "capability": ToolCapability.WRITE,
        "input_types": (("description", STRING),),
        "output_type": WRITE_RESULT,
        "required_scope": "finance:voucher:write",
        "output_classification": DataClassification.CONFIDENTIAL,
        "operation_kind": OperationKind.CREATE_DRAFT,
        "risk_level": RiskLevel.R3,
        "write_semantics": _write_semantics(),
    }
    values.update(overrides)
    return ToolContract(**values)  # type: ignore[arg-type]


def test_tol_201_write_tool_requires_complete_write_capability_contract() -> None:
    contract = _write_contract()

    assert contract.capability is ToolCapability.WRITE
    with pytest.raises(ValueError, match="operation_kind"):
        replace(
            build_tool_catalog().get(
                "PROJECT.LIST_PARENT_PROJECTS", "1.0.0"
            ),
            capability=ToolCapability.WRITE,
        )
    with pytest.raises(ValueError, match="write_semantics"):
        _write_contract(write_semantics=None)
    with pytest.raises(ValueError, match="capability=WRITE"):
        replace(contract, capability=ToolCapability.READ)


def test_tol_201_v0_1_compiler_rejects_complete_write_contract() -> None:
    read_catalog = build_tool_catalog()
    parent = read_catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    child = read_catalog.get("PROJECT.LIST_CHILD_PROJECTS", "1.0.0")
    source = Path("examples/project_budget_check.ns").read_text(
        encoding="utf-8"
    )
    catalog = ToolContractCatalog(
        (
            replace(
                _write_contract(),
                tool_id=parent.tool_id,
                input_types=parent.input_types,
                output_type=parent.output_type,
            ),
            child,
        )
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(catalog).compile(source)

    assert captured.value.code == DiagnosticCode.SEM_WRITE_TOOL_FORBIDDEN


@pytest.mark.parametrize("invalid", (None, "", "DELETE"))
def test_tol_202_write_operation_kind_is_required_and_bounded(invalid) -> None:
    with pytest.raises(ValueError, match="operation_kind"):
        _write_contract(operation_kind=invalid)

    assert _write_contract(operation_kind="CREATE").operation_kind is OperationKind.CREATE
    update = _write_contract(operation_kind="UPDATE")
    assert update.operation_kind is OperationKind.UPDATE
    assert update.to_canonical_data()["operation_kind"] == "UPDATE"


@pytest.mark.parametrize("invalid", (False, 0, 1, None))
def test_tol_203_write_idempotency_must_be_explicitly_required(invalid) -> None:
    with pytest.raises(ValueError, match="idempotency.required"):
        _idempotency(required=invalid)


@pytest.mark.parametrize(
    "field",
    (
        "scope",
        "retention",
        "duplicate_behavior",
        "payload_conflict_behavior",
    ),
)
@pytest.mark.parametrize("invalid", (None, "", " "))
def test_tol_203_write_idempotency_metadata_rejects_missing_values(
    field: str, invalid
) -> None:
    with pytest.raises(ValueError, match=f"idempotency.{field}"):
        _idempotency(**{field: invalid})


@pytest.mark.parametrize("invalid", (None, "BEST_EFFORT", "PARTIAL_COMMIT"))
def test_tol_204_write_atomicity_must_be_atomic(invalid) -> None:
    with pytest.raises(ValueError, match="atomicity"):
        _write_semantics(atomicity=invalid)

    assert _write_semantics(atomicity="ATOMIC").atomicity is Atomicity.ATOMIC


def test_tol_205_write_commit_semantics_are_required_and_explicit() -> None:
    with pytest.raises(ValueError, match="commit semantics"):
        _write_semantics(commit=None)
    with pytest.raises(ValueError, match="commit.confirmation"):
        CommitSemantics("IMPLICIT")  # type: ignore[arg-type]

    semantics = CommitSemantics("EXPLICIT")  # type: ignore[arg-type]
    assert semantics.confirmation is CommitConfirmation.EXPLICIT
    assert semantics.to_data() == {"confirmation": "EXPLICIT"}


def test_tol_206_post_write_verification_is_optional_but_structured() -> None:
    without_verification = _write_contract()
    with_verification = _write_contract(
        write_semantics=_write_semantics(
            verification=PostWriteVerification(
                tool_id="FINANCE.GET_VOUCHER",
                reference_schema=WRITE_RESULT,
                consistency_hint="READ_AFTER_WRITE",
            )
        )
    )

    assert without_verification.write_semantics.verification is None
    assert with_verification.write_semantics.verification.to_data() == {
        "tool_id": "FINANCE.GET_VOUCHER",
        "reference_schema": WRITE_RESULT.to_data(),
        "consistency_hint": "READ_AFTER_WRITE",
    }


@pytest.mark.parametrize(
    ("verification", "expected"),
    (
        (
            PostWriteVerification(reference_schema=WRITE_RESULT),
            {"reference_schema": WRITE_RESULT.to_data()},
        ),
        (
            PostWriteVerification(consistency_hint="EVENTUAL"),
            {"consistency_hint": "EVENTUAL"},
        ),
    ),
)
def test_tol_206_each_verification_hint_is_independently_usable(
    verification: PostWriteVerification, expected: dict[str, object]
) -> None:
    assert verification.to_data() == expected


@pytest.mark.parametrize(
    "overrides",
    (
        {},
        {"tool_id": ""},
        {"tool_id": None, "reference_schema": "VoucherDraft"},
        {"tool_id": None, "consistency_hint": " "},
    ),
)
def test_tol_206_post_write_verification_rejects_malformed_hints(
    overrides: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="verification"):
        PostWriteVerification(**overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize("risk", (RiskLevel.R0, RiskLevel.R5, "R3"))
def test_rsk_001_write_contract_declares_bounded_risk_level(risk) -> None:
    contract = _write_contract(risk_level=risk)
    assert isinstance(contract.risk_level, RiskLevel)


@pytest.mark.parametrize("invalid", (None, "", "R-1", "R6"))
def test_rsk_001_write_contract_rejects_missing_or_invalid_risk(invalid) -> None:
    with pytest.raises(ValueError, match="risk_level"):
        _write_contract(risk_level=invalid)


@pytest.mark.parametrize("invalid", (None, "", " "))
def test_rsk_001_legacy_read_contract_retains_declared_static_risk(
    invalid,
) -> None:
    contract = build_tool_catalog().get(
        "PROJECT.LIST_PARENT_PROJECTS", "1.0.0"
    )

    assert contract.risk == "READ_ONLY"
    with pytest.raises(ValueError, match="tool risk"):
        replace(contract, risk=invalid)


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"side_effect": False}, "side_effect"),
        ({"side_effect": 1}, "side_effect"),
        ({"idempotency": None}, "idempotency semantics"),
        ({"approval_required": False}, "approval.required"),
        ({"approval_required": 1}, "approval.required"),
        ({"preconditions_supported": False}, "preconditions.supported"),
        ({"preconditions_supported": 1}, "preconditions.supported"),
        ({"verification": object()}, "verification"),
    ),
)
def test_write_semantics_reject_malformed_or_weakened_safety_fields(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _write_semantics(**overrides)


def test_write_contract_model_is_immutable() -> None:
    contract = _write_contract()
    with pytest.raises(FrozenInstanceError):
        contract.operation_kind = OperationKind.CREATE  # type: ignore[misc]


def test_legacy_read_contract_shape_and_hash_are_unchanged() -> None:
    catalog = build_tool_catalog()
    parent = catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    child = catalog.get("PROJECT.LIST_CHILD_PROJECTS", "1.0.0")

    assert parent.capability is ToolCapability.READ
    assert parent.operation_kind is None
    assert parent.risk_level is None
    assert parent.write_semantics is None
    assert parent.contract_hash == (
        "sha256:f673bce4e17a04cdec1b829b4c9c56d3ce01e22a7434b97605af6362500f488c"
    )
    assert child.contract_hash == (
        "sha256:a1e536cb1dd89c8177eedc53e9c3a5edf5eeef592617af9a110771fdabee9966"
    )


def test_tol_207_write_semantics_participate_in_contract_identity() -> None:
    contract = _write_contract()
    changed_operation = replace(contract, operation_kind=OperationKind.CREATE)
    changed_risk = replace(contract, risk_level=RiskLevel.R4)
    changed_verification = replace(
        contract,
        write_semantics=replace(
            contract.write_semantics,
            verification=PostWriteVerification(tool_id="FINANCE.GET_VOUCHER"),
        ),
    )

    assert len(
        {
            contract.contract_hash,
            changed_operation.contract_hash,
            changed_risk.contract_hash,
            changed_verification.contract_hash,
        }
    ) == 4
