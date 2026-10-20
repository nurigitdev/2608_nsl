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
    assert report.status_counts == {
        "IMPLEMENTED": 82,
        "PARTIAL": 1,
        "PLANNED": 40,
    }
    assert report.target_slice_counts == {
        "0033": 1,
        "0034": 7,
        "0035": 2,
        "0036": 4,
        "0037": 2,
        "0038": 3,
        "0039": 4,
        "0040": 7,
        "0041": 8,
        "0042": 2,
        "0043": 8,
        "0044": 3,
        "0045": 8,
        "0046": 6,
        "0047": 3,
        "0048": 8,
        "0049": 5,
        "0050": 2,
        "0051": 8,
        "0052": 4,
        "0053": 3,
        "0054": 6,
        "0055": 8,
        "0056": 5,
        "0058": 4,
        "0062": 2,
    }
    assert tuple(records)[0] == "NSL-ARC-201"
    assert tuple(records)[-1] == "NSL-RPL-203"
    assert records["NSL-ARC-201"]["status"] == "PARTIAL"
    assert records["NSL-TOL-201"]["status"] == "IMPLEMENTED"
    assert records["NSL-TOL-207"]["status"] == "IMPLEMENTED"
    assert records["NSL-RSK-004"]["status"] == "IMPLEMENTED"
    assert records["NSL-IR-202"]["status"] == "IMPLEMENTED"
    assert records["NSL-IR-205"]["status"] == "IMPLEMENTED"
    assert records["NSL-IR-201"]["status"] == "IMPLEMENTED"
    assert records["NSL-IR-208"]["status"] == "IMPLEMENTED"
    assert records["NSL-LEX-201"]["status"] == "IMPLEMENTED"
    assert records["NSL-LEX-203"]["status"] == "IMPLEMENTED"
    assert records["NSL-PAR-201"]["status"] == "IMPLEMENTED"
    assert records["NSL-PAR-204"]["status"] == "IMPLEMENTED"
    assert records["NSL-SEM-201"]["status"] == "IMPLEMENTED"
    assert records["NSL-SEM-206"]["status"] == "IMPLEMENTED"
    assert records["NSL-APR-001"]["status"] == "IMPLEMENTED"
    assert records["NSL-TYP-201"]["status"] == "IMPLEMENTED"
    assert records["NSL-TYP-204"]["status"] == "IMPLEMENTED"
    assert records["NSL-WRT-002"]["status"] == "IMPLEMENTED"
    assert records["NSL-WRT-004"]["status"] == "IMPLEMENTED"
    assert records["NSL-RSK-002"]["status"] == "IMPLEMENTED"
    assert records["NSL-IR-206"]["status"] == "IMPLEMENTED"
    assert records["NSL-IR-207"]["status"] == "IMPLEMENTED"
    assert records["NSL-ARC-211"]["status"] == "IMPLEMENTED"
    assert records["NSL-CTX-201"]["status"] == "IMPLEMENTED"
    assert records["NSL-CTX-206"]["status"] == "IMPLEMENTED"
    assert records["NSL-CTX-207"]["status"] == "IMPLEMENTED"
    assert records["NSL-CTX-209"]["status"] == "IMPLEMENTED"
    assert records["NSL-CTX-210"]["status"] == "IMPLEMENTED"
    assert records["NSL-STS-201"]["status"] == "IMPLEMENTED"
    assert records["NSL-STS-205"]["status"] == "IMPLEMENTED"
    assert records["NSL-RSL-201"]["status"] == "IMPLEMENTED"
    assert records["NSL-RSL-203"]["status"] == "IMPLEMENTED"
    assert records["NSL-WEL-001"]["status"] == "IMPLEMENTED"
    assert records["NSL-WEL-005"]["status"] == "IMPLEMENTED"
    assert records["NSL-WRT-005"]["status"] == "IMPLEMENTED"
    assert records["NSL-WRT-006"]["status"] == "IMPLEMENTED"
    assert records["NSL-WRT-010"]["status"] == "IMPLEMENTED"
    assert records["NSL-APR-002"]["status"] == "IMPLEMENTED"
    assert records["NSL-APR-003"]["status"] == "IMPLEMENTED"
    assert records["NSL-APR-014"]["status"] == "IMPLEMENTED"
    assert records["NSL-ARC-206"]["status"] == "IMPLEMENTED"
    assert records["NSL-ARC-208"]["status"] == "IMPLEMENTED"
    assert records["NSL-ARC-209"]["status"] == "IMPLEMENTED"
    assert records["NSL-APR-004"]["status"] == "IMPLEMENTED"
    assert records["NSL-APR-008"]["status"] == "IMPLEMENTED"
    assert records["NSL-APR-009"]["status"] == "IMPLEMENTED"
    assert records["NSL-APR-013"]["status"] == "IMPLEMENTED"
    assert all(
        record["status"] == "PLANNED"
        for requirement_id, record in records.items()
        if requirement_id
        not in {
            "NSL-ARC-201",
            "NSL-TOL-201",
            "NSL-TOL-202",
            "NSL-TOL-203",
            "NSL-TOL-204",
            "NSL-TOL-205",
            "NSL-TOL-206",
            "NSL-TOL-207",
            "NSL-RSK-001",
            "NSL-RSK-004",
            "NSL-IR-202",
            "NSL-IR-203",
            "NSL-IR-204",
            "NSL-IR-205",
            "NSL-IR-201",
            "NSL-IR-208",
            "NSL-LEX-201",
            "NSL-LEX-202",
            "NSL-LEX-203",
            "NSL-PAR-201",
            "NSL-PAR-202",
            "NSL-PAR-203",
            "NSL-PAR-204",
            "NSL-SEM-201",
            "NSL-SEM-202",
            "NSL-SEM-203",
            "NSL-SEM-204",
            "NSL-SEM-205",
            "NSL-SEM-206",
            "NSL-APR-001",
            "NSL-TYP-201",
            "NSL-TYP-202",
            "NSL-TYP-203",
            "NSL-TYP-204",
            "NSL-WRT-002",
            "NSL-WRT-003",
            "NSL-WRT-004",
            "NSL-RSK-002",
            "NSL-IR-206",
            "NSL-IR-207",
            "NSL-ARC-211",
            "NSL-CTX-201",
            "NSL-CTX-202",
            "NSL-CTX-203",
            "NSL-CTX-204",
            "NSL-CTX-205",
            "NSL-CTX-206",
            "NSL-CTX-207",
            "NSL-CTX-208",
            "NSL-CTX-209",
            "NSL-CTX-210",
            "NSL-STS-201",
            "NSL-STS-202",
            "NSL-STS-203",
            "NSL-STS-204",
            "NSL-STS-205",
            "NSL-RSL-201",
            "NSL-RSL-202",
            "NSL-RSL-203",
            "NSL-WEL-001",
            "NSL-WEL-002",
            "NSL-WEL-003",
            "NSL-WEL-004",
            "NSL-WEL-005",
            "NSL-WRT-005",
            "NSL-WRT-006",
            "NSL-WRT-010",
            "NSL-APR-002",
            "NSL-APR-003",
            "NSL-APR-014",
            "NSL-ARC-206",
            "NSL-ARC-208",
            "NSL-ARC-209",
            "NSL-APR-004",
            "NSL-APR-005",
            "NSL-APR-006",
            "NSL-APR-007",
            "NSL-APR-008",
            "NSL-APR-009",
            "NSL-APR-010",
            "NSL-APR-011",
            "NSL-APR-012",
            "NSL-APR-013",
        }
    )


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
