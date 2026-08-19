from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .core import encode_value

if TYPE_CHECKING:
    from .runtime_models import ExecutionResult


RESULT_SCHEMA_VERSION = "1.0"


def _check_to_data(check: Any) -> dict[str, Any]:
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


def _output_to_data(record: Any) -> dict[str, Any]:
    return {
        "fields": [
            {
                "name": name,
                "type": field.type_info.to_data(),
                "value": encode_value(field.value),
                "presence": field.presence.value,
                "completeness": field.completeness.value,
                "classification": field.classification.value,
                "provenance_refs": list(field.provenance_refs),
            }
            for name, field in record.fields.items()
        ]
    }


def _resources_to_data(resources: Any) -> dict[str, int]:
    return {
        "tool_calls": resources.tool_calls,
        "loop_iterations": resources.loop_iterations,
        "emitted_rows": resources.emitted_rows,
        "max_collection_size_seen": resources.max_collection_size_seen,
    }


def _error_to_data(error: Any) -> dict[str, Any] | None:
    if error is None:
        return None
    return {
        "code": error.code,
        "category": error.category,
        "message": error.message,
        "node_id": error.node_id,
        "detail_code": error.detail_code,
    }


def execution_result_to_data(result: ExecutionResult) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "execution_id": result.execution_id,
        "skill_id": result.skill_id,
        "skill_version": result.skill_version,
        "semantic_hash": result.semantic_hash,
        "status": result.status.value,
        "completeness": result.completeness.value,
        "checks": [_check_to_data(check) for check in result.checks],
        "outputs": [_output_to_data(record) for record in result.outputs],
        "resources": _resources_to_data(result.resources),
        "error": _error_to_data(result.error),
    }


def execution_result_to_json(result: ExecutionResult) -> str:
    return json.dumps(
        execution_result_to_data(result),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def semantic_result_view(result: ExecutionResult) -> dict[str, Any]:
    return {
        "skill_id": result.skill_id,
        "skill_version": result.skill_version,
        "semantic_hash": result.semantic_hash,
        "status": result.status.value,
        "completeness": result.completeness.value,
        "checks": [
            {
                key: value
                for key, value in _check_to_data(check).items()
                if key != "provenance_refs"
            }
            for check in result.checks
        ],
        "outputs": [encode_value(dict(record.values)) for record in result.outputs],
        "resources": _resources_to_data(result.resources),
        "error": _error_to_data(result.error),
    }
