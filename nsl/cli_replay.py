from __future__ import annotations

from typing import Any, cast

from .audit import (
    InMemoryAuditSink,
    InMemorySnapshotStore,
    RUNTIME_VERSION,
    SnapshotRef,
    value_hash,
)
from .cli_runtime import CLIRunSession
from .core import (
    Completeness,
    DataClassification,
    Presence,
    classification_allows,
    decode_value,
    encode_value,
)
from .ir import SkillObject, canonical_json
from .ir_schema import NsoSchemaError, load_nso_json
from .replay import (
    RecordedToolCall,
    ReplayBundle,
    ReplayReport,
    create_replay_bundle,
    replay_and_compare,
)
from .runtime import RuntimeEngine
from .runtime_models import highest_declared_classification
from .security import DataHandlingPolicy, ExecutionPrincipal
from .tools import (
    IncompatibleToolVersionError,
    ToolContractCatalog,
    ToolContractValidator,
    ToolExecutionError,
    ToolResultEnvelope,
    UnknownToolContractError,
    tool_result_hash,
)


REPLAY_FORMAT = "NSL-REPLAY"
REPLAY_SCHEMA_VERSION = "1.0"


class ReplayPackageSequenceError(RuntimeError):
    pass


def validate_portable_replay_skill(
    skill: SkillObject, catalog: ToolContractCatalog
) -> None:
    classifications = [
        *(item.classification for item in skill.inputs),
        *(item.classification for item in skill.contexts),
        *(item.classification for item in skill.outputs),
    ]
    try:
        classifications.extend(
            catalog.resolve(item.tool_id, item.version).output_classification
            for item in skill.required_tools
        )
    except (UnknownToolContractError, IncompatibleToolVersionError) as error:
        raise ValueError("portable replay requires every tool contract") from error
    for classification in classifications:
        _require_portable_classification(classification)


def encode_replay_package(
    session: CLIRunSession,
    skill: SkillObject,
    principal: ExecutionPrincipal,
) -> bytes:
    bundle = create_replay_bundle(
        skill,
        session.request,
        session.result,
        session.recording,
        session.snapshots,
    )
    original_result_ref = cast(SnapshotRef, bundle.original_result_ref)
    references = [bundle.inputs_ref, bundle.context_ref, original_result_ref]
    references.extend(call.result_ref for call in bundle.tool_calls)
    for reference in references:
        _require_portable_classification(reference.classification)

    tool_calls = []
    for call in bundle.tool_calls:
        result = session.snapshots.get(call.result_ref, principal)
        if not isinstance(result, ToolResultEnvelope):
            raise TypeError("recorded tool result snapshot is invalid")
        tool_calls.append(
            {
                "tool_id": call.tool_id,
                "tool_version": call.tool_version,
                "argument_hash": call.argument_hash,
                "classification": call.result_ref.classification.value,
                "result": encode_value(result.value),
            }
        )

    payload = {
        "format": REPLAY_FORMAT,
        "schema_version": REPLAY_SCHEMA_VERSION,
        "semantic_hash": bundle.semantic_hash,
        "tenant_id": bundle.tenant_id,
        "original_execution_id": bundle.original_execution_id,
        "runtime_version": bundle.runtime_version,
        "classifications": {
            "inputs": bundle.inputs_ref.classification.value,
            "context": bundle.context_ref.classification.value,
            "original_result": original_result_ref.classification.value,
        },
        "inputs": encode_value(session.snapshots.get(bundle.inputs_ref, principal)),
        "context": encode_value(session.snapshots.get(bundle.context_ref, principal)),
        "tool_calls": tool_calls,
        "original_result": encode_value(
            session.snapshots.get(original_result_ref, principal)
        ),
    }
    return canonical_json({**payload, "integrity_sha256": value_hash(payload)})


