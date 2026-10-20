from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from extension_traceability import (
    ExtensionTraceabilityError,
    validate_extension_traceability,
)
from requirements_traceability import (
    TraceabilityError,
    V0_2_BASELINE,
    V0_2_SOURCE,
    validate_traceability,
)
from quality_policy import (
    BRANCH_THRESHOLD,
    STATEMENT_THRESHOLD,
    failed_coverage_dimensions,
)
from v0_2_readiness import ReadinessError, validate_v0_2_readiness


COVERAGE_JSON = Path("test/coverage.json")
ROOT = Path(__file__).resolve().parents[1]


def percentage(covered: int, total: int) -> float:
    return 100.0 if total == 0 else 100.0 * covered / total


def main() -> int:
    try:
        traceability = validate_traceability()
    except TraceabilityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "v0.1 traceability gate: "
        f"{traceability.requirement_count} requirements, "
        f"statuses={traceability.status_counts}"
    )
    try:
        v0_2_traceability = validate_traceability(V0_2_SOURCE, V0_2_BASELINE)
    except TraceabilityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "v0.2 traceability gate: "
        f"{v0_2_traceability.requirement_count} requirements, "
        f"statuses={v0_2_traceability.status_counts}"
    )
    try:
        readiness = validate_v0_2_readiness()
    except ReadinessError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "v0.2 readiness gate: "
        f"{readiness.change_count} changes, "
        f"{readiness.decision_count} decisions"
    )
    try:
        extension = validate_extension_traceability()
    except ExtensionTraceabilityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        "Extension traceability gate: "
        f"{extension.requirement_count} requirements, "
        f"statuses={extension.status_counts}"
    )

    pytest_result = int(pytest.main([]))
    if pytest_result != 0:
        return pytest_result
    if not COVERAGE_JSON.is_file():
        print(f"coverage report not found: {COVERAGE_JSON}", file=sys.stderr)
        return 2

    report = json.loads(COVERAGE_JSON.read_text(encoding="utf-8"))
    totals = report["totals"]
    statement = percentage(totals["covered_lines"], totals["num_statements"])
    branch = percentage(totals["covered_branches"], totals["num_branches"])

    print(
        "Quality gate: "
        f"statement={statement:.2f}% (required {STATEMENT_THRESHOLD:.2f}%), "
        f"branch={branch:.2f}% (required {BRANCH_THRESHOLD:.2f}%)"
    )
    coverage_failures = failed_coverage_dimensions(statement, branch)
    for dimension in coverage_failures:
        print(f"{dimension} coverage gate failed", file=sys.stderr)
    failed = bool(coverage_failures)
    if not failed:
        architecture = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.architecture_certification",
                "--output",
                "test/architecture_certification.json",
            ],
            cwd=ROOT,
            check=False,
        )
        if architecture.returncode != 0:
            print("architecture certification gate failed", file=sys.stderr)
            failed = True
    if not failed:
        performance = subprocess.run(
            [sys.executable, "-m", "tools.performance_acceptance"],
            cwd=ROOT,
            check=False,
        )
        if performance.returncode != 0:
            print("performance acceptance gate failed", file=sys.stderr)
            failed = True
    if not failed:
        certification = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.srs_certification",
                "--output",
                "test/srs_certification.json",
            ],
            cwd=ROOT,
            check=False,
        )
        if certification.returncode != 0:
            print("SRS certification gate failed", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
