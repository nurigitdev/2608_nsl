from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from .audit import RUNTIME_VERSION
from .core import (
    ApprovalStatus,
    BusinessOperationId,
    CheckStatus,
    Completeness,
    DataClassification,
    ExecutionStatus,
    PayloadHash,
    Presence,
    TypeRef,
    ValueEnvelope,
    decode_value,
    encode_value,
)
from .ir import SkillObject, canonical_json
from .json_boundary import load_strict_json
from .runtime_models import (
    CheckResult,
    EmitRecord,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    RuntimeClock,
)


CHECKPOINT_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1


class CheckpointError(RuntimeError):
    pass


def _error(path: str, message: str) -> CheckpointError:
    return CheckpointError(f"{path}: {message}")


def _required_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, "expected a non-empty string")
    return value


def _non_negative_int(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _error(path, "expected a non-negative integer")
    return value


def _positive_int(value: object, path: str) -> int:
    value = _non_negative_int(value, path)
    if value == 0:
        raise _error(path, "expected a positive integer")
    return value


def _exact_object(
    value: object,
    fields: set[str],
    path: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise _error(path, "invalid object schema")
    return value


def _string_map(value: object, path: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise _error(path, "expected an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        result[_required_string(key, f"{path}.key")] = _required_string(
            item, f"{path}.{key}"
        )
    return result


def _optional_string(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, path)


def _integrity_material(
    *,
    execution_id: str,
    business_operation_id: BusinessOperationId,
    checkpoint_version: int,
    skill_id: str,
    skill_version: str,
    semantic_hash: str,
    ir_version: str,
    runtime_version: str,
    state_payload: bytes,
) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "execution_id": execution_id,
        "business_operation_id": business_operation_id,
        "checkpoint_version": checkpoint_version,
        "skill_id": skill_id,
        "skill_version": skill_version,
        "semantic_hash": semantic_hash,
        "ir_version": ir_version,
        "runtime_version": runtime_version,
        "state_payload": b64encode(state_payload).decode("ascii"),
    }


@dataclass(frozen=True, slots=True)
class ExecutionCheckpoint:
    execution_id: str
    business_operation_id: BusinessOperationId
    checkpoint_version: int
    skill_id: str
    skill_version: str
    semantic_hash: str
    ir_version: str
    runtime_version: str
    state_payload: bytes
    integrity_hash: str

    def __post_init__(self) -> None:
        for name in (
            "execution_id",
            "business_operation_id",
            "skill_id",
            "skill_version",
            "semantic_hash",
            "ir_version",
            "runtime_version",
        ):
            _required_string(getattr(self, name), f"$.{name}")
        _positive_int(self.checkpoint_version, "$.checkpoint_version")
        if type(self.state_payload) is not bytes:
            raise _error("$.state_payload", "expected bytes")
        if (
            not isinstance(self.integrity_hash, str)
            or len(self.integrity_hash) != 71
            or not self.integrity_hash.startswith("sha256:")
        ):
            raise _error("$.integrity_hash", "expected a SHA-256 value")

    @classmethod
    def create(
        cls,
        *,
        execution_id: str,
        business_operation_id: BusinessOperationId,
        checkpoint_version: int,
        skill_id: str,
        skill_version: str,
        semantic_hash: str,
        ir_version: str,
        runtime_version: str,
        state_payload: bytes,
    ) -> ExecutionCheckpoint:
        if type(state_payload) is not bytes:
            raise _error("$.state_payload", "expected bytes")
        material = _integrity_material(
            execution_id=execution_id,
            business_operation_id=business_operation_id,
            checkpoint_version=checkpoint_version,
            skill_id=skill_id,
            skill_version=skill_version,
            semantic_hash=semantic_hash,
            ir_version=ir_version,
            runtime_version=runtime_version,
            state_payload=state_payload,
        )
        integrity_hash = "sha256:" + sha256(canonical_json(material)).hexdigest()
        return cls(
            execution_id=execution_id,
            business_operation_id=business_operation_id,
            checkpoint_version=checkpoint_version,
            skill_id=skill_id,
            skill_version=skill_version,
            semantic_hash=semantic_hash,
            ir_version=ir_version,
            runtime_version=runtime_version,
            state_payload=state_payload,
            integrity_hash=integrity_hash,
        )

    def verify(self) -> None:
        expected = ExecutionCheckpoint.create(
            execution_id=self.execution_id,
            business_operation_id=self.business_operation_id,
            checkpoint_version=self.checkpoint_version,
            skill_id=self.skill_id,
            skill_version=self.skill_version,
            semantic_hash=self.semantic_hash,
            ir_version=self.ir_version,
            runtime_version=self.runtime_version,
            state_payload=self.state_payload,
        ).integrity_hash
        if self.integrity_hash != expected:
            raise _error("$.integrity_hash", "checkpoint integrity mismatch")

    def to_data(self) -> dict[str, Any]:
        return {
            **_integrity_material(
                execution_id=self.execution_id,
                business_operation_id=self.business_operation_id,
                checkpoint_version=self.checkpoint_version,
                skill_id=self.skill_id,
                skill_version=self.skill_version,
                semantic_hash=self.semantic_hash,
                ir_version=self.ir_version,
                runtime_version=self.runtime_version,
                state_payload=self.state_payload,
            ),
            "integrity_hash": self.integrity_hash,
        }

    def to_bytes(self) -> bytes:
        self.verify()
        return canonical_json(self.to_data())

    @classmethod
    def from_bytes(cls, data: bytes) -> ExecutionCheckpoint:
        document = load_strict_json(
            data,
            document_name="checkpoint",
            error_factory=_error,
        )
        item = _exact_object(
            document,
            {
                "schema_version",
                "execution_id",
                "business_operation_id",
                "checkpoint_version",
                "skill_id",
                "skill_version",
                "semantic_hash",
                "ir_version",
                "runtime_version",
                "state_payload",
                "integrity_hash",
            },
            "$",
        )
        if item["schema_version"] != CHECKPOINT_SCHEMA_VERSION:
            raise _error("$.schema_version", "unsupported checkpoint schema")
        try:
            state_payload = b64decode(item["state_payload"], validate=True)
        except (TypeError, ValueError) as error:
            raise _error("$.state_payload", "invalid base64 payload") from error
        checkpoint = cls(
            execution_id=item["execution_id"],
            business_operation_id=BusinessOperationId(
                item["business_operation_id"]
            ),
            checkpoint_version=item["checkpoint_version"],
            skill_id=item["skill_id"],
            skill_version=item["skill_version"],
            semantic_hash=item["semantic_hash"],
            ir_version=item["ir_version"],
            runtime_version=item["runtime_version"],
            state_payload=state_payload,
            integrity_hash=item["integrity_hash"],
        )
        checkpoint.verify()
        return checkpoint


@dataclass(frozen=True, slots=True)
class CheckpointedExecution:
    """Completed READ/CHECK execution paired with its immutable checkpoint."""

    result: ExecutionResult
    checkpoint: ExecutionCheckpoint | None

    def __post_init__(self) -> None:
        if not isinstance(self.result, ExecutionResult):
            raise TypeError("result must be an ExecutionResult")
        if self.checkpoint is not None and not isinstance(
            self.checkpoint, ExecutionCheckpoint
        ):
            raise TypeError("checkpoint must be an ExecutionCheckpoint or None")
        if self.result.status is not ExecutionStatus.COMPLETED:
            if self.checkpoint is not None:
                raise ValueError("only completed execution may carry a checkpoint")
            return
        if self.checkpoint is None:
            raise ValueError("completed execution requires a checkpoint")
        expected = {
            "execution_id": self.result.execution_id,
            "skill_id": self.result.skill_id,
            "skill_version": self.result.skill_version,
            "semantic_hash": self.result.semantic_hash,
        }
        for name, value in expected.items():
            if getattr(self.checkpoint, name) != value:
                raise ValueError(f"checkpoint {name} does not match result")


def _envelope_to_data(value: ValueEnvelope) -> dict[str, Any]:
    return {
        "value": encode_value(value.value),
        "type": value.type_info.to_data(),
        "presence": value.presence.value,
        "completeness": value.completeness.value,
        "classification": value.classification.value,
        "provenance_refs": list(value.provenance_refs),
    }


def _envelope_from_data(value: object, path: str) -> ValueEnvelope:
    item = _exact_object(
        value,
        {
            "value",
            "type",
            "presence",
            "completeness",
            "classification",
            "provenance_refs",
        },
        path,
    )
    if not isinstance(item["provenance_refs"], list) or not all(
        isinstance(ref, str) and ref for ref in item["provenance_refs"]
    ):
        raise _error(f"{path}.provenance_refs", "expected non-empty strings")
    try:
        return ValueEnvelope(
            decode_value(item["value"]),
            TypeRef.from_data(item["type"]),
            Presence(item["presence"]),
            Completeness(item["completeness"]),
            DataClassification(item["classification"]),
            tuple(item["provenance_refs"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _error(path, "invalid ValueEnvelope") from error


def _namespace_to_data(value: Mapping[str, ValueEnvelope]) -> dict[str, Any]:
    return {name: _envelope_to_data(item) for name, item in value.items()}


def _namespace_from_data(value: object, path: str) -> dict[str, ValueEnvelope]:
    if not isinstance(value, dict):
        raise _error(path, "expected an object")
    return {
        _required_string(name, f"{path}.key"): _envelope_from_data(
            item, f"{path}.{name}"
        )
        for name, item in value.items()
    }


def _check_to_data(check: CheckResult) -> dict[str, Any]:
    return {
        "check_id": check.check_id,
        "status": check.status.value,
        "severity": check.severity,
        "message": check.message,
        "condition_node_id": check.condition_node_id,
        "presence": check.presence.value,
        "completeness": check.completeness.value,
        "provenance_refs": list(check.provenance_refs),
        "reason_code": check.reason_code,
    }


def _check_from_data(value: object, path: str) -> CheckResult:
    item = _exact_object(
        value,
        {
            "check_id",
            "status",
            "severity",
            "message",
            "condition_node_id",
            "presence",
            "completeness",
            "provenance_refs",
            "reason_code",
        },
        path,
    )
    try:
        refs = tuple(item["provenance_refs"])
        if not all(isinstance(ref, str) and ref for ref in refs):
            raise ValueError
        return CheckResult(
            _required_string(item["check_id"], f"{path}.check_id"),
            CheckStatus(item["status"]),
            _required_string(item["severity"], f"{path}.severity"),
            str(item["message"]),
            _required_string(
                item["condition_node_id"], f"{path}.condition_node_id"
            ),
            Presence(item["presence"]),
            Completeness(item["completeness"]),
            refs,
            _optional_string(item["reason_code"], f"{path}.reason_code"),
        )
    except (TypeError, ValueError) as error:
        raise _error(path, "invalid CheckResult") from error


def _state_payload(ctx: ExecutionContext, checkpoint_version: int) -> bytes:
    principal = ctx.security_context.principal
    policy = ctx.security_context.data_policy
    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "checkpoint_version": checkpoint_version,
        "security": {
            "principal": {
                "tenant_id": principal.tenant_id,
                "subject_id": principal.subject_id,
                "actor_type": principal.actor_type,
                "roles": sorted(principal.roles),
                "scopes": sorted(principal.scopes),
                "auth_context_ref": principal.auth_context_ref,
                "verification": principal.verification.value,
                "on_behalf_of": principal.on_behalf_of,
            },
            "data_policy": {
                "max_trace_classification": policy.max_trace_classification.value,
                "snapshot_retention_days": policy.snapshot_retention_days,
                "audit_retention_days": policy.audit_retention_days,
            },
        },
        "symbols": {
            "inputs": _namespace_to_data(ctx.input_values),
            "contexts": _namespace_to_data(ctx.context_values),
            "frames": [_namespace_to_data(frame) for frame in ctx.frames],
            "check_frames": [
                _namespace_to_data(frame) for frame in ctx.check_frames
            ],
        },
        "checks": [_check_to_data(check) for check in ctx.checks],
        "outputs": [
            _namespace_to_data(record.fields) for record in ctx.outputs
        ],
        "resources": {
            "tool_calls": ctx.resources.tool_calls,
            "loop_iterations": ctx.resources.loop_iterations,
            "emitted_rows": ctx.resources.emitted_rows,
            "max_collection_size_seen": ctx.resources.max_collection_size_seen,
        },
        "execution": {
            "data_completeness": ctx.data_completeness.value,
            "invocation_counter": ctx.invocation_counter,
            "foreach_depth": ctx.foreach_depth,
            "current_node_id": ctx.current_node_id,
        },
        "approvals": {
            name: status.value for name, status in ctx.approvals.records.items()
        },
        "writes": encode_value(ctx.writes.entries),
        "idempotency": {
            "keys_by_write_node": dict(ctx.idempotency.keys_by_write_node),
            "payload_hashes_by_write_node": dict(
                ctx.idempotency.payload_hashes_by_write_node
            ),
        },
        "checkpoint": {
            "checkpoint_version": checkpoint_version,
            "last_completed_node_id": ctx.checkpoint.last_completed_node_id,
            "suspended_at_node_id": ctx.checkpoint.suspended_at_node_id,
            "trace_sequence": ctx.checkpoint.trace_sequence,
            "approval_request_published": (
                ctx.checkpoint.approval_request_published
            ),
            "write_request_durable": ctx.checkpoint.write_request_durable,
            "approval_request_ids_by_write_node": dict(
                ctx.checkpoint.approval_request_ids_by_write_node
            ),
            "write_intent_hashes_by_write_node": dict(
                ctx.checkpoint.write_intent_hashes_by_write_node
            ),
            "payload_hashes_by_write_node": dict(
                ctx.checkpoint.payload_hashes_by_write_node
            ),
        },
    }
    try:
        return canonical_json(payload)
    except (TypeError, ValueError) as error:
        raise _error("$.state_payload", "state is not serializable") from error


class CheckpointCoordinator:
    def __init__(self, runtime_version: str = RUNTIME_VERSION) -> None:
        self.runtime_version = _required_string(runtime_version, "runtime_version")

    def capture(self, ctx: ExecutionContext) -> ExecutionCheckpoint:
        business_operation_id = ctx.identity.business_operation_id
        if business_operation_id is None:
            raise _error(
                "$.business_operation_id",
                "checkpoint requires a business operation identity",
            )
        next_version = ctx.checkpoint.checkpoint_version + 1
        state_payload = _state_payload(ctx, next_version)
        checkpoint = ExecutionCheckpoint.create(
            execution_id=ctx.identity.execution_id,
            business_operation_id=business_operation_id,
            checkpoint_version=next_version,
            skill_id=ctx.skill.skill_id,
            skill_version=ctx.skill.skill_version,
            semantic_hash=ctx.skill.semantic_hash,
            ir_version=ctx.skill.ir_version,
            runtime_version=self.runtime_version,
            state_payload=state_payload,
        )
        ctx.checkpoint.checkpoint_version = next_version
        return checkpoint

    def restore(
        self,
        checkpoint: ExecutionCheckpoint,
        skill: SkillObject,
        request: ExecutionRequest,
        runtime_clock: RuntimeClock | None = None,
    ) -> ExecutionContext:
        checkpoint.verify()
        self._validate_bindings(checkpoint, skill, request)
        document = load_strict_json(
            checkpoint.state_payload,
            document_name="checkpoint state",
            error_factory=_error,
        )
        state = _decode_state(document, checkpoint, request)
        if runtime_clock is None:
            ctx = ExecutionContext(skill, request)
        else:
            ctx = ExecutionContext(skill, request, runtime_clock=runtime_clock)
        _restore_state(ctx, state)
        return ctx

    def _validate_bindings(
        self,
        checkpoint: ExecutionCheckpoint,
        skill: SkillObject,
        request: ExecutionRequest,
    ) -> None:
        expected = {
            "execution_id": request.execution_id,
            "business_operation_id": request.business_operation_id,
            "skill_id": skill.skill_id,
            "skill_version": skill.skill_version,
            "semantic_hash": skill.semantic_hash,
            "ir_version": skill.ir_version,
            "runtime_version": self.runtime_version,
        }
        for name, value in expected.items():
            if getattr(checkpoint, name) != value:
                raise _error(f"$.{name}", "checkpoint binding mismatch")


def _decode_state(
    value: object,
    checkpoint: ExecutionCheckpoint,
    request: ExecutionRequest,
) -> dict[str, Any]:
    state = _exact_object(
        value,
        {
            "schema_version",
            "checkpoint_version",
            "security",
            "symbols",
            "checks",
            "outputs",
            "resources",
            "execution",
            "approvals",
            "writes",
            "idempotency",
            "checkpoint",
        },
        "$",
    )
    if state["schema_version"] != STATE_SCHEMA_VERSION:
        raise _error("$.schema_version", "unsupported state schema")
    if state["checkpoint_version"] != checkpoint.checkpoint_version:
        raise _error("$.checkpoint_version", "checkpoint binding mismatch")
    _validate_security(state["security"], request)
    return state


def _validate_security(value: object, request: ExecutionRequest) -> None:
    security = _exact_object(value, {"principal", "data_policy"}, "$.security")
    principal = request.principal
    policy = request.data_policy
    expected = {
        "principal": {
            "tenant_id": principal.tenant_id,
            "subject_id": principal.subject_id,
            "actor_type": principal.actor_type,
            "roles": sorted(principal.roles),
            "scopes": sorted(principal.scopes),
            "auth_context_ref": principal.auth_context_ref,
            "verification": principal.verification.value,
            "on_behalf_of": principal.on_behalf_of,
        },
        "data_policy": {
            "max_trace_classification": policy.max_trace_classification.value,
            "snapshot_retention_days": policy.snapshot_retention_days,
            "audit_retention_days": policy.audit_retention_days,
        },
    }
    if security != expected:
        raise _error("$.security", "security context binding mismatch")


def _restore_state(ctx: ExecutionContext, state: Mapping[str, Any]) -> None:
    symbols = _exact_object(
        state["symbols"],
        {"inputs", "contexts", "frames", "check_frames"},
        "$.symbols",
    )
    frames = symbols["frames"]
    check_frames = symbols["check_frames"]
    if not isinstance(frames, list) or not frames:
        raise _error("$.symbols.frames", "expected a non-empty array")
    if not isinstance(check_frames, list) or len(check_frames) != len(frames):
        raise _error("$.symbols.check_frames", "frame depth mismatch")
    ctx.input_values = _namespace_from_data(symbols["inputs"], "$.symbols.inputs")
    ctx.context_values = _namespace_from_data(
        symbols["contexts"], "$.symbols.contexts"
    )
    ctx.frames = [
        _namespace_from_data(frame, f"$.symbols.frames[{index}]")
        for index, frame in enumerate(frames)
    ]
    ctx.check_frames = [
        _namespace_from_data(frame, f"$.symbols.check_frames[{index}]")
        for index, frame in enumerate(check_frames)
    ]

    checks = state["checks"]
    outputs = state["outputs"]
    if not isinstance(checks, list) or not isinstance(outputs, list):
        raise _error("$", "checks and outputs must be arrays")
    ctx.checks = [
        _check_from_data(item, f"$.checks[{index}]")
        for index, item in enumerate(checks)
    ]
    ctx.outputs = [
        EmitRecord(_namespace_from_data(item, f"$.outputs[{index}]"))
        for index, item in enumerate(outputs)
    ]

    resources = _exact_object(
        state["resources"],
        {
            "tool_calls",
            "loop_iterations",
            "emitted_rows",
            "max_collection_size_seen",
        },
        "$.resources",
    )
    for name in resources:
        setattr(
            ctx.resources,
            name,
            _non_negative_int(resources[name], f"$.resources.{name}"),
        )

    execution = _exact_object(
        state["execution"],
        {
            "data_completeness",
            "invocation_counter",
            "foreach_depth",
            "current_node_id",
        },
        "$.execution",
    )
    try:
        ctx.data_completeness = Completeness(execution["data_completeness"])
    except (TypeError, ValueError) as error:
        raise _error("$.execution.data_completeness", "invalid value") from error
    ctx.invocation_counter = _non_negative_int(
        execution["invocation_counter"], "$.execution.invocation_counter"
    )
    ctx.foreach_depth = _non_negative_int(
        execution["foreach_depth"], "$.execution.foreach_depth"
    )
    ctx.current_node_id = _optional_string(
        execution["current_node_id"], "$.execution.current_node_id"
    )

    approvals = state["approvals"]
    if not isinstance(approvals, dict):
        raise _error("$.approvals", "expected an object")
    try:
        ctx.approvals.records = {
            _required_string(name, "$.approvals.key"): ApprovalStatus(status)
            for name, status in approvals.items()
        }
    except (TypeError, ValueError) as error:
        raise _error("$.approvals", "invalid approval status") from error
    try:
        writes = decode_value(state["writes"])
    except (TypeError, ValueError) as error:
        raise _error("$.writes", "invalid encoded value") from error
    if not isinstance(writes, list):
        raise _error("$.writes", "expected an array")
    ctx.writes.entries = writes

    idempotency = _exact_object(
        state["idempotency"],
        {"keys_by_write_node", "payload_hashes_by_write_node"},
        "$.idempotency",
    )
    ctx.idempotency.keys_by_write_node = _string_map(
        idempotency["keys_by_write_node"],
        "$.idempotency.keys_by_write_node",
    )
    ctx.idempotency.payload_hashes_by_write_node = {
        name: PayloadHash(value)
        for name, value in _string_map(
            idempotency["payload_hashes_by_write_node"],
            "$.idempotency.payload_hashes_by_write_node",
        ).items()
    }

    checkpoint = _exact_object(
        state["checkpoint"],
        {
            "checkpoint_version",
            "last_completed_node_id",
            "suspended_at_node_id",
            "trace_sequence",
            "approval_request_published",
            "write_request_durable",
            "approval_request_ids_by_write_node",
            "write_intent_hashes_by_write_node",
            "payload_hashes_by_write_node",
        },
        "$.checkpoint",
    )
    restored_version = _positive_int(
        checkpoint["checkpoint_version"], "$.checkpoint.checkpoint_version"
    )
    if restored_version != state["checkpoint_version"]:
        raise _error(
            "$.checkpoint.checkpoint_version", "checkpoint binding mismatch"
        )
    ctx.checkpoint.checkpoint_version = restored_version
    ctx.checkpoint.last_completed_node_id = _optional_string(
        checkpoint["last_completed_node_id"],
        "$.checkpoint.last_completed_node_id",
    )
    ctx.checkpoint.suspended_at_node_id = _optional_string(
        checkpoint["suspended_at_node_id"],
        "$.checkpoint.suspended_at_node_id",
    )
    ctx.checkpoint.trace_sequence = _non_negative_int(
        checkpoint["trace_sequence"], "$.checkpoint.trace_sequence"
    )
    for name in ("approval_request_published", "write_request_durable"):
        if type(checkpoint[name]) is not bool:
            raise _error(f"$.checkpoint.{name}", "expected a boolean")
        setattr(ctx.checkpoint, name, checkpoint[name])
    ctx.checkpoint.approval_request_ids_by_write_node = _string_map(
        checkpoint["approval_request_ids_by_write_node"],
        "$.checkpoint.approval_request_ids_by_write_node",
    )
    ctx.checkpoint.write_intent_hashes_by_write_node = _string_map(
        checkpoint["write_intent_hashes_by_write_node"],
        "$.checkpoint.write_intent_hashes_by_write_node",
    )
    ctx.checkpoint.payload_hashes_by_write_node = {
        name: PayloadHash(value)
        for name, value in _string_map(
            checkpoint["payload_hashes_by_write_node"],
            "$.checkpoint.payload_hashes_by_write_node",
        ).items()
    }
