from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.v0_2_certification import (
    DEFAULT_ACCEPTANCE_BASELINE,
    V02CertificationError,
    certify_v0_2,
    extract_mandatory_test_titles,
    main,
    validate_acceptance_evidence,
)


def _acceptance() -> dict:
    return json.loads(DEFAULT_ACCEPTANCE_BASELINE.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _release_reports(tmp_path: Path) -> tuple[Path, Path, Path]:
    architecture = _write_json(
        tmp_path / "architecture.json",
        {
            "status": "CERTIFIED",
            "checked_file_count": 64,
            "violation_count": 0,
            "violations": [],
        },
    )
    performance = _write_json(
        tmp_path / "performance.json",
        {
            "status": "PASSED",
            "iterations": 25,
            "python_version": "3.12.10",
            "platform": "test-platform",
            "parse_p95_ns": 1,
            "compile_median_ns": 2,
            "nso_load_median_ns": 1,
            "runtime_total_ns": 3,
            "runtime_overhead_ns": 2,
            "tool_duration_ns": 1,
            "tool_call_count": 2,
            "protected_evidence_median_ns": 1,
            "bounded_loop_duration_ns": 4,
            "bounded_loop_iterations": 1000,
            "bounded_loop_emitted_rows": 1000,
        },
    )
    coverage = _write_json(
        tmp_path / "coverage.json",
        {
            "totals": {
                "covered_lines": 990,
                "num_statements": 1000,
                "covered_branches": 980,
                "num_branches": 1000,
            }
        },
    )
    return architecture, performance, coverage


def test_slice_0069_acceptance_baseline_matches_srs_and_repository_evidence() -> None:
    titles = extract_mandatory_test_titles()
    report = validate_acceptance_evidence()

    assert len(titles) == 32
    assert titles[0] == "write without requires"
    assert titles[-1] == "language 0.2 → IR 1.1"
    assert report.mandatory_test_count == 32
    assert report.vertical_acceptance_count == 2
    assert report.compatibility_acceptance_count == 8
    assert report.performance_acceptance_count == 6
    assert report.negative_safety_acceptance_count == 15
    assert report.total_evidence_count == 63


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data.update(extra=True), "fields are not exact"),
        (
            lambda data: data.update(expected_mandatory_test_count=31),
            "expected_mandatory_test_count",
        ),
        (
            lambda data: data["mandatory_tests"][0].update(id="V02-T999"),
            "mandatory test IDs",
        ),
        (
            lambda data: data["mandatory_tests"][0].update(title="changed"),
            "titles or order",
        ),
        (
            lambda data: data["vertical_acceptance"].reverse(),
            "IDs or order",
        ),
        (
            lambda data: data["negative_safety_acceptance"][0].update(
                evidence=[]
            ),
            "non-empty string list",
        ),
        (
            lambda data: data["negative_safety_acceptance"][0].update(
                evidence=["tests/missing.py::test_missing"]
            ),
            "path is missing",
        ),
        (
            lambda data: data["negative_safety_acceptance"][0].update(
                evidence=["tests/test_quality_policy.py::test_missing"]
            ),
            "symbol is missing",
        ),
    ],
)
def test_slice_0069_acceptance_baseline_rejects_drift_and_missing_evidence(
    tmp_path: Path, mutator, message: str
) -> None:
    data = _acceptance()
    mutator(data)
    path = _write_json(tmp_path / "acceptance.json", data)

    with pytest.raises(V02CertificationError, match=message):
        validate_acceptance_evidence(baseline_path=path)


