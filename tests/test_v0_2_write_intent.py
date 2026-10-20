from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from nsl.approval import ApprovalEvidence, ApprovalRequest
from nsl.core import (
    ApprovalDecision,
    ApprovalRequestId,
    BusinessOperationId,
    CheckStatus,
    DataClassification,
    DECIMAL,
    Money,
    PayloadHash,
    RiskLevel,
    STRING,
    ToolCapability,
    ValueEnvelope,
    domain,
)
from nsl.compiler import NslCompiler
from nsl.ir import LiteralExpr, RequiredTool, WriteExpression
from nsl.runtime_models import ExecutionContext, ExecutionRequest
from nsl.security import DataHandlingPolicy
from nsl.vertical_slice import build_principal, build_tool_catalog
from nsl.write_intent import (
    ApprovalRefreshRequiredError,
    CanonicalWriteValueError,
    WriteIntent,
    WriteIntentBuilder,
    WriteIntentError,
    canonical_write_arguments,
    compute_payload_hash,
    compute_write_intent_hash,
)


def _value(value: object) -> ValueEnvelope:
    return ValueEnvelope.complete(
        value,
        DECIMAL,
        DataClassification.CONFIDENTIAL,
        ("source:test",),
    )


def _write_fixture(
    *,
    business_operation_id: BusinessOperationId | None = BusinessOperationId(
        "business-0050"
    ),
    capability: ToolCapability = ToolCapability.WRITE,
    duplicate_tool: bool = False,
) -> tuple[WriteExpression, dict[str, ValueEnvelope], ExecutionContext]:
    base = NslCompiler(build_tool_catalog()).compile(
        '''language NSL "0.1";
skill TEST.WRITE_INTENT_BASE {
    version "1.0.0";
    risk READ_ONLY;
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 8;
    }
    output { result: String; }
}
'''
    ).skill
    required = RequiredTool(
        tool_ref="write_tool",
        tool_id="FINANCE.CREATE_DRAFT",
        version="1.0.0",
        capability=capability,
        contract_hash="sha256:" + "c" * 64,
        required_scope="finance:draft:write",
        output_classification=DataClassification.CONFIDENTIAL,
        risk_level=RiskLevel.R3,
    )
    required_tools = (required, required) if duplicate_tool else (required,)
    skill = replace(
        base,
        ir_version="1.1",
        language_version="0.2",
        semantic_hash="",
        features=frozenset({"approval", "write"}),
        required_tools=required_tools,
    ).with_computed_hash()
    expression = WriteExpression(
        node_id="write0001",
        tool_ref="write_tool",
        arguments=(
            (
                "detail",
                LiteralExpr("literal0001", "draft", STRING),
            ),
        ),
        approval_ref="APPROVE_DRAFT",
        type_info=domain("DraftRecord"),
    )
    arguments = {
        "detail": ValueEnvelope.complete(
            {
                "description": "KTX",
                "amount": Decimal("84000"),
                "lines": [{"sequence": 1}],
            },
            domain("DraftPayload"),
            DataClassification.CONFIDENTIAL,
        )
    }
    request = ExecutionRequest(
        execution_id="exec-0050",
        inputs={},
        runtime_context={},
        principal=build_principal(),
        data_policy=DataHandlingPolicy(),
        business_operation_id=business_operation_id,
    )
    return expression, arguments, ExecutionContext(skill, request)


def test_rd_001_canonical_write_arguments_preserve_typed_values() -> None:
    arguments = {
        "z": _value(
            {
                "money": Money(Decimal("84000.00"), "KRW"),
                "decimal": Decimal("1.2300"),
                "date": date(2026, 10, 20),
                "datetime": datetime(
                    2026,
                    10,
                    20,
                    9,
                    30,
                    tzinfo=timezone(timedelta(hours=9)),
                ),
                "status": CheckStatus.PASS,
                "items": (None, True, 7, "text"),
            }
        ),
        "a": _value([]),
    }

    assert canonical_write_arguments(arguments) == (
        b'{"a":[],"z":{"date":{"$type":"Date","value":"2026-10-20"},'
        b'"datetime":{"$type":"DateTime","value":"2026-10-20T09:30:00+09:00"},'
        b'"decimal":{"$type":"Decimal","value":"1.2300"},'
        b'"items":[null,true,7,"text"],'
        b'"money":{"$type":"Money","amount":"84000.00","currency":"KRW"},'
        b'"status":"PASS"}}'
    )


