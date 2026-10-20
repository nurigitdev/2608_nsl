from __future__ import annotations


STATEMENT_THRESHOLD = 99.0
BRANCH_THRESHOLD = 98.0


def failed_coverage_dimensions(
    statement: float,
    branch: float,
) -> tuple[str, ...]:
    failures: list[str] = []
    if statement < STATEMENT_THRESHOLD:
        failures.append("statement")
    if branch < BRANCH_THRESHOLD:
        failures.append("branch")
    return tuple(failures)
