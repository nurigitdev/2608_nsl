from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nsl.audit import InMemoryAuditSink, InMemorySnapshotStore
from nsl.compiler import NslCompiler
from nsl.core import Completeness, ExecutionStatus
from nsl.ir import NsoCodec
from nsl.replay import RecordingToolExecutor, create_replay_bundle, replay_and_compare
from nsl.runtime import RuntimeEngine
from nsl.runtime_models import ExecutionRequest, ExecutionResult, ResourceUsage
from nsl.security import DataHandlingPolicy
from nsl.source import SourceFile
from nsl.tools import ToolContractCatalog
from nsl.vertical_slice import build_mock_executor, build_principal, build_tool_catalog
from tools.architecture_certification import (
    DEFAULT_NSL_ROOT,
    certify_architecture,
    main,
)


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SOURCE = (ROOT / "examples/project_budget_check.ns").read_text(
    encoding="utf-8"
)


def _synthetic_architecture(tmp_path: Path, runtime_addition: str = "") -> Path:
    package = tmp_path / "nsl"
    package.mkdir(parents=True)
    (package / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "ir.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "compiler.py").write_text(
        "from .core import VALUE\nfrom .ir import VALUE as IR_VALUE\n",
        encoding="utf-8",
    )
    (package / "runtime.py").write_text(
        "from .core import VALUE\n"
        "from .ir import VALUE as IR_VALUE\n"
        "from .tools import ToolExecutionPort, execute_with_timeout\n"
        "\n"
        "async def dispatch(tools):\n"
        "    return await execute_with_timeout(tools)\n"
        f"{runtime_addition}",
        encoding="utf-8",
    )
    (package / "tools.py").write_text(
        "class ToolExecutionPort: pass\n"
        "async def execute_with_timeout(tools): return tools\n",
        encoding="utf-8",
    )
    return package


