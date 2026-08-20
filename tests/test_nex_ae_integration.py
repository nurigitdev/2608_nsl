from __future__ import annotations

from pathlib import Path
from datetime import date
from decimal import Decimal
import asyncio
import json

import pytest

import nsl.api as nsl_api
from nsl.dispatch import (
    DispatchStatus,
    InMemoryJobDispatcher,
    JobDispatcher,
    JobDispatchReceipt,
)
from nsl.outcome_records import (
    InMemoryLlmExplanationStore,
    InMemoryRuntimeResultStore,
    LlmExplanationRecord,
    LlmExplanationStore,
    RuntimeResultRecord,
    RuntimeResultStore,
)

from nsl.compiler import NslCompiler
from nsl.core import Completeness, DataClassification, ExecutionStatus
from nsl.integration import (
    IntegrationContractError,
    ExplicitDataHandlingPolicy,
    InputProvenance,
    InputSource,
    InMemoryProgressSink,
    MAX_INTEGRATION_VALUE_DEPTH,
    MAX_INTEGRATION_VALUE_NODES,
    MAX_INTEGRATION_DOCUMENT_BYTES,
    MAX_INTEGRATION_STRING_BYTES,
    NEX_RUNTIME_RESULT_FORMAT,
    NEX_RUNTIME_RESULT_SCHEMA_VERSION,
    NEX_SKILL_EXECUTION_JOB_FORMAT,
    NEX_SKILL_EXECUTION_JOB_SCHEMA_VERSION,
    NEX_PROGRESS_EVENT_FORMAT,
    NEX_PROGRESS_EVENT_SCHEMA_VERSION,
    NullProgressSink,
    ProgressEvent,
    ProgressState,
    RuntimeResultEnvelope,
    SkillExecutionJob,
    SkillResolutionCode,
    SkillResolutionError,
    StructuredInputs,
    StructuredRuntimeContext,
    VerifiedPrincipalContext,
    VerifiedPackageSkillResolver,
)
from nsl.nsp import NspBuilder
from nsl.nsp_verification import (
    NspVerificationPolicy,
    NspVerifier,
    VerifiedNspPackage,
)
from nsl.security import (
    ExecutionPrincipal,
    DataHandlingPolicy,
    PrincipalVerification,
    RuntimeEnvironment,
)
from nsl.runtime_models import ExecutionResult, ResourceUsage, RuntimeErrorInfo
from nsl.runtime import RuntimeEngine
from nsl.audit import InMemoryAuditSink, InMemorySnapshotStore
from nsl.vertical_slice import build_mock_executor, build_principal
from nsl.worker import (
    SkillExecutionWorker,
    WorkerBoundaryCode,
    WorkerBoundaryError,
)
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SKILL_SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def build_verified_package(
    skill_id: str = "AE_SKILL",
    version: str = "1.0.0",
    *,
    signed: bool = False,
) -> VerifiedNspPackage:
    source = SKILL_SOURCE.replace(
        "skill FINANCE.PROJECT_BUDGET_CHECK",
        f"skill {skill_id}",
        1,
    ).replace(
        'version "1.0.0"',
        f'version "{version}"',
        1,
    )
    result = NslCompiler(build_tool_catalog()).compile(source)
    assert result.nso_bytes is not None
    signer = _FixedSigner() if signed else None
    package = NspBuilder().build([result.nso_bytes], signer=signer)
    return NspVerifier(
        policy=NspVerificationPolicy(
            RuntimeEnvironment.DEVELOPMENT,
            allow_unsigned_development=True,
        ),
        signature_verifier=_AcceptingVerifier() if signed else None,
    ).verify(package.data)


class _FixedSigner:
    algorithm = "Ed25519"
    key_id = "publisher-1"

    def sign(self, message: bytes) -> bytes:
        assert message
        return b"s" * 64


class _AcceptingVerifier:
    def verify(self, **arguments) -> bool:
        return bool(arguments)


def test_ae_001_resolves_skill_id_and_exact_version_from_verified_package() -> None:
    package = build_verified_package()
    resolved = VerifiedPackageSkillResolver([package]).resolve(
        "AE_SKILL", "1.0.0"
    )

    assert resolved.skill is package.skills[0].skill
    assert resolved.package_hash == package.package_hash
    assert resolved.signer_key_id is None


@pytest.mark.parametrize(
    ("skill_id", "version", "code"),
    [
        ("UNKNOWN", "1.0.0", SkillResolutionCode.UNKNOWN_SKILL),
        ("AE_SKILL", "9.9.9", SkillResolutionCode.UNKNOWN_SKILL),
        ("", "1.0.0", SkillResolutionCode.INVALID_SELECTOR),
        ("AE_SKILL", "", SkillResolutionCode.INVALID_SELECTOR),
        ("기술", "1.0.0", SkillResolutionCode.INVALID_SELECTOR),
        (True, "1.0.0", SkillResolutionCode.INVALID_SELECTOR),
        ("Authorization: Bearer raw-token", "1.0.0", SkillResolutionCode.INVALID_SELECTOR),
    ],
)
def test_ae_001_unknown_and_invalid_selectors_fail_closed(
    skill_id, version, code
) -> None:
    resolver = VerifiedPackageSkillResolver([build_verified_package()])

    with pytest.raises(SkillResolutionError) as captured:
        resolver.resolve(skill_id, version)

    assert captured.value.code is code


