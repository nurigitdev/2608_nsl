from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.extension_traceability import (
    DEFAULT_BASELINE,
    ExtensionTraceabilityError,
    extension_requirement_fingerprint,
    validate_extension_traceability,
)


ROOT = Path(__file__).resolve().parents[1]


def _baseline() -> dict:
    return json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))


def _write_baseline(tmp_path: Path, baseline) -> Path:
    path = tmp_path / "extension.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    return path


def test_cli_extension_baseline_is_complete_and_verifiable() -> None:
    baseline = _baseline()
    report = validate_extension_traceability()

    assert report.baseline_id == "NSL-CLI-LOCAL-EXECUTION-EXT-1"
    assert report.requirement_count == 15
    assert report.status_counts == {"IMPLEMENTED": 15}
    assert report.part_counts == {"A": 5, "B": 5, "C": 5}
    assert extension_requirement_fingerprint(baseline["requirements"]) == (
        "616522c34faccad5e742ec969977f2362a0b1c0ab25d2215d4be9d632cd992d5"
    )


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda item: item.update(schema_version=2), "schema_version must be 1"),
        (
            lambda item: item.update(expected_requirement_count=14),
            "must both be 15",
        ),
        (
            lambda item: item["requirements"][0].update(id="NSL-CLX-999"),
            "id must be NSL-CLX-001",
        ),
        (
            lambda item: item["requirements"][0].update(part="B"),
            "part must be A",
        ),
        (
            lambda item: item["requirements"][0].update(status="PLANNED"),
            "status must be IMPLEMENTED or PARTIAL",
        ),
        (
            lambda item: item["requirements"][0].update(verification=[]),
            "verification must be a non-empty string list",
        ),
        (
            lambda item: item["requirements"][0].update(
                evidence=["missing.py::test_missing"]
            ),
            "evidence path does not exist",
        ),
        (
            lambda item: item.update(requirements_sha256="0" * 64),
            "requirements_sha256",
        ),
    ],
)
def test_cli_extension_baseline_rejects_invalid_boundaries(
    tmp_path, mutator, message
) -> None:
    baseline = deepcopy(_baseline())
    mutator(baseline)
    with pytest.raises(ExtensionTraceabilityError, match=message):
        validate_extension_traceability(_write_baseline(tmp_path, baseline), ROOT)


def test_cli_extension_baseline_rejects_malformed_documents(tmp_path) -> None:
    path = tmp_path / "extension.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ExtensionTraceabilityError, match="cannot load"):
        validate_extension_traceability(path, ROOT)

    with pytest.raises(ExtensionTraceabilityError, match="must be an object"):
        validate_extension_traceability(_write_baseline(tmp_path, []), ROOT)
