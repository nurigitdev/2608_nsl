from __future__ import annotations

from decimal import Decimal

import pytest

from nsl.audit import (
    AuditRecorder,
    InMemoryAuditSink,
    InMemorySnapshotStore,
    SnapshotRef,
    value_hash,
)
from nsl.core import (
    CHECK_STATUS,
    INT,
    CheckStatus,
    Completeness,
    DataClassification,
    Money,
    Presence,
    TypeRef,
    ValueEnvelope,
    classification_allows,
    decode_value,
    encode_value,
    highest_classification,
    list_type,
    money_type,
    record_type,
)
from nsl.security import (
    AuthorizationError,
    DataHandlingPolicy,
    ExecutionPrincipal,
    StaticAuthorizer,
)


@pytest.mark.parametrize(
    ("value", "presence"),
    [
        (None, Presence.EMPTY),
        ([], Presence.EMPTY),
        ({}, Presence.EMPTY),
        (0, Presence.PRESENT),
        ([0], Presence.PRESENT),
    ],
)
def test_value_envelope_presence_boundaries(value, presence) -> None:
    envelope = ValueEnvelope.complete(value, INT)
    assert envelope.presence is presence
    assert envelope.completeness is Completeness.COMPLETE


def test_money_arithmetic_comparison_and_currency_guard() -> None:
    low = Money(Decimal("0"), "KRW")
    high = Money(Decimal("1"), "KRW")

    assert low + high == high
    assert high - low == high
    assert low < high
    assert low <= high
    assert high <= high

    usd = Money(Decimal("1"), "USD")
    for operation in (
        lambda: low + usd,
        lambda: low - usd,
        lambda: low < usd,
        lambda: low <= usd,
    ):
        with pytest.raises(ValueError, match="currency mismatch"):
            operation()


def test_money_requires_currency() -> None:
    with pytest.raises(ValueError, match="currency is required"):
        Money(Decimal("1"), "")


def test_type_ref_nested_round_trip_and_missing_field() -> None:
    project = record_type(
        "Project",
        amount=money_type("KRW"),
        statuses=list_type(CHECK_STATUS),
    )
    restored = TypeRef.from_data(project.to_data())

    assert restored == project
    assert restored.field("amount") == money_type("KRW")
    with pytest.raises(KeyError):
        restored.field("missing")


def test_value_codec_covers_decimal_enum_tuple_list_and_plain_dict() -> None:
    value = {
        "money": Money(Decimal("1.25"), "KRW"),
        "decimal": Decimal("2.50"),
        "status": CheckStatus.PASS,
        "tuple": (Money(Decimal("0"), "KRW"),),
        "list": [{"plain": "value"}],
    }
    encoded = encode_value(value)
    decoded = decode_value(encoded)

    assert decoded["money"] == value["money"]
    assert decoded["decimal"] == value["decimal"]
    assert decoded["status"] == "PASS"
    assert decoded["tuple"] == [Money(Decimal("0"), "KRW")]
    assert decode_value("plain") == "plain"


def test_classification_order_and_allowance() -> None:
    assert highest_classification(
        DataClassification.PUBLIC,
        DataClassification.RESTRICTED,
        DataClassification.INTERNAL,
    ) is DataClassification.RESTRICTED
    assert classification_allows(
        DataClassification.INTERNAL, DataClassification.CONFIDENTIAL
    )
    assert not classification_allows(
        DataClassification.RESTRICTED, DataClassification.CONFIDENTIAL
    )


def principal(**overrides) -> ExecutionPrincipal:
    values = {
        "tenant_id": "tenant-a",
        "subject_id": "user-a",
        "actor_type": "USER",
        "roles": frozenset({"FINANCE"}),
        "scopes": frozenset({"scope:a", "nsl:replay:read"}),
        "auth_context_ref": "auth-a",
    }
    values.update(overrides)
    return ExecutionPrincipal(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tenant_id": ""}, "tenant_id"),
        ({"subject_id": ""}, "subject_id"),
        ({"actor_type": "ROBOT"}, "actor_type"),
        ({"auth_context_ref": ""}, "auth_context_ref"),
    ],
)
def test_execution_principal_robustness(overrides, message) -> None:
    with pytest.raises(AuthorizationError, match=message):
        principal(**overrides).validate()


@pytest.mark.parametrize("days", [0, -1])
def test_data_policy_rejects_non_positive_retention(days) -> None:
    with pytest.raises(ValueError, match="positive"):
        DataHandlingPolicy(snapshot_retention_days=days)


def test_authorizer_default_deny_and_allow() -> None:
    authorizer = StaticAuthorizer("policy", "7")
    allowed = authorizer.authorize(principal(), "read", frozenset({"scope:a"}))
    assert allowed.effect == "ALLOW"
    assert allowed.policy_version == "7"

    with pytest.raises(AuthorizationError, match="scope:missing"):
        authorizer.authorize(
            principal(), "read", frozenset({"scope:a", "scope:missing"})
        )


def test_audit_plain_and_redacted_payloads() -> None:
    sink = InMemoryAuditSink()
    recorder = AuditRecorder(
        sink,
        DataHandlingPolicy(
            max_trace_classification=DataClassification.INTERNAL,
            snapshot_retention_days=1,
        ),
    )
    recorder.emit("PLAIN", {"value": 1}, DataClassification.INTERNAL)
    recorder.emit("SECRET", {"value": 2}, DataClassification.CONFIDENTIAL)

    assert sink.events[0].payload == {"value": 1}
    assert sink.events[1].payload["redacted"] is True
    assert sink.events[1].payload["value_hash"] == value_hash({"value": 2})


def test_snapshot_store_not_found_scope_and_tenant_guards() -> None:
    store = InMemorySnapshotStore()
    reference = store.put(
        "tenant-a",
        {"secret": 1},
        DataClassification.CONFIDENTIAL,
        retention_days=1,
    )
    assert store.get(reference, principal()) == {"secret": 1}

    with pytest.raises(PermissionError, match="nsl:replay:read"):
        store.get(reference, principal(scopes=frozenset()))
    with pytest.raises(PermissionError, match="cross-tenant"):
        store.get(reference, principal(tenant_id="tenant-b"))
    missing = SnapshotRef(
        "snapshot-missing",
        "tenant-a",
        DataClassification.INTERNAL,
        "sha256:missing",
    )
    with pytest.raises(KeyError, match="snapshot not found"):
        store.get(missing, principal())

