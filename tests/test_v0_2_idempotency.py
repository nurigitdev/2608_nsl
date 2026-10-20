from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from nsl.compiler import NslCompiler
from nsl.core import (
    BusinessOperationId,
    DataClassification,
    IdempotencyKey,
    PayloadHash,
    RiskLevel,
    STRING,
    ToolCapability,
    ValueEnvelope,
    domain,
)
from nsl.idempotency import (
    IdempotencyConflictError,
    IdempotencyCoordinator,
    IdempotencyDisposition,
    IdempotencyError,
    IdempotencyEvidenceRecord,
    IdempotencyIdentity,
    IdempotencyKeyFactory,
    IdempotencyReservation,
    IdempotencyRetentionError,
    IdempotencyRetentionValidator,
    IdempotencyStateError,
    ProviderIdempotencyEvidence,
    idempotency_identity,
)
from nsl.ir import canonical_json
from nsl.runtime_models import ExecutionContext, ExecutionRequest
from nsl.security import DataHandlingPolicy
from nsl.tools import (
    Atomicity,
    CommitConfirmation,
    CommitSemantics,
    IdempotencySemantics,
    OperationKind,
    ToolContract,
    WriteSemantics,
)
from nsl.write_intent import (
    WriteIntent,
    compute_payload_hash,
    compute_write_intent_hash,
)
from nsl.vertical_slice import build_principal, build_tool_catalog


NOW = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)


def _arguments(amount: str = "84000") -> dict[str, ValueEnvelope]:
    return {
        "detail": ValueEnvelope.complete(
            {"description": "KTX", "amount": Decimal(amount)},
            domain("DraftPayload"),
            DataClassification.CONFIDENTIAL,
        )
    }


def _intent(
    *,
    tenant_id: str = "tenant-a",
    business_operation_id: str = "business-0051",
    node_id: str = "write0001",
    amount: str = "84000",
) -> WriteIntent:
    arguments = _arguments(amount)
    payload_hash = compute_payload_hash(arguments)
    semantic_hash = "sha256:" + "a" * 64
    return WriteIntent(
        tenant_id=tenant_id,
        business_operation_id=BusinessOperationId(business_operation_id),
        node_id=node_id,
        tool_ref="write_tool",
        tool_id="FINANCE.CREATE_DRAFT",
        approval_id="APPROVE_DRAFT",
        skill_semantic_hash=semantic_hash,
        canonical_arguments=arguments,
        payload_hash=payload_hash,
        write_intent_hash=compute_write_intent_hash(
            skill_semantic_hash=semantic_hash,
            write_node_id=node_id,
            tool_id="FINANCE.CREATE_DRAFT",
            payload_hash=payload_hash,
        ),
    )


def _idempotency(**changes: object) -> IdempotencySemantics:
    values: dict[str, object] = {
        "required": True,
        "scope": "TOOL_AND_TENANT",
        "retention": "PROVIDER_DEFINED",
        "duplicate_behavior": "RETURN_ORIGINAL_RESULT",
        "payload_conflict_behavior": "IDEMPOTENCY_CONFLICT",
    }
    values.update(changes)
    return IdempotencySemantics(**values)  # type: ignore[arg-type]


def _contract(*, idempotency: IdempotencySemantics | None = None) -> ToolContract:
    return ToolContract(
        tool_id="FINANCE.CREATE_DRAFT",
        version="1.0.0",
        capability=ToolCapability.WRITE,
        input_types=(("detail", domain("DraftPayload")),),
        output_type=domain("DraftRecord"),
        required_scope="finance:draft:write",
        output_classification=DataClassification.CONFIDENTIAL,
        operation_kind=OperationKind.CREATE,
        risk_level=RiskLevel.R3,
        write_semantics=WriteSemantics(
            side_effect=True,
            atomicity=Atomicity.ATOMIC,
            idempotency=idempotency or _idempotency(),
            commit=CommitSemantics(CommitConfirmation.EXPLICIT),
            approval_required=True,
            preconditions_supported=True,
        ),
    )