def test_ae_001_rejects_unverified_and_duplicate_skill_identities() -> None:
    package = build_verified_package()

    with pytest.raises(IntegrationContractError):
        VerifiedPackageSkillResolver([object()])
    with pytest.raises(IntegrationContractError):
        VerifiedPackageSkillResolver(object())
    with pytest.raises(SkillResolutionError) as captured:
        VerifiedPackageSkillResolver([package, package])

    assert captured.value.code is SkillResolutionCode.DUPLICATE_IDENTITY


def test_ae_001_preserves_signer_identity_from_verified_boundary() -> None:
    package = build_verified_package(signed=True)

    resolved = VerifiedPackageSkillResolver([package]).resolve(
        "AE_SKILL", "1.0.0"
    )

    assert resolved.signer_key_id == "publisher-1"


def test_ae_002_structured_inputs_round_trip_typed_nested_data() -> None:
    inputs = StructuredInputs(
        {
            "year": 2026,
            "period": {"start": date(2026, 1, 1), "ratio": Decimal("1.25")},
            "teams": ["FINANCE", "PURCHASE"],
            "enabled": True,
            "optional": None,
        }
    )

    wire = inputs.to_data()
    restored = StructuredInputs.from_data(wire)

    assert restored.to_runtime() == inputs.to_runtime()
    assert wire["period"]["start"] == {"$type": "Date", "value": "2026-01-01"}
    assert wire["period"]["ratio"] == {"$type": "Decimal", "value": "1.25"}


def test_ae_002_structured_inputs_are_defensively_copied() -> None:
    source = {"items": [{"value": 1}]}
    inputs = StructuredInputs(source)

    source["items"][0]["value"] = 99
    first = inputs.to_runtime()
    first["items"][0]["value"] = 77

    assert inputs.to_runtime() == {"items": [{"value": 1}]}


@pytest.mark.parametrize(
    "value",
    [
        [],
        "natural-language input",
        {"value": 1.5},
        {"value": float("nan")},
        {"value": b"bytes"},
        {"value": {1, 2}},
        {"": 1},
        {1: "value"},
        {"api_key": "forbidden"},
        {"nested": {"Authorization": "Bearer raw-secret"}},
    ],
)
def test_ae_002_rejects_non_structured_ambiguous_and_sensitive_inputs(value) -> None:
    with pytest.raises(IntegrationContractError):
        StructuredInputs(value)


def test_ae_002_structured_input_depth_boundary() -> None:
    accepted: object = 1
    for _ in range(MAX_INTEGRATION_VALUE_DEPTH - 1):
        accepted = [accepted]
    StructuredInputs({"value": accepted})

    rejected: object = accepted
    rejected = [rejected]
    with pytest.raises(IntegrationContractError, match="depth"):
        StructuredInputs({"value": rejected})


def test_ae_002_structured_input_size_boundary() -> None:
    StructuredInputs({"values": [None] * (MAX_INTEGRATION_VALUE_NODES - 2)})

    with pytest.raises(IntegrationContractError, match="size"):
        StructuredInputs({"values": [None] * (MAX_INTEGRATION_VALUE_NODES - 1)})


@pytest.mark.parametrize(
    "wire",
    [
        [],
        {"value": {"$type": "Decimal", "value": "NaN"}},
        {"value": {"$type": "Date", "value": "not-a-date"}},
        {"value": {"$type": "Money", "amount": "1", "currency": "bad"}},
    ],
)
def test_ae_002_rejects_invalid_wire_typed_values(wire) -> None:
    with pytest.raises(IntegrationContractError):
        StructuredInputs.from_data(wire)


def test_ae_002_typed_wire_rejects_unknown_extra_and_cyclic_values() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    for wire in (
        {"value": {"$type": "Unknown", "value": "x"}},
        {"value": {"$type": "Decimal", "value": "1", "extra": True}},
        {"value": {"$type": 1, "value": "x"}},
        cyclic,
    ):
        with pytest.raises(IntegrationContractError):
            StructuredInputs.from_data(wire)

    deep: object = 1
    for _ in range(MAX_INTEGRATION_VALUE_DEPTH + 1):
        deep = [deep]
    with pytest.raises(IntegrationContractError, match="typed wire depth"):
        StructuredInputs.from_data({"value": deep})


def test_ae_002_string_size_boundary() -> None:
    StructuredInputs({"value": "a" * MAX_INTEGRATION_STRING_BYTES})

    with pytest.raises(IntegrationContractError, match="1 MiB"):
        StructuredInputs({"value": "a" * (MAX_INTEGRATION_STRING_BYTES + 1)})


def test_ae_003_runtime_context_is_a_distinct_structured_contract() -> None:
    inputs = StructuredInputs({"team_id": "INPUT-TEAM"})
    context = StructuredRuntimeContext(
        {"user": {"team_id": "CONTEXT-TEAM"}, "as_of": date(2026, 8, 20)}
    )

    restored = StructuredRuntimeContext.from_data(context.to_data())

    assert inputs.to_runtime()["team_id"] == "INPUT-TEAM"
    assert restored.to_runtime() == {
        "user": {"team_id": "CONTEXT-TEAM"},
        "as_of": date(2026, 8, 20),
    }


def test_ae_003_runtime_context_is_defensively_copied() -> None:
    source = {"user": {"team_id": "FINANCE"}}
    context = StructuredRuntimeContext(source)

    source["user"]["team_id"] = "CHANGED"
    projected = context.to_runtime()
    projected["user"]["team_id"] = "ALSO-CHANGED"

    assert context.to_runtime() == {"user": {"team_id": "FINANCE"}}


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "chat context",
        {"access_token": "raw-secret"},
        {"user": {"Authorization": "Bearer raw-context-token"}},
    ],
)
def test_ae_003_rejects_invalid_or_sensitive_runtime_context(value) -> None:
    with pytest.raises(IntegrationContractError):
        StructuredRuntimeContext(value)


