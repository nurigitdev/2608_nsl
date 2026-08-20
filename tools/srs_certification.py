from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import re
import sys
from typing import Any

from tools.requirements_traceability import (
    DEFAULT_BASELINE,
    DEFAULT_SOURCE,
    ROOT,
    TraceabilityError,
    effective_traceability_records,
    extract_requirements,
    validate_traceability,
)


DEFAULT_NEGATIVE_BASELINE = ROOT / "requirements/nsl_v0_1_negative_acceptance.json"
NEGATIVE_HEADING = re.compile(r"^##\s+(AC-N\d{2})\s+(.+?)\s*$")
EXPECTED_NEGATIVE_IDS = tuple(f"AC-N{number:02d}" for number in range(1, 11))


class CertificationError(ValueError):
    pass


class CertificationStatus(StrEnum):
    CERTIFIED = "CERTIFIED"
    CONDITIONAL = "CONDITIONAL"


@dataclass(frozen=True, slots=True)
class SrsCertificationReport:
    status: CertificationStatus
    baseline_id: str
    requirement_count: int
    requirements_sha256: str
    status_counts: dict[str, int]
    gap_requirement_ids: tuple[str, ...]
    negative_acceptance_count: int

    def to_data(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "baseline_id": self.baseline_id,
            "requirement_count": self.requirement_count,
            "requirements_sha256": self.requirements_sha256,
            "status_counts": self.status_counts,
            "gap_requirement_ids": list(self.gap_requirement_ids),
            "negative_acceptance_count": self.negative_acceptance_count,
        }


def extract_negative_acceptance(source_path: Path) -> dict[str, str]:
    try:
        lines = source_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise CertificationError(f"cannot load SRS: {source_path}") from error
    in_section = False
    cases: dict[str, str] = {}
    for line in lines:
        if line.strip() == "# 40. Mandatory Negative Acceptance Cases":
            in_section = True
            continue
        if in_section and line.startswith("# 41."):
            break
        if not in_section:
            continue
        match = NEGATIVE_HEADING.fullmatch(line.strip())
        if match is None:
            continue
        case_id, title = match.groups()
        if case_id in cases:
            raise CertificationError(f"duplicate negative acceptance case: {case_id}")
        cases[case_id] = title
    if tuple(cases) != EXPECTED_NEGATIVE_IDS:
        raise CertificationError(
            "SRS negative acceptance IDs must be exactly AC-N01 through AC-N10"
        )
    return cases


def validate_negative_acceptance(
    source_path: Path = DEFAULT_SOURCE,
    baseline_path: Path = DEFAULT_NEGATIVE_BASELINE,
    repo_root: Path = ROOT,
) -> dict[str, dict[str, Any]]:
    srs_cases = extract_negative_acceptance(source_path)
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CertificationError(
            f"cannot load negative acceptance baseline: {baseline_path}"
        ) from error
    if baseline.get("schema_version") != 1:
        raise CertificationError("negative acceptance schema_version must be 1")
    if baseline.get("srs_section") != "40":
        raise CertificationError("negative acceptance srs_section must be 40")
    if baseline.get("expected_case_count") != len(EXPECTED_NEGATIVE_IDS):
        raise CertificationError("negative acceptance expected_case_count must be 10")
    raw_cases = baseline.get("cases")
    if not isinstance(raw_cases, list):
        raise CertificationError("negative acceptance cases must be a list")

    cases: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw_cases):
        if not isinstance(item, dict) or set(item) != {"id", "title", "evidence"}:
            raise CertificationError(f"negative acceptance case {index} is malformed")
        case_id = item["id"]
        if case_id in cases:
            raise CertificationError(f"duplicate negative acceptance case: {case_id}")
        if case_id not in srs_cases or item["title"] != srs_cases[case_id]:
            raise CertificationError(f"negative acceptance case differs from SRS: {case_id}")
        evidence = item["evidence"]
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(reference, str) and reference for reference in evidence
        ):
            raise CertificationError(
                f"negative acceptance evidence is invalid: {case_id}"
            )
        for reference in evidence:
            path_text, separator, symbol = reference.partition("::")
            path = repo_root / path_text
            if not path.is_file():
                raise CertificationError(
                    f"negative acceptance evidence path is missing: {reference}"
                )
            if not separator or not symbol:
                raise CertificationError(
                    f"negative acceptance evidence symbol is required: {reference}"
                )
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError) as error:
                raise CertificationError(
                    f"negative acceptance evidence cannot be parsed: {reference}"
                ) from error
            definitions = {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            }
            if symbol not in definitions:
                raise CertificationError(
                    f"negative acceptance evidence symbol is missing: {reference}"
                )
        cases[case_id] = item
    if tuple(cases) != EXPECTED_NEGATIVE_IDS:
        raise CertificationError(
            "negative acceptance baseline must contain AC-N01 through AC-N10 in order"
        )
    return cases


def certify_srs(
    source_path: Path = DEFAULT_SOURCE,
    baseline_path: Path = DEFAULT_BASELINE,
    negative_baseline_path: Path = DEFAULT_NEGATIVE_BASELINE,
    repo_root: Path = ROOT,
) -> SrsCertificationReport:
    try:
        traceability = validate_traceability(source_path, baseline_path, repo_root)
        records = effective_traceability_records(
            source_path, baseline_path, repo_root
        )
    except TraceabilityError as error:
        raise CertificationError(str(error)) from error
    requirements = extract_requirements(source_path)
    negative_cases = validate_negative_acceptance(
        source_path, negative_baseline_path, repo_root
    )
    gaps = tuple(
        requirement_id
        for requirement_id in requirements
        if records[requirement_id]["status"] != "IMPLEMENTED"
    )
    status_counts = dict(
        sorted(Counter(records[item]["status"] for item in requirements).items())
    )
    status = (
        CertificationStatus.CERTIFIED
        if not gaps
        else CertificationStatus.CONDITIONAL
    )
    return SrsCertificationReport(
        status=status,
        baseline_id=traceability.baseline_id,
        requirement_count=traceability.requirement_count,
        requirements_sha256=traceability.requirements_sha256,
        status_counts=status_counts,
        gap_requirement_ids=gaps,
        negative_acceptance_count=len(negative_cases),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Certify the NSL v0.1 SRS baseline")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--negative-baseline", type=Path, default=DEFAULT_NEGATIVE_BASELINE
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = certify_srs(
            args.source,
            args.baseline,
            args.negative_baseline,
        )
    except CertificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    payload = json.dumps(report.to_data(), ensure_ascii=False, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