def _provider_evidence(
    contract: ToolContract,
    **changes: object,
) -> ProviderIdempotencyEvidence:
    values: dict[str, object] = {
        "tool_id": contract.tool_id,
        "contract_hash": contract.contract_hash,
        "retention_policy": "PROVIDER_DEFINED",
        "provider_guarantee_ref": "provider-policy://finance/idempotency-v1",
        "verified_at": NOW,
        "guaranteed_until": NOW + timedelta(days=30),
        "durable_duplicate_guarantee": True,
    }
    values.update(changes)
    return ProviderIdempotencyEvidence(**values)  # type: ignore[arg-type]


def test_idm_001_every_write_identity_has_an_idempotency_key() -> None:
    identity = idempotency_identity(_intent())

    assert isinstance(identity, IdempotencyIdentity)
    assert identity.idempotency_key.startswith("idem:sha256:")
    assert len(identity.idempotency_key) == 76


def test_idm_002_and_007_key_is_stable_across_execution_lifecycles() -> None:
    intent = _intent()
    factory = IdempotencyKeyFactory()

    keys = {
        factory.create(intent.tenant_id, intent.business_operation_id, intent.node_id)
        for _phase in ("suspend", "resume", "re-execution", "reconciliation")
    }

    assert keys == {idempotency_identity(intent).idempotency_key}


def test_idm_003_key_is_domain_separated_and_bound_to_all_identities() -> None:
    factory = IdempotencyKeyFactory()
    base = factory.create("tenant-a", BusinessOperationId("business-a"), "write-a")
    expected = "idem:sha256:" + sha256(
        b"NSL-IDEMPOTENCY-KEY-V1\0"
        + canonical_json(
            {
                "tenant_id": "tenant-a",
                "business_operation_id": "business-a",
                "write_node_id": "write-a",
            }
        )
    ).hexdigest()

    assert base == IdempotencyKey(expected)
    assert base != factory.create(
        "tenant-b", BusinessOperationId("business-a"), "write-a"
    )
    assert base != factory.create(
        "tenant-a", BusinessOperationId("business-b"), "write-a"
    )
    assert base != factory.create(
        "tenant-a", BusinessOperationId("business-a"), "write-b"
    )


def test_idm_004_payload_hash_is_separate_and_does_not_change_key() -> None:
    original = idempotency_identity(_intent(amount="84000"))
    changed = idempotency_identity(_intent(amount="84001"))

    assert original.idempotency_key == changed.idempotency_key
    assert original.payload_hash != changed.payload_hash
    assert original.idempotency_key != original.payload_hash


@pytest.mark.parametrize("field", ["tenant_id", "business_operation_id", "node_id"])
def test_key_factory_rejects_missing_identity(field: str) -> None:
    values = {
        "tenant_id": "tenant-a",
        "business_operation_id": BusinessOperationId("business-a"),
        "node_id": "write-a",
    }
    values[field] = ""

    with pytest.raises(IdempotencyError, match=field.replace("node_id", "write_node_id")):
        IdempotencyKeyFactory().create(**values)  # type: ignore[arg-type]


def test_idempotency_identity_is_immutable_and_validated() -> None:
    identity = idempotency_identity(_intent())

    with pytest.raises(FrozenInstanceError):
        identity.payload_hash = PayloadHash("sha256:" + "0" * 64)  # type: ignore[misc]
    with pytest.raises(IdempotencyError, match="idem SHA-256"):
        replace(identity, idempotency_key=IdempotencyKey("bad"))
    with pytest.raises(IdempotencyError, match="payload_hash"):
        replace(identity, payload_hash=PayloadHash("bad"))
    with pytest.raises(TypeError, match="WriteIntent"):
        idempotency_identity(object())  # type: ignore[arg-type]