def test_ae_003_rejects_invalid_runtime_context_wire_type() -> None:
    with pytest.raises(IntegrationContractError):
        StructuredRuntimeContext.from_data([])
    with pytest.raises(IntegrationContractError):
        StructuredRuntimeContext.from_data(
            {"as_of": {"$type": "DateTime", "value": "invalid"}}
        )
    with pytest.raises(IntegrationContractError):
        StructuredRuntimeContext.from_data(
            {"as_of": {"$type": "Date", "value": "2026-01-01", "extra": 1}}
        )


def build_runtime_result(
    *,
    status: ExecutionStatus = ExecutionStatus.COMPLETED,
    error: RuntimeErrorInfo | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        execution_id="exec-ae-007",
        skill_id="AE_SKILL",
        skill_version="1.0.0",
        semantic_hash="sha256:" + "a" * 64,
        status=status,
        completeness=(
            Completeness.COMPLETE
            if status is ExecutionStatus.COMPLETED
            else Completeness.UNKNOWN
        ),
        checks=(),
        outputs=(),
        resources=ResourceUsage(2, 3, 1, 10),
        error=error,
    )


def test_ae_007_runtime_result_envelope_is_versioned_and_structured() -> None:
    envelope = RuntimeResultEnvelope(build_runtime_result())

    data = envelope.to_data()

    assert data == {
        "format": NEX_RUNTIME_RESULT_FORMAT,
        "schema_version": NEX_RUNTIME_RESULT_SCHEMA_VERSION,
        "runtime_result": {
            "schema_version": "1.0",
            "execution_id": "exec-ae-007",
            "skill_id": "AE_SKILL",
            "skill_version": "1.0.0",
            "semantic_hash": "sha256:" + "a" * 64,
            "status": "COMPLETED",
            "completeness": "COMPLETE",
            "checks": [],
            "outputs": [],
            "resources": {
                "tool_calls": 2,
                "loop_iterations": 3,
                "emitted_rows": 1,
                "max_collection_size_seen": 10,
            },
            "error": None,
        },
    }


def test_ae_007_runtime_result_json_is_canonical_and_keeps_failure_state() -> None:
    error = RuntimeErrorInfo(
        code="NSL-E5000",
        category="RUNTIME",
        message="controlled failure",
        detail_code="FAILED_PRECONDITION",
    )
    envelope = RuntimeResultEnvelope(
        build_runtime_result(status=ExecutionStatus.FAILED, error=error)
    )

    first = envelope.to_json()
    second = envelope.to_json()

    assert first == second
    assert '"status":"FAILED"' in first
    assert '"completeness":"UNKNOWN"' in first
    assert '"error":{"category":"RUNTIME"' in first
    assert "explanation" not in first


@pytest.mark.parametrize("value", [None, {}, object()])
def test_ae_007_runtime_result_envelope_rejects_non_runtime_results(value) -> None:
    with pytest.raises(IntegrationContractError):
        RuntimeResultEnvelope(value)


def principal_data(**overrides):
    value = {
        "tenant_id": "tenant-a",
        "subject_id": "user-a",
        "actor_type": "USER",
        "roles": ["FINANCE", "REVIEWER"],
        "scopes": ["finance:read", "nsl:skill:execute"],
        "auth_context_ref": "authz-context-001",
        "verification": "VERIFIED",
        "on_behalf_of": None,
    }
    value.update(overrides)
    return value


def test_ae_009_verified_principal_and_authorization_context_round_trip() -> None:
    context = VerifiedPrincipalContext.from_data(principal_data())

    assert context.principal.verification is PrincipalVerification.VERIFIED
    assert context.to_data() == principal_data(
        roles=["FINANCE", "REVIEWER"],
        scopes=["finance:read", "nsl:skill:execute"],
    )


def test_ae_009_principal_output_orders_roles_and_scopes_canonically() -> None:
    context = VerifiedPrincipalContext.from_data(
        principal_data(
            roles=["REVIEWER", "FINANCE"],
            scopes=["nsl:skill:execute", "finance:read"],
            actor_type="SERVICE",
            on_behalf_of="user-a",
        )
    )

    assert context.to_data()["roles"] == ["FINANCE", "REVIEWER"]
    assert context.to_data()["scopes"] == ["finance:read", "nsl:skill:execute"]
    assert context.principal.actor_type == "SERVICE"


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        principal_data(extra="field"),
        principal_data(verification="UNVERIFIED"),
        principal_data(verification="UNKNOWN"),
        principal_data(actor_type="ROBOT"),
        principal_data(roles=["FINANCE", "FINANCE"]),
        principal_data(scopes="nsl:skill:execute"),
        principal_data(auth_context_ref="Authorization: Bearer raw-token"),
        principal_data(on_behalf_of=""),
    ],
)
def test_ae_009_rejects_unverified_malformed_or_sensitive_principal(data) -> None:
    with pytest.raises(IntegrationContractError):
        VerifiedPrincipalContext.from_data(data)


def test_ae_009_rejects_non_principal_model() -> None:
    with pytest.raises(IntegrationContractError):
        VerifiedPrincipalContext(object())
    with pytest.raises(IntegrationContractError):
        VerifiedPrincipalContext(
            ExecutionPrincipal(
                tenant_id="tenant-a",
                subject_id="user-a",
                actor_type="USER",
                roles=frozenset(),
                scopes=frozenset(),
                auth_context_ref="authz-1",
                verification=PrincipalVerification.UNVERIFIED,
            )
        )


