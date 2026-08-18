from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from .audit import InMemoryAuditSink, InMemorySnapshotStore
from .compiler import CompilationResult, NslCompiler
from .core import (
    YEAR,
    DataClassification,
    Money,
    domain,
    list_type,
    money_type,
    record_type,
)
from .ir import NsoCodec
from .replay import (
    RecordingToolExecutor,
    ReplayBundle,
    ReplayToolExecutor,
    create_replay_bundle,
    load_replay_inputs,
)
from .runtime import ExecutionRequest, ExecutionResult, RuntimeEngine
from .security import DataHandlingPolicy, ExecutionPrincipal
from .tools import MockToolExecutor, ToolContract, ToolContractCatalog


PROJECT_CODE = domain("ProjectCode")
TEAM_ID = domain("TeamId")
KRW = money_type("KRW")
PARENT_PROJECT = record_type(
    "ParentProject",
    project_code=PROJECT_CODE,
    budget=KRW,
)
CHILD_PROJECT = record_type(
    "ChildProject",
    project_code=PROJECT_CODE,
    parent_project=PROJECT_CODE,
    expense_amount=KRW,
)


def build_tool_catalog() -> ToolContractCatalog:
    return ToolContractCatalog(
        (
            ToolContract(
                tool_id="PROJECT.LIST_PARENT_PROJECTS",
                version="1.0.0",
                capability="READ",
                input_types=(("year", YEAR), ("team_id", TEAM_ID)),
                output_type=list_type(PARENT_PROJECT),
                required_scope="project:budget:read",
                output_classification=DataClassification.CONFIDENTIAL,
            ),
            ToolContract(
                tool_id="PROJECT.LIST_CHILD_PROJECTS",
                version="1.0.0",
                capability="READ",
                input_types=(("parent_project", PROJECT_CODE), ("year", YEAR)),
                output_type=list_type(CHILD_PROJECT),
                required_scope="project:budget:read",
                output_classification=DataClassification.CONFIDENTIAL,
            ),
        )
    )


def build_mock_executor(catalog: ToolContractCatalog) -> MockToolExecutor:
    def parent_projects(arguments: dict[str, Any]) -> list[dict[str, Any]]:
        assert arguments["year"] == 2026
        assert arguments["team_id"] == "TEAM-FINANCE"
        return [
            {
                "project_code": "PARENT-001",
                "budget": Money(Decimal("100000000"), "KRW"),
            }
        ]

    def child_projects(arguments: dict[str, Any]) -> list[dict[str, Any]]:
        assert arguments["parent_project"] == "PARENT-001"
        assert arguments["year"] == 2026
        return [
            {
                "project_code": "CHILD-001",
                "parent_project": "PARENT-001",
                "expense_amount": Money(Decimal("30000000"), "KRW"),
            },
            {
                "project_code": "CHILD-002",
                "parent_project": "PARENT-001",
                "expense_amount": Money(Decimal("25000000"), "KRW"),
            },
            {
                "project_code": "CHILD-003",
                "parent_project": "PARENT-001",
                "expense_amount": Money(Decimal("32500000"), "KRW"),
            },
        ]

    return MockToolExecutor(
        catalog,
        {
            "PROJECT.LIST_PARENT_PROJECTS": parent_projects,
            "PROJECT.LIST_CHILD_PROJECTS": child_projects,
        },
    )


def build_principal(
    *,
    tenant_id: str = "tenant-nex",
    include_tool_scope: bool = True,
    include_replay_scope: bool = True,
) -> ExecutionPrincipal:
    scopes = {"nsl:skill:execute"}
    if include_tool_scope:
        scopes.add("project:budget:read")
    if include_replay_scope:
        scopes.add("nsl:replay:read")
    return ExecutionPrincipal(
        tenant_id=tenant_id,
        subject_id="user-finance-001",
        actor_type="USER",
        roles=frozenset({"FINANCE_ANALYST"}),
        scopes=frozenset(scopes),
        auth_context_ref="auth-context-demo-001",
    )


@dataclass(frozen=True, slots=True)
class VerticalSliceResult:
    compilation: CompilationResult
    live: ExecutionResult
    replay: ExecutionResult
    replay_bundle: ReplayBundle
    live_audit: InMemoryAuditSink
    replay_audit: InMemoryAuditSink
    mock_call_count: int
    replay_call_count: int


async def run_vertical_slice(
    source_path: Path | None = None,
) -> VerticalSliceResult:
    root = Path(__file__).resolve().parents[1]
    path = source_path or root / "examples" / "project_budget_check.ns"
    source = path.read_text(encoding="utf-8")

    catalog = build_tool_catalog()
    compilation = NslCompiler(catalog).compile(source)
    skill = NsoCodec.decode(compilation.nso_bytes)
    engine = RuntimeEngine(catalog)
    principal = build_principal()
    policy = DataHandlingPolicy(
        max_trace_classification=DataClassification.INTERNAL,
        snapshot_retention_days=30,
    )
    inputs = {"year": 2026}
    runtime_context = {"user": {"team_id": "TEAM-FINANCE"}}
    request = ExecutionRequest(
        execution_id="exec-live-001",
        inputs=inputs,
        runtime_context=runtime_context,
        principal=principal,
        data_policy=policy,
    )

    snapshots = InMemorySnapshotStore()
    mock = build_mock_executor(catalog)
    recording = RecordingToolExecutor(mock, snapshots, policy)
    live_audit = InMemoryAuditSink()
    live = await engine.execute(skill, request, recording, live_audit, snapshots)

    bundle = create_replay_bundle(
        skill.semantic_hash,
        inputs,
        runtime_context,
        principal,
        policy,
        recording,
        snapshots,
    )
    replay_inputs, replay_context = load_replay_inputs(bundle, snapshots, principal)
    replay_request = ExecutionRequest(
        execution_id="exec-replay-001",
        inputs=replay_inputs,
        runtime_context=replay_context,
        principal=principal,
        data_policy=policy,
    )
    replay_tools = ReplayToolExecutor(bundle.tool_calls, snapshots)
    replay_audit = InMemoryAuditSink()
    replay = await engine.execute(
        skill, replay_request, replay_tools, replay_audit, snapshots
    )
    replay_tools.assert_consumed()

    if live.semantic_view() != replay.semantic_view():
        raise RuntimeError("live and replay semantic results differ")
    return VerticalSliceResult(
        compilation,
        live,
        replay,
        bundle,
        live_audit,
        replay_audit,
        mock.call_count,
        replay_tools.call_count,
    )


def main() -> None:
    result = asyncio.run(run_vertical_slice())
    summary = {
        "semantic_hash": result.compilation.semantic_hash,
        "source_bundle_hash": result.compilation.source_bundle_hash,
        "nso_bytes": len(result.compilation.nso_bytes),
        "live": result.live.semantic_view(),
        "replay_equal": result.live.semantic_view() == result.replay.semantic_view(),
        "mock_provider_calls": result.mock_call_count,
        "replay_snapshot_calls": result.replay_call_count,
        "audit_events": len(result.live_audit.events),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
