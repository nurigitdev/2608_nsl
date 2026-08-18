from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import unittest

from nsl.audit import InMemoryAuditSink, InMemorySnapshotStore
from nsl.compiler import CompileError, NslCompiler
from nsl.core import (
    CheckStatus,
    Completeness,
    DataClassification,
    ExecutionStatus,
    Money,
)
from nsl.ir import NsoCodec
from nsl.replay import (
    RecordingToolExecutor,
    create_replay_bundle,
    load_replay_inputs,
)
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.security import DataHandlingPolicy
from nsl.tools import ToolCallRequest, ToolExecutionError, ToolExecutionPort
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
    run_vertical_slice,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "examples" / "project_budget_check.ns"


class CompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SOURCE_PATH.read_text(encoding="utf-8")
        self.catalog = build_tool_catalog()

    def test_compile_is_deterministic_and_nso_round_trips(self) -> None:
        compiler = NslCompiler(self.catalog)
        first = compiler.compile(self.source)
        second = compiler.compile(self.source)

        self.assertEqual(first.semantic_hash, second.semantic_hash)
        self.assertEqual(first.nso_bytes, second.nso_bytes)
        loaded = NsoCodec.decode(first.nso_bytes)
        self.assertEqual(loaded.semantic_hash, first.skill.semantic_hash)
        self.assertEqual(loaded, first.skill)

    def test_compile_rejects_undeclared_tool(self) -> None:
        source = self.source.replace(
            "read PROJECT.LIST_CHILD_PROJECTS(",
            "read PROJECT.UNKNOWN_TOOL(",
        )
        with self.assertRaisesRegex(CompileError, "tool not declared"):
            NslCompiler(self.catalog).compile(source)

    def test_nso_tamper_is_rejected(self) -> None:
        artifact = NslCompiler(self.catalog).compile(self.source).nso_bytes
        tampered = artifact.replace(b'"tool_calls":11', b'"tool_calls":12')
        with self.assertRaisesRegex(ValueError, "semantic hash mismatch"):
            NsoCodec.decode(tampered)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.source = SOURCE_PATH.read_text(encoding="utf-8")
        self.catalog = build_tool_catalog()
        self.skill = NsoCodec.decode(
            NslCompiler(self.catalog).compile(self.source).nso_bytes
        )
        self.engine = RuntimeEngine(self.catalog)
        self.policy = DataHandlingPolicy(
            max_trace_classification=DataClassification.INTERNAL,
            snapshot_retention_days=30,
        )

    def request(self, principal=None, execution_id: str = "exec-test") -> ExecutionRequest:
        return ExecutionRequest(
            execution_id=execution_id,
            inputs={"year": 2026},
            runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
            principal=principal or build_principal(),
            data_policy=self.policy,
        )

    async def test_vertical_slice_live_and_replay_are_equal(self) -> None:
        result = await run_vertical_slice()

        self.assertEqual(result.live.status, ExecutionStatus.COMPLETED)
        self.assertEqual(result.live.checks[0].status, CheckStatus.PASS)
        self.assertEqual(
            result.live.outputs[0].values["spent"],
            Money(Decimal("87500000"), "KRW"),
        )
        self.assertEqual(
            result.live.outputs[0].values["remaining"],
            Money(Decimal("12500000"), "KRW"),
        )
        self.assertEqual(result.live.semantic_view(), result.replay.semantic_view())
        self.assertEqual(result.mock_call_count, 2)
        self.assertEqual(result.replay_call_count, 2)

    async def test_missing_tool_scope_is_denied_before_provider_call(self) -> None:
        mock = build_mock_executor(self.catalog)
        result = await self.engine.execute(
            self.skill,
            self.request(build_principal(include_tool_scope=False)),
            mock,
            InMemoryAuditSink(),
        )

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(result.error.code, "AUTHORIZATION_DENIED")
        self.assertEqual(mock.call_count, 0)

    async def test_partial_tool_data_cannot_produce_pass(self) -> None:
        delegate = build_mock_executor(self.catalog)

        class PartialChildExecutor:
            async def execute(self, request: ToolCallRequest):
                result = await delegate.execute(request)
                if request.tool_id == "PROJECT.LIST_CHILD_PROJECTS":
                    return replace(result, completeness=Completeness.PARTIAL)
                return result

        result = await self.engine.execute(
            self.skill,
            self.request(),
            PartialChildExecutor(),
            InMemoryAuditSink(),
        )

        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(result.checks[0].status, CheckStatus.UNKNOWN)
        self.assertNotEqual(result.checks[0].status, CheckStatus.PASS)

    async def test_tool_failure_is_not_converted_to_empty_data(self) -> None:
        delegate = build_mock_executor(self.catalog)

        class FailingChildExecutor:
            async def execute(self, request: ToolCallRequest):
                if request.tool_id == "PROJECT.LIST_CHILD_PROJECTS":
                    raise ToolExecutionError("UPSTREAM_TIMEOUT", "ERP timeout")
                return await delegate.execute(request)

        result = await self.engine.execute(
            self.skill,
            self.request(),
            FailingChildExecutor(),
            InMemoryAuditSink(),
        )

        self.assertEqual(result.status, ExecutionStatus.TOOL_ERROR)
        self.assertEqual(result.error.code, "UPSTREAM_TIMEOUT")
        self.assertEqual(result.checks, ())
        self.assertEqual(result.outputs, ())

    async def test_confidential_values_are_redacted_from_audit(self) -> None:
        snapshots = InMemorySnapshotStore()
        mock = build_mock_executor(self.catalog)
        recording = RecordingToolExecutor(mock, snapshots, self.policy)
        audit = InMemoryAuditSink()
        result = await self.engine.execute(
            self.skill, self.request(), recording, audit
        )

        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        serialized = json.dumps(
            [
                {
                    "event_type": event.event_type,
                    "classification": event.classification.value,
                    "payload": event.payload,
                }
                for event in audit.events
            ],
            ensure_ascii=False,
        )
        self.assertNotIn("100000000", serialized)
        self.assertNotIn("87500000", serialized)
        self.assertNotIn("32500000", serialized)
        tool_events = [
            event for event in audit.events if event.event_type == "TOOL_COMPLETED"
        ]
        self.assertTrue(all(event.payload["snapshot_ref"] for event in tool_events))
        started = next(
            event for event in audit.events if event.event_type == "EXECUTION_STARTED"
        )
        # Direct unit executions may omit a snapshot port, but hashes remain mandatory.
        self.assertTrue(started.payload["input_hash"].startswith("sha256:"))
        self.assertTrue(started.payload["context_hash"].startswith("sha256:"))
        emit_event = next(
            event for event in audit.events if event.event_type == "EMIT_COMPLETED"
        )
        self.assertTrue(emit_event.payload["redacted"])

    async def test_replay_snapshot_is_tenant_isolated(self) -> None:
        principal = build_principal()
        snapshots = InMemorySnapshotStore()
        mock = build_mock_executor(self.catalog)
        recording = RecordingToolExecutor(mock, snapshots, self.policy)
        request = self.request(principal)
        await self.engine.execute(
            self.skill, request, recording, InMemoryAuditSink()
        )
        bundle = create_replay_bundle(
            self.skill.semantic_hash,
            dict(request.inputs),
            dict(request.runtime_context),
            principal,
            self.policy,
            recording,
            snapshots,
        )

        other_tenant = build_principal(tenant_id="tenant-other")
        with self.assertRaisesRegex(PermissionError, "cross-tenant"):
            load_replay_inputs(bundle, snapshots, other_tenant)

    async def test_replay_requires_separate_read_scope(self) -> None:
        principal = build_principal()
        snapshots = InMemorySnapshotStore()
        mock = build_mock_executor(self.catalog)
        recording = RecordingToolExecutor(mock, snapshots, self.policy)
        request = self.request(principal)
        await self.engine.execute(
            self.skill, request, recording, InMemoryAuditSink()
        )
        bundle = create_replay_bundle(
            self.skill.semantic_hash,
            dict(request.inputs),
            dict(request.runtime_context),
            principal,
            self.policy,
            recording,
            snapshots,
        )

        no_replay_scope = build_principal(include_replay_scope=False)
        with self.assertRaisesRegex(PermissionError, "nsl:replay:read"):
            load_replay_inputs(bundle, snapshots, no_replay_scope)


if __name__ == "__main__":
    unittest.main()