def test_ae_010_explicit_data_handling_policy_round_trip() -> None:
    contract = ExplicitDataHandlingPolicy.from_data(
        {
            "max_trace_classification": "CONFIDENTIAL",
            "snapshot_retention_days": 7,
            "audit_retention_days": 365,
        }
    )

    assert contract.policy == DataHandlingPolicy(
        max_trace_classification=DataClassification.CONFIDENTIAL,
        snapshot_retention_days=7,
        audit_retention_days=365,
    )
    assert contract.to_data() == {
        "max_trace_classification": "CONFIDENTIAL",
        "snapshot_retention_days": 7,
        "audit_retention_days": 365,
    }


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {
            "max_trace_classification": "INTERNAL",
            "snapshot_retention_days": 30,
            "audit_retention_days": 90,
            "unexpected": True,
        },
        {
            "max_trace_classification": "SECRET",
            "snapshot_retention_days": 30,
            "audit_retention_days": 90,
        },
        {
            "max_trace_classification": "INTERNAL",
            "snapshot_retention_days": 0,
            "audit_retention_days": 90,
        },
        {
            "max_trace_classification": "INTERNAL",
            "snapshot_retention_days": True,
            "audit_retention_days": 90,
        },
        {
            "max_trace_classification": "INTERNAL",
            "snapshot_retention_days": 30,
            "audit_retention_days": -1,
        },
    ],
)
def test_ae_010_rejects_missing_extra_and_invalid_data_policy(data) -> None:
    with pytest.raises(IntegrationContractError):
        ExplicitDataHandlingPolicy.from_data(data)


def test_ae_010_rejects_invalid_policy_model() -> None:
    with pytest.raises(IntegrationContractError):
        ExplicitDataHandlingPolicy(object())
    with pytest.raises(IntegrationContractError):
        ExplicitDataHandlingPolicy(
            DataHandlingPolicy(max_trace_classification="INTERNAL")
        )


def test_ae_011_input_provenance_tracks_all_supported_sources() -> None:
    inputs = StructuredInputs(
        {"year": 2026, "team_id": "FINANCE", "currency": "KRW"}
    )
    provenance = InputProvenance.from_data(
        {"year": "USER", "team_id": "CONTEXT", "currency": "DEFAULT"}
    )

    provenance.validate_inputs(inputs)

    assert provenance.sources == {
        "year": InputSource.USER,
        "team_id": InputSource.CONTEXT,
        "currency": InputSource.DEFAULT,
    }
    assert provenance.to_data() == {
        "currency": "DEFAULT",
        "team_id": "CONTEXT",
        "year": "USER",
    }


def test_ae_011_empty_inputs_require_empty_provenance() -> None:
    InputProvenance({}).validate_inputs(StructuredInputs({}))

    with pytest.raises(IntegrationContractError):
        InputProvenance({"extra": InputSource.DEFAULT}).validate_inputs(
            StructuredInputs({})
        )


@pytest.mark.parametrize(
    ("inputs", "sources"),
    [
        ({"year": 2026}, {}),
        ({"year": 2026}, {"year": InputSource.USER, "extra": InputSource.DEFAULT}),
        ({"year": 2026, "team": "A"}, {"year": InputSource.USER}),
    ],
)
def test_ae_011_rejects_missing_and_extra_provenance(inputs, sources) -> None:
    with pytest.raises(IntegrationContractError, match="exactly cover"):
        InputProvenance(sources).validate_inputs(StructuredInputs(inputs))


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"year": "MODEL"},
        {"year": 1},
        {"": "USER"},
        {"api_key": "USER"},
    ],
)
def test_ae_011_rejects_invalid_or_sensitive_input_provenance(value) -> None:
    with pytest.raises(IntegrationContractError):
        InputProvenance.from_data(value)


def test_ae_011_provenance_model_is_defensive_and_type_safe() -> None:
    source = {"year": InputSource.USER}
    provenance = InputProvenance(source)
    source["year"] = InputSource.DEFAULT

    assert provenance.sources["year"] is InputSource.USER
    with pytest.raises(IntegrationContractError):
        InputProvenance(None)
    with pytest.raises(IntegrationContractError):
        InputProvenance({"year": "USER"})
    with pytest.raises(IntegrationContractError):
        provenance.validate_inputs(object())


def build_job(package: VerifiedNspPackage | None = None, **overrides):
    verified = package or build_verified_package()
    values = {
        "execution_id": "exec-worker-001",
        "skill_id": "AE_SKILL",
        "skill_version": "1.0.0",
        "expected_semantic_hash": verified.skills[0].skill.semantic_hash,
        "inputs": StructuredInputs({"year": 2026}),
        "input_provenance": InputProvenance({"year": InputSource.USER}),
        "runtime_context": StructuredRuntimeContext(
            {"user": {"team_id": "TEAM-FINANCE"}}
        ),
        "principal": VerifiedPrincipalContext(build_principal()),
        "data_policy": ExplicitDataHandlingPolicy(
            DataHandlingPolicy(
                max_trace_classification=DataClassification.INTERNAL,
                snapshot_retention_days=30,
                audit_retention_days=90,
            )
        ),
    }
    values.update(overrides)
    return SkillExecutionJob(**values)


