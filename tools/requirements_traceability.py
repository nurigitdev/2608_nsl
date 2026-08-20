from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs/01_NeX_Skill_Language_v0.1_Interpreter_Runtime_SRS_v1.1_Draft.md"
DEFAULT_BASELINE = ROOT / "requirements/nsl_v0_1_traceability.json"

REQUIREMENT_ROW = re.compile(
    r"^\|\s*(NSL-[A-Z]+-\d{3})\s*\|\s*(.*?)\s*\|\s*(MUST|SHOULD|MAY)\s*\|$"
)
REQUIREMENT_ID = re.compile(r"^NSL-[A-Z]+-\d{3}$")
SELECTOR_RANGE = re.compile(r"^(NSL-[A-Z]+)-(\d{3})\.\.(\d{3})$")
SLICE_ID = re.compile(r"^\d{4}$")
ALLOWED_STATUSES = {
    "IMPLEMENTED",
    "PARTIAL",
    "PLANNED",
    "OUT_OF_SCOPE",
    "SUPERSEDED",
}


class TraceabilityError(ValueError):
    """Raised when the SRS or its traceability baseline is invalid."""


@dataclass(frozen=True, slots=True)
class Requirement:
    requirement_id: str
    text: str
    priority: str
    line: int


@dataclass(frozen=True, slots=True)
class TraceabilityReport:
    baseline_id: str
    requirement_count: int
    requirements_sha256: str
    priority_counts: dict[str, int]
    status_counts: dict[str, int]
    target_slice_counts: dict[str, int]

    def to_data(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "requirement_count": self.requirement_count,
            "requirements_sha256": self.requirements_sha256,
            "priority_counts": self.priority_counts,
            "status_counts": self.status_counts,
            "target_slice_counts": self.target_slice_counts,
        }


