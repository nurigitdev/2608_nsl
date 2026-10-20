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
    IdempotencyKey,
    PayloadHash,
    RiskLevel,
    STRING,
    ToolCapability,
    ValueEnvelope,
    VerificationStatus,
    domain,
)
from nsl.runtime_models import ExecutionContext, ExecutionRequest
from nsl.security import DataHandlingPolicy
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
from nsl.verification import (
    PostWriteVerifier,
    VerificationError,
    VerificationObservation,
    VerificationRequest,
    VerificationResult,
)
from nsl.vertical_slice import build_principal, build_tool_catalog
from nsl.write_execution import (
    WriteCommitUnknown,
    WriteOutcomeIdentity,
    WriteResultEnvelope,
)


NOW = datetime(2026, 10, 20, 5, 0, tzinfo=UTC)


def _identity(**changes: object) -> WriteOutcomeIdentity:
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "business_operation_id": BusinessOperationId("business-0057"),
        "invocation_id": "invoke-0057",
        "node_id": "write0001",
        "tool_id": "FINANCE.CREATE_DRAFT",
        "idempotency_key": IdempotencyKey("idem:sha256:" + "d" * 64),
        "payload_hash": PayloadHash("sha256:" + "e" * 64),
        "approval_request_id": ApprovalRequestId("approval-request-0057"),
        "write_intent_hash": "sha256:" + "f" * 64,
    }
    values.update(changes)
    return WriteOutcomeIdentity(**values)  # type: ignore[arg-type]


def _committed(**changes: object) -> WriteResultEnvelope:
    values: dict[str, object] = {
        "identity": _identity(),
        "value": ValueEnvelope.complete(
            {"draft_id": "DRAFT-0057", "amount": Decimal("84000")},
            domain("DraftRecord"),
            DataClassification.CONFIDENTIAL,
        ),
        "business_reference": "DRAFT-0057",
        "provider_transaction_id": "tx-0057",
        "committed_at": NOW - timedelta(seconds=2),
        "evidence_refs": ("write:commit:0057",),
    }
    values.update(changes)
    return WriteResultEnvelope(**values)  # type: ignore[arg-type]


def _verification_hint(**changes: object) -> PostWriteVerification:
    values: dict[str, object] = {
        "tool_id": "FINANCE.GET_DRAFT",
        "reference_schema": STRING,
        "consistency_hint": "READ_AFTER_WRITE_STRONG",
    }
    values.update(changes)
    return PostWriteVerification(**values)  # type: ignore[arg-type]


def _contract(
    *,
    verification: PostWriteVerification | None = None,
    tool_id: str = "FINANCE.CREATE_DRAFT",
) -> ToolContract:
    return ToolContract(
        tool_id=tool_id,
        version="1.0.0",
        capability=ToolCapability.WRITE,
        input_types=(("detail", STRING),),
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
            verification=verification,
        ),
    )


