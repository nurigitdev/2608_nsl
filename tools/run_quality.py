from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


STATEMENT_THRESHOLD = 95.0
BRANCH_THRESHOLD = 90.0
COVERAGE_JSON = Path("test/coverage.json")


def percentage(covered: int, total: int) -> float:
    return 100.0 if total == 0 else 100.0 * covered / total


def main() -> int:
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
    failed = False
    if statement < STATEMENT_THRESHOLD:
        print("statement coverage gate failed", file=sys.stderr)
        failed = True
    if branch < BRANCH_THRESHOLD:
        print("branch coverage gate failed", file=sys.stderr)
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

