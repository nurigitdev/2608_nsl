from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.requirements_traceability import (
    V0_2_BASELINE,
    V0_2_SOURCE,
    effective_traceability_records,
    validate_traceability,
)
from tools.v0_2_readiness import (
    DEFAULT_BASELINE,
    ROOT,
    ReadinessError,
    main,
    validate_v0_2_readiness,
)


def _baseline() -> dict:
    return json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))


def _write_baseline(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "readiness.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _assert_invalid(tmp_path: Path, value: object, message: str) -> None:
    with pytest.raises(ReadinessError, match=message):
        validate_v0_2_readiness(_write_baseline(tmp_path, value))


def test_repository_v0_2_requirement_baseline_is_complete() -> None:
    report = validate_traceability(V0_2_SOURCE, V0_2_BASELINE)
    records = effective_traceability_records(V0_2_SOURCE, V0_2_BASELINE)

    assert report.baseline_id == "NSL-V0.2-SRS-RB1"
    assert report.requirement_count == 123
    assert report.priority_counts == {"MUST": 121, "SHOULD": 2}
    assert report.status_counts == {"PLANNED": 123}
    assert report.target_slice_counts == {
        "0033": 16,
        "0034": 17,
        "0035": 18,
        "0036": 14,
        "0037": 21,
        "0038": 12,
        "0039": 14,
        "0040": 11,
    }
    assert tuple(records)[0] == "NSL-ARC-201"
    assert tuple(records)[-1] == "NSL-RPL-203"
    assert all(record["status"] == "PLANNED" for record in records.values())


def test_repository_v0_2_readiness_baseline_is_complete(capsys) -> None:
    report = validate_v0_2_readiness()

    assert report.readiness_id == "NSL-V0.2-IMPLEMENTATION-READINESS-RB1"
    assert report.authority_document_count == 5
    assert report.source_document_count == 14
    assert report.change_count == 32
    assert report.change_priority_counts == {"P0": 22, "P1": 10}
    assert report.decision_count == 11
    assert report.to_data()["change_count"] == 32

    assert main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision_count"] == 11
    assert main([]) == 0
    assert "32 changes, 11 decisions" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("{", "cannot load readiness baseline"),
        ([], "readiness baseline must contain a JSON object"),
    ],
)
def test_readiness_rejects_malformed_root(tmp_path, value, message) -> None:
    path = tmp_path / "malformed.json"
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ReadinessError, match=message):
        validate_v0_2_readiness(path)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda data: data.update(schema_version=2), "schema_version must be 1"),
        (lambda data: data.update(readiness_id=""), "readiness_id must be"),
        (
            lambda data: data.update(traceability_baseline="../outside.json"),
            "must stay inside the repository",
        ),
        (
            lambda data: data.update(traceability_baseline="missing.json"),
            "traceability_baseline does not exist",
        ),
        (
            lambda data: data.update(traceability_baseline_id="WRONG"),
            "traceability baseline id mismatch",
        ),
        (
            lambda data: data.update(traceability_baseline_id=""),
            "traceability_baseline_id must be",
        ),
    ],
)
def test_readiness_rejects_invalid_root_contract(
    tmp_path, mutator, message
) -> None:
    data = _baseline()
    mutator(data)
    _assert_invalid(tmp_path, data, message)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda data: data.update(expected_authority_document_count=4),
            "expected_authority_document_count=4, actual=5",
        ),
        (
            lambda data: data.update(expected_authority_document_count=-1),
            "must be a non-negative integer",
        ),
        (
            lambda data: data.update(authority_documents={}),
            "authority_documents must be a list",
        ),
        (
            lambda data: data["authority_documents"].append("invalid"),
            "authority_documents\\[5\\] must be an object",
        ),
        (
            lambda data: data["authority_documents"][1].update(
                path=data["authority_documents"][0]["path"]
            ),
            "duplicate authority document",
        ),
        (
            lambda data: data["authority_documents"][0].update(path="missing.docx"),
            "authority_documents\\[0\\]\\.path does not exist",
        ),
        (
            lambda data: data["authority_documents"][0].update(sha256="ABC"),
            "sha256 must be lowercase SHA-256",
        ),
        (
            lambda data: data["authority_documents"][0].update(sha256="0" * 64),
            "sha256 mismatch",
        ),
    ],
)
def test_readiness_rejects_authority_boundary_and_tamper(
    tmp_path, mutator, message
) -> None:
    data = _baseline()
    mutator(data)
    _assert_invalid(tmp_path, data, message)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda data: data.update(source_documents=[]),
            "source_documents must be an object",
        ),
        (
            lambda data: data["source_documents"].pop("12"),
            "missing source document keys: 12",
        ),
        (
            lambda data: data["source_documents"].update(
                extra=data["source_documents"]["12"]
            ),
            "unknown source document keys: extra",
        ),
        (
            lambda data: data["source_documents"].update(
                **{"12": data["source_documents"]["11"]}
            ),
            "duplicate source document path",
        ),
        (
            lambda data: data["source_documents"].update(**{"12": "missing.md"}),
            "source_documents.12 does not exist",
        ),
    ],
)
def test_readiness_rejects_source_document_set(tmp_path, mutator, message) -> None:
    data = _baseline()
    mutator(data)
    _assert_invalid(tmp_path, data, message)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda data: data.update(change_ledger={}), "change_ledger must be a list"),
        (
            lambda data: data["change_ledger"].pop(),
            "expected_change_count=32, actual=31",
        ),
        (
            lambda data: data["change_ledger"].append(
                {
                    "change_id": "CHG-033",
                    "priority": "P1",
                    "title": "Unexpected",
                    "affected_documents": ["12"],
                }
            ),
            "expected_change_count=32, actual=33",
        ),
        (
            lambda data: data["change_ledger"][1].update(change_id="CHG-001"),
            "duplicate change id: CHG-001",
        ),
        (
            lambda data: data["change_ledger"][0].update(change_id="CHG-X"),
            "change_id is invalid",
        ),
        (
            lambda data: data["change_ledger"][0].update(priority="P2"),
            "priority must be P0 or P1",
        ),
        (
            lambda data: data["change_ledger"][0].update(title=""),
            "title must be non-empty",
        ),
        (
            lambda data: data["change_ledger"][0].update(affected_documents=[]),
            "affected_documents must be non-empty",
        ),
        (
            lambda data: data["change_ledger"][0].update(
                affected_documents=["00-master", "00-master"]
            ),
            "affected_documents contains duplicates",
        ),
        (
            lambda data: data["change_ledger"][0].update(
                affected_documents=["unknown"]
            ),
            "references unknown document key",
        ),
        (
            lambda data: data["change_ledger"][0].update(
                affected_documents=["06"]
            ),
            "CHG-001 is missing from affected document: 06",
        ),
        (
            lambda data: data["change_ledger"].append("invalid"),
            "change_ledger\\[32\\] must be an object",
        ),
    ],
)
def test_readiness_rejects_change_ledger_boundary_and_robustness(
    tmp_path, mutator, message
) -> None:
    data = _baseline()
    mutator(data)
    _assert_invalid(tmp_path, data, message)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda data: data.update(readiness_decisions={}),
            "readiness_decisions must be a list",
        ),
        (
            lambda data: data["readiness_decisions"].pop(),
            "expected_decision_count=11, actual=10",
        ),
        (
            lambda data: data["readiness_decisions"].append(
                {
                    "decision_id": "RD-012",
                    "status": "FROZEN",
                    "target_slice": "0039",
                    "evidence": data["readiness_decisions"][0]["evidence"],
                }
            ),
            "expected_decision_count=11, actual=12",
        ),
        (
            lambda data: data["readiness_decisions"][1].update(
                decision_id="RD-001"
            ),
            "duplicate readiness decision: RD-001",
        ),
        (
            lambda data: data["readiness_decisions"][0].update(decision_id="BAD"),
            "decision_id is invalid",
        ),
        (
            lambda data: data["readiness_decisions"][0].update(status="OPEN"),
            "status must be FROZEN",
        ),
        (
            lambda data: data["readiness_decisions"][0].update(
                target_slice="9999"
            ),
            "target_slice is not declared",
        ),
        (
            lambda data: data["readiness_decisions"][0].update(evidence="missing"),
            "evidence must be path::symbol",
        ),
        (
            lambda data: data["readiness_decisions"][0].update(
                evidence="docs/v0_2/13_NeX_Skill_Language_v0.2_Requirement_Baseline_and_Implementation_Readiness_v1.0.md::"
            ),
            "evidence symbol must be non-empty",
        ),
        (
            lambda data: data["readiness_decisions"][0].update(
                evidence="missing.md::RD-001"
            ),
            "RD-001.evidence does not exist",
        ),
        (
            lambda data: data["readiness_decisions"][0].update(
                evidence="docs/v0_2/13_NeX_Skill_Language_v0.2_Requirement_Baseline_and_Implementation_Readiness_v1.0.md::RD-999"
            ),
            "evidence symbol not found",
        ),
        (
            lambda data: data["readiness_decisions"].append("invalid"),
            "readiness_decisions\\[11\\] must be an object",
        ),
    ],
)
def test_readiness_rejects_decision_boundary_and_evidence(
    tmp_path, mutator, message
) -> None:
    data = _baseline()
    mutator(data)
    _assert_invalid(tmp_path, data, message)


def test_readiness_rejects_malformed_traceability_and_cli_reports_failure(
    tmp_path, capsys
) -> None:
    malformed = tmp_path / "traceability.json"
    malformed.write_text("{", encoding="utf-8")
    data = _baseline()
    data["traceability_baseline"] = malformed.relative_to(ROOT).as_posix()
    baseline = _write_baseline(tmp_path, data)

    with pytest.raises(ReadinessError, match="cannot load traceability baseline"):
        validate_v0_2_readiness(baseline)
    assert main(["--baseline", str(baseline)]) == 1
    assert "cannot load traceability baseline" in capsys.readouterr().out


def test_readiness_mutation_helpers_do_not_modify_repository_baseline(tmp_path) -> None:
    original = _baseline()
    changed = deepcopy(original)
    changed["change_ledger"].pop()
    _write_baseline(tmp_path, changed)

    assert len(_baseline()["change_ledger"]) == 32
    assert len(original["change_ledger"]) == 32