def test_idm_008_and_rd_009_valid_provider_guarantee_covers_exact_horizon() -> None:
    intent = _intent()
    contract = _contract()
    evidence = _provider_evidence(
        contract,
        guaranteed_until=NOW + timedelta(days=7),
    )

    assert IdempotencyRetentionValidator().validate(
        intent=intent,
        contract=contract,
        evidence=evidence,
        now=NOW,
        required_until=NOW + timedelta(days=7),
    ) is evidence


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"tool_id": "OTHER.WRITE"}, "tool binding"),
        ({"contract_hash": "sha256:" + "b" * 64}, "contract binding"),
        ({"retention_policy": "OTHER"}, "retention policy"),
        ({"durable_duplicate_guarantee": False}, "guarantee is absent"),
        ({"verified_at": NOW + timedelta(seconds=1)}, "from the future"),
        ({"guaranteed_until": NOW}, "must end after verification"),
    ],
)
def test_rd_009_missing_mismatched_or_stale_guarantee_is_rejected(
    change: dict[str, object],
    message: str,
) -> None:
    contract = _contract()
    if change == {"guaranteed_until": NOW}:
        with pytest.raises(IdempotencyError, match=message):
            _provider_evidence(contract, **change)
        return
    evidence = _provider_evidence(contract, **change)

    with pytest.raises(IdempotencyRetentionError, match=message):
        IdempotencyRetentionValidator().validate(
            intent=_intent(),
            contract=contract,
            evidence=evidence,
            now=NOW,
            required_until=NOW + timedelta(days=7),
        )


def test_rd_009_expiry_and_recovery_horizon_boundaries_fail_closed() -> None:
    contract = _contract()
    validator = IdempotencyRetentionValidator()
    evidence = _provider_evidence(
        contract,
        verified_at=NOW - timedelta(days=1),
        guaranteed_until=NOW + timedelta(days=7),
    )

    with pytest.raises(IdempotencyRetentionError, match="after now"):
        validator.validate(
            intent=_intent(),
            contract=contract,
            evidence=evidence,
            now=NOW,
            required_until=NOW,
        )
    with pytest.raises(IdempotencyRetentionError, match="has expired"):
        validator.validate(
            intent=_intent(),
            contract=contract,
            evidence=evidence,
            now=NOW + timedelta(days=7),
            required_until=NOW + timedelta(days=8),
        )
    with pytest.raises(IdempotencyRetentionError, match="recovery horizon"):
        validator.validate(
            intent=_intent(),
            contract=contract,
            evidence=evidence,
            now=NOW,
            required_until=NOW + timedelta(days=7, seconds=1),
        )


@pytest.mark.parametrize(
    "idempotency",
    [
        _idempotency(scope="GLOBAL"),
        _idempotency(duplicate_behavior="ACCEPT_DUPLICATE"),
        _idempotency(payload_conflict_behavior="OVERWRITE"),
    ],
)
def test_idm_008_contract_must_declare_exact_durable_semantics(
    idempotency: IdempotencySemantics,
) -> None:
    contract = _contract(idempotency=idempotency)

    with pytest.raises(IdempotencyRetentionError, match="durable duplicate"):
        IdempotencyRetentionValidator().validate(
            intent=_intent(),
            contract=contract,
            evidence=_provider_evidence(contract),
            now=NOW,
            required_until=NOW + timedelta(days=7),
        )


def test_provider_evidence_rejects_malformed_values() -> None:
    contract = _contract()

    with pytest.raises(TypeError, match="bool"):
        _provider_evidence(contract, durable_duplicate_guarantee=1)
    with pytest.raises(TypeError, match="datetime"):
        _provider_evidence(contract, verified_at="now")
    with pytest.raises(IdempotencyError, match="contract_hash"):
        _provider_evidence(contract, contract_hash="bad")
    with pytest.raises(IdempotencyError, match="timezone-aware UTC"):
        _provider_evidence(contract, verified_at=NOW.replace(tzinfo=None))
    with pytest.raises(IdempotencyError, match="must use UTC"):
        _provider_evidence(
            contract,
            verified_at=NOW.astimezone(timezone(timedelta(hours=9))),
        )


