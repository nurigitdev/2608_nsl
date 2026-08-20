from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any, Mapping

from .audit import AuditEvent
from .cli_config import (
    load_mock_handlers_document,
    load_principal_document,
    load_tool_catalog_document,
)
from .cli_runtime import execute_skill
from .core import ExecutionStatus, decode_value, encode_value
from .data_protection import ensure_no_credential_material
from .ir import NsoCodec, SkillObject, canonical_json
from .ir_schema import NsoSchemaError, load_nso_json
from .process_isolation import (
    MAX_ISOLATED_PAYLOAD_BYTES,
    IsolatedProcessStatus,
    ProcessIsolatedRuntime,
)
from .security import ExecutionPrincipal
from .tools import ToolContract, ToolContractCatalog


ISOLATED_CLI_FORMAT = "NSL-CLI-ISOLATED-EXECUTION"
ISOLATED_CLI_SCHEMA_VERSION = "1.0"


class CLIIsolationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class IsolatedCLIExecution:
    result: dict[str, Any]
    audit_events: tuple[AuditEvent, ...]
    timing: dict[str, int] | None
    process_status: IsolatedProcessStatus
    exit_code: int | None


def encode_isolated_cli_request(
    skill: SkillObject,
    catalog: ToolContractCatalog,
    principal: ExecutionPrincipal,
    execution_id: str,
    *,
    inputs: Mapping[str, Any] | None,
    runtime_context: Mapping[str, Any] | None,
    fixture_document: Mapping[str, Any],
    measure_timing: bool,
) -> bytes:
    document = {
        "format": ISOLATED_CLI_FORMAT,
        "schema_version": ISOLATED_CLI_SCHEMA_VERSION,
        "execution_id": execution_id,
        "nso_base64": base64.b64encode(NsoCodec.encode(skill)).decode("ascii"),
        "tool_contracts": _catalog_document(skill, catalog),
        "principal": _principal_document(principal),
        "inputs": encode_value({} if inputs is None else dict(inputs)),
        "runtime_context": encode_value(
            {} if runtime_context is None else dict(runtime_context)
        ),
        "fixture": fixture_document,
        "measure_timing": measure_timing,
    }
    ensure_no_credential_material(document, "isolated CLI request")
    payload = canonical_json(document)
    if len(payload) > MAX_ISOLATED_PAYLOAD_BYTES:
        raise ValueError("isolated CLI request exceeds the payload limit")
    return payload


def execute_isolated_cli_request(payload: bytes) -> bytes:
    document = _load_canonical_document(payload, "isolated CLI request")
    item = _exact_object(
        document,
        {
            "format",
            "schema_version",
            "execution_id",
            "nso_base64",
            "tool_contracts",
            "principal",
            "inputs",
            "runtime_context",
            "fixture",
            "measure_timing",
        },
        "isolated CLI request",
    )
    if item["format"] != ISOLATED_CLI_FORMAT:
        raise ValueError("isolated CLI request format is invalid")
    if item["schema_version"] != ISOLATED_CLI_SCHEMA_VERSION:
        raise ValueError("isolated CLI request schema version is unsupported")
    if type(item["execution_id"]) is not str or not item["execution_id"]:
        raise ValueError("isolated CLI execution_id must be non-empty")
    if type(item["nso_base64"]) is not str:
        raise ValueError("isolated CLI NSO encoding is invalid")
    try:
        nso_bytes = base64.b64decode(item["nso_base64"], validate=True)
    except ValueError as error:
        raise ValueError("isolated CLI NSO encoding is invalid") from error
    if type(item["measure_timing"]) is not bool:
        raise ValueError("isolated CLI measure_timing must be boolean")
    skill = NsoCodec.decode(nso_bytes)
    catalog = load_tool_catalog_document(item["tool_contracts"])
    principal = load_principal_document(item["principal"])
    inputs = decode_value(item["inputs"])
    runtime_context = decode_value(item["runtime_context"])
    if type(inputs) is not dict or type(runtime_context) is not dict:
        raise ValueError("isolated CLI input and context must be objects")
    handlers = load_mock_handlers_document(item["fixture"], catalog)
    session = asyncio.run(
        execute_skill(
            skill,
            catalog,
            principal,
            item["execution_id"],
            inputs=inputs,
            runtime_context=runtime_context,
            handlers=handlers,
            measure_timing=item["measure_timing"],
        )
    )
    response = {
        "format": ISOLATED_CLI_FORMAT,
        "schema_version": ISOLATED_CLI_SCHEMA_VERSION,
        "result": session.result.to_data(),
        "audit": [event.to_data() for event in session.audit.events],
        "timing": _timing_document(session.timing),
    }
    ensure_no_credential_material(response, "isolated CLI response")
    return canonical_json(response)