def test_rd_001_payload_hash_is_domain_separated_and_order_independent() -> None:
    first = {"b": _value(Decimal("2")), "a": _value(Decimal("1"))}
    second = {"a": _value(Decimal("1")), "b": _value(Decimal("2"))}
    canonical = canonical_write_arguments(first)
    expected = "sha256:" + sha256(
        b"NSL-WRITE-PAYLOAD-V1\0" + canonical
    ).hexdigest()

    assert compute_payload_hash(first) == PayloadHash(expected)
    assert compute_payload_hash(second) == PayloadHash(expected)
    assert canonical_write_arguments({}) == b"{}"
    assert canonical_write_arguments({"only": _value(1)}) == b'{"only":1}'


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_rd_001_non_finite_decimal_is_rejected(value: Decimal) -> None:
    with pytest.raises(CanonicalWriteValueError, match="Decimal must be finite"):
        canonical_write_arguments({"amount": _value(value)})


def test_rd_001_naive_datetime_is_rejected_but_utc_is_supported() -> None:
    with pytest.raises(CanonicalWriteValueError, match="timezone-aware"):
        canonical_write_arguments(
            {"occurred_at": _value(datetime(2026, 10, 20, 1, 2, 3))}
        )

    assert canonical_write_arguments(
        {"occurred_at": _value(datetime(2026, 10, 20, 1, 2, 3, tzinfo=UTC))}
    ).endswith(b'"2026-10-20T01:02:03+00:00"}}')


def test_rd_001_non_string_keys_fail_at_argument_and_nested_boundaries() -> None:
    with pytest.raises(CanonicalWriteValueError, match="argument object keys"):
        canonical_write_arguments({1: _value("invalid")})  # type: ignore[dict-item]

    with pytest.raises(CanonicalWriteValueError, match="object keys"):
        canonical_write_arguments(
            {"payload": _value({1: "invalid"})}
        )


@pytest.mark.parametrize("value", [1.5, b"bytes", {"set"}, object()])
def test_rd_001_unsupported_values_fail_closed(value: object) -> None:
    with pytest.raises(CanonicalWriteValueError, match="unsupported WRITE value"):
        canonical_write_arguments({"payload": _value(value)})


def test_rd_001_requires_mapping_of_value_envelopes() -> None:
    with pytest.raises(CanonicalWriteValueError, match="must be a mapping"):
        canonical_write_arguments([])  # type: ignore[arg-type]

    with pytest.raises(CanonicalWriteValueError, match="ValueEnvelope"):
        canonical_write_arguments({"amount": Decimal("1")})  # type: ignore[dict-item]


def test_rd_001_cyclic_values_are_rejected_without_runtime_recursion() -> None:
    mapping: dict[str, object] = {}
    mapping["self"] = mapping
    sequence: list[object] = []
    sequence.append(sequence)

    for value in (mapping, sequence):
        with pytest.raises(CanonicalWriteValueError, match="cyclic WRITE value"):
            canonical_write_arguments({"payload": _value(value)})


def test_rd_001_excessive_nesting_is_a_controlled_failure() -> None:
    nested: object = "leaf"
    for _ in range(2_000):
        nested = [nested]

    with pytest.raises(CanonicalWriteValueError, match="nesting depth"):
        canonical_write_arguments({"payload": _value(nested)})


def test_wrt_006_builder_creates_bound_deterministic_write_intent() -> None:
    expression, arguments, context = _write_fixture()
    intent = WriteIntentBuilder().build(expression, arguments, context)
    expected_intent_hash = compute_write_intent_hash(
        skill_semantic_hash=context.skill.semantic_hash,
        write_node_id=expression.node_id,
        tool_id="FINANCE.CREATE_DRAFT",
        payload_hash=intent.payload_hash,
    )

    assert intent.tenant_id == context.tenant_id
    assert intent.business_operation_id == "business-0050"
    assert intent.node_id == "write0001"
    assert intent.tool_ref == "write_tool"
    assert intent.tool_id == "FINANCE.CREATE_DRAFT"
    assert intent.approval_id == "APPROVE_DRAFT"
    assert intent.skill_semantic_hash == context.skill.semantic_hash
    assert intent.payload_hash == compute_payload_hash(arguments)
    assert intent.write_intent_hash == expected_intent_hash


def test_wrt_006_intent_hash_uses_only_frozen_semantic_fields() -> None:
    expression, arguments, first_context = _write_fixture()
    _, _, second_context = _write_fixture(
        business_operation_id=BusinessOperationId("another-operation")
    )

    first = WriteIntentBuilder().build(expression, arguments, first_context)
    second = WriteIntentBuilder().build(expression, arguments, second_context)

    assert first.business_operation_id != second.business_operation_id
    assert first.payload_hash == second.payload_hash
    assert first.write_intent_hash == second.write_intent_hash