def build_worker(
    package: VerifiedNspPackage | None = None,
    *,
    progress_sink=None,
):
    verified = package or build_verified_package()
    catalog = build_tool_catalog()
    audit = InMemoryAuditSink()
    snapshots = InMemorySnapshotStore()
    tools = build_mock_executor(catalog)
    worker = SkillExecutionWorker(
        runtime=RuntimeEngine(catalog),
        resolver=VerifiedPackageSkillResolver([verified]),
        tools=tools,
        audit_sink=audit,
        snapshot_store=snapshots,
        progress_sink=progress_sink,
    )
    return worker, tools, audit, snapshots


def test_ae_005_worker_executes_validated_job_through_runtime_ports() -> None:
    package = build_verified_package()
    worker, tools, audit, snapshots = build_worker(package)

    envelope = asyncio.run(worker.execute(build_job(package)))

    result = envelope.runtime_result
    assert result.status is ExecutionStatus.COMPLETED
    assert result.execution_id == "exec-worker-001"
    assert result.skill_id == "AE_SKILL"
    assert tools.call_count == 2
    assert audit.events[0].event_type == "EXECUTION_STARTED"
    assert audit.events[-1].event_type == "EXECUTION_COMPLETED"
    assert snapshots._counter > 0


def test_ae_005_skill_execution_job_is_versioned_and_round_trips() -> None:
    job = build_job()
    data = job.to_data()
    restored = SkillExecutionJob.from_data(data)

    assert data["format"] == NEX_SKILL_EXECUTION_JOB_FORMAT
    assert data["schema_version"] == NEX_SKILL_EXECUTION_JOB_SCHEMA_VERSION
    assert restored.to_data() == data
    assert restored.to_runtime_request().inputs == {"year": 2026}
    assert restored.to_json() == job.to_json()


def test_ae_005_worker_rejects_unknown_and_semantically_changed_skill() -> None:
    package = build_verified_package()
    worker, tools, audit, _ = build_worker(package)

    with pytest.raises(WorkerBoundaryError) as unknown:
        asyncio.run(
            worker.execute(build_job(package, skill_id="UNKNOWN"))
        )
    with pytest.raises(WorkerBoundaryError) as changed:
        asyncio.run(
            worker.execute(
                build_job(package, expected_semantic_hash="sha256:" + "0" * 64)
            )
        )

    assert unknown.value.code is WorkerBoundaryCode.SKILL_RESOLUTION_FAILED
    assert changed.value.code is WorkerBoundaryCode.SEMANTIC_IDENTITY_MISMATCH
    assert tools.call_count == 0
    assert audit.events == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution_id": ""},
        {"execution_id": "e" * 257},
        {"execution_id": "Authorization: Bearer raw-token"},
        {"skill_id": ""},
        {"skill_id": "S" * 257},
        {"skill_version": ""},
        {"expected_semantic_hash": "sha256:BAD"},
        {"inputs": {}},
        {"input_provenance": {}},
        {"runtime_context": {}},
        {"principal": object()},
        {"data_policy": object()},
    ],
)
def test_ae_005_job_contract_rejects_invalid_boundary_values(overrides) -> None:
    with pytest.raises(IntegrationContractError):
        build_job(**overrides)


def test_ae_005_job_decoder_rejects_schema_drift() -> None:
    data = build_job().to_data()
    for changed in (
        {**data, "format": "OTHER"},
        {**data, "schema_version": "2.0"},
        {**data, "extra": True},
        {key: value for key, value in data.items() if key != "inputs"},
        [],
    ):
        with pytest.raises(IntegrationContractError):
            SkillExecutionJob.from_data(changed)


def test_ae_005_worker_dependency_and_job_ports_fail_closed() -> None:
    package = build_verified_package()
    catalog = build_tool_catalog()
    valid = {
        "runtime": RuntimeEngine(catalog),
        "resolver": VerifiedPackageSkillResolver([package]),
        "tools": build_mock_executor(catalog),
        "audit_sink": InMemoryAuditSink(),
        "snapshot_store": InMemorySnapshotStore(),
    }
    for field in ("runtime", "resolver", "tools", "audit_sink", "snapshot_store"):
        invalid = {**valid, field: object()}
        with pytest.raises(WorkerBoundaryError) as captured:
            SkillExecutionWorker(**invalid)
        assert captured.value.code is WorkerBoundaryCode.INVALID_DEPENDENCY

    worker = SkillExecutionWorker(**valid)
    with pytest.raises(WorkerBoundaryError) as captured:
        asyncio.run(worker.execute(object()))
    assert captured.value.code is WorkerBoundaryCode.INVALID_JOB


def test_ae_006_worker_publishes_ordered_safe_progress_events() -> None:
    package = build_verified_package()
    progress = InMemoryProgressSink()
    worker, _, _, _ = build_worker(package, progress_sink=progress)

    result = asyncio.run(worker.execute(build_job(package))).runtime_result

    assert result.status is ExecutionStatus.COMPLETED
    assert [event.sequence for event in progress.events] == [1, 2, 3, 4]
    assert [event.state for event in progress.events] == [
        ProgressState.STARTED,
        ProgressState.SKILL_RESOLVED,
        ProgressState.RUNNING,
        ProgressState.COMPLETED,
    ]
    terminal = progress.events[-1].to_data()
    assert terminal["format"] == NEX_PROGRESS_EVENT_FORMAT
    assert terminal["schema_version"] == NEX_PROGRESS_EVENT_SCHEMA_VERSION
    assert terminal["runtime_status"] == "COMPLETED"
    assert "TEAM-FINANCE" not in repr([event.to_data() for event in progress.events])


