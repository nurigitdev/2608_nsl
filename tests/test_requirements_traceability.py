from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.requirements_traceability import (
    DEFAULT_BASELINE,
    DEFAULT_SOURCE,
    TraceabilityError,
    expand_selector,
    extract_requirements,
    main,
    requirement_fingerprint,
    validate_traceability,
)


def _minimal_baseline() -> dict:
    return {
        "schema_version": 1,
        "baseline_id": "TEST-RB",
        "source_document": "srs.md",
        "expected_requirement_count": 1,
        "requirements_sha256": "AUTO",
        "current_slice": "0001",
        "scope_decisions": [
            {"decision_id": "D-001", "decision": "Test scope decision."}
        ],
        "extension_requirement_exceptions": [],
        "slices": {
            "0001": {"scope": "SRS", "title": "Test Slice"},
            "0002": {"scope": "EXTENSION", "title": "Test Extension"},
        },
        "assignments": [
            {
                "selectors": ["NSL-X-001"],
                "status": "PLANNED",
                "target_slice": "0001",
                "verification": ["TEST-X-001"],
                "evidence": [],
            }
        ],
        "overrides": {},
    }


def _write_case(tmp_path: Path, baseline: dict, source: str | None = None):
    source_path = tmp_path / "srs.md"
    source_path.write_text(
        source or "| NSL-X-001 | Test requirement. | MUST |\n", encoding="utf-8"
    )
    if baseline.get("requirements_sha256") == "AUTO":
        baseline["requirements_sha256"] = requirement_fingerprint(
            extract_requirements(source_path)
        )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    return source_path, baseline_path


def test_repository_traceability_baseline_is_complete() -> None:
    report = validate_traceability(DEFAULT_SOURCE, DEFAULT_BASELINE)

    assert report.baseline_id == "NSL-V0.1-SRS-RB1"
    assert report.requirement_count == 325
    assert (
        report.requirements_sha256
        == "1aa65b838f6f3a54b3e76f0b73e3e0efa1fac0558ea1d2c05c03542433bbac77"
    )
    assert report.priority_counts == {"MAY": 1, "MUST": 294, "SHOULD": 30}
    assert report.status_counts == {
        "IMPLEMENTED": 315,
        "PARTIAL": 3,
        "PLANNED": 7,
    }
    assert sum(report.target_slice_counts.values()) == 325


def test_traceability_cli_json_output(capsys) -> None:
    assert main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["requirement_count"] == 325


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("plain prose\n", "no requirements found"),
        ("| NSL-X-001 | Missing priority |\n", "malformed requirement row"),
        (
            "| NSL-X-001 | First. | MUST |\n"
            "| NSL-X-001 | Duplicate. | SHOULD |\n",
            "duplicate requirement NSL-X-001",
        ),
    ],
)
def test_extract_requirements_rejects_empty_malformed_and_duplicate_rows(
    tmp_path, source, message
) -> None:
    source_path = tmp_path / "srs.md"
    source_path.write_text(source, encoding="utf-8")
    with pytest.raises(TraceabilityError, match=message):
        extract_requirements(source_path)


def test_requirement_selector_boundary_values() -> None:
    assert expand_selector("NSL-X-001") == ("NSL-X-001",)
    assert expand_selector("NSL-X-001..001") == ("NSL-X-001",)
    assert expand_selector("NSL-X-001..003") == (
        "NSL-X-001",
        "NSL-X-002",
        "NSL-X-003",
    )
    with pytest.raises(TraceabilityError, match="descending"):
        expand_selector("NSL-X-003..001")
    with pytest.raises(TraceabilityError, match="invalid"):
        expand_selector("NSL-X-1")


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda baseline: baseline.update(expected_requirement_count=2),
            "expected_requirement_count=2, extracted=1",
        ),
        (
            lambda baseline: baseline.update(source_document="wrong.md"),
            "source_document='wrong.md'",
        ),
        (
            lambda baseline: baseline.update(requirements_sha256="0" * 64),
            "requirements_sha256",
        ),
        (
            lambda baseline: baseline["assignments"].clear(),
            "assignments must be a non-empty list",
        ),
        (
            lambda baseline: baseline["assignments"][0].update(
                selectors=["NSL-X-002"]
            ),
            "unknown requirement in selector: NSL-X-002",
        ),
        (
            lambda baseline: baseline["assignments"].append(
                deepcopy(baseline["assignments"][0])
            ),
            "requirement mapped more than once: NSL-X-001",
        ),
    ],
)
def test_traceability_rejects_count_source_and_mapping_errors(
    tmp_path, mutator, message
) -> None:
    baseline = _minimal_baseline()
    mutator(baseline)
    source_path, baseline_path = _write_case(tmp_path, baseline)
    with pytest.raises(TraceabilityError, match=message):
        validate_traceability(source_path, baseline_path, tmp_path)


def test_implemented_requirement_requires_existing_evidence(tmp_path) -> None:
    baseline = _minimal_baseline()
    baseline["assignments"][0].update(status="IMPLEMENTED", evidence=[])
    source_path, baseline_path = _write_case(tmp_path, baseline)
    with pytest.raises(TraceabilityError, match="IMPLEMENTED requires evidence"):
        validate_traceability(source_path, baseline_path, tmp_path)

    baseline["assignments"][0]["evidence"] = ["missing.py::test_missing"]
    source_path, baseline_path = _write_case(tmp_path, baseline)
    with pytest.raises(TraceabilityError, match="evidence path does not exist"):
        validate_traceability(source_path, baseline_path, tmp_path)


def test_extension_requirement_requires_an_explicit_exception(tmp_path) -> None:
    baseline = _minimal_baseline()
    baseline["assignments"][0]["target_slice"] = "0002"
    source_path, baseline_path = _write_case(tmp_path, baseline)
    with pytest.raises(TraceabilityError, match="cannot target extension slice"):
        validate_traceability(source_path, baseline_path, tmp_path)

    baseline["extension_requirement_exceptions"] = ["NSL-X-001"]
    source_path, baseline_path = _write_case(tmp_path, baseline)
    report = validate_traceability(source_path, baseline_path, tmp_path)
    assert report.target_slice_counts == {"0002": 1}


def test_out_of_scope_requirement_requires_a_valid_decision(tmp_path) -> None:
    baseline = _minimal_baseline()
    baseline["assignments"][0].update(
        status="OUT_OF_SCOPE", target_slice=None, decision_ref="UNKNOWN"
    )
    source_path, baseline_path = _write_case(tmp_path, baseline)
    with pytest.raises(TraceabilityError, match="requires a valid decision_ref"):
        validate_traceability(source_path, baseline_path, tmp_path)

    baseline["assignments"][0]["decision_ref"] = "D-001"
    source_path, baseline_path = _write_case(tmp_path, baseline)
    report = validate_traceability(source_path, baseline_path, tmp_path)
    assert report.status_counts == {"OUT_OF_SCOPE": 1}