def run_isolated_cli(
    payload: bytes,
    *,
    timeout_ms: int,
) -> IsolatedCLIExecution:
    process_result = ProcessIsolatedRuntime(
        execute_isolated_cli_request,
        timeout_ms=timeout_ms,
    ).execute(payload)
    if process_result.status is not IsolatedProcessStatus.COMPLETED:
        raise CLIIsolationError(
            process_result.error_code or "ISOLATED_RUNTIME_FAILED",
            _isolation_message(process_result.status),
        )
    try:
        response = _load_canonical_document(
            process_result.payload, "isolated CLI response"
        )
        item = _exact_object(
            response,
            {"format", "schema_version", "result", "audit", "timing"},
            "isolated CLI response",
        )
        if (
            item["format"] != ISOLATED_CLI_FORMAT
            or item["schema_version"] != ISOLATED_CLI_SCHEMA_VERSION
        ):
            raise ValueError("isolated CLI response identity is invalid")
        result = _result_document(item["result"])
        audit = item["audit"]
        if type(audit) is not list:
            raise ValueError("isolated CLI audit response is invalid")
        audit_events = tuple(AuditEvent.from_data(event) for event in audit)
        timing = _validate_timing_document(item["timing"])
    except (CLIIsolationError, TypeError, ValueError) as error:
        raise CLIIsolationError(
            "ISOLATED_RUNTIME_PROTOCOL_ERROR", "isolated Runtime response is invalid"
        ) from error
    return IsolatedCLIExecution(
        result=result,
        audit_events=audit_events,
        timing=timing,
        process_status=process_result.status,
        exit_code=process_result.exit_code,
    )


def _catalog_document(
    skill: SkillObject, catalog: ToolContractCatalog
) -> dict[str, Any]:
    return {
        "tools": [
            _contract_document(catalog.resolve(item.tool_id, item.version))
            for item in skill.required_tools
        ]
    }


def _contract_document(contract: ToolContract) -> dict[str, Any]:
    return {
        "tool_id": contract.tool_id,
        "version": contract.version,
        "capability": contract.capability,
        "inputs": [
            {"name": name, "type": type_info.to_data()}
            for name, type_info in contract.input_types
        ],
        "output": contract.output_type.to_data(),
        "required_scope": contract.required_scope,
        "output_classification": contract.output_classification.value,
        "timeout_ms": contract.timeout_ms,
        "risk": contract.risk,
        "empty_is_valid": contract.empty_is_valid,
    }


def _principal_document(principal: ExecutionPrincipal) -> dict[str, Any]:
    document: dict[str, Any] = {
        "tenant_id": principal.tenant_id,
        "subject_id": principal.subject_id,
        "actor_type": principal.actor_type,
        "roles": sorted(principal.roles),
        "scopes": sorted(principal.scopes),
        "auth_context_ref": principal.auth_context_ref,
        "verification": principal.verification.value,
    }
    if principal.on_behalf_of is not None:
        document["on_behalf_of"] = principal.on_behalf_of
    return document


def _timing_document(timing: Any) -> dict[str, int] | None:
    if timing is None:
        return None
    return {
        "runtime_total_ns": timing.total_duration_ns,
        "tool_total_ns": timing.tool_duration_ns,
        "runtime_overhead_ns": timing.runtime_overhead_ns,
        "tool_call_count": timing.tool_call_count,
    }


def _validate_timing_document(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    item = _exact_object(
        value,
        {
            "runtime_total_ns",
            "tool_total_ns",
            "runtime_overhead_ns",
            "tool_call_count",
        },
        "isolated CLI timing",
    )
    if not all(type(number) is int and number >= 0 for number in item.values()):
        raise ValueError("isolated CLI timing values are invalid")
    expected = max(0, item["runtime_total_ns"] - item["tool_total_ns"])
    if item["runtime_overhead_ns"] != expected:
        raise ValueError("isolated CLI timing overhead is inconsistent")
    return item


def _result_document(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise CLIIsolationError(
            "ISOLATED_RUNTIME_PROTOCOL_ERROR", "isolated Runtime response is invalid"
        )
    if value.get("status") not in {status.value for status in ExecutionStatus}:
        raise CLIIsolationError(
            "ISOLATED_RUNTIME_PROTOCOL_ERROR", "isolated Runtime response is invalid"
        )
    return value


def _load_canonical_document(data: bytes | None, label: str) -> Any:
    if type(data) is not bytes or len(data) > MAX_ISOLATED_PAYLOAD_BYTES:
        raise ValueError(f"{label} exceeds the payload limit")
    document = load_nso_json(data)
    ensure_no_credential_material(document, label)
    if data != canonical_json(document):
        raise NsoSchemaError("$", f"{label} must be canonical JSON")
    return document


def _exact_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} schema is invalid")
    return value


def _isolation_message(status: IsolatedProcessStatus) -> str:
    if status is IsolatedProcessStatus.TIMED_OUT:
        return "isolated Runtime timed out"
    if status is IsolatedProcessStatus.CRASHED:
        return "isolated Runtime crashed"
    if status is IsolatedProcessStatus.TARGET_ERROR:
        return "isolated Runtime execution failed"
    return "isolated Runtime protocol failed"