def test_slice_0068_repository_architecture_is_certified_and_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = certify_architecture()

    assert report.passed
    assert report.checked_file_count == len(tuple(DEFAULT_NSL_ROOT.rglob("*.py")))
    assert report.violations == ()
    assert report.to_data()["status"] == "CERTIFIED"

    output = tmp_path / "architecture.json"
    assert main(["--output", str(output)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(output.read_text(encoding="utf-8"))
    assert printed["violation_count"] == 0


@pytest.mark.parametrize(
    ("addition", "rule_id"),
    [
        ("\nfrom .syntax import AstSkill\n", "NSL-ARC-202"),
        ("\nfrom nsl.compiler import NslCompiler\n", "NSL-ARC-202"),
        ("\nimport openai\n", "NSL-ARC-203"),
        ("\nimport pathlib\n", "ARCH-DIRECT-INFRASTRUCTURE"),
        ("\nimport requests\n", "ARCH-DIRECT-INFRASTRUCTURE"),
        ("\nimport sqlite3\n", "ARCH-DIRECT-INFRASTRUCTURE"),
        ("\nimport boto3\n", "ARCH-PROVIDER-BOUNDARY"),
        ("\nimport celery\n", "ARCH-WORKFLOW-BOUNDARY"),
        ("\nimport fastapi\n", "ARCH-HOST-FRAMEWORK"),
        ("\neval('1')\nexec('pass')\n", "ARCH-DYNAMIC-EXECUTION"),
    ],
)
def test_slice_0068_forbidden_dependency_paths_fail_closed(
    tmp_path: Path, addition: str, rule_id: str
) -> None:
    report = certify_architecture(_synthetic_architecture(tmp_path, addition))

    assert not report.passed
    assert rule_id in {item.rule_id for item in report.violations}
    assert report.to_data()["status"] == "FAILED"


def test_slice_0068_shared_model_and_tool_port_adoption_are_mandatory(
    tmp_path: Path,
) -> None:
    package = _synthetic_architecture(tmp_path)
    (package / "compiler.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "runtime.py").write_text(
        "from .core import VALUE\nfrom .ir import VALUE as IR_VALUE\n",
        encoding="utf-8",
    )

    report = certify_architecture(package)
    rule_ids = [item.rule_id for item in report.violations]

    assert rule_ids.count("NSL-ARC-201") == 1
    assert rule_ids.count("ARCH-TOOL-PORT") == 2


def test_slice_0068_gate_rejects_empty_missing_and_malformed_packages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = certify_architecture(tmp_path / "missing")
    empty = tmp_path / "empty"
    empty.mkdir()

    assert missing.violations[0].rule_id == "ARCH-GATE-ROOT"
    assert certify_architecture(empty).violations[0].rule_id == "ARCH-GATE-ROOT"

    package = _synthetic_architecture(tmp_path / "malformed")
    (package / "runtime.py").write_text("def broken(:\n", encoding="utf-8")
    malformed = certify_architecture(package)
    assert "ARCH-GATE-PARSE" in {item.rule_id for item in malformed.violations}

    assert main(["--nsl-root", str(package)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "FAILED"
    assert main(["--output", str(tmp_path)]) == 2
    assert "cannot write architecture report" in capsys.readouterr().err


def _write_less_source(version: str) -> SourceFile:
    return SourceFile.from_text(
        f"compatibility/{version}.ns",
        f'''language NSL "{version}";
skill TEST.COMPATIBILITY {{
    version "1.0.0";
    risk READ_ONLY;
    limits {{
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }}
    output {{
        result: String;
    }}
}}
''',
    )


def test_slice_0068_v01_source_ir_context_result_and_replay_remain_compatible() -> None:
    catalog = build_tool_catalog()
    compiler = NslCompiler(catalog)
    first = compiler.compile(LEGACY_SOURCE)
    second = compiler.compile(LEGACY_SOURCE)
    loaded = NsoCodec.decode(first.nso_bytes)
    principal = build_principal()
    policy = DataHandlingPolicy(snapshot_retention_days=7, audit_retention_days=30)
    request = ExecutionRequest(
        "compatibility-original",
        {"year": 2026},
        {"user": {"team_id": "TEAM-FINANCE"}},
        principal,
        policy,
    )
    snapshots = InMemorySnapshotStore()
    runtime = RuntimeEngine(catalog)
    recorder = RecordingToolExecutor(
        build_mock_executor(catalog), snapshots, policy
    )
    original = asyncio.run(
        runtime.execute(
            loaded,
            request,
            recorder,
            InMemoryAuditSink(),
            snapshots,
        )
    )
    bundle = create_replay_bundle(loaded, request, original, recorder, snapshots)
    replayed = asyncio.run(
        replay_and_compare(
            bundle,
            loaded,
            replay_execution_id="compatibility-replay",
            principal=principal,
            policy=policy,
            snapshots=snapshots,
            runtime=runtime,
            audit_sink=InMemoryAuditSink(),
        )
    )

    assert first.nso_bytes == second.nso_bytes
    assert loaded == first.skill
    assert (loaded.language_version, loaded.ir_version) == ("0.1", "1.0")
    assert request.principal is principal
    assert request.data_policy is policy
    assert original.status is ExecutionStatus.COMPLETED
    assert original.to_data()["schema_version"] == "1.0"
    assert replayed.matches
    assert replayed.differences == ()
    assert replayed.execution.result.semantic_view() == original.semantic_view()

    validation_error = ExecutionResult(
        execution_id="compatibility-validation-error",
        skill_id="TEST.COMPATIBILITY",
        skill_version="1.0.0",
        semantic_hash=loaded.semantic_hash,
        status=ExecutionStatus.VALIDATION_ERROR,
        completeness=Completeness.COMPLETE,
        checks=(),
        outputs=(),
        resources=ResourceUsage(0, 0, 0, 0),
    )
    assert validation_error.to_data()["status"] == "VALIDATION_ERROR"
    assert validation_error.to_data()["schema_version"] == "1.0"


def test_slice_0068_v02_write_less_source_maps_deterministically_to_ir_1_1() -> None:
    compiler = NslCompiler(ToolContractCatalog(()))
    first = compiler.compile(_write_less_source("0.2"))
    second = compiler.compile(_write_less_source("0.2"))

    assert first.nso_bytes == second.nso_bytes
    assert first.semantic_hash == second.semantic_hash
    assert (first.skill.language_version, first.skill.ir_version) == ("0.2", "1.1")
    assert NsoCodec.decode(first.nso_bytes) == first.skill
