from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from .core import CheckStatus, Completeness, ExecutionStatus
from .data_protection import ensure_no_credential_material
from .ir_schema import NsoSchemaError, load_nso_json
from .process_isolation import MAX_ISOLATED_TIMEOUT_MS


SCENARIO_SCHEMA_VERSION = "1.0"
MAX_SCENARIO_SUITE_BYTES = 8 * 1024 * 1024
MAX_SCENARIO_CASES = 1000
_RESULT_RESOURCE_FIELDS = {
    "tool_calls",
    "loop_iterations",
    "emitted_rows",
    "max_collection_size_seen",
}


@dataclass(frozen=True, slots=True)
class ScenarioExpectation:
    exit_code: int
    status: str | None
    completeness: str | None
    checks: tuple[tuple[str, str], ...]
    outputs: tuple[Mapping[str, Any], ...] | None
    error_code: str | None
    resources: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ScenarioCase:
    case_id: str
    principal: Path | None
    input: Path | None
    context: Path | None
    fixture: Path | None
    isolate: bool | None
    timeout_ms: int | None
    expectation: ScenarioExpectation


@dataclass(frozen=True, slots=True)
class ScenarioSuite:
    suite_path: Path
    root: Path
    profile: Path
    cases: tuple[ScenarioCase, ...]


@dataclass(frozen=True, slots=True)
class ScenarioInvocation:
    case_id: str
    exit_code: int
    output: Mapping[str, Any] | None
    error: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class ScenarioMismatch:
    path: str
    expected: Any
    actual: Any


@dataclass(frozen=True, slots=True)
class ScenarioEvaluation:
    case_id: str
    exit_code: int
    execution_id: str | None
    passed: bool
    mismatches: tuple[ScenarioMismatch, ...]


ScenarioInvoker = Callable[
    [Sequence[str]],
    tuple[int, Mapping[str, Any] | None, Mapping[str, Any] | None],
]


def load_scenario_suite(path: Path) -> ScenarioSuite:
    data = path.read_bytes()
    if len(data) > MAX_SCENARIO_SUITE_BYTES:
        raise NsoSchemaError("$", "scenario suite exceeds the 8 MiB limit")
    document = load_nso_json(data)
    ensure_no_credential_material(document, "CLI scenario suite")
    root_item = _object(document, "$", {"schema_version", "profile", "cases"})
    if root_item["schema_version"] != SCENARIO_SCHEMA_VERSION:
        raise NsoSchemaError("$.schema_version", "expected '1.0'")
    resolved_suite = path.resolve(strict=True)
    root = resolved_suite.parent
    profile = _resolve_file(root, root_item["profile"], "$.profile")
    raw_cases = _array(root_item["cases"], "$.cases")
    if not 1 <= len(raw_cases) <= MAX_SCENARIO_CASES:
        raise NsoSchemaError(
            "$.cases", f"expected between 1 and {MAX_SCENARIO_CASES} cases"
        )
    cases = tuple(
        _scenario_case(item, index, root)
        for index, item in enumerate(raw_cases)
    )
    case_ids = tuple(case.case_id for case in cases)
    if len(case_ids) != len(set(case_ids)):
        raise NsoSchemaError("$.cases", "duplicate scenario case id")
    return ScenarioSuite(resolved_suite, root, profile, cases)


def execute_scenario_suite(
    suite: ScenarioSuite, invoker: ScenarioInvoker
) -> tuple[ScenarioInvocation, ...]:
    if not isinstance(suite, ScenarioSuite):
        raise TypeError("scenario execution requires ScenarioSuite")
    if not callable(invoker):
        raise TypeError("scenario execution requires an invoker")
    invocations: list[ScenarioInvocation] = []
    for case in suite.cases:
        code, output, error = invoker(_case_arguments(suite, case))
        if type(code) is not int:
            raise TypeError("scenario invoker exit code must be an integer")
        if output is not None and not isinstance(output, Mapping):
            raise TypeError("scenario invoker output must be an object or null")
        if error is not None and not isinstance(error, Mapping):
            raise TypeError("scenario invoker error must be an object or null")
        invocations.append(
            ScenarioInvocation(
                case.case_id,
                code,
                None if output is None else MappingProxyType(deepcopy(dict(output))),
                None if error is None else MappingProxyType(deepcopy(dict(error))),
            )
        )
    return tuple(invocations)