def test_retention_validator_rejects_wrong_contract_types() -> None:
    validator = IdempotencyRetentionValidator()
    contract = _contract()
    evidence = _provider_evidence(contract)
    values = {
        "intent": _intent(),
        "contract": contract,
        "evidence": evidence,
        "now": NOW,
        "required_until": NOW + timedelta(days=7),
    }

    for field in ("intent", "contract", "evidence"):
        malformed = dict(values)
        malformed[field] = object()
        with pytest.raises(TypeError, match=field):
            validator.validate(**malformed)  # type: ignore[arg-type]


def test_retention_validator_rejects_read_tool_contract() -> None:
    read_contract = ToolContract(
        tool_id="FINANCE.CREATE_DRAFT",
        version="1.0.0",
        capability=ToolCapability.READ,
        input_types=(("detail", domain("DraftPayload")),),
        output_type=domain("DraftRecord"),
        required_scope="finance:draft:read",
        output_classification=DataClassification.CONFIDENTIAL,
    )

    with pytest.raises(IdempotencyRetentionError, match="WRITE Tool Contract"):
        IdempotencyRetentionValidator().validate(
            intent=_intent(),
            contract=read_contract,
            evidence=_provider_evidence(read_contract),
            now=NOW,
            required_until=NOW + timedelta(days=7),
        )


def _context(
    *,
    tenant_id: str = "tenant-a",
    business_operation_id: str | None = "business-0051",
) -> ExecutionContext:
    skill = NslCompiler(build_tool_catalog()).compile(
        '''language NSL "0.1";
skill TEST.IDEMPOTENCY_CONTEXT {
    version "1.0.0";
    risk READ_ONLY;
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }
    output { result: String; }
}
'''
    ).skill
    return ExecutionContext(
        skill,
        ExecutionRequest(
            execution_id="exec-0051",
            inputs={},
            runtime_context={},
            principal=build_principal(tenant_id=tenant_id),
            data_policy=DataHandlingPolicy(),
            business_operation_id=(
                None
                if business_operation_id is None
                else BusinessOperationId(business_operation_id)
            ),
        ),
    )


class _DurableIdempotencyPort:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.records: dict[str, IdempotencyEvidenceRecord] = {}
        self.result_refs: dict[str, str] = {}
        self.calls = 0

    async def reserve(
        self,
        evidence: IdempotencyEvidenceRecord,
    ) -> IdempotencyReservation:
        async with self._lock:
            self.calls += 1
            key = str(evidence.idempotency_key)
            existing = self.records.get(key)
            if existing is None:
                self.records[key] = evidence
                return IdempotencyReservation(
                    IdempotencyDisposition.CREATED,
                    evidence,
                )
            if existing.payload_hash != evidence.payload_hash:
                return IdempotencyReservation(
                    IdempotencyDisposition.CONFLICT,
                    evidence,
                )
            return IdempotencyReservation(
                IdempotencyDisposition.REUSED,
                evidence,
                self.result_refs.get(key),
            )

    def complete(self, key: str, result_ref: str) -> None:
        self.result_refs[key] = result_ref


def _reserve(
    coordinator: IdempotencyCoordinator,
    *,
    intent: WriteIntent | None = None,
    context: ExecutionContext | None = None,
    contract: ToolContract | None = None,
    provider_evidence: ProviderIdempotencyEvidence | None = None,
) -> IdempotencyReservation:
    selected_intent = intent or _intent()
    selected_contract = contract or _contract()
    return asyncio.run(
        coordinator.reserve(
            intent=selected_intent,
            context=context or _context(),
            contract=selected_contract,
            provider_evidence=(
                provider_evidence or _provider_evidence(selected_contract)
            ),
            now=NOW,
            required_until=NOW + timedelta(days=7),
        )
    )