def decode_replay_package(
    data: bytes,
    skill: SkillObject,
    catalog: ToolContractCatalog,
    principal: ExecutionPrincipal,
) -> tuple[ReplayBundle, InMemorySnapshotStore]:
    root = _object(
        load_nso_json(data),
        "$",
        {
            "format",
            "schema_version",
            "semantic_hash",
            "tenant_id",
            "original_execution_id",
            "runtime_version",
            "classifications",
            "inputs",
            "context",
            "tool_calls",
            "original_result",
            "integrity_sha256",
        },
    )
    if root["format"] != REPLAY_FORMAT or root["schema_version"] != REPLAY_SCHEMA_VERSION:
        raise NsoSchemaError("$", "unsupported replay package format")
    integrity = _digest(root["integrity_sha256"], "$.integrity_sha256")
    payload = {key: value for key, value in root.items() if key != "integrity_sha256"}
    if integrity != value_hash(payload):
        raise NsoSchemaError("$.integrity_sha256", "replay package integrity mismatch")

    semantic_hash = _string(root["semantic_hash"], "$.semantic_hash")
    if semantic_hash != skill.semantic_hash:
        raise NsoSchemaError("$.semantic_hash", "replay semantic identity mismatch")
    tenant_id = _string(root["tenant_id"], "$.tenant_id")
    if tenant_id != principal.tenant_id:
        raise NsoSchemaError("$.tenant_id", "cross-tenant replay is forbidden")

    classifications = _object(
        root["classifications"],
        "$.classifications",
        {"inputs", "context", "original_result"},
    )
    input_classification = _classification(
        classifications["inputs"], "$.classifications.inputs"
    )
    context_classification = _classification(
        classifications["context"], "$.classifications.context"
    )
    result_classification = _classification(
        classifications["original_result"], "$.classifications.original_result"
    )
    expected_classifications = (
        highest_declared_classification(skill.inputs),
        highest_declared_classification(skill.contexts),
        highest_declared_classification((*skill.inputs, *skill.contexts, *skill.outputs)),
    )
    if (
        input_classification,
        context_classification,
        result_classification,
    ) != expected_classifications:
        raise NsoSchemaError("$.classifications", "replay classification mismatch")
    for classification in expected_classifications:
        _require_portable_classification(classification)

    inputs = _decoded_object(root["inputs"], "$.inputs")
    context = _decoded_object(root["context"], "$.context")
    original_result = _decoded_object(root["original_result"], "$.original_result")
    store = InMemorySnapshotStore()
    policy = DataHandlingPolicy()
    inputs_ref = store.put(tenant_id, inputs, input_classification, policy.snapshot_retention_days)
    context_ref = store.put(
        tenant_id, context, context_classification, policy.snapshot_retention_days
    )
    calls = _decode_tool_calls(
        root["tool_calls"], catalog, tenant_id, store, policy
    )
    original_result_ref = store.put(
        tenant_id,
        original_result,
        result_classification,
        policy.snapshot_retention_days,
    )
    return (
        ReplayBundle(
            semantic_hash=semantic_hash,
            tenant_id=tenant_id,
            inputs_ref=inputs_ref,
            context_ref=context_ref,
            tool_calls=calls,
            original_execution_id=_string(
                root["original_execution_id"], "$.original_execution_id"
            ),
            runtime_version=_string(root["runtime_version"], "$.runtime_version"),
            original_result_ref=original_result_ref,
        ),
        store,
    )


async def execute_replay_package(
    bundle: ReplayBundle,
    store: InMemorySnapshotStore,
    skill: SkillObject,
    catalog: ToolContractCatalog,
    principal: ExecutionPrincipal,
    execution_id: str,
) -> ReplayReport:
    try:
        return await replay_and_compare(
            bundle,
            skill,
            replay_execution_id=execution_id,
            principal=principal,
            policy=DataHandlingPolicy(),
            snapshots=store,
            runtime=RuntimeEngine(catalog),
            audit_sink=InMemoryAuditSink(),
            runtime_version=RUNTIME_VERSION,
        )
    except RuntimeError as error:
        raise ReplayPackageSequenceError(
            "recorded tool sequence differs from replay execution"
        ) from error