def test_wrt_006_write_intent_is_deeply_immutable() -> None:
    expression, arguments, context = _write_fixture()
    source = arguments["detail"].value
    intent = WriteIntentBuilder().build(expression, arguments, context)

    assert isinstance(source, dict)
    source["description"] = "changed"
    assert intent.canonical_arguments["detail"].value["description"] == "KTX"
    assert intent.canonical_arguments["detail"].value["lines"] == (
        {"sequence": 1},
    )
    with pytest.raises(TypeError):
        intent.canonical_arguments["detail"].value["description"] = "blocked"
    with pytest.raises(TypeError):
        intent.canonical_arguments["other"] = _value("blocked")
    with pytest.raises(FrozenInstanceError):
        intent.node_id = "changed"  # type: ignore[misc]


def test_wrt_006_write_intent_rejects_forged_hashes() -> None:
    expression, arguments, context = _write_fixture()
    intent = WriteIntentBuilder().build(expression, arguments, context)

    with pytest.raises(WriteIntentError, match="payload_hash does not match"):
        replace(intent, payload_hash=PayloadHash("sha256:" + "0" * 64))
    with pytest.raises(WriteIntentError, match="write_intent_hash does not match"):
        replace(intent, write_intent_hash="sha256:" + "0" * 64)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"skill_semantic_hash": "bad"}, "skill_semantic_hash"),
        ({"node_id": ""}, "node_id"),
        ({"tool_id": ""}, "tool_id"),
        ({"payload_hash": PayloadHash("bad")}, "payload_hash"),
        ({"write_intent_hash": "bad"}, "write_intent_hash"),
    ],
)
def test_wrt_006_write_intent_rejects_malformed_identity(
    change: dict[str, object],
    message: str,
) -> None:
    expression, arguments, context = _write_fixture()
    intent = WriteIntentBuilder().build(expression, arguments, context)

    with pytest.raises(WriteIntentError, match=message):
        replace(intent, **change)


def test_wrt_006_builder_requires_business_operation_and_exact_write_tool() -> None:
    expression, arguments, context = _write_fixture(business_operation_id=None)
    with pytest.raises(WriteIntentError, match="BusinessOperationId"):
        WriteIntentBuilder().build(expression, arguments, context)

    expression, arguments, context = _write_fixture(duplicate_tool=True)
    with pytest.raises(WriteIntentError, match="exactly one"):
        WriteIntentBuilder().build(expression, arguments, context)

    expression, arguments, context = _write_fixture(capability=ToolCapability.READ)
    with pytest.raises(WriteIntentError, match="WRITE-capable"):
        WriteIntentBuilder().build(expression, arguments, context)


def test_wrt_006_builder_rejects_unknown_tool_and_argument_drift() -> None:
    expression, arguments, context = _write_fixture()
    unknown = replace(expression, tool_ref="unknown")
    with pytest.raises(WriteIntentError, match="exactly one"):
        WriteIntentBuilder().build(unknown, arguments, context)

    for malformed in ({}, {**arguments, "extra": _value(1)}):
        with pytest.raises(WriteIntentError, match="do not match"):
            WriteIntentBuilder().build(expression, malformed, context)


def test_wrt_006_builder_rejects_wrong_contract_types() -> None:
    expression, arguments, context = _write_fixture()

    with pytest.raises(TypeError, match="WriteExpression"):
        WriteIntentBuilder().build(object(), arguments, context)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ExecutionContext"):
        WriteIntentBuilder().build(expression, arguments, object())  # type: ignore[arg-type]


def test_wrt_006_public_write_intent_api_is_framework_neutral() -> None:
    from nsl import api

    assert api.WriteIntent is WriteIntent
    assert api.WriteIntentBuilder is WriteIntentBuilder
    assert api.compute_payload_hash is compute_payload_hash
    assert api.compute_write_intent_hash is compute_write_intent_hash


def _approval_evidence(intent: WriteIntent) -> ApprovalEvidence:
    return ApprovalEvidence(
        tenant_id=intent.tenant_id,
        execution_id="exec-0050",
        business_operation_id=intent.business_operation_id,
        approval_id=intent.approval_id,
        approval_request_id=ApprovalRequestId("approval-request-0050-a"),
        skill_semantic_hash=intent.skill_semantic_hash,
        decision=ApprovalDecision.APPROVED,
        approver_id="approver-0050",
        decided_at=datetime(2026, 10, 20, 1, 2, 3, tzinfo=UTC),
        expires_at=datetime(2026, 10, 20, 2, 2, 3, tzinfo=UTC),
        payload_hash=intent.payload_hash,
        write_intent_hash=intent.write_intent_hash,
        evidence_integrity="signature-ref-0050",
    )


