from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from .approval import (
    ApprovalEvidence,
    ApprovalPort,
    ApprovalRequest,
    ApprovalResumeCoordinator,
    ApprovalResumeOutcome,
    ApprovalSuspensionCoordinator,
    CheckpointPort,
    RecordedApprovalClock,
    ResumeCheckpointPort,
    ResumeClaimError,
    ResumeToken,
)
from .audit import AuditEvent, InMemoryAuditSink, InMemorySnapshotStore
from .checkpoints import CheckpointCoordinator, ExecutionCheckpoint
from .core import (
    ApprovalDecision,
    ApprovalRequestId,
    BusinessOperationId,
    DataClassification,
    ExecutionStatus,
    PayloadHash,
    RiskLevel,
    highest_classification,
)
from .data_protection import ensure_no_credential_material, redact_text
from .ir import SkillObject, canonical_json
from .ir_schema import NsoSchemaError, load_nso_json
from .replay import RecordingToolExecutor
from .runtime import ControlledExecutionPlan, ExecutionRequest, RuntimeEngine
from .runtime_models import ValidationSummaryStatus
from .security import RuntimeEnvironment
from .tools import FixtureHandler, MockToolExecutor, ToolContractCatalog
from .validation import PreWriteEvidence


CONTROLLED_DOCUMENT_VERSION = "1.0"
_FAKE_APPROVAL_PREFIX = "fake-approval:sha256:"


@dataclass(frozen=True, slots=True)
class ControlledRunSession:
    result: Any
    approval_request: ApprovalRequest | None
    checkpoint: ExecutionCheckpoint | None
    audit_events: tuple[AuditEvent, ...]


