from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "requirements/nsl_cli_local_execution_extension.json"
EXPECTED_FIELDS = {
    "id",
    "title",
    "priority",
    "part",
    "status",
    "verification",
    "evidence",
}


class ExtensionTraceabilityError(ValueError):
    """Raised when an extension requirement baseline is not verifiable."""


@dataclass(frozen=True, slots=True)
class ExtensionTraceabilityReport:
    baseline_id: str
    requirement_count: int
    status_counts: dict[str, int]
    part_counts: dict[str, int]


def extension_requirement_fingerprint(requirements: list[dict[str, Any]]) -> str:
    canonical = "\n".join(
        f"{item.get('id')}|{item.get('priority')}|{item.get('part')}|{item.get('title')}"
        for item in requirements
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _string_list(value: Any, field: str, owner: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        errors.append(f"{owner}.{field} must be a non-empty string list")
        return []
    if len(value) != len(set(value)):
        errors.append(f"{owner}.{field} contains duplicates")
    return value


def _check_evidence(
    references: list[str], repo_root: Path, requirement_id: str, errors: list[str]
) -> None:
    for reference in references:
        path_text, separator, symbol = reference.partition("::")
        evidence_path = repo_root / path_text
        if not evidence_path.is_file():
            errors.append(f"{requirement_id} evidence path does not exist: {path_text}")
            continue
        if separator and symbol:
            content = evidence_path.read_text(encoding="utf-8")
            if symbol not in content:
                errors.append(
                    f"{requirement_id} evidence symbol not found: {reference}"
                )


def validate_extension_traceability(
    baseline_path: Path = DEFAULT_BASELINE,
    repo_root: Path = ROOT,
) -> ExtensionTraceabilityReport:
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExtensionTraceabilityError(
            f"cannot load extension baseline {baseline_path}: {error}"
        ) from error

    errors: list[str] = []
    if not isinstance(baseline, dict):
        raise ExtensionTraceabilityError("extension baseline must be an object")
    if baseline.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    baseline_id = baseline.get("baseline_id")
    if not isinstance(baseline_id, str) or not baseline_id:
        errors.append("baseline_id must be a non-empty string")
        baseline_id = "<invalid>"
    if baseline.get("slice") != "0031":
        errors.append("slice must be 0031")
    if not isinstance(baseline.get("title"), str) or not baseline["title"]:
        errors.append("title must be a non-empty string")

    requirements = baseline.get("requirements")
    if not isinstance(requirements, list):
        errors.append("requirements must be an array")
        requirements = []
    expected_count = baseline.get("expected_requirement_count")
    if expected_count != 15 or len(requirements) != 15:
        errors.append(
            f"expected_requirement_count and requirements length must both be 15; "
            f"got {expected_count!r} and {len(requirements)}"
        )

    statuses: Counter[str] = Counter()
    parts: Counter[str] = Counter()
    for index, requirement in enumerate(requirements, start=1):
        owner = f"requirements[{index - 1}]"
        if not isinstance(requirement, dict):
            errors.append(f"{owner} must be an object")
            continue
        if set(requirement) != EXPECTED_FIELDS:
            errors.append(f"{owner} fields must exactly match the extension schema")
        requirement_id = requirement.get("id")
        expected_id = f"NSL-CLX-{index:03d}"
        if requirement_id != expected_id:
            errors.append(f"{owner}.id must be {expected_id}")
        title = requirement.get("title")
        if not isinstance(title, str) or not title:
            errors.append(f"{owner}.title must be a non-empty string")
        if requirement.get("priority") not in {"MUST", "SHOULD", "MAY"}:
            errors.append(f"{owner}.priority is invalid")
        expected_part = "A" if index <= 5 else "B" if index <= 10 else "C"
        part = requirement.get("part")
        if part != expected_part:
            errors.append(f"{owner}.part must be {expected_part}")
        if isinstance(part, str):
            parts[part] += 1
        status = requirement.get("status")
        if status not in {"IMPLEMENTED", "PARTIAL"}:
            errors.append(f"{owner}.status must be IMPLEMENTED or PARTIAL")
        if isinstance(status, str):
            statuses[status] += 1
        verification = _string_list(
            requirement.get("verification"), "verification", owner, errors
        )
        evidence = _string_list(
            requirement.get("evidence"), "evidence", owner, errors
        )
        if verification and verification != [f"TEST-CLX-{index:03d}"]:
            errors.append(f"{owner}.verification must match its requirement id")
        if isinstance(requirement_id, str):
            _check_evidence(evidence, repo_root, requirement_id, errors)

    fingerprint = extension_requirement_fingerprint(requirements)
    if baseline.get("requirements_sha256") != fingerprint:
        errors.append("requirements_sha256 does not match the extension requirements")
    if errors:
        raise ExtensionTraceabilityError("\n".join(errors))
    return ExtensionTraceabilityReport(
        baseline_id, len(requirements), dict(statuses), dict(parts)
    )
