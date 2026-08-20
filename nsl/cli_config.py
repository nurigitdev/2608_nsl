from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .core import DataClassification, TypeRef, decode_value
from .data_protection import ensure_no_credential_material
from .ir_schema import NsoSchemaError, load_nso_json, validate_type_document
from .security import (
    AuthorizationError,
    ExecutionPrincipal,
    PrincipalVerification,
)
from .tools import (
    FixtureHandler,
    IncompatibleToolVersionError,
    ToolContract,
    ToolContractCatalog,
    ToolExecutionError,
    UnknownToolContractError,
)


def load_json_document(path: Path) -> Any:
    return load_nso_json(path.read_bytes())


def load_value_object(path: Path, label: str) -> dict[str, Any]:
    decoded = decode_value(load_json_document(path))
    if type(decoded) is not dict:
        raise NsoSchemaError("$", f"{label} must be a JSON object")
    return decoded


def load_tool_catalog(path: Path | None) -> ToolContractCatalog:
    if path is None:
        return ToolContractCatalog(())
    return load_tool_catalog_document(load_json_document(path))


def load_tool_catalog_document(document: Any) -> ToolContractCatalog:
    root = _object(document, "$", {"tools"})
    contracts = tuple(
        _tool_contract(item, f"$.tools[{index}]")
        for index, item in enumerate(_array(root["tools"], "$.tools"))
    )
    return ToolContractCatalog(contracts)


def load_principal(path: Path) -> ExecutionPrincipal:
    return load_principal_document(load_json_document(path))


def load_principal_document(document: Any) -> ExecutionPrincipal:
    item = _object(
        document,
        "$",
        {
            "tenant_id",
            "subject_id",
            "actor_type",
            "roles",
            "scopes",
            "auth_context_ref",
            "verification",
        },
        {"on_behalf_of"},
    )
    verification_name = _string(item["verification"], "$.verification")
    try:
        verification = PrincipalVerification(verification_name)
    except ValueError as error:
        raise NsoSchemaError("$.verification", "unknown verification state") from error
    on_behalf_of = item.get("on_behalf_of")
    if on_behalf_of is not None:
        on_behalf_of = _string(on_behalf_of, "$.on_behalf_of")
    principal = ExecutionPrincipal(
        tenant_id=_string(item["tenant_id"], "$.tenant_id"),
        subject_id=_string(item["subject_id"], "$.subject_id"),
        actor_type=_string(item["actor_type"], "$.actor_type"),
        roles=_string_set(item["roles"], "$.roles"),
        scopes=_string_set(item["scopes"], "$.scopes"),
        auth_context_ref=_string(item["auth_context_ref"], "$.auth_context_ref"),
        verification=verification,
        on_behalf_of=on_behalf_of,
    )
    try:
        principal.validate(require_verified=True)
    except AuthorizationError as error:
        raise NsoSchemaError("$", str(error)) from error
    return principal


def load_mock_handlers_document(
    document: Any, catalog: ToolContractCatalog
) -> dict[str, FixtureHandler]:
    ensure_no_credential_material(document, "CLI mock fixture")
    root = _object(document, "$", {"schema_version", "tools"})
    if root["schema_version"] != "1.0":
        raise NsoSchemaError("$.schema_version", "expected '1.0'")

    handlers: dict[str, FixtureHandler] = {}
    for index, value in enumerate(_array(root["tools"], "$.tools")):
        tool_path = f"$.tools[{index}]"
        item = _object(value, tool_path, {"tool_id", "version", "cases"})
        tool_id = _string(item["tool_id"], f"{tool_path}.tool_id")
        version = _string(item["version"], f"{tool_path}.version")
        if tool_id in handlers:
            raise NsoSchemaError(tool_path, "duplicate fixture tool_id")
        try:
            catalog.resolve(tool_id, version)
        except (UnknownToolContractError, IncompatibleToolVersionError) as error:
            raise NsoSchemaError(tool_path, "fixture tool contract unavailable") from error
        cases = _fixture_cases(item["cases"], f"{tool_path}.cases")
        handlers[tool_id] = _fixture_handler(tool_id, cases)
    return handlers