class FileControlledStatePort(CheckpointPort, ApprovalPort, ResumeCheckpointPort):
    """Local durable state adapter with one-consumer checkpoint claims."""

    def __init__(self, checkpoint_path: Path, approval_request_path: Path) -> None:
        self.checkpoint_path = checkpoint_path
        self.approval_request_path = approval_request_path
        self.claim_path = checkpoint_path.with_name(f"{checkpoint_path.name}.claim")
        self.consumed_path = checkpoint_path.with_name(
            f"{checkpoint_path.name}.consumed"
        )

    async def save(self, checkpoint: ExecutionCheckpoint) -> None:
        checkpoint.verify()
        _write_once(self.checkpoint_path, checkpoint.to_bytes())

    async def publish(self, request: ApprovalRequest) -> None:
        _write_once(
            self.approval_request_path,
            canonical_json(
                {
                    "schema_version": CONTROLLED_DOCUMENT_VERSION,
                    "approval_request": request.to_data(),
                }
            ),
        )

    async def claim(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> ExecutionCheckpoint:
        checkpoint = ExecutionCheckpoint.from_bytes(self.checkpoint_path.read_bytes())
        if (
            checkpoint.execution_id != execution_id
            or checkpoint.checkpoint_version != checkpoint_version
        ):
            raise ResumeClaimError("checkpoint identity mismatch")
        if self.consumed_path.exists():
            raise ResumeClaimError("checkpoint already consumed")
        claim = canonical_json(
            {
                "schema_version": CONTROLLED_DOCUMENT_VERSION,
                "execution_id": execution_id,
                "checkpoint_version": checkpoint_version,
                "claim_id": claim_id,
            }
        )
        try:
            with self.claim_path.open("xb") as stream:
                stream.write(claim)
                stream.flush()
        except FileExistsError as error:
            raise ResumeClaimError("checkpoint already has a live claim") from error
        return checkpoint

    async def release(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> None:
        self._validate_claim(execution_id, checkpoint_version, claim_id)
        self.claim_path.unlink()

    async def consume(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> None:
        claim = self._validate_claim(execution_id, checkpoint_version, claim_id)
        try:
            with self.consumed_path.open("xb") as stream:
                stream.write(claim)
                stream.flush()
        except FileExistsError as error:
            raise ResumeClaimError("checkpoint already consumed") from error
        self.claim_path.unlink()

    def _validate_claim(
        self,
        execution_id: str,
        checkpoint_version: int,
        claim_id: str,
    ) -> bytes:
        try:
            claim = self.claim_path.read_bytes()
        except FileNotFoundError as error:
            raise ResumeClaimError("checkpoint claim is not active") from error
        document = _object(
            load_nso_json(claim),
            "$",
            {"schema_version", "execution_id", "checkpoint_version", "claim_id"},
        )
        expected = {
            "schema_version": CONTROLLED_DOCUMENT_VERSION,
            "execution_id": execution_id,
            "checkpoint_version": checkpoint_version,
            "claim_id": claim_id,
        }
        if document != expected:
            raise ResumeClaimError("checkpoint claim ownership mismatch")
        return claim


class FakeApprovalVerifier:
    def verify(self, evidence: ApprovalEvidence) -> bool:
        return evidence.evidence_integrity == _fake_evidence_integrity(evidence)


class RejectingApprovalVerifier:
    def verify(self, evidence: ApprovalEvidence) -> bool:
        return False


async def start_controlled_run(
    *,
    skill: SkillObject,
    catalog: ToolContractCatalog,
    request: ExecutionRequest,
    handlers: Mapping[str, FixtureHandler],
    state_port: FileControlledStatePort,
    environment: RuntimeEnvironment,
    fake_approval_enabled: bool,
) -> ControlledRunSession:
    audit = InMemoryAuditSink()
    snapshots = InMemorySnapshotStore()
    tools = RecordingToolExecutor(
        MockToolExecutor(catalog, handlers), snapshots, request.data_policy
    )
    engine = RuntimeEngine(catalog, environment=environment)
    prefix = await engine.execute_controlled_prefix(
        skill, request, tools, audit, snapshots
    )
    if prefix.checkpoint is None or prefix.result.status is not ExecutionStatus.COMPLETED:
        return ControlledRunSession(
            prefix.result, None, prefix.checkpoint, tuple(audit.events)
        )
    if (
        prefix.result.validation is None
        or prefix.result.validation.status is not ValidationSummaryStatus.ALL_PASS
    ):
        return ControlledRunSession(
            prefix.result, None, prefix.checkpoint, tuple(audit.events)
        )

    context = CheckpointCoordinator().restore(prefix.checkpoint, skill, request)
    intent = await engine.prepare_controlled_write_intent(context, tools)
    plan = ControlledExecutionPlan.from_skill(skill)
    required_tool = next(
        item for item in skill.required_tools if item.tool_ref == intent.tool_ref
    )
    contract = catalog.resolve(intent.tool_id, required_tool.version)
    if contract.risk_level is None:
        raise ValueError("controlled WRITE contract requires risk_level")
    approval_request = _approval_request(
        context=context,
        skill=skill,
        plan=plan,
        intent=intent,
        risk_level=contract.risk_level,
    )
    pre_write = PreWriteEvidence(
        validation=prefix.result.validation,
        runtime_preflight_passed=True,
        current_policy_allowed=True,
        tool_preconditions_passed=True,
        business_preconditions_passed=True,
        intent_binding_valid=True,
        idempotency_ready=True,
        durability_ready=True,
        trust_valid=True,
        replay_live_write_allowed=True,
    )
    suspension = await ApprovalSuspensionCoordinator().suspend(
        context,
        approval_request,
        state_port,
        state_port,
        pre_write,
    )
    _append_profile_event(
        audit,
        request,
        skill,
        environment,
        fake_approval_enabled,
        "CLI_APPROVAL_SUSPENDED",
    )
    return ControlledRunSession(
        suspension.result,
        approval_request,
        suspension.checkpoint,
        tuple(audit.events),
    )


async def resume_controlled_run(
    *,
    skill: SkillObject,
    catalog: ToolContractCatalog,
    request: ExecutionRequest,
    handlers: Mapping[str, FixtureHandler],
    state_port: FileControlledStatePort,
    approval_request: ApprovalRequest,
    evidence: ApprovalEvidence,
    claim_id: str,
    environment: RuntimeEnvironment,
    fake_approval_enabled: bool,
) -> tuple[ApprovalResumeOutcome, tuple[AuditEvent, ...]]:
    checkpoint = ExecutionCheckpoint.from_bytes(state_port.checkpoint_path.read_bytes())
    context = CheckpointCoordinator().restore(checkpoint, skill, request)
    snapshots = InMemorySnapshotStore()
    tools = RecordingToolExecutor(
        MockToolExecutor(catalog, handlers), snapshots, request.data_policy
    )
    engine = RuntimeEngine(catalog, environment=environment)
    intent = await engine.prepare_controlled_write_intent(context, tools)
    plan = ControlledExecutionPlan.from_skill(skill)
    verifier = (
        FakeApprovalVerifier() if fake_approval_enabled else RejectingApprovalVerifier()
    )
    outcome = await ApprovalResumeCoordinator().resume(
        token=ResumeToken(
            execution_id=request.execution_id,
            business_operation_id=BusinessOperationId(
                approval_request.business_operation_id
            ),
            checkpoint_version=checkpoint.checkpoint_version,
            approval_evidence=evidence,
        ),
        skill=skill,
        request=request,
        approval_id=plan.approval.approval_id,
        write_node_id=intent.node_id,
        current_write_intent_hash=intent.write_intent_hash,
        current_payload_hash=intent.payload_hash,
        claim_id=claim_id,
        checkpoint_port=state_port,
        verifier=verifier,
        clock=RecordedApprovalClock(datetime.now(UTC)),
    )
    audit = InMemoryAuditSink()
    _append_profile_event(
        audit,
        request,
        skill,
        environment,
        fake_approval_enabled,
        "CLI_APPROVAL_RESUMED",
        {
            "approval_status": outcome.validation.approval_status.value,
            "approval_validation": outcome.validation.validation.value,
            "claim_consumed": outcome.claim_consumed,
        },
    )
    return outcome, tuple(audit.events)


def create_fake_approval_evidence(
    request: ApprovalRequest,
    *,
    approver_id: str,
    now: datetime | None = None,
) -> ApprovalEvidence:
    decided_at = datetime.now(UTC) if now is None else now
    evidence = ApprovalEvidence(
        tenant_id=request.tenant_id,
        execution_id=request.execution_id,
        business_operation_id=request.business_operation_id,
        approval_id=request.approval_id,
        approval_request_id=request.approval_request_id,
        skill_semantic_hash=request.skill_semantic_hash,
        decision=ApprovalDecision.APPROVED,
        approver_id=approver_id,
        decided_at=decided_at,
        expires_at=request.expires_at,
        payload_hash=request.payload_hash,
        write_intent_hash=request.write_intent_hash,
        evidence_integrity="pending",
    )
    return ApprovalEvidence(
        **{
            **evidence.to_data(),
            "business_operation_id": BusinessOperationId(
                evidence.business_operation_id
            ),
            "approval_request_id": ApprovalRequestId(
                evidence.approval_request_id
            ),
            "payload_hash": PayloadHash(evidence.payload_hash),
            "decision": evidence.decision,
            "decided_at": evidence.decided_at,
            "expires_at": evidence.expires_at,
            "evidence_integrity": _fake_evidence_integrity(evidence),
        }
    )


def write_approval_evidence(path: Path, evidence: ApprovalEvidence) -> None:
    _write_once(
        path,
        canonical_json(
            {
                "schema_version": CONTROLLED_DOCUMENT_VERSION,
                "approval_evidence": evidence.to_data(),
            }
        ),
    )


def load_approval_request(path: Path) -> ApprovalRequest:
    root = _object(
        load_nso_json(path.read_bytes()),
        "$",
        {"schema_version", "approval_request"},
    )
    _schema_version(root)
    item = _object(
        root["approval_request"],
        "$.approval_request",
        {
            "tenant_id",
            "execution_id",
            "business_operation_id",
            "approval_id",
            "approval_request_id",
            "skill_id",
            "skill_version",
            "skill_semantic_hash",
            "write_node_id",
            "tool_id",
            "payload_hash",
            "write_intent_hash",
            "risk_level",
            "message",
            "expires_at",
            "display_safe_summary",
            "protected_payload_ref",
            "data_classification",
        },
    )
    try:
        return ApprovalRequest(
            tenant_id=_string(item["tenant_id"], "$.approval_request.tenant_id"),
            execution_id=_string(
                item["execution_id"], "$.approval_request.execution_id"
            ),
            business_operation_id=BusinessOperationId(
                _string(
                    item["business_operation_id"],
                    "$.approval_request.business_operation_id",
                )
            ),
            approval_id=_string(
                item["approval_id"], "$.approval_request.approval_id"
            ),
            approval_request_id=ApprovalRequestId(
                _string(
                    item["approval_request_id"],
                    "$.approval_request.approval_request_id",
                )
            ),
            skill_id=_string(item["skill_id"], "$.approval_request.skill_id"),
            skill_version=_string(
                item["skill_version"], "$.approval_request.skill_version"
            ),
            skill_semantic_hash=_string(
                item["skill_semantic_hash"],
                "$.approval_request.skill_semantic_hash",
            ),
            write_node_id=_string(
                item["write_node_id"], "$.approval_request.write_node_id"
            ),
            tool_id=_string(item["tool_id"], "$.approval_request.tool_id"),
            payload_hash=PayloadHash(
                _string(item["payload_hash"], "$.approval_request.payload_hash")
            ),
            write_intent_hash=_string(
                item["write_intent_hash"],
                "$.approval_request.write_intent_hash",
            ),
            risk_level=RiskLevel(item["risk_level"]),
            message=_string(item["message"], "$.approval_request.message"),
            expires_at=_instant(
                item["expires_at"], "$.approval_request.expires_at", optional=True
            ),
            display_safe_summary=_mapping(
                item["display_safe_summary"],
                "$.approval_request.display_safe_summary",
            ),
            protected_payload_ref=_optional_string(
                item["protected_payload_ref"],
                "$.approval_request.protected_payload_ref",
            ),
            data_classification=DataClassification(item["data_classification"]),
        )
    except ValueError as error:
        raise NsoSchemaError("$.approval_request", str(error)) from error


def load_approval_evidence(path: Path) -> ApprovalEvidence:
    root = _object(
        load_nso_json(path.read_bytes()),
        "$",
        {"schema_version", "approval_evidence"},
    )
    _schema_version(root)
    item = _object(
        root["approval_evidence"],
        "$.approval_evidence",
        {
            "tenant_id",
            "execution_id",
            "business_operation_id",
            "approval_id",
            "approval_request_id",
            "skill_semantic_hash",
            "decision",
            "approver_id",
            "decided_at",
            "expires_at",
            "payload_hash",
            "write_intent_hash",
            "evidence_integrity",
        },
    )
    try:
        return ApprovalEvidence(
            tenant_id=_string(item["tenant_id"], "$.approval_evidence.tenant_id"),
            execution_id=_string(
                item["execution_id"], "$.approval_evidence.execution_id"
            ),
            business_operation_id=BusinessOperationId(
                _string(
                    item["business_operation_id"],
                    "$.approval_evidence.business_operation_id",
                )
            ),
            approval_id=_string(
                item["approval_id"], "$.approval_evidence.approval_id"
            ),
            approval_request_id=ApprovalRequestId(
                _string(
                    item["approval_request_id"],
                    "$.approval_evidence.approval_request_id",
                )
            ),
            skill_semantic_hash=_string(
                item["skill_semantic_hash"],
                "$.approval_evidence.skill_semantic_hash",
            ),
            decision=ApprovalDecision(item["decision"]),
            approver_id=_string(
                item["approver_id"], "$.approval_evidence.approver_id"
            ),
            decided_at=_instant(
                item["decided_at"], "$.approval_evidence.decided_at"
            ),
            expires_at=_instant(
                item["expires_at"], "$.approval_evidence.expires_at", optional=True
            ),
            payload_hash=PayloadHash(
                _string(item["payload_hash"], "$.approval_evidence.payload_hash")
            ),
            write_intent_hash=_string(
                item["write_intent_hash"],
                "$.approval_evidence.write_intent_hash",
            ),
            evidence_integrity=_string(
                item["evidence_integrity"],
                "$.approval_evidence.evidence_integrity",
            ),
        )
    except ValueError as error:
        raise NsoSchemaError("$.approval_evidence", str(error)) from error


def _approval_request(*, context, skill, plan, intent, risk_level) -> ApprovalRequest:
    identity = canonical_json(
        {
            "tenant_id": context.tenant_id,
            "business_operation_id": intent.business_operation_id,
            "skill_semantic_hash": skill.semantic_hash,
            "write_node_id": intent.node_id,
            "write_intent_hash": intent.write_intent_hash,
        }
    )
    digest = sha256(identity).hexdigest()
    expires_at = (
        None
        if plan.approval.policy.ttl_seconds is None
        else datetime.now(UTC)
        + timedelta(seconds=plan.approval.policy.ttl_seconds)
    )
    return ApprovalRequest(
        tenant_id=context.tenant_id,
        execution_id=context.identity.execution_id,
        business_operation_id=intent.business_operation_id,
        approval_id=plan.approval.approval_id,
        approval_request_id=ApprovalRequestId(f"cli-approval-{digest[:32]}"),
        skill_id=skill.skill_id,
        skill_version=skill.skill_version,
        skill_semantic_hash=skill.semantic_hash,
        write_node_id=intent.node_id,
        tool_id=intent.tool_id,
        payload_hash=intent.payload_hash,
        write_intent_hash=intent.write_intent_hash,
        risk_level=risk_level,
        message=plan.approval.message,
        expires_at=expires_at,
        display_safe_summary={
            "tool_id": intent.tool_id,
            "risk_level": risk_level.value,
        },
        protected_payload_ref=(
            f"protected://cli/{context.tenant_id}/{intent.payload_hash[7:23]}"
        ),
        data_classification=highest_classification(
            *(item.classification for item in intent.canonical_arguments.values())
        ),
    )


def _fake_evidence_integrity(evidence: ApprovalEvidence) -> str:
    data = evidence.to_data()
    data.pop("evidence_integrity")
    return _FAKE_APPROVAL_PREFIX + sha256(canonical_json(data)).hexdigest()


def _append_profile_event(
    sink: InMemoryAuditSink,
    request: ExecutionRequest,
    skill: SkillObject,
    environment: RuntimeEnvironment,
    fake_approval_enabled: bool,
    event_type: str,
    extra: Mapping[str, Any] | None = None,
) -> None:
    previous = sink.events[-1] if sink.events else None
    payload = {
        "cli_profile_environment": environment.value,
        "fake_approval_enabled": fake_approval_enabled,
        **({} if extra is None else dict(extra)),
    }
    sink.append(
        AuditEvent.create(
            execution_id=request.execution_id,
            sequence=1 if previous is None else previous.sequence + 1,
            event_type=event_type,
            skill_id=skill.skill_id,
            skill_version=skill.skill_version,
            semantic_hash=skill.semantic_hash,
            runtime_version="0.1.0",
            tenant_id=redact_text(request.principal.tenant_id),
            subject_id=redact_text(request.principal.subject_id),
            auth_context_ref=redact_text(request.principal.auth_context_ref),
            classification=DataClassification.INTERNAL,
            payload=payload,
            previous_event_hash=None if previous is None else previous.event_hash,
            retention_days=request.data_policy.audit_retention_days,
        )
    )


def _write_once(path: Path, data: bytes) -> None:
    ensure_no_credential_material(load_nso_json(data), "CLI controlled evidence")
    if path.exists():
        if path.read_bytes() == data:
            return
        raise ValueError(f"durable evidence collision: {path.name}")
    temporary = path.with_name(f".{path.name}.nsl-tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _schema_version(root: dict[str, Any]) -> None:
    if root["schema_version"] != CONTROLLED_DOCUMENT_VERSION:
        raise NsoSchemaError("$.schema_version", "expected '1.0'")


def _object(
    value: Any, path: str, required: set[str], optional: set[str] | None = None
) -> dict[str, Any]:
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected object")
    allowed = required | (optional or set())
    missing = required - set(value)
    if missing:
        raise NsoSchemaError(path, f"missing fields: {sorted(missing)}")
    unexpected = set(value) - allowed
    if unexpected:
        raise NsoSchemaError(path, f"unexpected fields: {sorted(unexpected)}")
    return value


def _string(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise NsoSchemaError(path, "expected non-empty string")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected object")
    return value


def _instant(value: Any, path: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if type(value) is not str:
        raise NsoSchemaError(path, "expected UTC date-time string")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as error:
        raise NsoSchemaError(path, "invalid date-time") from error
    if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        raise NsoSchemaError(path, "expected UTC date-time string")
    return instant