def evaluate_scenario_suite(
    suite: ScenarioSuite,
    invocations: tuple[ScenarioInvocation, ...],
) -> tuple[ScenarioEvaluation, ...]:
    if not isinstance(suite, ScenarioSuite):
        raise TypeError("scenario evaluation requires ScenarioSuite")
    if type(invocations) is not tuple or len(invocations) != len(suite.cases):
        raise ValueError("scenario invocations must exactly cover the suite")
    evaluations: list[ScenarioEvaluation] = []
    for case, invocation in zip(suite.cases, invocations, strict=True):
        if not isinstance(invocation, ScenarioInvocation):
            raise TypeError("scenario evaluation requires ScenarioInvocation values")
        if invocation.case_id != case.case_id:
            raise ValueError("scenario invocation order differs from the suite")
        evaluations.append(_evaluate_case(case, invocation))
    return tuple(evaluations)


def _evaluate_case(
    case: ScenarioCase, invocation: ScenarioInvocation
) -> ScenarioEvaluation:
    expectation = case.expectation
    result = _invocation_result(invocation)
    mismatches: list[ScenarioMismatch] = []
    _compare(mismatches, "exit_code", expectation.exit_code, invocation.exit_code)
    if expectation.status is not None:
        _compare(
            mismatches,
            "result.status",
            expectation.status,
            None if result is None else result.get("status"),
        )
    if expectation.completeness is not None:
        _compare(
            mismatches,
            "result.completeness",
            expectation.completeness,
            None if result is None else result.get("completeness"),
        )
    actual_checks = _actual_checks(result)
    for check_id, status in expectation.checks:
        _compare(mismatches, f"checks.{check_id}", status, actual_checks.get(check_id))
    if expectation.outputs is not None:
        _compare(
            mismatches,
            "result.outputs",
            [dict(item) for item in expectation.outputs],
            _actual_outputs(result),
        )
    if expectation.error_code is not None:
        error = None if result is None else result.get("error")
        actual_error_code = error.get("code") if isinstance(error, Mapping) else None
        _compare(
            mismatches,
            "result.error.code",
            expectation.error_code,
            actual_error_code,
        )
    resources = None if result is None else result.get("resources")
    for name, expected in expectation.resources:
        actual = resources.get(name) if isinstance(resources, Mapping) else None
        _compare(mismatches, f"resources.{name}", expected, actual)
    execution_id = None if result is None else result.get("execution_id")
    if not isinstance(execution_id, str):
        execution_id = None
    return ScenarioEvaluation(
        case_id=case.case_id,
        exit_code=invocation.exit_code,
        execution_id=execution_id,
        passed=not mismatches,
        mismatches=tuple(mismatches),
    )


def _invocation_result(invocation: ScenarioInvocation) -> Mapping[str, Any] | None:
    if invocation.output is not None:
        result = invocation.output.get("result")
        return result if isinstance(result, Mapping) else None
    if invocation.error is None:
        return None
    envelope = invocation.error.get("error")
    if not isinstance(envelope, Mapping):
        return None
    details = envelope.get("details")
    return details if isinstance(details, Mapping) and "status" in details else None


def _actual_checks(result: Mapping[str, Any] | None) -> dict[str, Any]:
    if result is None or type(result.get("checks")) is not list:
        return {}
    return {
        item["check_id"]: item.get("status")
        for item in result["checks"]
        if isinstance(item, Mapping) and isinstance(item.get("check_id"), str)
    }