def _fixture_cases(
    value: Any, path: str
) -> tuple[tuple[dict[str, Any], Any], ...]:
    items = _array(value, path)
    if not items:
        raise NsoSchemaError(path, "at least one fixture case is required")
    cases: list[tuple[dict[str, Any], Any]] = []
    for index, value in enumerate(items):
        case_path = f"{path}[{index}]"
        item = _object(value, case_path, {"arguments", "result"})
        arguments = decode_value(item["arguments"])
        if type(arguments) is not dict:
            raise NsoSchemaError(f"{case_path}.arguments", "expected object")
        if any(expected == arguments for expected, _ in cases):
            raise NsoSchemaError(case_path, "duplicate fixture arguments")
        cases.append((arguments, decode_value(item["result"])))
    return tuple(cases)


def _fixture_handler(
    tool_id: str, cases: tuple[tuple[dict[str, Any], Any], ...]
) -> FixtureHandler:
    def handler(arguments: Mapping[str, Any]) -> Any:
        actual = dict(arguments)
        for expected, result in cases:
            if actual == expected:
                return result
        raise ToolExecutionError(
            "MOCK_FIXTURE_NOT_FOUND",
            f"no matching fixture case for {tool_id}",
        )

    return handler


def _tool_contract(value: Any, path: str) -> ToolContract:
    item = _object(
        value,
        path,
        {
            "tool_id",
            "version",
            "capability",
            "inputs",
            "output",
            "required_scope",
            "output_classification",
        },
        {"timeout_ms", "risk", "empty_is_valid"},
    )
    inputs = tuple(
        _tool_input(input_value, f"{path}.inputs[{index}]")
        for index, input_value in enumerate(
            _array(item["inputs"], f"{path}.inputs")
        )
    )
    classification_name = _string(
        item["output_classification"], f"{path}.output_classification"
    )
    try:
        classification = DataClassification(classification_name)
    except ValueError as error:
        raise NsoSchemaError(
            f"{path}.output_classification", "unknown data classification"
        ) from error

    timeout_ms = item.get("timeout_ms", 30_000)
    if type(timeout_ms) is not int:
        raise NsoSchemaError(f"{path}.timeout_ms", "expected integer")
    empty_is_valid = item.get("empty_is_valid", True)
    if type(empty_is_valid) is not bool:
        raise NsoSchemaError(f"{path}.empty_is_valid", "expected boolean")

    return ToolContract(
        tool_id=_string(item["tool_id"], f"{path}.tool_id"),
        version=_string(item["version"], f"{path}.version"),
        capability=_string(item["capability"], f"{path}.capability"),
        input_types=inputs,
        output_type=_type_ref(item["output"], f"{path}.output"),
        required_scope=_string(item["required_scope"], f"{path}.required_scope"),
        output_classification=classification,
        timeout_ms=timeout_ms,
        risk=_string(item.get("risk", "READ_ONLY"), f"{path}.risk"),
        empty_is_valid=empty_is_valid,
    )


def _tool_input(value: Any, path: str) -> tuple[str, TypeRef]:
    item = _object(value, path, {"name", "type"})
    return _string(item["name"], f"{path}.name"), _type_ref(
        item["type"], f"{path}.type"
    )


def _type_ref(value: Any, path: str) -> TypeRef:
    validate_type_document(value, path)
    return TypeRef.from_data(value)


def _object(
    value: Any,
    path: str,
    required: set[str],
    optional: set[str] | None = None,
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


def _array(value: Any, path: str) -> list[Any]:
    if type(value) is not list:
        raise NsoSchemaError(path, "expected array")
    return value


def _string_set(value: Any, path: str) -> frozenset[str]:
    values = tuple(
        _string(item, f"{path}[{index}]")
        for index, item in enumerate(_array(value, path))
    )
    if len(values) != len(set(values)):
        raise NsoSchemaError(path, "duplicate values are forbidden")
    return frozenset(values)


def _string(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise NsoSchemaError(path, "expected non-empty string")
    return value