def test_ae_006_failed_resolution_publishes_terminal_progress_without_execution() -> None:
    package = build_verified_package()
    progress = InMemoryProgressSink()
    worker, tools, audit, _ = build_worker(package, progress_sink=progress)

    with pytest.raises(WorkerBoundaryError):
        asyncio.run(worker.execute(build_job(package, skill_id="UNKNOWN")))

    assert [event.state for event in progress.events] == [
        ProgressState.STARTED,
        ProgressState.FAILED,
    ]
    assert progress.events[-1].error_code == "SKILL_RESOLUTION_FAILED"
    assert tools.call_count == 0
    assert audit.events == []


def test_ae_006_runtime_failure_is_not_reported_as_completed() -> None:
    package = build_verified_package()
    progress = InMemoryProgressSink()
    worker, tools, _, _ = build_worker(package, progress_sink=progress)
    denied = build_principal(include_tool_scope=False)

    envelope = asyncio.run(
        worker.execute(
            build_job(package, principal=VerifiedPrincipalContext(denied))
        )
    )

    assert envelope.runtime_result.status is ExecutionStatus.FAILED
    assert progress.events[-1].state is ProgressState.FAILED
    assert progress.events[-1].runtime_status == "FAILED"
    assert progress.events[-1].error_code is not None
    assert tools.call_count == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"sequence": 0},
        {"sequence": 1_000_001},
        {"sequence": True},
        {"state": "RUNNING"},
        {"runtime_status": "COMPLETED"},
        {"state": ProgressState.COMPLETED, "error_code": "ERROR"},
        {"state": ProgressState.FAILED, "error_code": ""},
    ],
)
def test_ae_006_progress_event_boundary_values(overrides) -> None:
    values = {
        "execution_id": "exec-progress-1",
        "sequence": 1,
        "state": ProgressState.STARTED,
        "skill_id": "AE_SKILL",
        "skill_version": "1.0.0",
    }
    values.update(overrides)
    with pytest.raises(IntegrationContractError):
        ProgressEvent(**values)


def test_ae_006_progress_sink_enforces_sequence_and_terminal_state() -> None:
    first = ProgressEvent(
        "exec-progress-2", 1, ProgressState.STARTED, "AE_SKILL", "1.0.0"
    )
    completed = ProgressEvent(
        "exec-progress-2",
        2,
        ProgressState.COMPLETED,
        "AE_SKILL",
        "1.0.0",
        runtime_status="COMPLETED",
    )
    sink = InMemoryProgressSink()
    asyncio.run(sink.publish(first))

    with pytest.raises(IntegrationContractError, match="exactly one"):
        asyncio.run(
            sink.publish(
                ProgressEvent(
                    "exec-progress-2",
                    3,
                    ProgressState.RUNNING,
                    "AE_SKILL",
                    "1.0.0",
                )
            )
        )
    asyncio.run(sink.publish(completed))
    with pytest.raises(IntegrationContractError, match="terminal"):
        asyncio.run(
            sink.publish(
                ProgressEvent(
                    "exec-progress-2",
                    3,
                    ProgressState.RUNNING,
                    "AE_SKILL",
                    "1.0.0",
                )
            )
        )

    with pytest.raises(IntegrationContractError, match="first"):
        asyncio.run(
            InMemoryProgressSink().publish(
                ProgressEvent(
                    "exec-progress-3",
                    2,
                    ProgressState.RUNNING,
                    "AE_SKILL",
                    "1.0.0",
                )
            )
        )
    with pytest.raises(IntegrationContractError):
        asyncio.run(sink.publish(object()))
    with pytest.raises(IntegrationContractError):
        asyncio.run(NullProgressSink().publish(object()))


class _FailingProgressSink:
    async def publish(self, event: ProgressEvent) -> None:
        raise RuntimeError("SSE unavailable")


def test_ae_006_progress_delivery_failure_prevents_runtime_start() -> None:
    package = build_verified_package()
    worker, tools, audit, _ = build_worker(
        package,
        progress_sink=_FailingProgressSink(),
    )

    with pytest.raises(WorkerBoundaryError) as captured:
        asyncio.run(worker.execute(build_job(package)))

    assert captured.value.code is WorkerBoundaryCode.PROGRESS_DELIVERY_FAILED
    assert tools.call_count == 0
    assert audit.events == []


def test_ae_006_worker_rejects_invalid_progress_port() -> None:
    package = build_verified_package()
    catalog = build_tool_catalog()

    with pytest.raises(WorkerBoundaryError) as captured:
        SkillExecutionWorker(
            runtime=RuntimeEngine(catalog),
            resolver=VerifiedPackageSkillResolver([package]),
            tools=build_mock_executor(catalog),
            audit_sink=InMemoryAuditSink(),
            progress_sink=object(),
        )

    assert captured.value.code is WorkerBoundaryCode.INVALID_DEPENDENCY


def test_arc_008_public_api_runs_worker_in_the_host_process() -> None:
    package = build_verified_package()
    catalog = build_tool_catalog()
    worker = nsl_api.SkillExecutionWorker(
        runtime=nsl_api.RuntimeEngine(catalog),
        resolver=nsl_api.VerifiedPackageSkillResolver([package]),
        tools=build_mock_executor(catalog),
        audit_sink=InMemoryAuditSink(),
    )

    result = asyncio.run(worker.execute(build_job(package))).runtime_result

    assert result.status is ExecutionStatus.COMPLETED
    assert nsl_api.SkillExecutionWorker is SkillExecutionWorker
    assert nsl_api.SkillExecutionJob is SkillExecutionJob