def _actual_outputs(result: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if result is None or type(result.get("outputs")) is not list:
        return []
    outputs: list[dict[str, Any]] = []
    for record in result["outputs"]:
        fields = record.get("fields") if isinstance(record, Mapping) else None
        if type(fields) is not list:
            return []
        outputs.append(
            {
                field["name"]: deepcopy(field.get("value"))
                for field in fields
                if isinstance(field, Mapping) and isinstance(field.get("name"), str)
            }
        )
    return outputs


def _compare(
    mismatches: list[ScenarioMismatch], path: str, expected: Any, actual: Any
) -> None:
    if expected != actual:
        mismatches.append(
            ScenarioMismatch(path, deepcopy(expected), deepcopy(actual))
        )


def _case_arguments(suite: ScenarioSuite, case: ScenarioCase) -> tuple[str, ...]:
    arguments = [
        "run",
        "--profile",
        str(suite.profile),
        "--execution-id",
        f"scenario-{case.case_id}",
    ]
    for option, path in (
        ("--principal", case.principal),
        ("--input", case.input),
        ("--context", case.context),
        ("--fixture", case.fixture),
    ):
        if path is not None:
            arguments.extend((option, str(path)))
    if case.isolate is not None:
        arguments.append("--isolate" if case.isolate else "--no-isolate")
    if case.timeout_ms is not None:
        arguments.extend(("--timeout-ms", str(case.timeout_ms)))
    return tuple(arguments)


def _scenario_case(value: Any, index: int, root: Path) -> ScenarioCase:
    path = f"$.cases[{index}]"
    item = _object(
        value,
        path,
        {"id", "expect"},
        {"principal", "input", "context", "fixture", "isolate", "timeout_ms"},
    )
    case_id = _case_id(item["id"], f"{path}.id")
    isolate = item.get("isolate")
    if isolate is not None and type(isolate) is not bool:
        raise NsoSchemaError(f"{path}.isolate", "expected boolean")
    timeout_ms = item.get("timeout_ms")
    if timeout_ms is not None and (
        type(timeout_ms) is not int
        or timeout_ms < 1
        or timeout_ms > MAX_ISOLATED_TIMEOUT_MS
    ):
        raise NsoSchemaError(
            f"{path}.timeout_ms",
            f"expected integer between 1 and {MAX_ISOLATED_TIMEOUT_MS}",
        )
    return ScenarioCase(
        case_id=case_id,
        principal=_optional_file(root, item, "principal", path),
        input=_optional_file(root, item, "input", path),
        context=_optional_file(root, item, "context", path),
        fixture=_optional_file(root, item, "fixture", path),
        isolate=isolate,
        timeout_ms=timeout_ms,
        expectation=_expectation(item["expect"], f"{path}.expect"),
    )


def _expectation(value: Any, path: str) -> ScenarioExpectation:
    item = _object(
        value,
        path,
        {"exit_code"},
        {"status", "completeness", "checks", "outputs", "error_code", "resources"},
    )
    exit_code = item["exit_code"]
    if type(exit_code) is not int or not 0 <= exit_code <= 255:
        raise NsoSchemaError(f"{path}.exit_code", "expected integer between 0 and 255")
    status = item.get("status")
    if status is not None and status not in {item.value for item in ExecutionStatus}:
        raise NsoSchemaError(f"{path}.status", "unknown execution status")
    completeness = item.get("completeness")
    if completeness is not None and completeness not in {
        item.value for item in Completeness
    }:
        raise NsoSchemaError(f"{path}.completeness", "unknown completeness")
    checks = _checks(item.get("checks", {}), f"{path}.checks")
    outputs = _outputs(item.get("outputs"), f"{path}.outputs")
    error_code = item.get("error_code")
    if error_code is not None:
        error_code = _string(error_code, f"{path}.error_code")
    resources = _resources(item.get("resources", {}), f"{path}.resources")
    return ScenarioExpectation(
        exit_code,
        status,
        completeness,
        checks,
        outputs,
        error_code,
        resources,
    )


def _checks(value: Any, path: str) -> tuple[tuple[str, str], ...]:
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected object")
    allowed = {status.value for status in CheckStatus}
    checks: list[tuple[str, str]] = []
    for check_id, status in value.items():
        if not isinstance(check_id, str) or not check_id:
            raise NsoSchemaError(path, "check id must be non-empty")
        if status not in allowed:
            raise NsoSchemaError(f"{path}.{check_id}", "unknown check status")
        checks.append((check_id, status))
    return tuple(sorted(checks))


def _outputs(value: Any, path: str) -> tuple[Mapping[str, Any], ...] | None:
    if value is None:
        return None
    outputs = _array(value, path)
    copied: list[Mapping[str, Any]] = []
    for index, output in enumerate(outputs):
        if type(output) is not dict or not all(
            isinstance(name, str) and name for name in output
        ):
            raise NsoSchemaError(f"{path}[{index}]", "expected named output object")
        copied.append(MappingProxyType(deepcopy(output)))
    return tuple(copied)


def _resources(value: Any, path: str) -> tuple[tuple[str, int], ...]:
    if type(value) is not dict or not set(value) <= _RESULT_RESOURCE_FIELDS:
        raise NsoSchemaError(path, "invalid resource expectation")
    if not all(type(number) is int and number >= 0 for number in value.values()):
        raise NsoSchemaError(path, "resource expectations must be non-negative integers")
    return tuple(sorted(value.items()))


def _optional_file(
    root: Path, item: dict[str, Any], field: str, case_path: str
) -> Path | None:
    if field not in item:
        return None
    return _resolve_file(root, item[field], f"{case_path}.{field}")


def _resolve_file(root: Path, value: Any, path: str) -> Path:
    text = _string(value, path)
    relative = Path(text)
    if relative.is_absolute() or relative.drive:
        raise NsoSchemaError(path, "scenario path must be relative")
    resolved = (root / relative).resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise NsoSchemaError(path, "scenario path escapes its root") from error
    if not resolved.is_file():
        raise NsoSchemaError(path, "scenario path must reference a file")
    return resolved


def _case_id(value: Any, path: str) -> str:
    text = _string(value, path)
    if (
        len(text) > 64
        or not text.isascii()
        or not text[0].isalnum()
        or any(not (character.isalnum() or character in "-_") for character in text)
    ):
        raise NsoSchemaError(path, "invalid scenario case id")
    return text


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


def _string(value: Any, path: str) -> str:
    if type(value) is not str or not value:
        raise NsoSchemaError(path, "expected non-empty string")
    return value