def test_idm_005_same_key_and_payload_reuses_original_business_result() -> None:
    port = _DurableIdempotencyPort()
    coordinator = IdempotencyCoordinator(port)
    first_context = _context()

    first = _reserve(coordinator, context=first_context)
    port.complete(str(first.evidence.idempotency_key), "result:expense-draft-42")
    resumed_context = _context()
    resumed_context.idempotency.keys_by_write_node.update(
        first_context.idempotency.keys_by_write_node
    )
    resumed_context.idempotency.payload_hashes_by_write_node.update(
        first_context.idempotency.payload_hashes_by_write_node
    )
    second = _reserve(coordinator, context=resumed_context)

    assert first.disposition is IdempotencyDisposition.CREATED
    assert second.disposition is IdempotencyDisposition.REUSED
    assert second.business_result_ref == "result:expense-draft-42"
    assert second.evidence == first.evidence
    assert port.calls == 2


def test_idm_006_same_key_with_different_payload_fails_closed() -> None:
    port = _DurableIdempotencyPort()
    coordinator = IdempotencyCoordinator(port)
    first = _reserve(coordinator)
    changed = _intent(amount="84001")

    with pytest.raises(IdempotencyConflictError) as captured:
        _reserve(coordinator, intent=changed)

    assert captured.value.detail_code == "IDEMPOTENCY_CONFLICT"
    assert port.calls == 2
    assert port.records[str(first.evidence.idempotency_key)].payload_hash == (
        first.evidence.payload_hash
    )


def test_idm_007_checkpoint_state_reuses_key_and_rejects_payload_change() -> None:
    port = _DurableIdempotencyPort()
    coordinator = IdempotencyCoordinator(port)
    context = _context()
    first = _reserve(coordinator, context=context)

    assert context.idempotency.keys_by_write_node == {
        "write0001": first.evidence.idempotency_key
    }
    assert context.idempotency.payload_hashes_by_write_node == {
        "write0001": first.evidence.payload_hash
    }
    context.idempotency.payload_hashes_by_write_node["write0001"] = PayloadHash(
        "sha256:" + "f" * 64
    )
    with pytest.raises(IdempotencyConflictError):
        _reserve(coordinator, context=context)
    assert port.calls == 1


def test_idm_003_tenant_isolation_produces_distinct_durable_keys() -> None:
    port = _DurableIdempotencyPort()
    coordinator = IdempotencyCoordinator(port)
    first = _reserve(coordinator)
    second = _reserve(
        coordinator,
        intent=_intent(tenant_id="tenant-b"),
        context=_context(tenant_id="tenant-b"),
    )

    assert first.evidence.idempotency_key != second.evidence.idempotency_key
    assert len(port.records) == 2


def test_idm_005_concurrent_reservations_create_exactly_once() -> None:
    async def scenario() -> list[IdempotencyReservation]:
        port = _DurableIdempotencyPort()
        coordinator = IdempotencyCoordinator(port)
        contract = _contract()
        evidence = _provider_evidence(contract)
        return await asyncio.gather(
            *(
                coordinator.reserve(
                    intent=_intent(),
                    context=_context(),
                    contract=contract,
                    provider_evidence=evidence,
                    now=NOW,
                    required_until=NOW + timedelta(days=7),
                )
                for _ in range(10)
            )
        )

    reservations = asyncio.run(scenario())

    assert sum(
        item.disposition is IdempotencyDisposition.CREATED
        for item in reservations
    ) == 1
    assert sum(
        item.disposition is IdempotencyDisposition.REUSED
        for item in reservations
    ) == 9


def test_idempotency_context_binding_must_match_intent() -> None:
    coordinator = IdempotencyCoordinator(_DurableIdempotencyPort())

    with pytest.raises(IdempotencyStateError, match="tenant"):
        _reserve(coordinator, context=_context(tenant_id="tenant-b"))
    with pytest.raises(IdempotencyStateError, match="lacks"):
        _reserve(coordinator, context=_context(business_operation_id=None))
    with pytest.raises(IdempotencyStateError, match="business operation"):
        _reserve(
            coordinator,
            context=_context(business_operation_id="different-business"),
        )