def test_slice_0069_srs_test_extraction_rejects_missing_or_drifted_sections(
    tmp_path: Path,
) -> None:
    with pytest.raises(V02CertificationError, match="cannot load v0.2 SRS"):
        extract_mandatory_test_titles(tmp_path / "missing.md")

    missing = tmp_path / "missing-section.md"
    missing.write_text("# SRS\n", encoding="utf-8")
    with pytest.raises(V02CertificationError, match="cannot locate"):
        extract_mandatory_test_titles(missing)

    duplicate = tmp_path / "duplicate.md"
    duplicate.write_text(
        "신규 필수 Test:\n\n```text\n" + "same\n" * 32 + "```\n",
        encoding="utf-8",
    )
    with pytest.raises(V02CertificationError, match="32 unique"):
        extract_mandatory_test_titles(duplicate)


def test_slice_0069_final_certification_closes_requirements_and_rd_011(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    architecture, performance, coverage = _release_reports(tmp_path)
    report = certify_v0_2(
        architecture_report_path=architecture,
        performance_report_path=performance,
        coverage_report_path=coverage,
    )
    data = report.to_data()

    assert data["status"] == "CERTIFIED"
    assert data["requirement_count"] == 123
    assert data["status_counts"] == {"IMPLEMENTED": 123}
    assert data["v0_1_requirement_count"] == 325
    assert data["acceptance_evidence_count"] == 63
    assert data["statement_coverage"] == 99.0
    assert data["branch_coverage"] == 98.0
    assert data["coverage_thresholds"] == {"statement": 99.0, "branch": 98.0}
    assert data["performance"]["bounded_loop_iterations"] == 1000

    output = tmp_path / "certification.json"
    assert main(
        [
            "--architecture-report",
            str(architecture),
            "--performance-report",
            str(performance),
            "--coverage-report",
            str(coverage),
            "--output",
            str(output),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out) == json.loads(
        output.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("report_name", "mutator", "message"),
    [
        (
            "architecture",
            lambda data: data.update(status="FAILED", violation_count=1),
            "architecture report",
        ),
        (
            "performance",
            lambda data: data.update(status="FAILED"),
            "performance report",
        ),
        (
            "performance",
            lambda data: data.update(bounded_loop_iterations=999),
            "1,000-loop",
        ),
        (
            "performance",
            lambda data: data.update(python_version="3.11.9"),
            "Python 3.12",
        ),
        (
            "coverage",
            lambda data: data["totals"].update(covered_lines=989),
            "statement",
        ),
        (
            "coverage",
            lambda data: data["totals"].update(covered_branches=979),
            "branch",
        ),
        (
            "coverage",
            lambda data: data["totals"].update(covered_lines=1001),
            "exceeds total",
        ),
    ],
)
def test_slice_0069_final_certification_fails_closed_on_release_report_drift(
    tmp_path: Path, report_name: str, mutator, message: str
) -> None:
    architecture, performance, coverage = _release_reports(tmp_path)
    selected = {
        "architecture": architecture,
        "performance": performance,
        "coverage": coverage,
    }[report_name]
    data = json.loads(selected.read_text(encoding="utf-8"))
    mutator(data)
    _write_json(selected, data)

    with pytest.raises(V02CertificationError, match=message):
        certify_v0_2(
            architecture_report_path=architecture,
            performance_report_path=performance,
            coverage_report_path=coverage,
        )


def test_slice_0069_certification_cli_reports_input_and_output_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    architecture, performance, coverage = _release_reports(tmp_path)

    assert main(["--architecture-report", str(tmp_path / "missing.json")]) == 1
    assert "cannot load architecture report" in capsys.readouterr().err
    assert main(
        [
            "--architecture-report",
            str(architecture),
            "--performance-report",
            str(performance),
            "--coverage-report",
            str(coverage),
            "--output",
            str(tmp_path),
        ]
    ) == 2
    assert "cannot write v0.2 certification report" in capsys.readouterr().err


def test_slice_0069_acceptance_mutation_does_not_change_repository_baseline(
    tmp_path: Path,
) -> None:
    original = _acceptance()
    changed = deepcopy(original)
    changed["mandatory_tests"].pop()
    _write_json(tmp_path / "changed.json", changed)

    assert len(_acceptance()["mandatory_tests"]) == 32
    assert len(original["mandatory_tests"]) == 32