def extract_requirements(source_path: Path) -> dict[str, Requirement]:
    requirements: dict[str, Requirement] = {}
    for line_number, line in enumerate(
        source_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.lstrip().startswith("| NSL-"):
            continue
        match = REQUIREMENT_ROW.fullmatch(line.strip())
        if match is None:
            raise TraceabilityError(
                f"malformed requirement row at {source_path}:{line_number}"
            )
        requirement_id, text, priority = match.groups()
        if requirement_id in requirements:
            first = requirements[requirement_id]
            raise TraceabilityError(
                f"duplicate requirement {requirement_id} at lines "
                f"{first.line} and {line_number}"
            )
        requirements[requirement_id] = Requirement(
            requirement_id=requirement_id,
            text=text,
            priority=priority,
            line=line_number,
        )
    if not requirements:
        raise TraceabilityError(f"no requirements found in {source_path}")
    return requirements


def requirement_fingerprint(requirements: dict[str, Requirement]) -> str:
    canonical = "\n".join(
        f"{item.requirement_id}|{item.priority}|{item.text}"
        for item in requirements.values()
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def expand_selector(selector: str) -> tuple[str, ...]:
    range_match = SELECTOR_RANGE.fullmatch(selector)
    if range_match is None:
        if REQUIREMENT_ID.fullmatch(selector):
            return (selector,)
        raise TraceabilityError(f"invalid requirement selector: {selector}")

    prefix, start_text, end_text = range_match.groups()
    start = int(start_text)
    end = int(end_text)
    if start > end:
        raise TraceabilityError(f"descending requirement selector: {selector}")
    return tuple(f"{prefix}-{number:03d}" for number in range(start, end + 1))


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
    evidence: list[str], repo_root: Path, requirement_id: str, errors: list[str]
) -> None:
    for reference in evidence:
        path_text, separator, symbol = reference.partition("::")
        evidence_path = repo_root / path_text
        if not evidence_path.is_file():
            errors.append(
                f"{requirement_id} evidence path does not exist: {path_text}"
            )
            continue
        if separator and symbol:
            content = evidence_path.read_text(encoding="utf-8")
            if symbol not in content:
                errors.append(
                    f"{requirement_id} evidence symbol not found: {reference}"
                )


def validate_traceability(
    source_path: Path = DEFAULT_SOURCE,
    baseline_path: Path = DEFAULT_BASELINE,
    repo_root: Path = ROOT,
) -> TraceabilityReport:
    requirements = extract_requirements(source_path)
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TraceabilityError(f"cannot load baseline {baseline_path}: {exc}") from exc

    errors: list[str] = []
    if baseline.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    baseline_id = baseline.get("baseline_id")
    if not isinstance(baseline_id, str) or not baseline_id:
        errors.append("baseline_id must be a non-empty string")
        baseline_id = "<invalid>"
    expected_count = baseline.get("expected_requirement_count")
    if expected_count != len(requirements):
        errors.append(
            f"expected_requirement_count={expected_count!r}, extracted={len(requirements)}"
        )
    requirements_sha256 = requirement_fingerprint(requirements)
    if baseline.get("requirements_sha256") != requirements_sha256:
        errors.append(
            f"requirements_sha256={baseline.get('requirements_sha256')!r}, "
            f"extracted={requirements_sha256!r}"
        )
    try:
        expected_source = source_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        expected_source = source_path.resolve().as_posix()
    if baseline.get("source_document") != expected_source:
        errors.append(
            f"source_document={baseline.get('source_document')!r}, "
            f"expected={expected_source!r}"
        )

    slices = baseline.get("slices")
    if not isinstance(slices, dict) or not slices:
        errors.append("slices must be a non-empty object")
        slices = {}
    for slice_id, definition in slices.items():
        if SLICE_ID.fullmatch(slice_id) is None:
            errors.append(f"invalid slice id: {slice_id}")
        if not isinstance(definition, dict):
            errors.append(f"slice {slice_id} must be an object")
            continue
        if definition.get("scope") not in {"SRS", "EXTENSION"}:
            errors.append(f"slice {slice_id} has invalid scope")
        if not isinstance(definition.get("title"), str) or not definition["title"]:
            errors.append(f"slice {slice_id} must have a title")
    if baseline.get("current_slice") not in slices:
        errors.append("current_slice must reference a declared slice")

    decisions = baseline.get("scope_decisions")
    if not isinstance(decisions, list) or not decisions:
        errors.append("scope_decisions must be a non-empty list")
        decisions = []
    decision_ids: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            errors.append("scope decision must be an object")
            continue
        decision_id = decision.get("decision_id")
        if not isinstance(decision_id, str) or not decision_id:
            errors.append("scope decision must have a decision_id")
            continue
        if decision_id in decision_ids:
            errors.append(f"duplicate scope decision: {decision_id}")
        decision_ids.add(decision_id)
        if not isinstance(decision.get("decision"), str) or not decision["decision"]:
            errors.append(f"scope decision {decision_id} must have text")

    assignments = baseline.get("assignments")
    if not isinstance(assignments, list) or not assignments:
        errors.append("assignments must be a non-empty list")
        assignments = []

    mapped: dict[str, dict[str, Any]] = {}
    for index, assignment in enumerate(assignments):
        owner = f"assignments[{index}]"
        if not isinstance(assignment, dict):
            errors.append(f"{owner} must be an object")
            continue
        selectors = _string_list(assignment.get("selectors"), "selectors", owner, errors)
        for selector in selectors:
            try:
                selected_ids = expand_selector(selector)
            except TraceabilityError as exc:
                errors.append(str(exc))
                continue
            for requirement_id in selected_ids:
                if requirement_id not in requirements:
                    errors.append(f"unknown requirement in selector: {requirement_id}")
                elif requirement_id in mapped:
                    errors.append(f"requirement mapped more than once: {requirement_id}")
                else:
                    mapped[requirement_id] = dict(assignment)

    missing = sorted(set(requirements) - set(mapped))
    if missing:
        errors.append(f"unmapped requirements: {', '.join(missing)}")

    overrides = baseline.get("overrides", {})
    if not isinstance(overrides, dict):
        errors.append("overrides must be an object")
        overrides = {}
    for requirement_id, override in overrides.items():
        if requirement_id not in requirements:
            errors.append(f"override references unknown requirement: {requirement_id}")
        elif requirement_id not in mapped:
            errors.append(f"override has no base assignment: {requirement_id}")
        elif not isinstance(override, dict):
            errors.append(f"override for {requirement_id} must be an object")
        else:
            mapped[requirement_id].update(override)

    extension_exceptions = baseline.get("extension_requirement_exceptions", [])
    if not isinstance(extension_exceptions, list) or not all(
        isinstance(item, str) for item in extension_exceptions
    ):
        errors.append("extension_requirement_exceptions must be a string list")
        extension_exceptions = []
    unknown_exceptions = sorted(set(extension_exceptions) - set(requirements))
    if unknown_exceptions:
        errors.append(
            "unknown extension exceptions: " + ", ".join(unknown_exceptions)
        )

    status_counts: Counter[str] = Counter()
    target_slice_counts: Counter[str] = Counter()
    for requirement_id, requirement in requirements.items():
        record = mapped.get(requirement_id)
        if record is None:
            continue
        status = record.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{requirement_id} has invalid status: {status!r}")
            continue
        status_counts[status] += 1

        verification = _string_list(
            record.get("verification"), "verification", requirement_id, errors
        )
        target_slice = record.get("target_slice")
        if status in {"IMPLEMENTED", "PARTIAL", "PLANNED"}:
            if target_slice not in slices:
                errors.append(
                    f"{requirement_id} target_slice is not declared: {target_slice!r}"
                )
            else:
                target_slice_counts[target_slice] += 1
                if (
                    slices[target_slice].get("scope") == "EXTENSION"
                    and requirement_id not in extension_exceptions
                ):
                    errors.append(
                        f"{requirement_id} cannot target extension slice {target_slice}"
                    )
        else:
            decision_ref = record.get("decision_ref")
            if decision_ref not in decision_ids:
                errors.append(
                    f"{requirement_id} {status} requires a valid decision_ref"
                )
            if target_slice is not None:
                errors.append(f"{requirement_id} {status} cannot have target_slice")

        evidence = record.get("evidence", [])
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) and item for item in evidence
        ):
            errors.append(f"{requirement_id}.evidence must be a string list")
            evidence = []
        if status == "IMPLEMENTED" and not evidence:
            errors.append(f"{requirement_id} IMPLEMENTED requires evidence")
        _check_evidence(evidence, repo_root, requirement_id, errors)

        if requirement.priority == "MUST" and not verification:
            errors.append(f"{requirement_id} MUST requires verification")

    if errors:
        raise TraceabilityError("traceability validation failed:\n- " + "\n- ".join(errors))

    return TraceabilityReport(
        baseline_id=baseline_id,
        requirement_count=len(requirements),
        requirements_sha256=requirements_sha256,
        priority_counts=dict(sorted(Counter(r.priority for r in requirements.values()).items())),
        status_counts=dict(sorted(status_counts.items())),
        target_slice_counts=dict(sorted(target_slice_counts.items())),
    )


def effective_traceability_records(
    source_path: Path = DEFAULT_SOURCE,
    baseline_path: Path = DEFAULT_BASELINE,
    repo_root: Path = ROOT,
) -> dict[str, dict[str, Any]]:
    """Return effective assignments only after the full baseline gate succeeds."""
    validate_traceability(source_path, baseline_path, repo_root)
    requirements = extract_requirements(source_path)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    records: dict[str, dict[str, Any]] = {}
    for assignment in baseline["assignments"]:
        for selector in assignment["selectors"]:
            for requirement_id in expand_selector(selector):
                records[requirement_id] = dict(assignment)
    for requirement_id, override in baseline.get("overrides", {}).items():
        records[requirement_id].update(override)
    return {requirement_id: records[requirement_id] for requirement_id in requirements}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate NSL SRS traceability")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        report = validate_traceability(args.source, args.baseline)
    except TraceabilityError as exc:
        print(str(exc))
        return 1
    if args.as_json:
        print(json.dumps(report.to_data(), ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"Traceability gate: {report.requirement_count} requirements, "
            f"statuses={report.status_counts}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