def test_wrt_006_intent_accepts_only_identical_approval_hash_identity() -> None:
    expression, arguments, context = _write_fixture()
    intent = WriteIntentBuilder().build(expression, arguments, context)

    assert intent.require_approval_identity(_approval_evidence(intent)) is None


def test_wrt_010_payload_change_requires_new_intent_and_approval_request() -> None:
    expression, arguments, context = _write_fixture()
    original = WriteIntentBuilder().build(expression, arguments, context)
    original_evidence = _approval_evidence(original)
    changed_arguments = {
        "detail": ValueEnvelope.complete(
            {
                "description": "KTX",
                "amount": Decimal("84001"),
                "lines": [{"sequence": 1}],
            },
            domain("DraftPayload"),
            DataClassification.CONFIDENTIAL,
        )
    }
    changed = WriteIntentBuilder().build(
        expression,
        changed_arguments,
        context,
    )

    assert changed.payload_hash != original.payload_hash
    assert changed.write_intent_hash != original.write_intent_hash
    with pytest.raises(ApprovalRefreshRequiredError) as captured:
        changed.require_approval_identity(original_evidence)
    assert captured.value.detail_code == "NEW_APPROVAL_REQUIRED"
    assert "84000" not in str(captured.value)
    assert "84001" not in str(captured.value)

    replacement_request = ApprovalRequest(
        tenant_id=changed.tenant_id,
        execution_id="exec-0050",
        business_operation_id=changed.business_operation_id,
        approval_id=changed.approval_id,
        approval_request_id=ApprovalRequestId("approval-request-0050-b"),
        skill_id=context.skill.skill_id,
        skill_version=context.skill.skill_version,
        skill_semantic_hash=changed.skill_semantic_hash,
        write_node_id=changed.node_id,
        tool_id=changed.tool_id,
        payload_hash=changed.payload_hash,
        write_intent_hash=changed.write_intent_hash,
        risk_level=RiskLevel.R3,
        message="Approve the changed draft",
        expires_at=datetime(2026, 10, 20, 2, 2, 3, tzinfo=UTC),
        display_safe_summary={"changed": True},
        protected_payload_ref="snapshot://tenant-a/write-0050-b",
        data_classification=DataClassification.CONFIDENTIAL,
    )
    assert replacement_request.approval_request_id != (
        original_evidence.approval_request_id
    )
    assert replacement_request.payload_hash == changed.payload_hash
    assert replacement_request.write_intent_hash == changed.write_intent_hash


def test_wrt_010_semantically_identical_rebuild_reuses_hash_identity() -> None:
    expression, arguments, context = _write_fixture()
    original = WriteIntentBuilder().build(expression, arguments, context)
    reordered_arguments = {
        "detail": ValueEnvelope.complete(
            {
                "lines": [{"sequence": 1}],
                "amount": Decimal("84000"),
                "description": "KTX",
            },
            domain("DraftPayload"),
            DataClassification.CONFIDENTIAL,
        )
    }
    rebuilt = WriteIntentBuilder().build(
        expression,
        reordered_arguments,
        context,
    )

    assert rebuilt.payload_hash == original.payload_hash
    assert rebuilt.write_intent_hash == original.write_intent_hash
    assert rebuilt.require_approval_identity(_approval_evidence(original)) is None


def test_wrt_010_approval_identity_contract_fails_closed() -> None:
    expression, arguments, context = _write_fixture()
    intent = WriteIntentBuilder().build(expression, arguments, context)

    with pytest.raises(TypeError, match="must provide"):
        intent.require_approval_identity(object())  # type: ignore[arg-type]

    class MalformedEvidence:
        payload_hash = "bad"
        write_intent_hash = "bad"

    with pytest.raises(WriteIntentError, match="payload_hash"):
        intent.require_approval_identity(MalformedEvidence())  # type: ignore[arg-type]


def test_wrt_010_intent_hash_only_change_requires_new_approval() -> None:
    expression, arguments, context = _write_fixture()
    intent = WriteIntentBuilder().build(expression, arguments, context)
    evidence = _approval_evidence(intent)

    class DifferentIntentEvidence:
        payload_hash = evidence.payload_hash
        write_intent_hash = "sha256:" + "f" * 64

    with pytest.raises(ApprovalRefreshRequiredError):
        intent.require_approval_identity(DifferentIntentEvidence())
