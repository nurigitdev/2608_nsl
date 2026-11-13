"""Export the first compatibility corpus using the locked Python source tree."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from shutil import copytree
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "rust-transition/baseline/python-oracle-lock.json"
BUDGET_SOURCE = "examples/project_budget_check.ns"
BUDGET_FIXTURES = "examples/project_budget_check_cli"
PROJECT_OPERATIONS = "examples/project_operations"


def json_bytes(value: object) -> bytes:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    return (text + "\n").encode("utf-8")


class Corpus:
    def __init__(self, destination: Path) -> None:
        self.destination = destination
        self.entries: list[dict[str, str]] = []

    def add(
        self,
        category: str,
        case_id: str,
        *,
        contract_refs: list[str],
        source_refs: list[str],
        inputs: dict[str, tuple[str, bytes]] | None = None,
        artifacts: dict[str, tuple[str, bytes]] | None = None,
        status: str | None = None,
        error: dict[str, str] | None = None,
        hashes: dict[str, str] | None = None,
        classification: str = "INTERNAL",
        notes: str = "",
    ) -> None:
        directory = self.destination / category / case_id
        directory.mkdir(parents=True)

        def write_files(files: dict[str, tuple[str, bytes]]) -> dict[str, dict[str, str]]:
            references = {}
            for role, (filename, content) in files.items():
                with (directory / filename).open("xb") as file:
                    file.write(content)
                references[role] = {"path": filename, "sha256": sha256(content).hexdigest()}
            return references

        descriptor = directory / "case.json"
        descriptor.write_bytes(json_bytes({
            "case_id": case_id,
            "category": category,
            "contract_refs": contract_refs,
            "source_refs": source_refs,
            "inputs": write_files(inputs or {}),
            "expected_artifacts": write_files(artifacts or {}),
            "expected_status": status,
            "expected_error": error,
            "expected_hashes": hashes or {},
            "classification": classification,
            "notes": notes,
        }))
        self.entries.append({
            "case_id": case_id,
            "category": category,
            "path": descriptor.relative_to(self.destination).as_posix(),
            "sha256": sha256(descriptor.read_bytes()).hexdigest(),
        })


def export_cases(oracle: Path, destination: Path) -> None:
    # This worker runs in an isolated interpreter with the archived source first.
    sys.path.insert(0, str(oracle))
    from nsl import core
    from nsl._version import __version__
    from nsl.cli_config import (
        load_mock_handlers_document,
        load_principal,
        load_tool_catalog,
        load_value_object,
    )
    from nsl.cli_runtime import execute_skill
    from nsl.compiler import CompileError, NslCompiler
    from nsl.ir import NsoCodec, canonical_json

    lock = json.loads(LOCK.read_bytes())
    corpus = Corpus(destination)
    budget_source = (oracle / BUDGET_SOURCE).read_bytes()
    budget_tools = f"{BUDGET_FIXTURES}/tools.json"
    write_source = f"{PROJECT_OPERATIONS}/project_project_deactivate.ns"
    write_tools = f"{PROJECT_OPERATIONS}/tools_write.json"

    def input_file(path: str) -> tuple[str, bytes]:
        file = oracle / path
        return file.name, file.read_bytes()

    compilations = {}
    for version, source, tools in (("1.0", BUDGET_SOURCE, budget_tools), ("1.1", write_source, write_tools)):
        catalog = load_tool_catalog(oracle / tools)
        compiled = NslCompiler(catalog).compile((oracle / source).read_text(encoding="utf-8"))
        if compiled.skill.ir_version != version:
            raise ValueError(f"expected IR {version} from {source}")
        if NsoCodec.encode(NsoCodec.decode(compiled.nso_bytes)) != compiled.nso_bytes:
            raise ValueError(f"noncanonical NSO from {source}")
        compilations[version] = compiled
        corpus.add(
            "nso", f"ir-{version.replace('.', '-')}-canonical",
            contract_refs=[
                "NSL-RUST-IR-006", "NSL-RUST-IR-009", "NSL-RUST-IR-010",
                "NSL-RUST-IR-011", "NSL-RUST-IR-012",
            ],
            source_refs=[source, tools, "tests/test_v0_2_ir_codec.py"],
            inputs={"source": input_file(source), "tool_catalog": input_file(tools)},
            artifacts={"nso": ("expected.nso", compiled.nso_bytes)},
            hashes={
                "semantic_hash": compiled.semantic_hash.removeprefix("sha256:"),
                "artifact_hash": sha256(compiled.nso_bytes).hexdigest(),
                "source_bundle_hash": compiled.source_bundle_hash.removeprefix("sha256:"),
            },
            notes="Compile as memory/root.ns, the Oracle compiler's logical path for a string input.",
        )

    strict = compilations["1.1"].skill
    versions = {
        "package_version": __version__, "language_version": strict.language_version,
        "ir_version": strict.ir_version, "profile": strict.semantics_profile,
    }
    if versions != {key: lock[key] for key in versions}:
        raise ValueError("Oracle runtime, language, IR or profile version differs from the lock")
    versions["compatibility_ir_version"] = compilations["1.0"].skill.ir_version
    corpus.add(
        "source", "version-identity", contract_refs=["NSL-RUST-GOAL-002", "NSL-RUST-COMPAT-002"],
        source_refs=["nsl/_version.py", "nsl/compiler.py", BUDGET_SOURCE, write_source],
        artifacts={"versions": ("versions.json", json_bytes(versions))},
    )

    enums = (
        core.CheckStatus, core.CommitState, core.VerificationStatus, core.ApprovalStatus,
        core.RiskLevel, core.DataClassification, core.Presence, core.Completeness,
        core.ExecutionStatus,
    )
    enum_values = {kind.__name__: sorted(item.value for item in kind) for kind in enums}
    corpus.add(
        "values", "core-status-enums",
        contract_refs=[f"NSL-RUST-CORE-00{number}" for number in range(1, 5)],
        source_refs=["nsl/core.py"],
        artifacts={"enum_values": ("enum-values.json", json_bytes(enum_values))},
    )

    encoded_values = [
        {"$type": "Decimal", "value": "1E-3"},
        {"$type": "Decimal", "value": "-0.00"},
        {"$type": "Money", "amount": "0.10", "currency": "KRW"},
        {"$type": "Money", "amount": "0.20", "currency": "KRW"},
    ]
    values = [core.decode_value(value) for value in encoded_values]
    corpus.add(
        "values", "decimal-money-exact",
        contract_refs=["NSL-RUST-MONEY-003", "NSL-RUST-MONEY-007", "NSL-RUST-MONEY-008"],
        source_refs=["nsl/core.py", "tests/test_money_semantics.py"],
        inputs={"values": ("values.json", json_bytes(encoded_values))},
        artifacts={"values": ("expected.json", json_bytes({
            "round_trip": core.encode_value(values),
            "decimal_sum": core.encode_value(values[0] + values[1]),
            "money_sum": core.encode_value(values[2] + values[3]),
        }))},
        notes="Round trip all values, then add the first pair as Decimals and the second pair as Money.",
    )

    catalog = load_tool_catalog(oracle / budget_tools)
    contract = catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    corpus.add(
        "contracts", "read-tool-contract",
        contract_refs=["NSL-RUST-TOOL-001", "NSL-RUST-TOOL-003", "NSL-RUST-HASH-004"],
        source_refs=[budget_tools, "nsl/tools.py", "tests/test_tool_contracts.py"],
        inputs={"tool_catalog": input_file(budget_tools)},
        artifacts={"canonical_contract": ("contract.json", canonical_json(contract.to_canonical_data()))},
        hashes={"tool_contract_hash": contract.contract_hash.removeprefix("sha256:")},
        notes="Select PROJECT.LIST_PARENT_PROJECTS version 1.0.0 from the catalog.",
    )

    invalid_source = budget_source.replace(
        b"read PROJECT.LIST_CHILD_PROJECTS(", b"read PROJECT.UNKNOWN_TOOL(", 1,
    )
    try:
        NslCompiler(catalog).compile(invalid_source.decode("utf-8"))
    except CompileError as exc:
        diagnostic = asdict(exc.diagnostic)
        error = {"code": exc.code, "phase": exc.diagnostic.phase.value}
    else:
        raise ValueError("Oracle accepted an undeclared tool")
    corpus.add(
        "diagnostics", "undeclared-tool",
        contract_refs=["NSL-RUST-DIFF-001", "NSL-RUST-DIFF-002"],
        source_refs=[BUDGET_SOURCE, "tests/test_vertical_slice.py::CompilerTests.test_compile_rejects_undeclared_tool"],
        inputs={"source": ("invalid.ns", invalid_source), "tool_catalog": input_file(budget_tools)},
        artifacts={"diagnostic": ("diagnostic.json", json_bytes(diagnostic))},
        error=error,
        notes="Compile as memory/root.ns and compare the structured, user-safe diagnostic.",
    )

    input_path = f"{BUDGET_FIXTURES}/input.json"
    context_path = f"{BUDGET_FIXTURES}/context.json"
    principal_path = f"{BUDGET_FIXTURES}/principal.json"
    for check, fixture_name in (("PASS", "success"), ("FAIL", "over_budget"), ("UNKNOWN", "success")):
        fixture_path = f"{BUDGET_FIXTURES}/fixtures/{fixture_name}.json"
        fixture = json.loads((oracle / fixture_path).read_bytes())
        if check == "UNKNOWN":
            child_tool = next(
                tool for tool in fixture["tools"] if tool["tool_id"] == "PROJECT.LIST_CHILD_PROJECTS"
            )
            child_tool["cases"][0]["completeness"] = "PARTIAL"
        execution_id = f"budget-check-{check.lower()}"
        result = asyncio.run(execute_skill(
            NsoCodec.decode(compilations["1.0"].nso_bytes), catalog,
            load_principal(oracle / principal_path), execution_id,
            inputs=load_value_object(oracle / input_path, "input"),
            runtime_context=load_value_object(oracle / context_path, "context"),
            handlers=load_mock_handlers_document(fixture, catalog),
        )).result
        if [item.status.value for item in result.checks] != [check]:
            raise ValueError(f"Oracle did not produce CHECK {check}: {result.semantic_view()}")
        corpus.add(
            "runtime", execution_id,
            contract_refs=["NSL-RUST-RUN-004", "NSL-RUST-RUN-006", "NSL-RUST-RUN-009", "NSL-RUST-RUN-016"],
            source_refs=[BUDGET_SOURCE, fixture_path, "tests/test_vertical_slice.py", "tests/test_cli_local_execution.py"],
            inputs={
                "nso": ("program.nso", compilations["1.0"].nso_bytes),
                "tool_catalog": input_file(budget_tools), "input": input_file(input_path),
                "context": input_file(context_path), "principal": input_file(principal_path),
                "mock_tool_results": ("fixture.json", json_bytes(fixture)),
            },
            artifacts={"runtime_result": ("result.json", json_bytes(result.semantic_view()))},
            status=result.status.value,
            classification="CONFIDENTIAL",
            notes="Synthetic project budget data. Compare the runtime semantic view including checks, outputs and resource use.",
        )

    sys.path.append(str(Path(__file__).resolve().parent))
    from controlled_write_cases import add_controlled_write_cases

    add_controlled_write_cases(corpus, oracle, json_bytes)
    (destination / "manifest.json").write_bytes(json_bytes({
        "schema_version": "1.0", "corpus_version": "1.1.0", "oracle_commit": lock["commit"],
        "cases": sorted(corpus.entries, key=lambda entry: entry["case_id"]),
    }))


def export(destination: Path) -> None:
    lock = json.loads(LOCK.read_bytes())
    commit = lock["commit"]
    tree = subprocess.check_output(
        ["git", "rev-parse", f"{commit}^{{tree}}"], cwd=ROOT, text=True,
    ).strip()
    if tree != lock["tree"]:
        raise ValueError("Oracle Git tree differs from the lock")
    with TemporaryDirectory(prefix="nsl-oracle-export-") as temporary:
        workspace = Path(temporary)
        source = workspace / "source"
        generated = workspace / "corpus"
        archive = subprocess.check_output(
            ["git", "archive", commit, "nsl", "examples", "tests"], cwd=ROOT,
        )
        with tarfile.open(fileobj=BytesIO(archive)) as files:
            files.extractall(source, filter="data")
        subprocess.run(
            [sys.executable, "-I", "-B", __file__, "--worker", str(source), str(generated)],
            check=True,
        )
        copytree(generated, destination)


def main() -> None:
    if sys.argv[1:2] == ["--worker"]:
        export_cases(Path(sys.argv[2]), Path(sys.argv[3]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new directory for the generated corpus")
    destination = parser.parse_args().destination
    if destination.exists() or destination.is_symlink():
        parser.error(f"destination already exists: {destination}")
    export(destination)


if __name__ == "__main__":
    main()