def test_checkpoint_key_mismatch_is_rejected_before_durable_reservation() -> None:
    port = _DurableIdempotencyPort()
    context = _context()
    context.idempotency.keys_by_write_node["write0001"] = IdempotencyKey(
        "idem:sha256:" + "f" * 64
    )

    with pytest.raises(IdempotencyStateError, match="semantic identity"):
        _reserve(IdempotencyCoordinator(port), context=context)

    assert port.calls == 0


def test_durable_port_failure_leaves_runtime_state_unchanged() -> None:
    class FailingPort:
        async def reserve(
            self,
            evidence: IdempotencyEvidenceRecord,
        ) -> IdempotencyReservation:
            raise OSError("storage unavailable")

    context = _context()
    with pytest.raises(OSError, match="storage unavailable"):
        _reserve(IdempotencyCoordinator(FailingPort()), context=context)

    assert context.idempotency.keys_by_write_node == {}
    assert context.idempotency.payload_hashes_by_write_node == {}


@pytest.mark.parametrize("response_kind", ["wrong_type", "wrong_evidence"])
def test_invalid_durable_port_response_is_rejected(
    response_kind: str,
) -> None:
    class InvalidPort:
        async def reserve(self, evidence: IdempotencyEvidenceRecord) -> object:
            if response_kind == "wrong_type":
                return object()
            return IdempotencyReservation(
                IdempotencyDisposition.CREATED,
                replace(evidence, tenant_id="tenant-other"),
            )

    context = _context()
    with pytest.raises(IdempotencyStateError, match="invalid|mismatched"):
        _reserve(IdempotencyCoordinator(InvalidPort()), context=context)
    assert context.idempotency.keys_by_write_node == {}


def test_idempotency_models_reject_malformed_boundary_values() -> None:
    record = IdempotencyEvidenceRecord(
        tenant_id="tenant-a",
        business_operation_id=BusinessOperationId("business-0051"),
        write_node_id="write0001",
        tool_id="FINANCE.CREATE_DRAFT",
        contract_hash="sha256:" + "c" * 64,
        idempotency_key=IdempotencyKey("idem:sha256:" + "d" * 64),
        payload_hash=PayloadHash("sha256:" + "e" * 64),
        provider_guarantee_ref="provider-policy-0051",
        retained_until=NOW + timedelta(days=7),
    )

    with pytest.raises(TypeError, match="disposition"):
        IdempotencyReservation("CREATED", record)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="evidence"):
        IdempotencyReservation(
            IdempotencyDisposition.CREATED,
            object(),  # type: ignore[arg-type]
        )
    with pytest.raises(IdempotencyError, match="business_result_ref"):
        IdempotencyReservation(IdempotencyDisposition.REUSED, record, "")
    with pytest.raises(IdempotencyError, match="cannot expose"):
        IdempotencyReservation(
            IdempotencyDisposition.CONFLICT,
            record,
            "result:forbidden",
        )
    with pytest.raises(IdempotencyError, match="idempotency_key"):
        replace(record, idempotency_key=IdempotencyKey("bad"))
    with pytest.raises(TypeError, match="port"):
        IdempotencyCoordinator(object())  # type: ignore[arg-type]


def test_coordinator_rejects_wrong_argument_types() -> None:
    coordinator = IdempotencyCoordinator(_DurableIdempotencyPort())
    contract = _contract()
    evidence = _provider_evidence(contract)

    with pytest.raises(TypeError, match="intent"):
        asyncio.run(
            coordinator.reserve(
                intent=object(),  # type: ignore[arg-type]
                context=_context(),
                contract=contract,
                provider_evidence=evidence,
                now=NOW,
                required_until=NOW + timedelta(days=7),
            )
        )
    with pytest.raises(TypeError, match="context"):
        asyncio.run(
            coordinator.reserve(
                intent=_intent(),
                context=object(),  # type: ignore[arg-type]
                contract=contract,
                provider_evidence=evidence,
                now=NOW,
                required_until=NOW + timedelta(days=7),
            )
        )