def _decode_tool_calls(
    value: Any,
    catalog: ToolContractCatalog,
    tenant_id: str,
    store: InMemorySnapshotStore,
    policy: DataHandlingPolicy,
) -> tuple[RecordedToolCall, ...]:
    calls = []
    validator = ToolContractValidator()
    for index, value in enumerate(_array(value, "$.tool_calls")):
        path = f"$.tool_calls[{index}]"
        item = _object(
            value,
            path,
            {
                "tool_id",
                "tool_version",
                "argument_hash",
                "classification",
                "result",
            },
        )
        tool_id = _string(item["tool_id"], f"{path}.tool_id")
        version = _string(item["tool_version"], f"{path}.tool_version")
        try:
            contract = catalog.resolve(tool_id, version)
        except (UnknownToolContractError, IncompatibleToolVersionError) as error:
            raise NsoSchemaError(path, "replay tool contract unavailable") from error
        classification = _classification(
            item["classification"], f"{path}.classification"
        )
        if classification is not contract.output_classification:
            raise NsoSchemaError(path, "replay tool classification mismatch")
        _require_portable_classification(classification)
        result_value = decode_value(item["result"])
        try:
            validator.validate_result_value(result_value, contract)
        except ToolExecutionError as error:
            raise NsoSchemaError(path, "replay tool result is invalid") from error
        presence = (
            Presence.EMPTY
            if result_value == [] or result_value is None
            else Presence.PRESENT
        )
        result_hash = tool_result_hash(result_value)
        result = ToolResultEnvelope(
            invocation_id=f"recorded-{index + 1}",
            tool_id=tool_id,
            tool_version=version,
            value=result_value,
            type_info=contract.output_type,
            presence=presence,
            completeness=Completeness.COMPLETE,
            classification=classification,
            result_hash=result_hash,
        )
        result_ref = store.put(
            tenant_id,
            result,
            classification,
            policy.snapshot_retention_days,
            hash_material={
                "tool_id": tool_id,
                "tool_version": version,
                "value": result_value,
                "presence": presence.value,
                "completeness": Completeness.COMPLETE.value,
                "result_hash": result_hash,
            },
        )
        calls.append(
            RecordedToolCall(
                tool_id,
                version,
                _digest(item["argument_hash"], f"{path}.argument_hash"),
                result_ref,
            )
        )
    return tuple(calls)


def _require_portable_classification(classification: DataClassification) -> None:
    if not classification_allows(classification, DataClassification.INTERNAL):
        raise ValueError(
            "portable replay packages require PUBLIC or INTERNAL data; "
            "use a protected snapshot backend for higher classifications"
        )


def _decoded_object(value: Any, path: str) -> dict[str, Any]:
    decoded = decode_value(value)
    if type(decoded) is not dict:
        raise NsoSchemaError(path, "expected object")
    return decoded


def _classification(value: Any, path: str) -> DataClassification:
    try:
        return DataClassification(_string(value, path))
    except ValueError as error:
        raise NsoSchemaError(path, "unknown data classification") from error


def _digest(value: Any, path: str) -> str:
    digest = _string(value, path)
    if (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise NsoSchemaError(path, "expected lowercase sha256 digest")
    return digest


def _object(value: Any, path: str, fields: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected object")
    missing = fields - set(value)
    unexpected = set(value) - fields
    if missing:
        raise NsoSchemaError(path, f"missing fields: {sorted(missing)}")
    if unexpected:
        raise NsoSchemaError(path, f"unexpected fields: {sorted(unexpected)}")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if type(value) is not list:
        raise NsoSchemaError(path, "expected array")
    return value


def _string(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise NsoSchemaError(path, "expected non-empty string")
    return value
