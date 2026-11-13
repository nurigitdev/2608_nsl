"""Exercise frozen WRITE skills with deterministic, in-memory Platform ports."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from nsl.adapters.ed25519 import Ed25519PackageSigner, Ed25519TrustStore
from nsl.approval import (
    ApprovalEvidence, ApprovalEvidenceValidator, ApprovalResumeBindings, RecordedApprovalClock,
)
from nsl.audit import InMemoryAuditSink
from nsl.checkpoints import CheckpointCoordinator
from nsl.cli_config import load_tool_catalog_document
from nsl.cli_controlled import FileControlledStatePort, start_controlled_run
from nsl.compiler import NslCompiler
from nsl.core import ApprovalDecision, ApprovalRequestId, BusinessOperationId, ValueEnvelope
from nsl.idempotency import IdempotencyCoordinator, ProviderIdempotencyEvidence
from nsl.ir import NsoCodec, NsoIntegrityError, canonical_json
from nsl.nsp import NspBuilder
from nsl.nsp_verification import NspVerificationError, NspVerificationPolicy, NspVerifier
from nsl.preflight import ExecutionTrustProfile, TrustPreflightError
from nsl.runtime import ControlledExecutionPlan, ExecutionRequest, RuntimeEngine
from nsl.security import RuntimeEnvironment
from nsl.tools import MockToolExecutor
from nsl.validation import WriteGuard, WriteGuardRequest
from nsl.verification import PostWriteVerifier
from nsl.vertical_slice import build_principal
from nsl.write_execution import (
    TrustedCommitAwareWriteExecutor, TrustedWriteExecutionCoordinator, WriteCallRequestBuilder,
    WriteCommitted, WriteExecutionCoordinator, WriteOutcomeCoordinator, WriteOutcomeIdentity,
    WriteResultEnvelope,
)
from nsl.write_intent import canonical_write_arguments
from tests import test_v0_2_controlled_runtime as fixture


EXAMPLES = "examples/project_operations"
NOW = fixture.NOW
SUBJECTS = [f"subject-{index:02d}" for index in range(50)]
PROJECT = {"project_id": "project-rust-corpus", "project_code": "PROJECT-RUST"}
UPDATE_RESULT = {**PROJECT, "name": "Synthetic project", "status": "INACTIVE", "entity_version": 2}
BATCH_RESULT = {
    "project_id": PROJECT["project_id"], "member_role": "MEMBER", "operation_status": "COMPLETED",
    "requested_count": 50, "resolved_count": 50, "added_count": 50, "reactivated_count": 0,
    "unchanged_count": 0, "rejected_count": 0, "business_result_ref": "membership-result-1",
}


class Provider:
    def __init__(self, events, contract, result, business_reference):
        self.events = events
        self.contract = contract
        self.result = result
        self.business_reference = business_reference
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        self.events.append("PROVIDER_WRITE")
        return WriteCommitted(WriteResultEnvelope(
            identity=WriteOutcomeIdentity.from_request(request),
            value=ValueEnvelope.complete(self.result, self.contract.output_type, self.contract.output_classification),
            business_reference=self.business_reference,
            provider_transaction_id="mock-platform-tx-1",
            committed_at=NOW + timedelta(seconds=2),
            evidence_refs=("mock-platform-commit:1",),
        ))


def package_trust(nso, principal):
    # Public test vector seed used by the frozen Oracle tests. Export only the public key.
    signer = Ed25519PackageSigner("corpus-test-key", bytes(range(1, 33)))
    package = NspBuilder().build((nso,), signer=signer)
    keys = {signer.key_id: signer.public_key_bytes}
    verified = NspVerifier(
        policy=NspVerificationPolicy(RuntimeEnvironment.PRODUCTION),
        signature_verifier=Ed25519TrustStore(keys),
    ).verify(package.data)
    artifact, = verified.trusted_artifacts(
        publisher_id="corpus-test-publisher", trust_policy_id="corpus-test-policy", verified_at=NOW,
    )
    profile = ExecutionTrustProfile.production(
        tenant_id=principal.tenant_id, trust_policy_id="corpus-test-policy",
        expected_package_id=artifact.trust.package_id, expected_artifact_hash=artifact.artifact_hash,
        expected_semantic_hash=artifact.semantic_hash,
        trusted_publisher_ids=frozenset({artifact.trust.publisher_id}),
        trusted_signing_key_ids=frozenset(keys), max_trust_age_seconds=60,
    )
    return package, keys, artifact, profile


async def run_platform(skill, catalog, contract, request, artifact, profile, provider_result, business_reference):
    engine = RuntimeEngine(catalog)
    tools = MockToolExecutor(catalog, {})
    audit = InMemoryAuditSink()
    prefix = await engine.execute_controlled_prefix(skill, request, tools, audit)
    assert prefix.result.validation.status == "NO_CHECK" and prefix.checkpoint is not None
    context = CheckpointCoordinator().restore(prefix.checkpoint, skill, request)
    intent = await engine.prepare_controlled_write_intent(context, tools)
    plan = ControlledExecutionPlan.from_skill(skill)

    # The mock Platform supplies proof for the exact intent. Use the Oracle validator and guard.
    bindings = ApprovalResumeBindings(
        tenant_id=context.tenant_id, execution_id=request.execution_id,
        business_operation_id=intent.business_operation_id, approval_id=plan.approval.approval_id,
        approval_request_id=ApprovalRequestId("mock-platform-approval-1"),
        skill_semantic_hash=skill.semantic_hash, payload_hash=intent.payload_hash,
        write_intent_hash=intent.write_intent_hash,
    )
    approval = ApprovalEvidence(
        **asdict(bindings), decision=ApprovalDecision.APPROVED, approver_id="mock-platform-approver",
        decided_at=NOW, expires_at=NOW + timedelta(minutes=5), evidence_integrity="approval-signature:0063",
    )
    validation = ApprovalEvidenceValidator().validate(
        approval, bindings, fixture._ApprovalVerifier(), RecordedApprovalClock(NOW),
    )
    assert validation.approval_gate_passed
    context.approvals.records[plan.approval.approval_id] = validation.approval_status

    idempotency = fixture._IdempotencyPort()
    guarantee = ProviderIdempotencyEvidence(
        tool_id=contract.tool_id, contract_hash=contract.contract_hash, retention_policy="PROVIDER_DEFINED",
        provider_guarantee_ref="mock-platform-idempotency:1", verified_at=NOW,
        guaranteed_until=NOW + timedelta(days=30), durable_duplicate_guarantee=True,
    )
    reservation = await IdempotencyCoordinator(idempotency).reserve(
        intent=intent, context=context, contract=contract, provider_evidence=guarantee,
        now=NOW, required_until=NOW + timedelta(days=7),
    )
    contract_evidence = fixture._contract_evidence(contract)
    precondition = fixture._business_precondition(intent, contract)
    policy = fixture._policy_decision(intent, context, contract_evidence)
    controls = replace(fixture._execution_evidence(context), approval_status=validation.approval_status,
                       approval_evidence_valid=validation.approval_gate_passed)
    guard = WriteGuard().evaluate(WriteGuardRequest(
        intent, context, contract_evidence, controls, policy, precondition,
    ), now=NOW)
    assert guard.eligibility == "ALLOWED"
    call = WriteCallRequestBuilder().build(
        intent=intent, context=context, contract_evidence=contract_evidence, reservation=reservation,
        approval_request_id=approval.approval_request_id, invocation_id="mock-platform-invocation-1",
        business_preconditions=precondition,
    )

    events = []
    provider = Provider(events, contract, provider_result, business_reference)
    evidence = fixture._WriteEvidencePort(events)
    outcomes = fixture._OutcomePort()
    executor = TrustedCommitAwareWriteExecutor(
        TrustedWriteExecutionCoordinator(WriteExecutionCoordinator(provider, evidence)),
        WriteOutcomeCoordinator(outcomes),
    )
    execution = dict(
        artifact=artifact, profile=profile, trust_checked_at=NOW, request=call, decision=guard,
        context=context, requested_at=NOW, recorded_at=NOW + timedelta(seconds=2),
    )
    wrong_tenant = {**execution, "profile": replace(profile, tenant_id="other-tenant")}
    try:
        await executor.execute(**wrong_tenant)
    except TrustPreflightError as error:
        assert error.reason_code == "TRUST_TENANT_BINDING_MISMATCH"
        assert not events and not provider.requests and not outcomes.records
        tenant_failure = {
            "reason_code": error.reason_code, "message": str(error),
            "provider_calls": len(provider.requests), "write_requests_persisted": len(evidence.records),
            "outcomes_persisted": len(outcomes.records),
        }
    else:
        raise AssertionError("Oracle accepted a cross-tenant trust profile")

    decision = await executor.execute(**execution)
    assert decision.write_summary.commit_state == "COMMITTED"
    assert len(provider.requests) == len(outcomes.records) == 1
    assert events == ["WRITE_REQUESTED", "PROVIDER_WRITE"]
    verifier = fixture._VerificationPort()
    verification = await PostWriteVerifier().verify(decision.outcome.result, contract, context, verifier)
    assert verification.status == "VERIFIED" and verifier.calls == 1
    completed = await engine.complete_controlled_execution(
        context=context, approval_request_id=approval.approval_request_id, commit=decision,
        verification=verification, tools=tools, audit_sink=audit,
    )
    assert completed.status == "COMPLETED" and [dict(item.values) for item in completed.outputs] == [provider_result]
    if "target_subject_ids" in request.inputs:
        assert list(provider.requests[0].arguments["target_subject_ids"].value) == SUBJECTS

    observations = {
        "operation_kind": contract.operation_kind, "validation": prefix.result.validation.status,
        "approval_validation": validation, "guard": guard, "idempotency": reservation,
        "write_node": plan.write_expression.node_id, "collection_limit": skill.limits.collection_size,
        "static_tool_call_bound": skill.analysis.max_tool_calls, "static_loop_bound": skill.analysis.max_loop_iterations,
        "provider_request": call, "provider_calls": len(provider.requests), "events": events,
        "durable_outcome": outcomes.records[0], "verification_calls": verifier.calls,
        "runtime_result": completed.semantic_view(),
    }
    controls = {"checked_at": NOW, "approval_evidence": approval, "approval_bindings": bindings,
                "mock_approval_verifier_result": True, "policy_decision": policy,
                "business_preconditions": precondition, "provider_guarantee": guarantee,
                "trust_profile": profile, "verified_at": artifact.trust.verified_at,
                "provider_response": decision.outcome.result, "verification_observation": verification}
    return observations, controls, tenant_failure, canonical_write_arguments(call.arguments)


async def run_local(skill, catalog, request):
    with TemporaryDirectory(prefix="nsl-local-write-") as temp:
        directory = Path(temp)
        approval_path = directory / "approval.json"
        session = await start_controlled_run(
            skill=skill, catalog=catalog, request=request, handlers={},
            state_port=FileControlledStatePort(directory / "checkpoint.json", approval_path),
            environment=RuntimeEnvironment.DEVELOPMENT, fake_approval_enabled=False,
        )
        assert session.approval_request is None and not approval_path.exists()
        assert session.result.resources.tool_calls == 0
        return {"runtime_result": session.result.semantic_view(), "checkpoint_available": session.checkpoint is not None,
                "approval_request": session.approval_request, "approval_request_persisted": approval_path.exists(),
                "tool_calls": session.result.resources.tool_calls}


def trust_cases(cases, artifact_bytes, package):
    untrusted = NspVerifier(
        policy=NspVerificationPolicy(RuntimeEnvironment.PRODUCTION), signature_verifier=Ed25519TrustStore({}),
    )
    try:
        untrusted.verify(package.data)
    except NspVerificationError as error:
        assert error.code == "NSP_INVALID_SIGNATURE"
        cases.add(
            "trust", "package-untrusted-signer", given={"environment": "PRODUCTION", "trusted_keys_hex": {}},
            inputs={"package": ("signed.nsp", package.data)},
            observed={"code": error.code, "message": str(error)},
            requirements=["NSL-RUST-TRUST-001", "NSL-RUST-TRUST-004"],
            references=["nsl/nsp_verification.py", "tests/test_v0_2_package_trust.py"],
            error={"code": error.code, "phase": "package_verification"},
        )
    else:
        raise AssertionError("Oracle accepted an untrusted NSP signer")

    tampered = json.loads(artifact_bytes)
    tampered["limits"]["tool_calls"] += 1
    bad_nso = canonical_json(tampered)
    try:
        NsoCodec.decode(bad_nso)
    except NsoIntegrityError as error:
        assert str(error) == "semantic hash mismatch"
        cases.add(
            "trust", "nso-semantic-tamper", given={"changed_field": "$.limits.tool_calls"},
            inputs={"untrusted_nso": ("tampered.nso", bad_nso)}, observed={"message": str(error)},
            requirements=["NSL-RUST-IR-014", "NSL-RUST-TRUST-003"],
            references=["tests/test_nso_trust_boundary.py::test_sec_009_untrusted_nso_requires_schema_and_semantic_integrity"],
            error={"code": "NSO_INTEGRITY_ERROR", "phase": "nso_decode"},
        )
    else:
        raise AssertionError("Oracle accepted tampered NSO")


def platform_cases(cases, oracle):
    catalog_path = f"{EXAMPLES}/tools_write.json"
    all_tools = json.loads((oracle / catalog_path).read_bytes())["tools"]
    for name, source_name, tool_id, operation, provider_result in (
        ("add-many", "project_membership_add_many.ns", "PROJECT.MEMBERSHIP.ADD_MANY", "CREATE", BATCH_RESULT),
        ("update", "project_project_deactivate.ns", "PROJECT.PROJECT.DEACTIVATE", "UPDATE", UPDATE_RESULT),
    ):
        source_path = f"{EXAMPLES}/{source_name}"
        source = (oracle / source_path).read_bytes()
        tool, = (tool for tool in all_tools if tool["tool_id"] == tool_id and tool["version"] == "1.0.0")
        catalog_document = {"tools": [tool]}
        catalog = load_tool_catalog_document(catalog_document)
        contract = catalog.get(tool_id, "1.0.0")
        assert contract.operation_kind == operation
        compiled = NslCompiler(catalog).compile(source.decode("utf-8"))
        skill = compiled.skill
        principal = build_principal(tenant_id="tenant-rust-corpus")
        principal = replace(principal, scopes=principal.scopes | {contract.required_scope})
        inputs = {"tenant_id": principal.tenant_id, "requester_subject_id": principal.subject_id,
                  "project_id": PROJECT["project_id"]}
        if name == "add-many":
            inputs.update(target_subject_ids=SUBJECTS, member_role="MEMBER", resolution_evidence_hash="sha256:" + "a" * 64)
            assert skill.limits.collection_size == 50 and skill.analysis.max_loop_iterations == 0
        else:
            inputs["expected_version"] = 1
        request = ExecutionRequest(
            execution_id=f"mock-platform-{name}", business_operation_id=BusinessOperationId(f"business-{name}"),
            inputs=inputs, runtime_context={}, principal=principal,
        )
        sources = {"source": (source_name, source), "tool_catalog": ("tools.json", cases.json_bytes(catalog_document))}
        references = [source_path, catalog_path, "tests/test_p8_project_write_contracts.py"]
        requirements = ["NSL-RUST-WRITE-009", "NSL-RUST-WRITE-010",
                        "NSL-RUST-COMPAT-004" if name == "add-many" else "NSL-RUST-COMPAT-005"]
        local = asyncio.run(run_local(skill, catalog, request))
        assert local["runtime_result"]["status"] == "COMPLETED" and local["checkpoint_available"]
        assert local["runtime_result"]["validation"]["status"] == "NO_CHECK"
        cases.add(
            "write", f"{name}-local-no-check", given={"execution_request": request, "environment": "DEVELOPMENT"},
            inputs=sources, observed=local, requirements=["NSL-RUST-COMPAT-004"],
            references=[*references, "nsl/cli_controlled.py"], status=local["runtime_result"]["status"],
            notes="Run the local controlled CLI path through its approval-publication boundary.",
        )

        package, keys, artifact, profile = package_trust(compiled.nso_bytes, principal)
        observed, controls, tenant_failure, canonical_arguments = asyncio.run(run_platform(
            skill, catalog, contract, request, artifact, profile, provider_result,
            provider_result.get("business_result_ref", PROJECT["project_id"]),
        ))
        signed_inputs = {**sources, "package": ("signed.nsp", package.data)}
        scenario = {"execution_request": request, "trusted_keys_hex": {key: value.hex() for key, value in keys.items()},
                    "controls": controls}
        cases.add(
            "write", f"{name}-mock-platform", given=scenario, inputs=signed_inputs, observed=observed,
            requirements=requirements, references=[*references, "tests/test_v0_2_controlled_runtime.py"],
            status=observed["runtime_result"]["status"],
            hashes={"payload_hash": observed["provider_request"].payload_hash.removeprefix("sha256:"),
                    "write_intent_hash": observed["provider_request"].write_intent_hash.removeprefix("sha256:"),
                    "package_hash": package.package_hash.removeprefix("sha256:")},
            notes="Mock Platform supplies bound approval proof and current policy. Verify trust, durable request order, a single provider call, and read-only post-WRITE verification.",
        )
        if name == "add-many":
            assert len(json.loads(canonical_arguments)) == 6
            over_limit = replace(request, inputs={**inputs, "target_subject_ids": [*SUBJECTS, "subject-50"]})
            limited = asyncio.run(run_local(skill, catalog, over_limit))
            assert limited["runtime_result"]["status"] == "LIMIT_EXCEEDED" and not limited["checkpoint_available"]
            cases.add(
                "write", "add-many-over-limit", given={"execution_request": over_limit, "environment": "DEVELOPMENT"},
                inputs=sources, observed=limited, requirements=["NSL-RUST-RUN-011"], references=references,
                status=limited["runtime_result"]["status"],
                error={"code": limited["runtime_result"]["error"]["code"], "phase": "runtime_input"},
            )
            cases.add(
                "trust", "write-cross-tenant", given={**scenario, "changed_trust_tenant": "other-tenant"},
                inputs=signed_inputs, observed=tenant_failure,
                requirements=["NSL-RUST-TRUST-009", "NSL-RUST-SEC-006"],
                references=["tests/test_v0_2_production_write_trust.py::test_trusted_write_rejects_cross_tenant_and_context_skill_binding"],
                error={"code": tenant_failure["reason_code"], "phase": "trust_preflight"},
            )
            trust_cases(cases, compiled.nso_bytes, package)
