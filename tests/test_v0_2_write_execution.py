from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from nsl.compiler import NslCompiler
from nsl.core import (
    ApprovalRequestId,
    BusinessOperationId,
    CommitState,
    DataClassification,
    ExecutionStatus,
    IdempotencyKey,
    PayloadHash,
    PreWriteEligibility,
    RiskLevel,
    ToolCapability,
    ValueEnvelope,
    domain,
)
from nsl.idempotency import (
    IdempotencyDisposition,
    IdempotencyEvidenceRecord,
    IdempotencyReservation,
)
from nsl.runtime_models import ExecutionContext, ExecutionRequest, WriteSummary
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
from nsl.validation import (
    BusinessPreconditionEvidence,
    BusinessPreconditionMechanism,
    WriteContractEvidence,
    WriteEligibilityError,
    WriteGuardDecision,
)
from nsl.vertical_slice import build_principal, build_tool_catalog
from nsl.write_execution import (
    CommitAwareWriteExecutor,
    ProviderInvocationUncertainError,
    WriteAttemptRecord,
    WriteCallRequest,
    WriteCallRequestBuilder,
    WriteCommitDecision,
    WriteCommitUnknown,
    WriteCommitted,
    WriteEvidenceError,
    WriteExecutionCoordinator,
    WriteExecutionError,
    WriteJournalEntry,
    WriteNotCommitted,
    WriteOutcomeCoordinator,
    WriteOutcomeIdentity,
    WriteOutcomeReceipt,
    WriteOutcomeRecord,
    WriteOutcomeError,
    WriteResultEnvelope,
    WriteRequestedReceipt,
)
from nsl.write_intent import (
    WriteIntent,
    compute_payload_hash,
    compute_write_intent_hash,
)


NOW = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)


def _intent(
    *,
    tenant_id: str = "tenant-a",
    business_operation_id: str = "business-0053",
    node_id: str = "write0001",
    tool_id: str = "FINANCE.CREATE_DRAFT",
) -> WriteIntent:
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
    payload_hash = compute_payload_hash(arguments)
    semantic_hash = "sha256:" + "a" * 64
    return WriteIntent(
        tenant_id=tenant_id,
        business_operation_id=BusinessOperationId(business_operation_id),
        node_id=node_id,
        tool_ref="write_tool",
        tool_id=tool_id,
        approval_id="APPROVE_DRAFT",
        skill_semantic_hash=semantic_hash,
        canonical_arguments=arguments,
        payload_hash=payload_hash,
        write_intent_hash=compute_write_intent_hash(
            skill_semantic_hash=semantic_hash,
            write_node_id=node_id,
            tool_id=tool_id,
            payload_hash=payload_hash,
        ),
    )


