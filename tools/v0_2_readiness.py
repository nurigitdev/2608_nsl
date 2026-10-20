from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "requirements/nsl_v0_2_readiness.json"
CHANGE_ID = re.compile(r"^CHG-\d{3}$")
DECISION_ID = re.compile(r"^RD-\d{3}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_SOURCE_DOCUMENT_KEYS = frozenset(
    {"00-master", "00-prd", *(f"{number:02d}" for number in range(1, 13))}
)


class ReadinessError(ValueError):
    """Raised when the v0.2 implementation-readiness baseline is invalid."""


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    readiness_id: str
    authority_document_count: int
    source_document_count: int
    change_count: int
    change_priority_counts: dict[str, int]
    decision_count: int

    def to_data(self) -> dict[str, Any]:
        return {
            "readiness_id": self.readiness_id,
            "authority_document_count": self.authority_document_count,
            "source_document_count": self.source_document_count,
            "change_count": self.change_count,
            "change_priority_counts": self.change_priority_counts,
            "decision_count": self.decision_count,
        }


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"cannot load {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReadinessError(f"{label} must contain a JSON object")
    return value


def _repo_file(
    value: Any,
    repo_root: Path,
    label: str,
    errors: list[str],
) -> Path | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a non-empty repository-relative path")
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "\\" in value:
        errors.append(f"{label} must stay inside the repository: {value!r}")
        return None
    root = repo_root.resolve()
    path = (root / Path(*pure.parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        errors.append(f"{label} escapes the repository: {value!r}")
        return None
    if not path.is_file():
        errors.append(f"{label} does not exist: {value}")
        return None
    return path


def _expected_count(
    baseline: dict[str, Any],
    field: str,
    actual: int,
    errors: list[str],
) -> None:
    expected = baseline.get(field)
    if type(expected) is not int or expected < 0:
        errors.append(f"{field} must be a non-negative integer")
    elif expected != actual:
        errors.append(f"{field}={expected}, actual={actual}")


def _validate_authorities(
    baseline: dict[str, Any], repo_root: Path, errors: list[str]
) -> int:
    authorities = baseline.get("authority_documents")
    if not isinstance(authorities, list):
        errors.append("authority_documents must be a list")
        authorities = []
    _expected_count(
        baseline,
        "expected_authority_document_count",
        len(authorities),
        errors,
    )
    paths: set[str] = set()
    for index, item in enumerate(authorities):
        owner = f"authority_documents[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{owner} must be an object")
            continue
        path_text = item.get("path")
        if isinstance(path_text, str):
            if path_text in paths:
                errors.append(f"duplicate authority document: {path_text}")
            paths.add(path_text)
        path = _repo_file(path_text, repo_root, f"{owner}.path", errors)
        digest = item.get("sha256")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            errors.append(f"{owner}.sha256 must be lowercase SHA-256")
        elif path is not None:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != actual:
                errors.append(
                    f"{owner}.sha256 mismatch: expected={digest}, actual={actual}"
                )
    return len(authorities)


def _validate_source_documents(
    baseline: dict[str, Any], repo_root: Path, errors: list[str]
) -> dict[str, str]:
    documents = baseline.get("source_documents")
    if not isinstance(documents, dict):
        errors.append("source_documents must be an object")
        documents = {}
    _expected_count(
        baseline,
        "expected_source_document_count",
        len(documents),
        errors,
    )
    keys = set(documents)
    missing = sorted(EXPECTED_SOURCE_DOCUMENT_KEYS - keys)
    extra = sorted(keys - EXPECTED_SOURCE_DOCUMENT_KEYS)
    if missing:
        errors.append("missing source document keys: " + ", ".join(missing))
    if extra:
        errors.append("unknown source document keys: " + ", ".join(extra))

    contents: dict[str, str] = {}
    paths: set[str] = set()
    for key, path_text in documents.items():
        if isinstance(path_text, str):
            if path_text in paths:
                errors.append(f"duplicate source document path: {path_text}")
            paths.add(path_text)
        path = _repo_file(path_text, repo_root, f"source_documents.{key}", errors)
        if path is not None:
            try:
                contents[key] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                errors.append(f"source_documents.{key} must be UTF-8 Markdown")
    return contents


def _validate_changes(
    baseline: dict[str, Any],
    source_contents: dict[str, str],
    errors: list[str],
) -> tuple[int, Counter[str]]:
    changes = baseline.get("change_ledger")
    if not isinstance(changes, list):
        errors.append("change_ledger must be a list")
        changes = []
    _expected_count(baseline, "expected_change_count", len(changes), errors)
    expected_count = baseline.get("expected_change_count")
    expected_ids = (
        {f"CHG-{number:03d}" for number in range(1, expected_count + 1)}
        if type(expected_count) is int and expected_count >= 0
        else set()
    )

    seen: set[str] = set()
    priorities: Counter[str] = Counter()
    for index, change in enumerate(changes):
        owner = f"change_ledger[{index}]"
        if not isinstance(change, dict):
            errors.append(f"{owner} must be an object")
            continue
        change_id = change.get("change_id")
        if not isinstance(change_id, str) or CHANGE_ID.fullmatch(change_id) is None:
            errors.append(f"{owner}.change_id is invalid")
            continue
        if change_id in seen:
            errors.append(f"duplicate change id: {change_id}")
        seen.add(change_id)
        priority = change.get("priority")
        if priority not in {"P0", "P1"}:
            errors.append(f"{change_id}.priority must be P0 or P1")
        else:
            priorities[priority] += 1
        if not isinstance(change.get("title"), str) or not change["title"]:
            errors.append(f"{change_id}.title must be non-empty")

        affected = change.get("affected_documents")
        if not isinstance(affected, list) or not affected or not all(
            isinstance(key, str) and key for key in affected
        ):
            errors.append(f"{change_id}.affected_documents must be non-empty")
            continue
        if len(affected) != len(set(affected)):
            errors.append(f"{change_id}.affected_documents contains duplicates")
        for key in affected:
            if key not in EXPECTED_SOURCE_DOCUMENT_KEYS:
                errors.append(f"{change_id} references unknown document key: {key}")
            elif key in source_contents and change_id not in source_contents[key]:
                errors.append(f"{change_id} is missing from affected document: {key}")

    missing = sorted(expected_ids - seen)
    extra = sorted(seen - expected_ids)
    if missing:
        errors.append("missing change ids: " + ", ".join(missing))
    if extra:
        errors.append("unexpected change ids: " + ", ".join(extra))
    return len(changes), priorities


def _validate_evidence(
    reference: Any,
    repo_root: Path,
    owner: str,
    errors: list[str],
) -> None:
    if not isinstance(reference, str) or "::" not in reference:
        errors.append(f"{owner}.evidence must be path::symbol")
        return
    path_text, symbol = reference.split("::", 1)
    if not symbol:
        errors.append(f"{owner}.evidence symbol must be non-empty")
        return
    path = _repo_file(path_text, repo_root, f"{owner}.evidence", errors)
    if path is not None:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"{owner}.evidence must reference UTF-8 text")
        else:
            if symbol not in content:
                errors.append(f"{owner}.evidence symbol not found: {reference}")


