from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .data_protection import ensure_no_credential_material
from .ir_schema import NsoSchemaError, load_nso_json
from .process_isolation import MAX_ISOLATED_TIMEOUT_MS


PROFILE_SCHEMA_VERSION = "1.0"
MAX_PROFILE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProfileExecutionOptions:
    isolate: bool = False
    timeout_ms: int = 30_000


@dataclass(frozen=True, slots=True)
class LocalExecutionProfileDocument:
    profile_path: Path
    program: str
    principal: str
    tool_contracts: str | None
    input: str | None
    context: str | None
    fixture: str | None
    execution: ProfileExecutionOptions


@dataclass(frozen=True, slots=True)
class LocalExecutionProfile:
    profile_path: Path
    root: Path
    program: Path
    principal: Path
    tool_contracts: Path | None
    input: Path | None
    context: Path | None
    fixture: Path | None
    execution: ProfileExecutionOptions


@dataclass(frozen=True, slots=True)
class ResolvedCLIConfiguration:
    profile_path: Path | None
    program: Path | None
    principal: Path | None
    tool_contracts: Path | None
    input: Path | None
    context: Path | None
    fixture: Path | None
    isolate: bool
    timeout_ms: int


def load_profile_document(path: Path) -> LocalExecutionProfileDocument:
    data = path.read_bytes()
    if len(data) > MAX_PROFILE_BYTES:
        raise NsoSchemaError("$", "CLI profile exceeds the 1 MiB limit")
    document = load_nso_json(data)
    ensure_no_credential_material(document, "CLI local execution profile")
    root = _object(
        document,
        "$",
        {"schema_version", "program", "principal"},
        {"tool_contracts", "input", "context", "fixture", "execution"},
    )
    if root["schema_version"] != PROFILE_SCHEMA_VERSION:
        raise NsoSchemaError("$.schema_version", "expected '1.0'")
    execution = _execution_options(root.get("execution", {}))
    return LocalExecutionProfileDocument(
        profile_path=path,
        program=_string(root["program"], "$.program"),
        principal=_string(root["principal"], "$.principal"),
        tool_contracts=_optional_string(root, "tool_contracts"),
        input=_optional_string(root, "input"),
        context=_optional_string(root, "context"),
        fixture=_optional_string(root, "fixture"),
        execution=execution,
    )


def load_local_execution_profile(path: Path) -> LocalExecutionProfile:
    document = load_profile_document(path)
    resolved_profile = path.resolve(strict=True)
    root = resolved_profile.parent
    return LocalExecutionProfile(
        profile_path=resolved_profile,
        root=root,
        program=_resolve_profile_file(root, document.program, "$.program"),
        principal=_resolve_profile_file(root, document.principal, "$.principal"),
        tool_contracts=_resolve_optional_file(
            root, document.tool_contracts, "$.tool_contracts"
        ),
        input=_resolve_optional_file(root, document.input, "$.input"),
        context=_resolve_optional_file(root, document.context, "$.context"),
        fixture=_resolve_optional_file(root, document.fixture, "$.fixture"),
        execution=document.execution,
    )


def resolve_cli_configuration(
    *,
    profile_path: Path | None = None,
    program: Path | None = None,
    principal: Path | None = None,
    tool_contracts: Path | None = None,
    input_path: Path | None = None,
    context: Path | None = None,
    fixture: Path | None = None,
    isolate: bool | None = None,
    timeout_ms: int | None = None,
) -> ResolvedCLIConfiguration:
    if timeout_ms is not None and (
        type(timeout_ms) is not int
        or timeout_ms < 1
        or timeout_ms > MAX_ISOLATED_TIMEOUT_MS
    ):
        raise NsoSchemaError(
            "$.execution.timeout_ms",
            f"expected integer between 1 and {MAX_ISOLATED_TIMEOUT_MS}",
        )
    profile = (
        None
        if profile_path is None
        else load_local_execution_profile(profile_path)
    )
    profile_execution = (
        ProfileExecutionOptions() if profile is None else profile.execution
    )
    return ResolvedCLIConfiguration(
        profile_path=None if profile is None else profile.profile_path,
        program=_prefer(program, profile, "program"),
        principal=_prefer(principal, profile, "principal"),
        tool_contracts=_prefer(tool_contracts, profile, "tool_contracts"),
        input=_prefer(input_path, profile, "input"),
        context=_prefer(context, profile, "context"),
        fixture=_prefer(fixture, profile, "fixture"),
        isolate=profile_execution.isolate if isolate is None else isolate,
        timeout_ms=(
            profile_execution.timeout_ms if timeout_ms is None else timeout_ms
        ),
    )


def _execution_options(value: Any) -> ProfileExecutionOptions:
    item = _object(value, "$.execution", set(), {"isolate", "timeout_ms"})
    isolate = item.get("isolate", False)
    if type(isolate) is not bool:
        raise NsoSchemaError("$.execution.isolate", "expected boolean")
    timeout_ms = item.get("timeout_ms", 30_000)
    if (
        type(timeout_ms) is not int
        or timeout_ms < 1
        or timeout_ms > MAX_ISOLATED_TIMEOUT_MS
    ):
        raise NsoSchemaError(
            "$.execution.timeout_ms",
            f"expected integer between 1 and {MAX_ISOLATED_TIMEOUT_MS}",
        )
    return ProfileExecutionOptions(isolate=isolate, timeout_ms=timeout_ms)


def _optional_string(root: dict[str, Any], field: str) -> str | None:
    if field not in root:
        return None
    return _string(root[field], f"$.{field}")


def _resolve_optional_file(
    root: Path, value: str | None, field_path: str
) -> Path | None:
    if value is None:
        return None
    return _resolve_profile_file(root, value, field_path)


def _resolve_profile_file(root: Path, value: str, field_path: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or relative.drive:
        raise NsoSchemaError(field_path, "profile path must be relative")
    resolved = (root / relative).resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise NsoSchemaError(field_path, "profile path escapes its root") from error
    if not resolved.is_file():
        raise NsoSchemaError(field_path, "profile path must reference a file")
    return resolved


def _prefer(
    explicit: Path | None,
    profile: LocalExecutionProfile | None,
    field: str,
) -> Path | None:
    if explicit is not None:
        return explicit
    if profile is None:
        return None
    return getattr(profile, field)


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


def _string(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise NsoSchemaError(path, "expected non-empty string")
    return value