def _context(
    *,
    tenant_id: str = "tenant-a",
    business_operation_id: str = "business-0053",
    execution_id: str = "exec-0053",
) -> ExecutionContext:
    skill = NslCompiler(build_tool_catalog()).compile(
        '''language NSL "0.1";
skill TEST.WRITE_EXECUTION_CONTEXT {
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
            execution_id=execution_id,
            inputs={},
            runtime_context={},
            principal=build_principal(tenant_id=tenant_id),
            data_policy=DataHandlingPolicy(),
            business_operation_id=BusinessOperationId(business_operation_id),
        ),
    )


def _tool_contract(
    *,
    tool_id: str = "FINANCE.CREATE_DRAFT",
    capability: ToolCapability = ToolCapability.WRITE,
) -> ToolContract:
    if capability is ToolCapability.READ:
        return ToolContract(
            tool_id=tool_id,
            version="1.0.0",
            capability=capability,
            input_types=(("detail", domain("DraftPayload")),),
            output_type=domain("DraftRecord"),
            required_scope="finance:draft:read",
            output_classification=DataClassification.CONFIDENTIAL,
        )
    return ToolContract(
        tool_id=tool_id,
        version="1.0.0",
        capability=capability,
        input_types=(("detail", domain("DraftPayload")),),
        output_type=domain("DraftRecord"),
        required_scope="finance:draft:write",
        output_classification=DataClassification.CONFIDENTIAL,
        operation_kind=OperationKind.CREATE,
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
        ),
    )


def _contract_evidence(contract: ToolContract | None = None) -> WriteContractEvidence:
    selected = contract or _tool_contract()
    return WriteContractEvidence(
        tool_id=selected.tool_id,
        contract_hash=selected.contract_hash,
        capability=selected.capability,
        risk_level=selected.risk_level or RiskLevel.R3,
        preconditions_supported=(
            selected.write_semantics is not None
            and selected.write_semantics.preconditions_supported
        ),
    )


def _reservation(
    intent: WriteIntent,
    contract: WriteContractEvidence,
    **changes: object,
) -> IdempotencyReservation:
    values: dict[str, object] = {
        "tenant_id": intent.tenant_id,
        "business_operation_id": intent.business_operation_id,
        "write_node_id": intent.node_id,
        "tool_id": intent.tool_id,
        "contract_hash": contract.contract_hash,
        "idempotency_key": IdempotencyKey("idem:sha256:" + "d" * 64),
        "payload_hash": intent.payload_hash,
        "provider_guarantee_ref": "provider-policy-0053",
        "retained_until": NOW + timedelta(days=30),
    }
    values.update(changes)
    return IdempotencyReservation(
        IdempotencyDisposition.CREATED,
        IdempotencyEvidenceRecord(**values),  # type: ignore[arg-type]
    )


def _precondition(
    intent: WriteIntent,
    contract: WriteContractEvidence,
    **changes: object,
) -> BusinessPreconditionEvidence:
    values: dict[str, object] = {
        "evidence_id": "precondition-0053",
        "tenant_id": intent.tenant_id,
        "business_operation_id": intent.business_operation_id,
        "write_node_id": intent.node_id,
        "tool_id": intent.tool_id,
        "contract_hash": contract.contract_hash,
        "payload_hash": intent.payload_hash,
        "write_intent_hash": intent.write_intent_hash,
        "mechanism": BusinessPreconditionMechanism.ETAG,
        "evidence_ref": "etag:opaque:0053",
        "observed_at": NOW - timedelta(seconds=1),
        "expires_at": NOW + timedelta(seconds=1),
        "provider_atomic_enforcement": True,
    }
    values.update(changes)
    return BusinessPreconditionEvidence(**values)  # type: ignore[arg-type]


def _request(
    *,
    intent: WriteIntent | None = None,
    context: ExecutionContext | None = None,
    contract: WriteContractEvidence | None = None,
    reservation: IdempotencyReservation | None = None,
    precondition: BusinessPreconditionEvidence | None = None,
    invocation_id: str = "invoke-0053",
    trace_refs: tuple[str, ...] = ("trace:0053",),
) -> WriteCallRequest:
    selected_intent = intent or _intent()
    selected_context = context or _context()
    selected_contract = contract or _contract_evidence()
    return WriteCallRequestBuilder().build(
        intent=selected_intent,
        context=selected_context,
        contract_evidence=selected_contract,
        reservation=reservation or _reservation(selected_intent, selected_contract),
        approval_request_id=ApprovalRequestId("approval-request-0053"),
        invocation_id=invocation_id,
        business_preconditions=(
            precondition or _precondition(selected_intent, selected_contract)
        ),
        trace_refs=trace_refs,
    )


class _EvidencePort:
    def __init__(self, events: list[str], mode: str = "valid") -> None:
        self.events = events
        self.mode = mode
        self.records: list[WriteAttemptRecord] = []

    async def record_requested(self, record: WriteAttemptRecord):
        self.events.append("WRITE_REQUESTED")
        self.records.append(record)
        if self.mode == "raise":
            raise OSError("durable storage unavailable")
        if self.mode == "wrong_type":
            return object()
        receipt = WriteRequestedReceipt(
            record_hash=record.record_hash,
            durable_ref="write-evidence:0053",
            persisted_at=record.requested_at,
            durable=self.mode != "not_durable",
        )
        if self.mode == "mismatch":
            return replace(receipt, record_hash="sha256:" + "f" * 64)
        if self.mode == "predates":
            return replace(
                receipt,
                persisted_at=record.requested_at - timedelta(microseconds=1),
            )
        return receipt


class _WritePort:
    def __init__(
        self,
        events: list[str],
        *,
        result: object = "raw-provider-response",
        fail: bool = False,
    ) -> None:
        self.events = events
        self.result = result
        self.fail = fail
        self.requests: list[WriteCallRequest] = []

    async def execute(self, request: WriteCallRequest):
        self.events.append("PROVIDER_WRITE")
        self.requests.append(request)
        if self.fail:
            raise TimeoutError("provider acknowledgement lost")
        return self.result


def _execute(
    coordinator: WriteExecutionCoordinator,
    *,
    request: WriteCallRequest | None = None,
    context: ExecutionContext | None = None,
    decision: WriteGuardDecision | None = None,
    requested_at: datetime = NOW,
):
    return asyncio.run(
        coordinator.execute(
            request=request or _request(context=context),
            decision=decision or WriteGuardDecision(PreWriteEligibility.ALLOWED),
            context=context or _context(),
            requested_at=requested_at,
        )
    )


def test_wrt_001_canonical_side_effect_request_preserves_all_identities() -> None:
    request = _request()

    assert request.tenant_id == "tenant-a"
    assert request.execution_id == "exec-0053"
    assert request.business_operation_id == "business-0053"
    assert request.invocation_id == "invoke-0053"
    assert request.node_id == "write0001"
    assert request.tool_ref == "write_tool"
    assert request.tool_id == "FINANCE.CREATE_DRAFT"
    assert request.idempotency_key == "idem:sha256:" + "d" * 64
    assert request.payload_hash == compute_payload_hash(request.arguments)
    assert request.approval_request_id == "approval-request-0053"
    assert request.business_preconditions.evidence_id == "precondition-0053"
    assert request.trace_refs == ("trace:0053",)


def test_arc_204_205_write_requested_is_durable_before_canonical_port_call() -> None:
    events: list[str] = []
    evidence_port = _EvidencePort(events)
    write_port = _WritePort(events)
    context = _context()
    request = _request(context=context)

    result = _execute(
        WriteExecutionCoordinator(write_port, evidence_port),
        request=request,
        context=context,
    )

    assert result == "raw-provider-response"
    assert events == ["WRITE_REQUESTED", "PROVIDER_WRITE"]
    assert write_port.requests == [request]
    assert context.checkpoint.write_request_durable is True
    assert evidence_port.records[0].tool_id == request.tool_id


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("raise", OSError),
        ("wrong_type", WriteEvidenceError),
        ("not_durable", WriteEvidenceError),
        ("mismatch", WriteEvidenceError),
        ("predates", WriteEvidenceError),
    ],
)
def test_arc_204_persistence_failure_prevents_provider_invocation(mode, error) -> None:
    events: list[str] = []
    context = _context()
    coordinator = WriteExecutionCoordinator(
        _WritePort(events),
        _EvidencePort(events, mode),
    )

    with pytest.raises(error):
        _execute(coordinator, context=context)

    assert events == ["WRITE_REQUESTED"]
    assert context.checkpoint.write_request_durable is False


def test_provider_failure_occurs_only_after_durable_request_evidence() -> None:
    events: list[str] = []
    context = _context()
    coordinator = WriteExecutionCoordinator(
        _WritePort(events, fail=True),
        _EvidencePort(events),
    )

    with pytest.raises(ProviderInvocationUncertainError) as captured:
        _execute(coordinator, context=context)

    assert isinstance(captured.value.cause, TimeoutError)
    assert captured.value.reason_code == "PROVIDER_TIMEOUT"
    assert events == ["WRITE_REQUESTED", "PROVIDER_WRITE"]
    assert context.checkpoint.write_request_durable is True


@pytest.mark.parametrize(
    "eligibility",
    [PreWriteEligibility.BLOCKED, PreWriteEligibility.REQUIRES_APPROVAL],
)
def test_non_allowed_guard_prevents_persistence_and_provider_call(eligibility) -> None:
    events: list[str] = []
    coordinator = WriteExecutionCoordinator(
        _WritePort(events),
        _EvidencePort(events),
    )

    with pytest.raises(WriteEligibilityError):
        _execute(
            coordinator,
            decision=WriteGuardDecision(eligibility, "NOT_ALLOWED"),
        )

    assert events == []


def test_acknowledgement_loss_retry_reuses_critical_request_identity() -> None:
    events: list[str] = []
    evidence_port = _EvidencePort(events)
    request = _request()
    coordinator = WriteExecutionCoordinator(_WritePort(events), evidence_port)

    _execute(coordinator, request=request)
    _execute(coordinator, request=request)

    assert len(evidence_port.records) == 2
    assert evidence_port.records[0] == evidence_port.records[1]
    assert evidence_port.records[0].record_hash == (
        evidence_port.records[1].record_hash
    )


def test_write_call_arguments_are_deeply_immutable() -> None:
    request = _request()

    with pytest.raises(TypeError):
        request.arguments["other"] = request.arguments["detail"]  # type: ignore[index]
    detail = request.arguments["detail"].value
    with pytest.raises(TypeError):
        detail["description"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        detail["lines"][0]["sequence"] = 2  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tenant_id", "tenant-other", "tenant"),
        ("business_operation_id", "business-other", "business operation"),
    ],
)
def test_builder_rejects_context_identity_mismatch(field, value, message) -> None:
    context_values = {"tenant_id": "tenant-a", "business_operation_id": "business-0053"}
    context_values[field] = value

    with pytest.raises(WriteExecutionError, match=message):
        _request(context=_context(**context_values))


def test_builder_rejects_noncanonical_tool_and_read_capability() -> None:
    intent = _intent()
    context = _context()
    mismatched = _contract_evidence(_tool_contract(tool_id="FINANCE.OTHER"))
    read = _contract_evidence(_tool_contract(capability=ToolCapability.READ))

    for contract in (mismatched, read):
        with pytest.raises(WriteExecutionError, match="canonical WRITE Tool"):
            _request(intent=intent, context=context, contract=contract)


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "tenant-other"},
        {"business_operation_id": BusinessOperationId("business-other")},
        {"write_node_id": "write9999"},
        {"tool_id": "FINANCE.OTHER"},
        {"contract_hash": "sha256:" + "e" * 64},
        {"payload_hash": PayloadHash("sha256:" + "f" * 64)},
    ],
)
def test_builder_rejects_idempotency_reservation_mismatch(changes) -> None:
    intent = _intent()
    contract = _contract_evidence()

    with pytest.raises(WriteExecutionError, match="reservation binding"):
        _request(
            intent=intent,
            contract=contract,
            reservation=_reservation(intent, contract, **changes),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "tenant-other"},
        {"business_operation_id": BusinessOperationId("business-other")},
        {"write_node_id": "write9999"},
        {"tool_id": "FINANCE.OTHER"},
        {"contract_hash": "sha256:" + "e" * 64},
        {"payload_hash": PayloadHash("sha256:" + "f" * 64)},
        {"write_intent_hash": "sha256:" + "b" * 64},
        {"provider_atomic_enforcement": False},
    ],
)
def test_builder_rejects_precondition_mismatch(changes) -> None:
    intent = _intent()
    contract = _contract_evidence()

    with pytest.raises(WriteExecutionError, match="precondition binding"):
        _request(
            intent=intent,
            contract=contract,
            precondition=_precondition(intent, contract, **changes),
        )


def test_coordinator_rejects_execution_context_mismatch_before_persistence() -> None:
    request = _request()

    for context, message in (
        (_context(execution_id="exec-other"), "execution identity"),
        (_context(tenant_id="tenant-other"), "tenant"),
        (_context(business_operation_id="business-other"), "business operation"),
    ):
        events: list[str] = []
        coordinator = WriteExecutionCoordinator(
            _WritePort(events),
            _EvidencePort(events),
        )
        with pytest.raises(WriteExecutionError, match=message):
            _execute(coordinator, request=request, context=context)
        assert events == []


def test_attempt_record_hash_is_deterministic_and_domain_separated() -> None:
    request = _request()
    first = WriteAttemptRecord.from_request(request, NOW)
    same = WriteAttemptRecord.from_request(request, NOW)
    later = WriteAttemptRecord.from_request(request, NOW + timedelta(microseconds=1))

    assert first.record_hash == same.record_hash
    assert first.record_hash.startswith("sha256:")
    assert first.record_hash != request.write_intent_hash
    assert first.record_hash != later.record_hash


def test_request_and_evidence_models_reject_malformed_values() -> None:
    request = _request()
    record = WriteAttemptRecord.from_request(request, NOW)
    receipt = WriteRequestedReceipt(
        record.record_hash,
        "write-evidence:0053",
        NOW,
        True,
    )

    with pytest.raises(FrozenInstanceError):
        request.tool_id = "changed"  # type: ignore[misc]
    with pytest.raises(WriteExecutionError, match="non-empty"):
        replace(request, invocation_id="")
    with pytest.raises(TypeError, match="arguments must be a mapping"):
        replace(request, arguments=object())
    with pytest.raises(TypeError, match="ValueEnvelope"):
        replace(request, arguments={"detail": object()})
    with pytest.raises(WriteExecutionError, match="payload_hash"):
        replace(request, payload_hash=PayloadHash("sha256:" + "f" * 64))
    with pytest.raises(WriteExecutionError, match="idempotency_key"):
        replace(request, idempotency_key=IdempotencyKey("bad"))
    with pytest.raises(TypeError, match="business_preconditions"):
        replace(request, business_preconditions=object())
    with pytest.raises(TypeError, match="trace_refs"):
        replace(request, trace_refs=["trace:0053"])
    with pytest.raises(ValueError, match="credential"):
        replace(request, trace_refs=("api_key=secret",))
    with pytest.raises(TypeError, match="request"):
        WriteAttemptRecord.from_request(object(), NOW)  # type: ignore[arg-type]
    with pytest.raises(WriteExecutionError, match="idempotency_key"):
        replace(record, idempotency_key=IdempotencyKey("bad"))
    with pytest.raises(WriteExecutionError, match="SHA-256"):
        replace(receipt, record_hash="bad")
    with pytest.raises(TypeError, match="Bool"):
        replace(receipt, durable=1)


@pytest.mark.parametrize(
    ("value", "error", "message"),
    [
        ("now", TypeError, "datetime"),
        (NOW.replace(tzinfo=None), WriteExecutionError, "timezone-aware UTC"),
        (
            NOW.astimezone(timezone(timedelta(hours=9))),
            WriteExecutionError,
            "must use UTC",
        ),
    ],
)
def test_attempt_record_requires_utc_requested_at(value, error, message) -> None:
    with pytest.raises(error, match=message):
        WriteAttemptRecord.from_request(_request(), value)


def test_builder_and_coordinator_reject_wrong_types() -> None:
    intent = _intent()
    context = _context()
    contract = _contract_evidence()
    reservation = _reservation(intent, contract)
    precondition = _precondition(intent, contract)
    values = {
        "intent": intent,
        "context": context,
        "contract_evidence": contract,
        "reservation": reservation,
        "approval_request_id": ApprovalRequestId("approval-request-0053"),
        "invocation_id": "invoke-0053",
        "business_preconditions": precondition,
    }
    for field in (
        "intent",
        "context",
        "contract_evidence",
        "reservation",
        "business_preconditions",
    ):
        malformed = dict(values)
        malformed[field] = object()
        with pytest.raises(TypeError, match=field):
            WriteCallRequestBuilder().build(**malformed)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="write_port"):
        WriteExecutionCoordinator(object(), _EvidencePort([]))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="evidence_port"):
        WriteExecutionCoordinator(_WritePort([]), object())  # type: ignore[arg-type]

    coordinator = WriteExecutionCoordinator(_WritePort([]), _EvidencePort([]))
    execute_values = {
        "request": _request(),
        "decision": WriteGuardDecision(PreWriteEligibility.ALLOWED),
        "context": _context(),
        "requested_at": NOW,
    }
    for field in ("request", "decision", "context"):
        malformed = dict(execute_values)
        malformed[field] = object()
        with pytest.raises(TypeError, match=field):
            asyncio.run(coordinator.execute(**malformed))  # type: ignore[arg-type]


class _OutcomePort:
    def __init__(self, events: list[str], mode: str = "valid") -> None:
        self.events = events
        self.mode = mode
        self.records: list[WriteOutcomeRecord] = []

    async def record_outcome(self, record: WriteOutcomeRecord):
        self.events.append("WRITE_OUTCOME")
        self.records.append(record)
        if self.mode == "raise":
            raise OSError("outcome storage unavailable")
        if self.mode == "wrong_type":
            return object()
        receipt = WriteOutcomeReceipt(
            record.record_hash,
            "write-outcome:0054",
            record.recorded_at,
        )
        if self.mode == "mismatch":
            return replace(receipt, record_hash="sha256:" + "f" * 64)
        if self.mode == "predates":
            return replace(
                receipt,
                persisted_at=record.recorded_at - timedelta(microseconds=1),
            )
        return receipt


class _ExceptionalWritePort:
    def __init__(self, events: list[str], error: BaseException) -> None:
        self.events = events
        self.error = error
        self.calls = 0

    async def execute(self, request: WriteCallRequest):
        self.events.append("PROVIDER_WRITE")
        self.calls += 1
        raise self.error


def _outcome_identity(request: WriteCallRequest) -> WriteOutcomeIdentity:
    return WriteOutcomeIdentity.from_request(request)


def _committed(request: WriteCallRequest) -> WriteCommitted:
    return WriteCommitted(
        WriteResultEnvelope(
            identity=_outcome_identity(request),
            value=ValueEnvelope.complete(
                {"draft_id": "DRAFT-0054"},
                domain("DraftRecord"),
                DataClassification.CONFIDENTIAL,
            ),
            business_reference="DRAFT-0054",
            provider_transaction_id="tx-0054",
            committed_at=NOW + timedelta(seconds=1),
            evidence_refs=("provider:commit:0054",),
        )
    )


def _not_committed(request: WriteCallRequest) -> WriteNotCommitted:
    return WriteNotCommitted(
        identity=_outcome_identity(request),
        reason_code="BUSINESS_PRECONDITION_REJECTED",
        message="provider confirmed no side effect",
        provider_transaction_id="tx-rejected-0054",
        evidence_refs=("provider:rejection:0054",),
    )


def _unknown(request: WriteCallRequest) -> WriteCommitUnknown:
    return WriteCommitUnknown(
        identity=_outcome_identity(request),
        reason_code="PROVIDER_ACKNOWLEDGEMENT_LOST",
        message="provider outcome is uncertain; reconciliation is required",
        provider_transaction_id="tx-unknown-0054",
        evidence_refs=("transport:unknown:0054",),
    )


def _seed_idempotency(context: ExecutionContext, request: WriteCallRequest) -> None:
    context.idempotency.keys_by_write_node[request.node_id] = request.idempotency_key
    context.idempotency.payload_hashes_by_write_node[request.node_id] = (
        request.payload_hash
    )


def _commit_aware_execute(
    raw_outcome: object,
    *,
    write_port=None,
    outcome_port: _OutcomePort | None = None,
) -> tuple[WriteCommitDecision, ExecutionContext, list[str], _OutcomePort]:
    events: list[str] = []
    context = _context()
    request = _request(context=context)
    _seed_idempotency(context, request)
    selected_outcome_port = outcome_port or _OutcomePort(events)
    if outcome_port is not None:
        outcome_port.events = events
    if write_port is not None:
        write_port.events = events
    invocation = WriteExecutionCoordinator(
        write_port or _WritePort(events, result=raw_outcome),
        _EvidencePort(events),
    )
    executor = CommitAwareWriteExecutor(
        invocation,
        WriteOutcomeCoordinator(selected_outcome_port),
    )
    decision = asyncio.run(
        executor.execute(
            request=request,
            decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
            context=context,
            requested_at=NOW,
            recorded_at=NOW + timedelta(seconds=2),
        )
    )
    return decision, context, events, selected_outcome_port


def test_wrt_007_cmt_001_committed_result_preserves_commit_state() -> None:
    context = _context()
    request = _request(context=context)
    _seed_idempotency(context, request)
    events: list[str] = []
    outcome_port = _OutcomePort(events)
    executor = CommitAwareWriteExecutor(
        WriteExecutionCoordinator(
            _WritePort(events, result=_committed(request)),
            _EvidencePort(events),
        ),
        WriteOutcomeCoordinator(outcome_port),
    )

    decision = asyncio.run(
        executor.execute(
            request=request,
            decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
            context=context,
            requested_at=NOW,
            recorded_at=NOW + timedelta(seconds=2),
        )
    )

    assert decision.execution_status is ExecutionStatus.COMPLETED
    assert decision.write_summary.commit_state is CommitState.COMMITTED
    assert decision.outcome.result.commit_state is CommitState.COMMITTED
    assert decision.write_summary.business_reference == "DRAFT-0054"
    assert decision.write_summary.provider_transaction_id == "tx-0054"
    assert decision.automatic_retry_allowed is False
    assert decision.reconciliation_required is False
    assert events == ["WRITE_REQUESTED", "PROVIDER_WRITE", "WRITE_OUTCOME"]
    assert outcome_port.records[0].commit_state is CommitState.COMMITTED
    assert isinstance(context.writes.entries[0], WriteJournalEntry)


def test_wrt_007_cmt_001_confirmed_rejection_is_not_committed() -> None:
    context = _context()
    request = _request(context=context)
    _seed_idempotency(context, request)
    events: list[str] = []
    executor = CommitAwareWriteExecutor(
        WriteExecutionCoordinator(
            _WritePort(events, result=_not_committed(request)),
            _EvidencePort(events),
        ),
        WriteOutcomeCoordinator(_OutcomePort(events)),
    )

    decision = asyncio.run(
        executor.execute(
            request=request,
            decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
            context=context,
            requested_at=NOW,
            recorded_at=NOW + timedelta(seconds=2),
        )
    )

    assert decision.execution_status is ExecutionStatus.FAILED
    assert decision.write_summary.commit_state is CommitState.NOT_COMMITTED
    assert decision.outcome.reason_code == "BUSINESS_PRECONDITION_REJECTED"
    assert decision.reconciliation_required is False
    assert decision.automatic_retry_allowed is False


def test_cmt_002_003_unknown_is_not_failure_and_requires_reconciliation() -> None:
    context = _context()
    request = _request(context=context)
    _seed_idempotency(context, request)
    events: list[str] = []
    executor = CommitAwareWriteExecutor(
        WriteExecutionCoordinator(
            _WritePort(events, result=_unknown(request)),
            _EvidencePort(events),
        ),
        WriteOutcomeCoordinator(_OutcomePort(events)),
    )

    decision = asyncio.run(
        executor.execute(
            request=request,
            decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
            context=context,
            requested_at=NOW,
            recorded_at=NOW + timedelta(seconds=2),
        )
    )

    assert decision.execution_status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert decision.write_summary.commit_state is CommitState.UNKNOWN_COMMIT_STATE
    assert decision.reconciliation_required is True
    assert decision.automatic_retry_allowed is False
    assert decision.execution_status is not ExecutionStatus.FAILED


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError("timeout"), "PROVIDER_TIMEOUT"),
        (ConnectionError("connection lost"), "PROVIDER_CONNECTION_LOST"),
        (asyncio.CancelledError(), "PROVIDER_CANCELLED"),
        (RuntimeError("provider exploded"), "PROVIDER_EXCEPTION"),
    ],
)
def test_wrt_008_uncertain_provider_exception_is_never_retried(error, reason) -> None:
    events: list[str] = []
    port = _ExceptionalWritePort(events, error)
    decision, context, scenario_events, outcome_port = _commit_aware_execute(
        object(),
        write_port=port,
    )

    assert port.calls == 1
    assert scenario_events == [
        "WRITE_REQUESTED",
        "PROVIDER_WRITE",
        "WRITE_OUTCOME",
    ]
    assert decision.execution_status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert decision.write_summary.commit_state is CommitState.UNKNOWN_COMMIT_STATE
    assert decision.outcome.reason_code == reason
    assert decision.automatic_retry_allowed is False
    assert len(context.writes.entries) == 1
    assert outcome_port.records[0].commit_state is CommitState.UNKNOWN_COMMIT_STATE


def test_pre_provider_persistence_failure_is_not_commit_unknown() -> None:
    events: list[str] = []
    context = _context()
    request = _request(context=context)
    _seed_idempotency(context, request)
    outcome_port = _OutcomePort(events)
    executor = CommitAwareWriteExecutor(
        WriteExecutionCoordinator(
            _WritePort(events, result=_committed(request)),
            _EvidencePort(events, "raise"),
        ),
        WriteOutcomeCoordinator(outcome_port),
    )

    with pytest.raises(OSError, match="durable storage unavailable"):
        asyncio.run(
            executor.execute(
                request=request,
                decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
                context=context,
                requested_at=NOW,
                recorded_at=NOW + timedelta(seconds=2),
            )
        )

    assert events == ["WRITE_REQUESTED"]
    assert outcome_port.records == []
    assert context.writes.entries == []
    assert context.checkpoint.write_request_durable is False


def test_malformed_provider_acknowledgement_is_commit_unknown() -> None:
    decision, _, events, _ = _commit_aware_execute({"status": "ok"})

    assert events == ["WRITE_REQUESTED", "PROVIDER_WRITE", "WRITE_OUTCOME"]
    assert decision.execution_status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert decision.outcome.reason_code == "PROVIDER_ACKNOWLEDGEMENT_MALFORMED"


def test_cmt_004_unknown_commit_preserves_key_and_blocks_new_key_retry() -> None:
    context = _context()
    request = _request(context=context)
    _seed_idempotency(context, request)
    original_keys = dict(context.idempotency.keys_by_write_node)
    events: list[str] = []
    executor = CommitAwareWriteExecutor(
        WriteExecutionCoordinator(
            _WritePort(events, result=_unknown(request)),
            _EvidencePort(events),
        ),
        WriteOutcomeCoordinator(_OutcomePort(events)),
    )

    decision = asyncio.run(
        executor.execute(
            request=request,
            decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
            context=context,
            requested_at=NOW,
            recorded_at=NOW + timedelta(seconds=2),
        )
    )

    assert context.idempotency.keys_by_write_node == original_keys
    assert decision.automatic_retry_allowed is False
    assert len([event for event in events if event == "PROVIDER_WRITE"]) == 1


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("raise", OSError),
        ("wrong_type", WriteOutcomeError),
        ("mismatch", WriteOutcomeError),
        ("predates", WriteOutcomeError),
    ],
)
def test_outcome_persistence_failure_prevents_journal_mutation(mode, error) -> None:
    events: list[str] = []
    context = _context()
    request = _request(context=context)
    _seed_idempotency(context, request)
    executor = CommitAwareWriteExecutor(
        WriteExecutionCoordinator(
            _WritePort(events, result=_committed(request)),
            _EvidencePort(events),
        ),
        WriteOutcomeCoordinator(_OutcomePort(events, mode)),
    )

    with pytest.raises(error):
        asyncio.run(
            executor.execute(
                request=request,
                decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
                context=context,
                requested_at=NOW,
                recorded_at=NOW + timedelta(seconds=2),
            )
        )

    assert context.writes.entries == []


def test_outcome_identity_and_context_mismatch_fail_before_persistence() -> None:
    request = _request()
    wrong_identity = replace(
        _outcome_identity(request),
        business_operation_id=BusinessOperationId("business-other"),
    )
    outcome = WriteCommitUnknown(
        wrong_identity,
        "UNKNOWN",
        "provider outcome is uncertain",
    )
    port = _OutcomePort([])
    coordinator = WriteOutcomeCoordinator(port)
    context = _context()
    _seed_idempotency(context, request)

    with pytest.raises(WriteOutcomeError, match="identity mismatch"):
        asyncio.run(
            coordinator.finalize(
                request=request,
                context=context,
                requested_record_hash=WriteAttemptRecord.from_request(
                    request,
                    NOW,
                ).record_hash,
                outcome=outcome,
                recorded_at=NOW + timedelta(seconds=2),
            )
        )
    assert port.records == []


@pytest.mark.parametrize(
    ("context", "message"),
    [
        (_context(execution_id="exec-other"), "execution"),
        (_context(tenant_id="tenant-other"), "tenant"),
        (_context(business_operation_id="business-other"), "business operation"),
    ],
)
def test_outcome_context_identity_mismatch_fails_closed(context, message) -> None:
    request = _request()
    _seed_idempotency(context, request)
    coordinator = WriteOutcomeCoordinator(_OutcomePort([]))

    with pytest.raises(WriteOutcomeError, match=message):
        asyncio.run(
            coordinator.finalize(
                request=request,
                context=context,
                requested_record_hash=WriteAttemptRecord.from_request(
                    request,
                    NOW,
                ).record_hash,
                outcome=_unknown(request),
                recorded_at=NOW + timedelta(seconds=2),
            )
        )


def test_outcome_requires_exact_existing_idempotency_state() -> None:
    request = _request()
    context = _context()
    coordinator = WriteOutcomeCoordinator(_OutcomePort([]))

    with pytest.raises(WriteOutcomeError, match="idempotency state"):
        asyncio.run(
            coordinator.finalize(
                request=request,
                context=context,
                requested_record_hash=WriteAttemptRecord.from_request(
                    request,
                    NOW,
                ).record_hash,
                outcome=_unknown(request),
                recorded_at=NOW + timedelta(seconds=2),
            )
        )


def test_commit_decision_rejects_inconsistent_states_and_retry() -> None:
    request = _request()
    outcome = _unknown(request)
    summary = WriteSummary(
        CommitState.UNKNOWN_COMMIT_STATE,
        request.tool_id,
        request.node_id,
        request.idempotency_key,
        request.payload_hash,
    )
    valid = WriteCommitDecision(
        ExecutionStatus.RECONCILIATION_REQUIRED,
        outcome,
        summary,
        False,
        True,
    )

    with pytest.raises(WriteOutcomeError, match="retry"):
        replace(valid, automatic_retry_allowed=True)
    with pytest.raises(WriteOutcomeError, match="reconciliation"):
        replace(valid, reconciliation_required=False)
    with pytest.raises(WriteOutcomeError, match="commit states"):
        replace(
            valid,
            write_summary=replace(summary, commit_state=CommitState.COMMITTED),
            reconciliation_required=False,
        )
    with pytest.raises(WriteOutcomeError, match="execution status"):
        replace(valid, execution_status=ExecutionStatus.FAILED)
    with pytest.raises(TypeError, match="ExecutionStatus"):
        replace(valid, execution_status="RECONCILIATION_REQUIRED")
    with pytest.raises(TypeError, match="WriteExecutionOutcome"):
        replace(valid, outcome=object())
    with pytest.raises(TypeError, match="WriteSummary"):
        replace(valid, write_summary=object())
    with pytest.raises(TypeError, match="automatic_retry_allowed"):
        replace(valid, automatic_retry_allowed=1)
    with pytest.raises(TypeError, match="reconciliation_required"):
        replace(valid, reconciliation_required=1)


def test_outcome_models_reject_malformed_values() -> None:
    request = _request()
    identity = _outcome_identity(request)
    committed = _committed(request)
    unknown = _unknown(request)
    record = WriteOutcomeRecord(
        requested_record_hash=WriteAttemptRecord.from_request(request, NOW).record_hash,
        identity=identity,
        commit_state=CommitState.UNKNOWN_COMMIT_STATE,
        reason_code=unknown.reason_code,
        provider_transaction_id=unknown.provider_transaction_id,
        business_reference=None,
        evidence_refs=unknown.evidence_refs,
        recorded_at=NOW + timedelta(seconds=2),
    )
    receipt = WriteOutcomeReceipt(
        record.record_hash,
        "write-outcome:0054",
        NOW + timedelta(seconds=2),
    )

    with pytest.raises(TypeError, match="request"):
        WriteOutcomeIdentity.from_request(object())  # type: ignore[arg-type]
    with pytest.raises(WriteOutcomeError, match="idempotency_key"):
        replace(identity, idempotency_key=IdempotencyKey("bad"))
    with pytest.raises(TypeError, match="result"):
        WriteCommitted(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="identity"):
        replace(unknown, identity=object())
    with pytest.raises(WriteExecutionError, match="non-empty"):
        replace(unknown, reason_code="")
    with pytest.raises(TypeError, match="evidence_refs"):
        replace(unknown, evidence_refs=["bad"])
    with pytest.raises(ValueError, match="credential"):
        replace(unknown, evidence_refs=("api_key=secret",))
    with pytest.raises(TypeError, match="value"):
        replace(committed.result, value=object())
    with pytest.raises(TypeError, match="identity"):
        replace(committed.result, identity=object())
    with pytest.raises(TypeError, match="identity"):
        replace(_not_committed(request), identity=object())
    with pytest.raises(TypeError, match="identity"):
        replace(record, identity=object())
    with pytest.raises(TypeError, match="CommitState"):
        replace(record, commit_state="UNKNOWN_COMMIT_STATE")
    with pytest.raises(WriteExecutionError, match="SHA-256"):
        replace(receipt, record_hash="bad")
    with pytest.raises(TypeError, match="record"):
        WriteJournalEntry(object(), "ref")  # type: ignore[arg-type]


def test_outcome_coordinators_reject_wrong_types() -> None:
    with pytest.raises(TypeError, match="record_outcome"):
        WriteOutcomeCoordinator(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="invocation"):
        CommitAwareWriteExecutor(object(), WriteOutcomeCoordinator(_OutcomePort([])))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="outcomes"):
        CommitAwareWriteExecutor(
            WriteExecutionCoordinator(_WritePort([]), _EvidencePort([])),
            object(),  # type: ignore[arg-type]
        )

    request = _request()
    context = _context()
    _seed_idempotency(context, request)
    coordinator = WriteOutcomeCoordinator(_OutcomePort([]))
    values = {
        "request": request,
        "context": context,
        "requested_record_hash": WriteAttemptRecord.from_request(request, NOW).record_hash,
        "outcome": _unknown(request),
        "recorded_at": NOW + timedelta(seconds=2),
    }
    for field in ("request", "context", "outcome"):
        malformed = dict(values)
        malformed[field] = object()
        with pytest.raises(TypeError, match=field):
            asyncio.run(coordinator.finalize(**malformed))  # type: ignore[arg-type]
