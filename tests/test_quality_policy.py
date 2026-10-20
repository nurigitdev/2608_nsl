from __future__ import annotations

import pytest

from tools.quality_policy import (
    BRANCH_THRESHOLD,
    STATEMENT_THRESHOLD,
    failed_coverage_dimensions,
)


def test_v0_2_quality_thresholds_preserve_v0_1_coverage_level() -> None:
    assert STATEMENT_THRESHOLD == 99.0
    assert BRANCH_THRESHOLD == 98.0


@pytest.mark.parametrize(
    ("statement", "branch", "expected"),
    [
        (0.0, 0.0, ("statement", "branch")),
        (98.99, 98.0, ("statement",)),
        (99.0, 97.99, ("branch",)),
        (99.0, 98.0, ()),
        (99.01, 98.01, ()),
        (100.0, 100.0, ()),
    ],
)
def test_coverage_policy_boundary_values(
    statement: float,
    branch: float,
    expected: tuple[str, ...],
) -> None:
    assert failed_coverage_dimensions(statement, branch) == expected
