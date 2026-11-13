"""Collect Controlled WRITE and Trust observations in the frozen Oracle worker."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import asdict, fields, is_dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
import json

from nsl.approval import ApprovalEvidenceValidator, ApprovalResumeBindings, RecordedApprovalClock
from nsl.core import ApprovalDecision, ApprovalStatus, CommitState, Money, PayloadHash, TypeRef, encode_value
from nsl.idempotency import IdempotencyConflictError, IdempotencyCoordinator
from nsl.validation import PreWriteEvaluator, WriteEligibilityError, WriteEligibilityGate
from nsl.write_replay_execution import ReplayWriteSession
from tests import test_v0_2_approval_resume_validation as approval_fixture
from tests import test_v0_2_idempotency as idempotency_fixture
from tests import test_v0_2_post_write_verification as verification_fixture
from tests import test_v0_2_prewrite_eligibility as eligibility_fixture
from tests import test_v0_2_write_execution as commit_fixture
from tests import test_v0_2_write_replay_evidence as replay_fixture


def fixture_data(value):
    """Project Oracle test inputs onto JSON values and preserve tagged NSL values."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, TypeRef):
        return value.to_data()
    if isinstance(value, Money):
        return encode_value(value)
    if isinstance(value, Mapping):
        return {key: fixture_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [fixture_data(item) for item in value]
    if isinstance(value, frozenset):
        return sorted(fixture_data(item) for item in value)
    if is_dataclass(value):
        return {field.name: fixture_data(getattr(value, field.name)) for field in fields(value)}
    return encode_value(value)


class WriteCases:
    def __init__(self, corpus, json_bytes):
        self.corpus = corpus
        self.json_bytes = json_bytes

    def add(
        self, category, case_id, *, given, observed, requirements, references,
        status=None, error=None, inputs=None, hashes=None, notes="",
    ):
        self.corpus.add(
            category, case_id, contract_refs=requirements, source_refs=references,
            inputs={"scenario": ("scenario.json", self.json_bytes(fixture_data(given))), **(inputs or {})},
            artifacts={"observation": ("observation.json", self.json_bytes(fixture_data(observed)))},
            status=status, error=error, hashes=hashes, notes=notes,
        )


def approval_cases(cases):
    context = approval_fixture._context()
    base_evidence = approval_fixture._evidence(context)
    base_bindings = ApprovalResumeBindings(**{
        field.name: getattr(base_evidence, field.name) for field in fields(ApprovalResumeBindings)
    })
    scenarios = (
        ("valid", "APPROVED", "VALID", None, "ALLOWED", None, "002"),
        ("missing", "NOT_REQUESTED", None, None, "REQUIRES_APPROVAL", "APPROVAL_REQUIRED", "003"),
        ("rejected", "REJECTED", "VALID", "APPROVAL_REJECTED", "BLOCKED", "APPROVAL_REJECTED", "004"),
        ("expired", "EXPIRED", "EXPIRED", "APPROVAL_EXPIRED", "REQUIRES_APPROVAL", "APPROVAL_REQUIRED", "004"),
        ("payload-mismatch", "PENDING", "PAYLOAD_MISMATCH", "APPROVAL_PAYLOAD_MISMATCH", "BLOCKED", "WRITE_BINDING_INVALID", "005"),
        ("intent-mismatch", "PENDING", "INTENT_MISMATCH", "APPROVAL_INTENT_MISMATCH", "BLOCKED", "WRITE_BINDING_INVALID", "005"),
    )
    for name, status, validation, reason, eligibility, guard_reason, req in scenarios:
        evidence = base_evidence
        bindings = base_bindings
        now = approval_fixture.NOW
        if name == "missing":
            evidence = None
        elif name == "rejected":
            evidence = replace(evidence, decision=ApprovalDecision.REJECTED)
        elif name == "expired":
            now = evidence.expires_at
        elif name == "payload-mismatch":
            bindings = replace(bindings, payload_hash=PayloadHash("sha256:" + "d" * 64))
        elif name == "intent-mismatch":
            bindings = replace(bindings, write_intent_hash="recalculated-intent")
        result = None if evidence is None else ApprovalEvidenceValidator().validate(
            evidence, bindings, approval_fixture.RecordingVerifier(), RecordedApprovalClock(now),
        )
        passed = result is not None and result.approval_gate_passed
        assert passed is (name == "valid")
        observed_status = result.approval_status if result else ApprovalStatus.NOT_REQUESTED
        assert observed_status == status
        assert (result.validation if result else None) == validation
        assert (result.reason_code if result else None) == reason
        controls = eligibility_fixture._allowed_evidence(
            approval_status=observed_status,
            approval_evidence_valid=passed,
            intent_binding_valid=name not in {"payload-mismatch", "intent-mismatch"},
        )
        guard = PreWriteEvaluator().evaluate(controls)
        assert (guard.eligibility, guard.reason_code) == (eligibility, guard_reason)
        try:
            WriteEligibilityGate.require_allowed(guard)
        except WriteEligibilityError as error:
            assert not passed and str(error) == f"WRITE blocked: {guard_reason}"
            gate_message = str(error)
        else:
            assert passed
            gate_message = None
        cases.add(
            "approval", f"approval-{name}",
            given={"evidence": evidence, "bindings": bindings, "checked_at": now,
                   "mock_verifier_result": True, "validation": controls.validation,
                   "other_controls": {key: value for key, value in asdict(controls).items()
                                      if key not in {"approval_status", "approval_evidence_valid", "validation"}}},
            observed={"approval_validation": result, "approval_gate_passed": passed,
                      "requires_reapproval": result.requires_reapproval if result else False,
                      "write_guard": guard, "write_gate_error": gate_message},
            requirements=[f"NSL-RUST-GATE-{req}", "NSL-RUST-WRITE-008"],
            references=["tests/test_v0_2_approval_resume_validation.py", "tests/test_v0_2_prewrite_eligibility.py"],
            status=guard.eligibility,
            error={"code": reason, "phase": "approval_validation"} if reason else None,
            notes="Validate supplied proof with a recorded clock and a mock verifier, then evaluate WRITE eligibility.",
        )


def idempotency_cases(cases):
    fixture = idempotency_fixture
    for conflict in (False, True):
        first_intent = fixture._intent()
        second_intent = fixture._intent(amount="84001") if conflict else first_intent
        contract = fixture._contract()
        provider_evidence = fixture._provider_evidence(contract)
        port = fixture._DurableIdempotencyPort()
        coordinator = IdempotencyCoordinator(port)
        first = fixture._reserve(coordinator, intent=first_intent, contract=contract)
        key = first.evidence.idempotency_key
        result_ref = "result:expense-draft-42"
        port.complete(key, result_ref)
        try:
            second = fixture._reserve(coordinator, intent=second_intent, contract=contract)
        except IdempotencyConflictError as error:
            assert conflict and error.detail_code == "IDEMPOTENCY_CONFLICT"
            disposition = "CONFLICT"
            second_result = None
            expected_error = {"code": error.detail_code, "phase": "idempotency_reservation"}
        else:
            assert not conflict and second.disposition == "REUSED"
            assert second.evidence == first.evidence and second.business_result_ref == result_ref
            disposition = second.disposition
            second_result = second.business_result_ref
            expected_error = None
        assert first.disposition == "CREATED" and port.calls == 2
        assert port.records[key] == first.evidence
        cases.add(
            "idempotency", "idempotency-conflict" if conflict else "idempotency-duplicate",
            given={"intents": [first_intent, second_intent], "contract": contract.to_canonical_data(),
                   "provider_evidence": provider_evidence, "checked_at": fixture.NOW,
                   "required_until": fixture.NOW + timedelta(days=7), "original_result_ref": result_ref},
            observed={"first_disposition": first.disposition, "second_disposition": disposition,
                      "second_result_ref": second_result, "stored_reservation": port.records[key],
                      "reservation_calls": port.calls},
            requirements=["NSL-RUST-IDEM-002", "NSL-RUST-IDEM-005" if conflict else "NSL-RUST-IDEM-004"],
            references=["tests/test_v0_2_idempotency.py"], status=disposition, error=expected_error,
            notes="Reserve the first intent and record its result. Reserve the second intent in a fresh context with the same business identity.",
        )


def commit_cases(cases):
    fixture = commit_fixture
    request = fixture._request()
    scenarios = (
        ("committed", fixture._committed(request), "COMMITTED", "COMPLETED", None),
        ("not-committed", fixture._not_committed(request), "NOT_COMMITTED", "FAILED", "BUSINESS_PRECONDITION_REJECTED"),
        ("unknown", None, "UNKNOWN_COMMIT_STATE", "RECONCILIATION_REQUIRED", "PROVIDER_TIMEOUT"),
    )
    for name, response, state, status, reason in scenarios:
        port = fixture._ExceptionalWritePort([], TimeoutError("timeout")) if response is None else None
        decision, _, events, durable = fixture._commit_aware_execute(response, write_port=port)
        assert decision.write_summary.commit_state == state and decision.execution_status == status
        assert decision.automatic_retry_allowed is False
        assert decision.reconciliation_required is (response is None)
        assert getattr(decision.outcome, "reason_code", None) == reason
        assert events == ["WRITE_REQUESTED", "PROVIDER_WRITE", "WRITE_OUTCOME"]
        assert len(durable.records) == 1 and durable.records[0].commit_state == state
        cases.add(
            "write", f"commit-{name}",
            given={"request": request, "provider_response": response,
                   "provider_fault": "timeout" if response is None else None,
                   "requested_at": fixture.NOW, "outcome_recorded_at": fixture.NOW + timedelta(seconds=2)},
            observed={"write_summary": decision.write_summary, "reason_code": reason,
                      "execution_status": decision.execution_status, "events": events,
                      "provider_calls": events.count("PROVIDER_WRITE"),
                      "durable_outcome": durable.records[0], "durable_outcome_hash": durable.records[0].record_hash,
                      "automatic_retry_allowed": decision.automatic_retry_allowed,
                      "reconciliation_required": decision.reconciliation_required},
            requirements=["NSL-RUST-COMMIT-001", "NSL-RUST-COMMIT-003", "NSL-RUST-COMMIT-004"],
            references=["tests/test_v0_2_write_execution.py"], status=decision.execution_status,
            notes="Use the recorded provider response or inject a timeout after the durable WRITE_REQUESTED event.",
        )


def verification_cases(cases):
    fixture = verification_fixture
    for name, mode, matched, reason in (
        ("verified", "valid", True, None),
        ("failed", "valid", False, "PROVIDER_STATE_MISMATCH"),
        ("unknown", "timeout", True, "VERIFICATION_TIMEOUT"),
    ):
        port = fixture._ReadPort(mode=mode, matched=matched, reason_code=reason)
        committed = fixture._committed()
        contract = fixture._contract(verification=fixture._verification_hint())
        result = fixture._verify(result=committed, contract=contract, port=port)
        assert result.commit_state is CommitState.COMMITTED
        assert result.status == name.upper() and result.reason_code == reason and len(port.requests) == 1
        cases.add(
            "write", f"verification-{name}",
            given={"committed_result": committed, "contract": contract.to_canonical_data(),
                   "observation": {"matched": matched, "reason_code": reason, "observed_at": fixture.NOW}
                   if mode == "valid" else None, "read_fault": None if mode == "valid" else mode},
            observed={"read_request": port.requests[0], "result": result, "read_calls": len(port.requests)},
            requirements=["NSL-RUST-VER-002", "NSL-RUST-VER-003", "NSL-RUST-VER-004"],
            references=["tests/test_v0_2_post_write_verification.py"], status=result.status,
            notes="Run read-only post-WRITE verification on a confirmed commit.",
        )


def replay_case(cases):
    request, bundle, loader, _ = replay_fixture._scenario_bundle()
    principal = replay_fixture.build_principal(tenant_id=request.tenant_id)
    evidence = loader.load(bundle, principal)
    observations = []
    for _ in range(2):
        session = ReplayWriteSession.open(bundle, loader, principal)
        decision = asyncio.run(session.execute_write(request))
        session.assert_complete()
        assert decision.source == "RECORDED" and decision.side_effect_performed is False
        assert decision.automatic_retry_allowed is False and session.write_call_count == 1
        observations.append({"decision": decision, "verification": session.verification(),
                             "recorded_calls": session.write_call_count})
    assert observations[0] == observations[1]
    cases.add(
        "replay", "replay-no-live-write",
        given={"request": request, "principal": principal, "bundle": bundle.to_data(),
               "synthetic_snapshot_evidence": evidence, "repetitions": 2},
        observed={"runs": observations},
        requirements=["NSL-RUST-REPLAY-001", "NSL-RUST-REPLAY-002", "NSL-RUST-REPLAY-006"],
        references=["tests/test_v0_2_write_replay_evidence.py::test_rpl_201_203_repeated_replay_has_zero_live_write_calls"],
        status=observations[0]["decision"].source,
        hashes={"replay_manifest_hash": bundle.manifest_hash.removeprefix("sha256:")},
        notes="Use the synthetic protected snapshot contents for a recorded-only session with no live WRITE port.",
    )


def add_controlled_write_cases(corpus, oracle, json_bytes):
    from platform_write_cases import platform_cases

    cases = WriteCases(corpus, json_bytes)
    approval_cases(cases)
    idempotency_cases(cases)
    commit_cases(cases)
    verification_cases(cases)
    replay_case(cases)
    platform_cases(cases, oracle)