def test_arc_009_transport_neutral_job_worker_result_round_trip() -> None:
    package = build_verified_package()
    worker, _, _, _ = build_worker(package)
    request_bytes = build_job(package).to_bytes()

    decoded_job = SkillExecutionJob.from_json(request_bytes)
    response = asyncio.run(worker.execute(decoded_job)).to_bytes()
    response_data = json.loads(response)

    assert decoded_job.to_bytes() == request_bytes
    assert response_data["format"] == NEX_RUNTIME_RESULT_FORMAT
    assert response_data["runtime_result"]["status"] == "COMPLETED"
    assert response_data["runtime_result"]["execution_id"] == "exec-worker-001"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data + b" ",
        lambda data: data.replace(b'"format":', b'"format" :', 1),
        lambda data: data.replace(
            b'"execution_id":"exec-worker-001"',
            b'"execution_id":"exec-worker-001","execution_id":"duplicate"',
            1,
        ),
        lambda data: data.replace(b'"year":2026', b'"year":NaN', 1),
        lambda data: b"\xff" + data,
        lambda data: b"[]",
    ],
)
def test_arc_009_job_transport_rejects_noncanonical_and_malformed_json(mutate) -> None:
    data = mutate(build_job().to_bytes())

    with pytest.raises(IntegrationContractError):
        SkillExecutionJob.from_json(data)


def test_arc_009_integration_document_size_boundary() -> None:
    oversized = b" " * (MAX_INTEGRATION_DOCUMENT_BYTES + 1)

    with pytest.raises(IntegrationContractError, match="8 MiB"):
        SkillExecutionJob.from_json(oversized)

    large_value = "a" * MAX_INTEGRATION_STRING_BYTES
    large_inputs = StructuredInputs(
        {f"value_{index}": large_value for index in range(9)}
    )
    large_provenance = InputProvenance(
        {f"value_{index}": InputSource.USER for index in range(9)}
    )
    with pytest.raises(IntegrationContractError, match="8 MiB"):
        build_job(inputs=large_inputs, input_provenance=large_provenance).to_bytes()


def test_arc_009_progress_event_has_a_strict_transport_round_trip() -> None:
    event = ProgressEvent(
        execution_id="exec-transport-1",
        sequence=1,
        state=ProgressState.COMPLETED,
        skill_id="AE_SKILL",
        skill_version="1.0.0",
        runtime_status="COMPLETED",
    )

    encoded = event.to_bytes()
    restored = ProgressEvent.from_json(encoded)

    assert restored == event
    assert restored.to_bytes() == encoded
    assert restored.to_json() == encoded.decode("utf-8")


@pytest.mark.parametrize(
    "value",
    [
        {},
        {
            "format": "OTHER",
            "schema_version": "1.0",
            "execution_id": "exec-1",
            "sequence": 1,
            "state": "RUNNING",
            "skill_id": "AE_SKILL",
            "skill_version": "1.0.0",
            "runtime_status": None,
            "error_code": None,
        },
        {
            "format": NEX_PROGRESS_EVENT_FORMAT,
            "schema_version": "2.0",
            "execution_id": "exec-1",
            "sequence": 1,
            "state": "RUNNING",
            "skill_id": "AE_SKILL",
            "skill_version": "1.0.0",
            "runtime_status": None,
            "error_code": None,
        },
        {
            "format": NEX_PROGRESS_EVENT_FORMAT,
            "schema_version": "1.0",
            "execution_id": "exec-1",
            "sequence": 1,
            "state": "UNKNOWN",
            "skill_id": "AE_SKILL",
            "skill_version": "1.0.0",
            "runtime_status": None,
            "error_code": None,
        },
    ],
)
def test_arc_009_progress_transport_rejects_schema_drift(value) -> None:
    with pytest.raises(IntegrationContractError):
        ProgressEvent.from_data(value)


def test_arc_009_progress_json_rejects_noncanonical_and_invalid_documents() -> None:
    event = ProgressEvent(
        "exec-transport-2", 1, ProgressState.RUNNING, "AE_SKILL", "1.0.0"
    )
    with pytest.raises(IntegrationContractError, match="canonical"):
        ProgressEvent.from_json(event.to_bytes() + b" ")
    with pytest.raises(IntegrationContractError):
        ProgressEvent.from_json(b"not-json")


def test_ae_004_async_dispatch_queues_job_without_running_runtime() -> None:
    package = build_verified_package()
    worker, tools, audit, _ = build_worker(package)
    dispatcher = InMemoryJobDispatcher()
    job = build_job(package)

    receipt = asyncio.run(dispatcher.submit(job))

    assert isinstance(dispatcher, JobDispatcher)
    assert receipt.status is DispatchStatus.QUEUED
    assert receipt.execution_id == job.execution_id
    assert receipt.job_id.startswith("job-")
    assert dispatcher.pending_jobs == (job.to_bytes(),)
    assert tools.call_count == 0
    assert audit.events == []
    assert worker is not None


def test_ae_004_dispatch_receipt_is_structured_and_strict() -> None:
    receipt = JobDispatchReceipt(
        "job-001", "exec-dispatch-001", DispatchStatus.QUEUED
    )

    assert receipt.to_data() == {
        "job_id": "job-001",
        "execution_id": "exec-dispatch-001",
        "status": "QUEUED",
    }

    for arguments in (
        ("", "exec-1", DispatchStatus.QUEUED),
        ("job-1", "", DispatchStatus.QUEUED),
        ("job-1", "exec-1", "QUEUED"),
        ("Authorization: Bearer raw-token", "exec-1", DispatchStatus.QUEUED),
        ("job-1", "실행-1", DispatchStatus.QUEUED),
        ("j" * 257, "exec-1", DispatchStatus.QUEUED),
    ):
        with pytest.raises(IntegrationContractError):
            JobDispatchReceipt(*arguments)