def _validate_decisions(
    baseline: dict[str, Any],
    traceability: dict[str, Any],
    repo_root: Path,
    errors: list[str],
) -> int:
    decisions = baseline.get("readiness_decisions")
    if not isinstance(decisions, list):
        errors.append("readiness_decisions must be a list")
        decisions = []
    _expected_count(baseline, "expected_decision_count", len(decisions), errors)
    expected_count = baseline.get("expected_decision_count")
    expected_ids = (
        {f"RD-{number:03d}" for number in range(1, expected_count + 1)}
        if type(expected_count) is int and expected_count >= 0
        else set()
    )
    slices = traceability.get("slices")
    declared_slices = set(slices) if isinstance(slices, dict) else set()
    seen: set[str] = set()
    for index, decision in enumerate(decisions):
        owner = f"readiness_decisions[{index}]"
        if not isinstance(decision, dict):
            errors.append(f"{owner} must be an object")
            continue
        decision_id = decision.get("decision_id")
        if not isinstance(decision_id, str) or DECISION_ID.fullmatch(decision_id) is None:
            errors.append(f"{owner}.decision_id is invalid")
            continue
        if decision_id in seen:
            errors.append(f"duplicate readiness decision: {decision_id}")
        seen.add(decision_id)
        if decision.get("status") != "FROZEN":
            errors.append(f"{decision_id}.status must be FROZEN")
        target_slice = decision.get("target_slice")
        if target_slice not in declared_slices:
            errors.append(f"{decision_id}.target_slice is not declared: {target_slice!r}")
        _validate_evidence(decision.get("evidence"), repo_root, decision_id, errors)

    missing = sorted(expected_ids - seen)
    extra = sorted(seen - expected_ids)
    if missing:
        errors.append("missing readiness decisions: " + ", ".join(missing))
    if extra:
        errors.append("unexpected readiness decisions: " + ", ".join(extra))
    return len(decisions)


def validate_v0_2_readiness(
    baseline_path: Path = DEFAULT_BASELINE,
    repo_root: Path = ROOT,
) -> ReadinessReport:
    baseline = _load_object(baseline_path, "readiness baseline")
    errors: list[str] = []
    if baseline.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    readiness_id = baseline.get("readiness_id")
    if not isinstance(readiness_id, str) or not readiness_id:
        errors.append("readiness_id must be a non-empty string")
        readiness_id = "<invalid>"

    traceability_path = _repo_file(
        baseline.get("traceability_baseline"),
        repo_root,
        "traceability_baseline",
        errors,
    )
    traceability: dict[str, Any] = {}
    if traceability_path is not None:
        try:
            traceability = _load_object(traceability_path, "traceability baseline")
        except ReadinessError as exc:
            errors.append(str(exc))
    expected_traceability_id = baseline.get("traceability_baseline_id")
    if not isinstance(expected_traceability_id, str) or not expected_traceability_id:
        errors.append("traceability_baseline_id must be a non-empty string")
    elif traceability and traceability.get("baseline_id") != expected_traceability_id:
        errors.append(
            "traceability baseline id mismatch: "
            f"expected={expected_traceability_id!r}, "
            f"actual={traceability.get('baseline_id')!r}"
        )

    authority_count = _validate_authorities(baseline, repo_root, errors)
    source_contents = _validate_source_documents(baseline, repo_root, errors)
    change_count, priorities = _validate_changes(baseline, source_contents, errors)
    decision_count = _validate_decisions(
        baseline, traceability, repo_root, errors
    )

    if errors:
        raise ReadinessError(
            "v0.2 readiness validation failed:\n- " + "\n- ".join(errors)
        )
    return ReadinessReport(
        readiness_id=readiness_id,
        authority_document_count=authority_count,
        source_document_count=len(source_contents),
        change_count=change_count,
        change_priority_counts=dict(sorted(priorities.items())),
        decision_count=decision_count,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate NSL v0.2 architecture and implementation readiness"
    )
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        report = validate_v0_2_readiness(args.baseline)
    except ReadinessError as exc:
        print(str(exc))
        return 1
    if args.as_json:
        print(json.dumps(report.to_data(), ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"v0.2 readiness gate: {report.change_count} changes, "
            f"{report.decision_count} decisions"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
