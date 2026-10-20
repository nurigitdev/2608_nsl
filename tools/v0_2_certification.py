from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any

from tools.quality_policy import (
    BRANCH_THRESHOLD,
    STATEMENT_THRESHOLD,
    failed_coverage_dimensions,
)
from tools.requirements_traceability import (
    ROOT,
    V0_1_BASELINE,
    V0_1_SOURCE,
    V0_2_BASELINE,
    V0_2_SOURCE,
    TraceabilityError,
    effective_traceability_records,
    validate_traceability,
)
from tools.v0_2_readiness import (
    DEFAULT_BASELINE as READINESS_BASELINE,
    ReadinessError,
    validate_v0_2_readiness,
)


DEFAULT_ACCEPTANCE_BASELINE = ROOT / "requirements/nsl_v0_2_acceptance.json"
DEFAULT_ARCHITECTURE_REPORT = ROOT / "test/architecture_certification.json"
DEFAULT_PERFORMANCE_REPORT = ROOT / "test/performance_acceptance.json"
DEFAULT_COVERAGE_REPORT = ROOT / "test/coverage.json"
MANDATORY_TEST_MARKER = "신규 필수 Test:"
CASE_ID = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
EXPECTED_GROUP_IDS = {
    "vertical_acceptance": ("AC-W01", "AC-W02"),
    "compatibility_acceptance": tuple(f"BC-{number:03d}" for number in range(1, 9)),
    "performance_acceptance": tuple(f"PERF-{number:03d}" for number in range(1, 7)),
    "negative_safety_acceptance": tuple(f"NEG-{number:03d}" for number in range(1, 16)),
}


class V02CertificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AcceptanceEvidenceReport:
    mandatory_test_count: int
    vertical_acceptance_count: int
    compatibility_acceptance_count: int
    performance_acceptance_count: int
    negative_safety_acceptance_count: int

    @property
    def total_evidence_count(self) -> int:
        return sum(
            (
                self.mandatory_test_count,
                self.vertical_acceptance_count,
                self.compatibility_acceptance_count,
                self.performance_acceptance_count,
                self.negative_safety_acceptance_count,
            )
        )


@dataclass(frozen=True, slots=True)
class V02CertificationReport:
    requirement_count: int
    requirements_sha256: str
    status_counts: dict[str, int]
    v0_1_requirement_count: int
    acceptance: AcceptanceEvidenceReport
    statement_coverage: float
    branch_coverage: float
    performance: dict[str, int | str]

    def to_data(self) -> dict[str, Any]:
        return {
            "status": "CERTIFIED",
            "baseline_id": "NSL-V0.2-SRS-RB1",
            "requirement_count": self.requirement_count,
            "requirements_sha256": self.requirements_sha256,
            "status_counts": self.status_counts,
            "v0_1_requirement_count": self.v0_1_requirement_count,
            "mandatory_test_count": self.acceptance.mandatory_test_count,
            "vertical_acceptance_count": self.acceptance.vertical_acceptance_count,
            "compatibility_acceptance_count": self.acceptance.compatibility_acceptance_count,
            "performance_acceptance_count": self.acceptance.performance_acceptance_count,
            "negative_safety_acceptance_count": self.acceptance.negative_safety_acceptance_count,
            "acceptance_evidence_count": self.acceptance.total_evidence_count,
            "statement_coverage": round(self.statement_coverage, 2),
            "branch_coverage": round(self.branch_coverage, 2),
            "coverage_thresholds": {
                "statement": STATEMENT_THRESHOLD,
                "branch": BRANCH_THRESHOLD,
            },
            "performance": self.performance,
        }


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise V02CertificationError(f"cannot load {label}: {path}") from error
    if not isinstance(value, dict):
        raise V02CertificationError(f"{label} must contain a JSON object")
    return value