def _context(
    identity: WriteOutcomeIdentity | None = None,
    *,
    tenant_id: str = "tenant-a",
    business_operation_id: str = "business-0057",
) -> ExecutionContext:
    selected = identity or _identity()
    skill = NslCompiler(build_tool_catalog()).compile(
        '''language NSL "0.1";
skill TEST.VERIFICATION_CONTEXT {
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
    context = ExecutionContext(
        skill,
        ExecutionRequest(
            execution_id="exec-0057",
            inputs={},
            runtime_context={},
            principal=build_principal(tenant_id=tenant_id),
            data_policy=DataHandlingPolicy(),
            business_operation_id=BusinessOperationId(business_operation_id),
        ),
    )
    context.idempotency.keys_by_write_node[selected.node_id] = (
        selected.idempotency_key
    )
    context.idempotency.payload_hashes_by_write_node[selected.node_id] = (
        selected.payload_hash
    )
    return context


class _ReadPort:
    def __init__(
        self,
        *,
        matched: bool = True,
        reason_code: str | None = None,
        mode: str = "valid",
    ) -> None:
        self.matched = matched
        self.reason_code = reason_code
        self.mode = mode
        self.requests: list[VerificationRequest] = []

    async def read(self, request: VerificationRequest):
        self.requests.append(request)
        if self.mode == "timeout":
            raise TimeoutError("verification timed out")
        if self.mode == "dependency":
            raise ConnectionError("verification backend unavailable")
        if self.mode == "cancelled":
            raise asyncio.CancelledError()
        if self.mode == "malformed":
            return {"matched": True}
        observation = VerificationObservation(
            request_hash=request.request_hash,
            business_reference=request.business_reference,
            matched=self.matched,
            evidence_ref="verification:0057",
            observed_at=NOW,
            reason_code=self.reason_code,
        )
        if self.mode == "wrong_hash":
            return replace(observation, request_hash="sha256:" + "0" * 64)
        if self.mode == "wrong_reference":
            return replace(observation, business_reference="DRAFT-OTHER")
        return observation


def _verify(
    *,
    result: WriteResultEnvelope | None = None,
    contract: ToolContract | None = None,
    context: ExecutionContext | None = None,
    port=None,
) -> VerificationResult:
    selected_result = result or _committed()
    return asyncio.run(
        PostWriteVerifier().verify(
            selected_result,
            contract or _contract(verification=_verification_hint()),
            context or _context(selected_result.identity),
            port or _ReadPort(),
        )
    )


def test_vfy_001_002_committed_business_reference_drives_read_only_method() -> None:
    port = _ReadPort()
    result = _verify(port=port)

    assert result.status is VerificationStatus.VERIFIED
    assert result.commit_state is CommitState.COMMITTED
    assert result.business_reference == "DRAFT-0057"
    assert result.verification_tool_id == "FINANCE.GET_DRAFT"
    assert result.evidence_ref == "verification:0057"
    assert len(port.requests) == 1
    request = port.requests[0]
    assert request.business_reference == "DRAFT-0057"
    assert request.identity == _identity()
    assert request.reference_schema == STRING
    assert request.consistency_hint == "READ_AFTER_WRITE_STRONG"
    assert not hasattr(port, "execute")


@pytest.mark.parametrize(
    ("matched", "reason", "status"),
    [
        (True, None, VerificationStatus.VERIFIED),
        (False, "PROVIDER_STATE_MISMATCH", VerificationStatus.FAILED),
    ],
)
def test_vfy_003_verification_outcome_is_typed_and_independent(
    matched,
    reason,
    status,
) -> None:
    result = _verify(port=_ReadPort(matched=matched, reason_code=reason))

    assert result.status is status
    assert result.reason_code == reason
    assert result.commit_state is CommitState.COMMITTED


@pytest.mark.parametrize(
    ("mode", "reason_code"),
    [
        ("timeout", "VERIFICATION_TIMEOUT"),
        ("dependency", "VERIFICATION_DEPENDENCY_FAILURE"),
        ("malformed", "VERIFICATION_RESPONSE_MALFORMED"),
        ("wrong_hash", "VERIFICATION_BINDING_MISMATCH"),
        ("wrong_reference", "VERIFICATION_BINDING_MISMATCH"),
    ],
)
def test_vfy_003_unknown_normalization_preserves_committed_state(
    mode,
    reason_code,
) -> None:
    result = _verify(port=_ReadPort(mode=mode))

    assert result.status is VerificationStatus.UNKNOWN
    assert result.reason_code == reason_code
    assert result.commit_state is CommitState.COMMITTED
    assert result.evidence_ref is None


def test_vfy_005_failed_and_unknown_never_reclassify_commit() -> None:
    failed = _verify(
        port=_ReadPort(
            matched=False,
            reason_code="PROVIDER_STATE_MISMATCH",
        )
    )
    unknown = _verify(port=_ReadPort(mode="timeout"))

    assert failed.commit_state is CommitState.COMMITTED
    assert unknown.commit_state is CommitState.COMMITTED
    with pytest.raises(VerificationError, match="cannot change COMMITTED"):
        replace(failed, commit_state=CommitState.NOT_COMMITTED)


@pytest.mark.parametrize(
    "verification",
    [
        None,
        PostWriteVerification(reference_schema=STRING),
        PostWriteVerification(consistency_hint="EVENTUAL"),
    ],
)
def test_no_executable_verification_method_returns_not_requested(
    verification,
) -> None:
    result = _verify(
        contract=_contract(verification=verification),
        port=None,
    )

    assert result.status is VerificationStatus.NOT_REQUESTED
    assert result.commit_state is CommitState.COMMITTED
    assert result.verification_tool_id is None
    assert result.reason_code is None


def test_verification_requires_a_read_only_port_only_when_method_is_declared() -> None:
    committed = _committed()
    with pytest.raises(TypeError, match="read-only"):
        asyncio.run(
            PostWriteVerifier().verify(
                committed,
                _contract(verification=_verification_hint()),
                _context(committed.identity),
                object(),  # type: ignore[arg-type]
            )
        )


def test_verification_cancellation_is_not_hidden_as_business_unknown() -> None:
    with pytest.raises(asyncio.CancelledError):
        _verify(port=_ReadPort(mode="cancelled"))


@pytest.mark.parametrize(
    ("result", "contract", "context", "error", "message"),
    [
        (object(), _contract(), _context(), TypeError, "WriteResultEnvelope"),
        (_committed(), object(), _context(), TypeError, "ToolContract"),
        (_committed(), _contract(), object(), TypeError, "ExecutionContext"),
        (
            _committed(),
            build_tool_catalog().get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0"),
            _context(),
            VerificationError,
            "canonical WRITE contract",
        ),
        (
            _committed(),
            _contract(tool_id="FINANCE.OTHER_WRITE"),
            _context(),
            VerificationError,
            "canonical WRITE contract",
        ),
        (
            _committed(),
            _contract(),
            _context(tenant_id="tenant-b"),
            VerificationError,
            "context",
        ),
        (
            _committed(),
            _contract(),
            _context(business_operation_id="business-other"),
            VerificationError,
            "context",
        ),
    ],
)
def test_verification_rejects_invalid_commit_contract_and_context_bindings(
    result,
    contract,
    context,
    error,
    message,
) -> None:
    with pytest.raises(error, match=message):
        asyncio.run(
            PostWriteVerifier().verify(
                result,
                contract,
                context,
                None,
            )
        )


@pytest.mark.parametrize("state_field", ["key", "payload"])
def test_verification_rejects_current_idempotency_state_mismatch(
    state_field: str,
) -> None:
    committed = _committed()
    context = _context(committed.identity)
    if state_field == "key":
        context.idempotency.keys_by_write_node[committed.identity.node_id] = (
            IdempotencyKey("idem:sha256:" + "0" * 64)
        )
    else:
        context.idempotency.payload_hashes_by_write_node[
            committed.identity.node_id
        ] = PayloadHash("sha256:" + "0" * 64)

    with pytest.raises(VerificationError, match="idempotency state"):
        _verify(result=committed, context=context)


def test_vfy_001_business_reference_is_required_for_committed_result() -> None:
    with pytest.raises(Exception, match="business_reference"):
        _committed(business_reference="")


def test_verification_request_hash_binds_every_commit_and_method_identity() -> None:
    result = _committed()
    baseline = PostWriteVerifier._request(result, _verification_hint())
    assert baseline.request_hash == baseline.request_hash

    variants = (
        replace(baseline, business_reference="DRAFT-OTHER"),
        replace(baseline, verification_tool_id="FINANCE.GET_OTHER"),
        replace(baseline, provider_transaction_id="tx-other"),
        replace(baseline, consistency_hint="EVENTUAL"),
        replace(baseline, reference_schema=domain("OtherReference")),
        replace(
            baseline,
            identity=replace(baseline.identity, invocation_id="invoke-other"),
        ),
    )
    assert len({baseline.request_hash, *(item.request_hash for item in variants)}) == (
        1 + len(variants)
    )


def test_verification_request_and_result_reject_invalid_typed_identities() -> None:
    request = PostWriteVerifier._request(_committed(), _verification_hint())
    with pytest.raises(TypeError, match="WriteOutcomeIdentity"):
        replace(request, identity=object())
    with pytest.raises(TypeError, match="TypeRef"):
        replace(request, reference_schema=object())

    result = _verify()
    with pytest.raises(TypeError, match="WriteOutcomeIdentity"):
        replace(result, identity=object())


@pytest.mark.parametrize(
    ("changes", "error", "message"),
    [
        ({"request_hash": "bad"}, VerificationError, "SHA-256"),
        ({"business_reference": ""}, VerificationError, "business_reference"),
        ({"matched": 1}, TypeError, "Bool"),
        ({"evidence_ref": ""}, VerificationError, "evidence_ref"),
        ({"observed_at": "now"}, TypeError, "datetime"),
        (
            {"observed_at": NOW.replace(tzinfo=None)},
            VerificationError,
            "timezone-aware UTC",
        ),
        (
            {"observed_at": NOW.astimezone(timezone(timedelta(hours=9)))},
            VerificationError,
            "timezone-aware UTC",
        ),
        ({"matched": True, "reason_code": "FAIL"}, VerificationError, "cannot"),
        ({"matched": False, "reason_code": None}, VerificationError, "reason_code"),
    ],
)
def test_verification_observation_rejects_malformed_boundaries(
    changes,
    error,
    message,
) -> None:
    values: dict[str, object] = {
        "request_hash": "sha256:" + "a" * 64,
        "business_reference": "DRAFT-0057",
        "matched": True,
        "evidence_ref": "verification:0057",
        "observed_at": NOW,
        "reason_code": None,
    }
    values.update(changes)
    with pytest.raises(error, match=message):
        VerificationObservation(**values)  # type: ignore[arg-type]


def test_verification_result_is_immutable_and_rejects_inconsistent_states() -> None:
    verified = _verify()
    with pytest.raises(FrozenInstanceError):
        verified.status = VerificationStatus.FAILED  # type: ignore[misc]
    with pytest.raises(TypeError, match="VerificationStatus"):
        replace(verified, status="VERIFIED")
    with pytest.raises(VerificationError, match="no failure reason"):
        replace(verified, reason_code="FAIL")
    with pytest.raises(VerificationError, match="requires evidence"):
        replace(verified, evidence_ref=None)

    not_requested = _verify(contract=_contract())
    with pytest.raises(VerificationError, match="cannot contain"):
        replace(not_requested, evidence_ref="unexpected")

    unknown = _verify(port=_ReadPort(mode="timeout"))
    with pytest.raises(VerificationError, match="requires reason_code"):
        replace(unknown, reason_code=None)
    with pytest.raises(VerificationError, match="verification_tool_id"):
        replace(unknown, verification_tool_id=None)


def test_noncommitted_outcome_cannot_enter_post_write_verifier() -> None:
    unknown = WriteCommitUnknown(
        identity=_identity(),
        reason_code="PROVIDER_TIMEOUT",
        message="commit outcome is unknown",
    )
    with pytest.raises(TypeError, match="WriteResultEnvelope"):
        asyncio.run(
            PostWriteVerifier().verify(
                unknown,  # type: ignore[arg-type]
                _contract(verification=_verification_hint()),
                _context(),
                _ReadPort(),
            )
        )