def test_ae_004_dispatch_rejects_invalid_and_duplicate_jobs() -> None:
    dispatcher = InMemoryJobDispatcher()
    job = build_job()

    with pytest.raises(IntegrationContractError):
        asyncio.run(dispatcher.submit(object()))
    asyncio.run(dispatcher.submit(job))
    with pytest.raises(IntegrationContractError, match="duplicate"):
        asyncio.run(dispatcher.submit(job))


def test_ae_008_runtime_result_and_llm_explanation_use_separate_records() -> None:
    envelope = RuntimeResultEnvelope(build_runtime_result())
    runtime_record = RuntimeResultRecord.create("runtime-record-1", envelope)
    explanation = LlmExplanationRecord.create(
        "explanation-1",
        runtime_record,
        "nex-ae-model-1",
        "예산 점검 실행이 완료되었습니다.",
    )
    runtime_store = InMemoryRuntimeResultStore()
    explanation_store = InMemoryLlmExplanationStore()

    asyncio.run(runtime_store.put(runtime_record))
    asyncio.run(explanation_store.put(explanation))

    assert isinstance(runtime_store, RuntimeResultStore)
    assert isinstance(explanation_store, LlmExplanationStore)
    assert runtime_store.records == {"runtime-record-1": runtime_record}
    assert explanation_store.records == {"explanation-1": explanation}
    assert runtime_record.to_data()["record_type"] == "NSL_RUNTIME_RESULT"
    assert explanation.to_data()["record_type"] == "LLM_EXPLANATION"
    assert "status" not in explanation.to_data()
    assert explanation.runtime_result_hash == runtime_record.result_hash


def test_ae_008_explanation_change_cannot_modify_runtime_result() -> None:
    runtime_record = RuntimeResultRecord.create(
        "runtime-record-2", RuntimeResultEnvelope(build_runtime_result())
    )
    original_bytes = runtime_record.envelope.to_bytes()
    first = LlmExplanationRecord.create(
        "explanation-2", runtime_record, "model-a", "첫 번째 설명"
    )
    second = LlmExplanationRecord.create(
        "explanation-3", runtime_record, "model-b", "완전히 다른 설명"
    )

    assert first.runtime_result_hash == second.runtime_result_hash
    assert runtime_record.envelope.to_bytes() == original_bytes
    assert b'"status":"COMPLETED"' in original_bytes


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RuntimeResultRecord.create(
            "", RuntimeResultEnvelope(build_runtime_result())
        ),
        lambda: RuntimeResultRecord.create(
            "Authorization: Bearer raw-token",
            RuntimeResultEnvelope(build_runtime_result()),
        ),
        lambda: RuntimeResultRecord.create("record-1", object()),
        lambda: RuntimeResultRecord(
            "record-1", "exec-ae-007", "sha256:" + "0" * 64, object()
        ),
        lambda: RuntimeResultRecord(
            "record-1",
            "wrong-execution",
            "sha256:" + "0" * 64,
            RuntimeResultEnvelope(build_runtime_result()),
        ),
        lambda: RuntimeResultRecord(
            "record-1",
            "exec-ae-007",
            "sha256:" + "0" * 64,
            RuntimeResultEnvelope(build_runtime_result()),
        ),
        lambda: LlmExplanationRecord.create(
            "exp-1", object(), "model-1", "text"
        ),
        lambda: LlmExplanationRecord(
            "", "exec-1", "sha256:" + "0" * 64, "model", "text"
        ),
        lambda: LlmExplanationRecord(
            "exp-1", "", "sha256:" + "0" * 64, "model", "text"
        ),
        lambda: LlmExplanationRecord("exp-1", "exec-1", "bad", "model", "text"),
        lambda: LlmExplanationRecord(
            "exp-1", "exec-1", "sha256:" + "0" * 64, "", "text"
        ),
        lambda: LlmExplanationRecord(
            "exp-1", "exec-1", "sha256:" + "0" * 64, "model", ""
        ),
        lambda: LlmExplanationRecord(
            "exp-1",
            "exec-1",
            "sha256:" + "0" * 64,
            "model",
            "가" * 349_526,
        ),
    ],
)
def test_ae_008_separate_record_boundary_values(factory) -> None:
    with pytest.raises(IntegrationContractError):
        factory()


def test_ae_008_record_stores_reject_wrong_types_and_duplicates() -> None:
    runtime = RuntimeResultRecord.create(
        "runtime-record-3", RuntimeResultEnvelope(build_runtime_result())
    )
    explanation = LlmExplanationRecord.create(
        "explanation-4", runtime, "model-1", "설명"
    )
    runtime_store = InMemoryRuntimeResultStore()
    explanation_store = InMemoryLlmExplanationStore()

    with pytest.raises(IntegrationContractError):
        asyncio.run(runtime_store.put(object()))
    with pytest.raises(IntegrationContractError):
        asyncio.run(explanation_store.put(object()))
    asyncio.run(runtime_store.put(runtime))
    asyncio.run(explanation_store.put(explanation))
    with pytest.raises(IntegrationContractError, match="duplicate"):
        asyncio.run(runtime_store.put(runtime))
    with pytest.raises(IntegrationContractError, match="duplicate"):
        asyncio.run(explanation_store.put(explanation))