def extract_mandatory_test_titles(source_path: Path = V0_2_SOURCE) -> tuple[str, ...]:
    try:
        lines = source_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise V02CertificationError(f"cannot load v0.2 SRS: {source_path}") from error
    try:
        marker = next(
            index for index, line in enumerate(lines) if line.strip() == MANDATORY_TEST_MARKER
        )
        fence_start = next(
            index
            for index in range(marker + 1, len(lines))
            if lines[index].strip() == "```text"
        )
        fence_end = next(
            index
            for index in range(fence_start + 1, len(lines))
            if lines[index].strip() == "```"
        )
    except StopIteration as error:
        raise V02CertificationError("cannot locate SRS section 25 mandatory tests") from error
    titles = tuple(
        line.strip() for line in lines[fence_start + 1 : fence_end] if line.strip()
    )
    if len(titles) != 32 or len(set(titles)) != 32:
        raise V02CertificationError(
            "SRS section 25 must contain exactly 32 unique mandatory tests"
        )
    return titles


def _evidence_definitions(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise V02CertificationError(f"acceptance evidence cannot be parsed: {path}") from error
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _validate_evidence(
    references: object,
    *,
    owner: str,
    repo_root: Path,
    definitions: dict[Path, set[str]],
) -> None:
    if not isinstance(references, list) or not references or not all(
        isinstance(item, str) and item for item in references
    ):
        raise V02CertificationError(f"{owner}.evidence must be a non-empty string list")
    if len(references) != len(set(references)):
        raise V02CertificationError(f"{owner}.evidence contains duplicates")
    root = repo_root.resolve()
    for reference in references:
        path_text, separator, symbol = reference.partition("::")
        pure = PurePosixPath(path_text)
        if (
            not separator
            or not symbol
            or pure.is_absolute()
            or ".." in pure.parts
            or "\\" in path_text
        ):
            raise V02CertificationError(f"invalid acceptance evidence: {reference}")
        path = (root / Path(*pure.parts)).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise V02CertificationError(
                f"acceptance evidence escapes repository: {reference}"
            ) from error
        if not path.is_file():
            raise V02CertificationError(f"acceptance evidence path is missing: {reference}")
        if path not in definitions:
            definitions[path] = _evidence_definitions(path)
        if symbol not in definitions[path]:
            raise V02CertificationError(f"acceptance evidence symbol is missing: {reference}")


def _validate_group(
    baseline: dict[str, Any],
    field: str,
    expected_ids: tuple[str, ...],
    repo_root: Path,
    definitions: dict[Path, set[str]],
) -> int:
    cases = baseline.get(field)
    if not isinstance(cases, list):
        raise V02CertificationError(f"{field} must be a list")
    actual_ids: list[str] = []
    for index, item in enumerate(cases):
        owner = f"{field}[{index}]"
        if not isinstance(item, dict) or set(item) != {"id", "evidence"}:
            raise V02CertificationError(f"{owner} is malformed")
        case_id = item["id"]
        if not isinstance(case_id, str) or CASE_ID.fullmatch(case_id) is None:
            raise V02CertificationError(f"{owner}.id is invalid")
        actual_ids.append(case_id)
        _validate_evidence(
            item["evidence"],
            owner=case_id,
            repo_root=repo_root,
            definitions=definitions,
        )
    if tuple(actual_ids) != expected_ids:
        raise V02CertificationError(f"{field} IDs or order differ from the baseline")
    return len(cases)


def validate_acceptance_evidence(
    source_path: Path = V0_2_SOURCE,
    baseline_path: Path = DEFAULT_ACCEPTANCE_BASELINE,
    repo_root: Path = ROOT,
) -> AcceptanceEvidenceReport:
    baseline = _load_object(baseline_path, "v0.2 acceptance baseline")
    expected_keys = {
        "schema_version",
        "baseline_id",
        "srs_section",
        "expected_mandatory_test_count",
        "mandatory_tests",
        *EXPECTED_GROUP_IDS,
    }
    if set(baseline) != expected_keys:
        raise V02CertificationError("v0.2 acceptance baseline fields are not exact")
    if baseline["schema_version"] != 1:
        raise V02CertificationError("acceptance schema_version must be 1")
    if baseline["baseline_id"] != "NSL-V0.2-ACCEPTANCE-RB1":
        raise V02CertificationError("acceptance baseline_id is invalid")
    if baseline["srs_section"] != "25":
        raise V02CertificationError("acceptance srs_section must be 25")

    titles = extract_mandatory_test_titles(source_path)
    if baseline["expected_mandatory_test_count"] != len(titles):
        raise V02CertificationError("expected_mandatory_test_count differs from the SRS")
    cases = baseline["mandatory_tests"]
    if not isinstance(cases, list):
        raise V02CertificationError("mandatory_tests must be a list")
    definitions: dict[Path, set[str]] = {}
    actual_titles: list[str] = []
    actual_ids: list[str] = []
    for index, item in enumerate(cases):
        owner = f"mandatory_tests[{index}]"
        if not isinstance(item, dict) or set(item) != {"id", "title", "evidence"}:
            raise V02CertificationError(f"{owner} is malformed")
        actual_ids.append(item["id"])
        actual_titles.append(item["title"])
        _validate_evidence(
            item["evidence"],
            owner=str(item["id"]),
            repo_root=repo_root,
            definitions=definitions,
        )
    expected_ids = tuple(f"V02-T{number:03d}" for number in range(1, 33))
    if tuple(actual_ids) != expected_ids:
        raise V02CertificationError("mandatory test IDs must be V02-T001 through V02-T032")
    if tuple(actual_titles) != titles:
        raise V02CertificationError("mandatory test titles or order differ from SRS section 25")

    group_counts = {
        field: _validate_group(
            baseline,
            field,
            expected_ids,
            repo_root,
            definitions,
        )
        for field, expected_ids in EXPECTED_GROUP_IDS.items()
    }
    return AcceptanceEvidenceReport(
        mandatory_test_count=len(cases),
        vertical_acceptance_count=group_counts["vertical_acceptance"],
        compatibility_acceptance_count=group_counts["compatibility_acceptance"],
        performance_acceptance_count=group_counts["performance_acceptance"],
        negative_safety_acceptance_count=group_counts[
            "negative_safety_acceptance"
        ],
    )


def _percentage(covered: object, total: object, label: str) -> float:
    if type(covered) is not int or type(total) is not int or covered < 0 or total < 0:
        raise V02CertificationError(f"coverage {label} counters are invalid")
    if covered > total:
        raise V02CertificationError(f"coverage {label} covered count exceeds total")
    return 100.0 if total == 0 else 100.0 * covered / total


def _validate_coverage(path: Path) -> tuple[float, float]:
    report = _load_object(path, "coverage report")
    totals = report.get("totals")
    if not isinstance(totals, dict):
        raise V02CertificationError("coverage report totals are missing")
    statement = _percentage(
        totals.get("covered_lines"), totals.get("num_statements"), "statement"
    )
    branch = _percentage(
        totals.get("covered_branches"), totals.get("num_branches"), "branch"
    )
    failures = failed_coverage_dimensions(statement, branch)
    if failures:
        raise V02CertificationError(
            "RD-011 coverage threshold failed: " + ", ".join(failures)
        )
    return statement, branch


def _validate_performance(path: Path) -> dict[str, int | str]:
    report = _load_object(path, "performance report")
    required = {
        "status",
        "iterations",
        "python_version",
        "platform",
        "parse_p95_ns",
        "compile_median_ns",
        "nso_load_median_ns",
        "runtime_total_ns",
        "runtime_overhead_ns",
        "tool_duration_ns",
        "tool_call_count",
        "protected_evidence_median_ns",
        "bounded_loop_duration_ns",
        "bounded_loop_iterations",
        "bounded_loop_emitted_rows",
    }
    if set(report) != required or report["status"] != "PASSED":
        raise V02CertificationError("performance report is incomplete or failed")
    numeric_fields = required - {"status", "python_version", "platform"}
    if not all(type(report[field]) is int and report[field] >= 0 for field in numeric_fields):
        raise V02CertificationError("performance report contains invalid measurements")
    if report["iterations"] < 1 or report["tool_call_count"] < 1:
        raise V02CertificationError("performance report did not execute required work")
    if (
        report["bounded_loop_iterations"] != 1000
        or report["bounded_loop_emitted_rows"] != 1000
    ):
        raise V02CertificationError("1,000-loop acceptance evidence is incomplete")
    version = report["python_version"]
    if not isinstance(version, str) or not isinstance(report["platform"], str):
        raise V02CertificationError("performance environment is missing")
    try:
        major_minor = tuple(int(item) for item in version.split(".")[:2])
    except ValueError as error:
        raise V02CertificationError("performance Python version is invalid") from error
    if major_minor < (3, 12):
        raise V02CertificationError("performance certification requires Python 3.12+")
    return {key: report[key] for key in sorted(report)}


def certify_v0_2(
    *,
    acceptance_baseline_path: Path = DEFAULT_ACCEPTANCE_BASELINE,
    architecture_report_path: Path = DEFAULT_ARCHITECTURE_REPORT,
    performance_report_path: Path = DEFAULT_PERFORMANCE_REPORT,
    coverage_report_path: Path = DEFAULT_COVERAGE_REPORT,
    repo_root: Path = ROOT,
) -> V02CertificationReport:
    try:
        v0_2 = validate_traceability(V0_2_SOURCE, V0_2_BASELINE, repo_root)
        records = effective_traceability_records(
            V0_2_SOURCE, V0_2_BASELINE, repo_root
        )
        v0_1 = validate_traceability(V0_1_SOURCE, V0_1_BASELINE, repo_root)
        validate_v0_2_readiness(READINESS_BASELINE, repo_root)
    except (TraceabilityError, ReadinessError) as error:
        raise V02CertificationError(str(error)) from error
    gaps = tuple(
        requirement_id
        for requirement_id, record in records.items()
        if record["status"] != "IMPLEMENTED"
    )
    if gaps:
        raise V02CertificationError(
            "v0.2 SRS has non-implemented requirements: " + ", ".join(gaps)
        )

    acceptance = validate_acceptance_evidence(
        V0_2_SOURCE, acceptance_baseline_path, repo_root
    )
    architecture = _load_object(architecture_report_path, "architecture report")
    if (
        architecture.get("status") != "CERTIFIED"
        or architecture.get("violation_count") != 0
        or architecture.get("violations") != []
    ):
        raise V02CertificationError("architecture report is not certified")
    performance = _validate_performance(performance_report_path)
    statement, branch = _validate_coverage(coverage_report_path)
    return V02CertificationReport(
        requirement_count=v0_2.requirement_count,
        requirements_sha256=v0_2.requirements_sha256,
        status_counts=dict(
            sorted(Counter(item["status"] for item in records.values()).items())
        ),
        v0_1_requirement_count=v0_1.requirement_count,
        acceptance=acceptance,
        statement_coverage=statement,
        branch_coverage=branch,
        performance=performance,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Certify the NSL v0.2 release")
    parser.add_argument("--acceptance-baseline", type=Path, default=DEFAULT_ACCEPTANCE_BASELINE)
    parser.add_argument("--architecture-report", type=Path, default=DEFAULT_ARCHITECTURE_REPORT)
    parser.add_argument("--performance-report", type=Path, default=DEFAULT_PERFORMANCE_REPORT)
    parser.add_argument("--coverage-report", type=Path, default=DEFAULT_COVERAGE_REPORT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = certify_v0_2(
            acceptance_baseline_path=args.acceptance_baseline,
            architecture_report_path=args.architecture_report,
            performance_report_path=args.performance_report,
            coverage_report_path=args.coverage_report,
        )
    except V02CertificationError as error:
        print(str(error), file=sys.stderr)
        return 1
    payload = json.dumps(
        report.to_data(), ensure_ascii=False, indent=2, sort_keys=True
    )
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        except OSError as error:
            print(f"cannot write v0.2 certification report: {error}", file=sys.stderr)
            return 2
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
