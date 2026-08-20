from __future__ import annotations

from pathlib import Path
import asyncio
from dataclasses import replace
import os
import time

import pytest

from nsl import process_isolation as process_isolation_module
from nsl.performance import (
    BenchmarkSummary,
    MeasuredRuntimeExecution,
    PerformanceAcceptanceError,
    RuntimeExecutionTiming,
    ToolTimingSummary,
    benchmark,
    benchmark_nso_loading,
    benchmark_parsing,
    benchmark_source_compilation,
    measure_runtime_execution,
    require_immediate_small_source_parsing,
    require_bounded_loop_support,
    require_nso_loading_faster,
    summarize_tool_timings,
)
from nsl.process_isolation import (
    IsolatedProcessResult,
    IsolatedProcessStatus,
    ProcessIsolatedRuntime,
    ProcessIsolationError,
    _isolated_entry,
)
from nsl.compiler import NslCompiler
from nsl.audit import InMemoryAuditSink
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.tools import (
    InMemoryToolMeasurementSink,
    MockToolExecutor,
    ToolContractCatalog,
    ToolExecutionMeasurement,
    ToolExecutionOutcome,
)
from nsl.vertical_slice import build_mock_executor, build_principal, build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")
LOOP_SOURCE = '''language NSL "0.1";
skill TEST.BOUNDED_LOOP_1000 {
    version "1.0.0";
    risk READ_VALIDATE;
    limits {
        tool_calls 1;
        loop_iterations 1000;
        emitted_rows 1000;
        collection_size 1000;
    }
    input {
        items: List<Int> classification INTERNAL;
    }
    output {
        value: Int classification INTERNAL;
    }
    foreach item in items max 1000 {
        emit {
            value: item;
        }
    }
}
'''


def isolated_echo(payload: bytes) -> bytes:
    return payload[::-1]


def isolated_error(payload: bytes) -> bytes:
    raise RuntimeError(f"sensitive target failure: {payload!r}")


def isolated_crash(payload: bytes) -> bytes:
    os._exit(23)


def isolated_sleep(payload: bytes) -> bytes:
    time.sleep(2)
    return payload


def isolated_invalid_result(payload: bytes):
    return "not-bytes"


class ManualClock:
    def __init__(self, values: list[int]) -> None:
        self.values = iter(values)

    def monotonic_ns(self) -> int:
        return next(self.values)


def test_perf_001_small_source_parsing_is_immediate() -> None:
    summary = benchmark_parsing(SOURCE, iterations=20, warmups=3)

    require_immediate_small_source_parsing(summary)
    assert summary.name == "nsl.source.parse"
    assert summary.count == 20
    assert summary.minimum_ns <= summary.median_ns <= summary.p95_ns
    assert summary.p95_ns <= summary.maximum_ns


def test_perf_001_benchmark_statistics_cover_even_odd_and_p95_boundaries() -> None:
    odd = BenchmarkSummary("odd", (5, 1, 4, 3, 2))
    even = BenchmarkSummary("even", (4, 1, 3, 2))
    percentile = BenchmarkSummary("p95", tuple(range(1, 101)))

    assert odd.median_ns == 3
    assert even.median_ns == 2
    assert percentile.p95_ns == 95
    assert percentile.minimum_ns == 1
    assert percentile.maximum_ns == 100


@pytest.mark.parametrize(
    ("arguments", "error", "message"),
    [
        ({"name": "", "operation": lambda: None}, ValueError, "name"),
        ({"name": "x", "operation": None}, TypeError, "operation"),
        ({"name": "x", "operation": lambda: None, "iterations": 0}, ValueError, "iterations"),
        ({"name": "x", "operation": lambda: None, "iterations": 10_001}, ValueError, "iterations"),
        ({"name": "x", "operation": lambda: None, "warmups": -1}, ValueError, "warmups"),
        ({"name": "x", "operation": lambda: None, "iterations": 1, "warmups": 2}, ValueError, "warmups"),
        ({"name": "x", "operation": lambda: None, "clock": object()}, TypeError, "clock"),
    ],
)
def test_perf_001_benchmark_rejects_invalid_boundaries(arguments, error, message) -> None:
    with pytest.raises(error, match=message):
        benchmark(**arguments)


def test_perf_001_benchmark_clamps_a_broken_clock_without_hiding_operation_errors() -> None:
    summary = benchmark(
        "clock",
        lambda: None,
        iterations=1,
        warmups=0,
        clock=ManualClock([10, 5]),
    )
    assert summary.samples_ns == (0,)

    with pytest.raises(RuntimeError, match="operation"):
        benchmark(
            "failure",
            lambda: (_ for _ in ()).throw(RuntimeError("operation")),
            iterations=1,
            warmups=0,
        )


@pytest.mark.parametrize(
    ("summary", "error", "message"),
    [
        ("not-summary", TypeError, "BenchmarkSummary"),
        (BenchmarkSummary("other", (1,)), ValueError, "parsing benchmark"),
    ],
)
def test_perf_001_parsing_acceptance_rejects_invalid_evidence(summary, error, message) -> None:
    with pytest.raises(error, match=message):
        require_immediate_small_source_parsing(summary)


def test_perf_001_parsing_acceptance_has_exact_budget_boundary() -> None:
    require_immediate_small_source_parsing(
        BenchmarkSummary("nsl.source.parse", (10,)), p95_budget_ns=10
    )
    with pytest.raises(PerformanceAcceptanceError, match="exceeds"):
        require_immediate_small_source_parsing(
            BenchmarkSummary("nsl.source.parse", (11,)), p95_budget_ns=10
        )
    with pytest.raises(ValueError, match="positive"):
        require_immediate_small_source_parsing(
            BenchmarkSummary("nsl.source.parse", (1,)), p95_budget_ns=0
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"name": "", "samples_ns": (1,)},
        {"name": "x", "samples_ns": ()},
        {"name": "x", "samples_ns": (-1,)},
        {"name": "x", "samples_ns": (True,)},
    ],
)
def test_perf_001_summary_rejects_invalid_samples(arguments) -> None:
    with pytest.raises(ValueError):
        BenchmarkSummary(**arguments)


def test_perf_001_small_source_size_boundary() -> None:
    oversized = " " * (64 * 1024 + 1)
    with pytest.raises(ValueError, match="65536"):
        benchmark_parsing(oversized, iterations=1, warmups=0)


def test_perf_002_nso_loading_is_faster_than_source_compilation() -> None:
    compiler = NslCompiler(build_tool_catalog())
    nso_bytes = compiler.compile(SOURCE).nso_bytes

    compilation = benchmark_source_compilation(
        compiler, SOURCE, iterations=10, warmups=2
    )
    loading = benchmark_nso_loading(nso_bytes, iterations=10, warmups=2)

    # Wall-clock acceptance runs after coverage in tools/run_quality.py.
    assert compilation.count == loading.count == 10
    assert compilation.median_ns > 0
    assert loading.median_ns > 0


def test_perf_002_loading_acceptance_has_strict_median_boundary() -> None:
    compile_summary = BenchmarkSummary("nsl.source.compile", (11,))
    require_nso_loading_faster(
        compile_summary, BenchmarkSummary("nsl.nso.load", (10,))
    )
    with pytest.raises(PerformanceAcceptanceError, match="lower"):
        require_nso_loading_faster(
            compile_summary, BenchmarkSummary("nsl.nso.load", (11,))
        )
    with pytest.raises(PerformanceAcceptanceError, match="lower"):
        require_nso_loading_faster(
            compile_summary, BenchmarkSummary("nsl.nso.load", (12,))
        )


@pytest.mark.parametrize(
    ("compilation", "loading", "error", "message"),
    [
        ("bad", BenchmarkSummary("nsl.nso.load", (1,)), TypeError, "BenchmarkSummary"),
        (BenchmarkSummary("bad", (2,)), BenchmarkSummary("nsl.nso.load", (1,)), ValueError, "compilation"),
        (BenchmarkSummary("nsl.source.compile", (2,)), BenchmarkSummary("bad", (1,)), ValueError, "loading"),
    ],
)
def test_perf_002_loading_acceptance_rejects_invalid_evidence(
    compilation, loading, error, message
) -> None:
    with pytest.raises(error, match=message):
        require_nso_loading_faster(compilation, loading)


@pytest.mark.parametrize("value", [b"", bytearray(b"{}"), "{}"])
def test_perf_002_loading_benchmark_rejects_non_nso_bytes(value) -> None:
    with pytest.raises(TypeError, match="non-empty bytes"):
        benchmark_nso_loading(value, iterations=1, warmups=0)


def test_perf_002_compilation_benchmark_rejects_invalid_compiler() -> None:
    with pytest.raises(TypeError, match="NslCompiler"):
        benchmark_source_compilation(object(), SOURCE, iterations=1, warmups=0)


def test_perf_003_runtime_overhead_is_measured_separately() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    request = ExecutionRequest(
        execution_id="exec-perf-003",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )
    tool_timings = InMemoryToolMeasurementSink()
    runtime = RuntimeEngine(catalog, tool_measurement_sink=tool_timings)

    measured = asyncio.run(
        measure_runtime_execution(
            request.execution_id,
            lambda: runtime.execute(
                skill,
                request,
                build_mock_executor(catalog),
                InMemoryAuditSink(),
            ),
            tool_timings.measurements,
        )
    )

    assert measured.result.status.value == "COMPLETED"
    assert measured.timing.runtime_status == "COMPLETED"
    assert measured.timing.tool_call_count == 2
    assert measured.timing.tool_duration_ns == sum(
        item.duration_ns for item in tool_timings.measurements
    )
    assert measured.timing.runtime_overhead_ns == max(
        0,
        measured.timing.total_duration_ns - measured.timing.tool_duration_ns,
    )


def test_perf_003_runtime_timing_clamps_overlapping_tool_time() -> None:
    timing = RuntimeExecutionTiming(
        execution_id="exec",
        runtime_status="COMPLETED",
        total_duration_ns=10,
        tool_duration_ns=20,
        runtime_overhead_ns=0,
        tool_call_count=2,
    )
    assert timing.runtime_overhead_ns == 0
    with pytest.raises(ValueError, match="inconsistent"):
        replace(timing, runtime_overhead_ns=1)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"execution_id": ""}, "execution_id"),
        ({"runtime_status": ""}, "status"),
        ({"total_duration_ns": -1}, "durations"),
        ({"tool_duration_ns": True}, "durations"),
        ({"tool_call_count": -1}, "tool_call_count"),
    ],
)
def test_perf_003_runtime_timing_rejects_invalid_boundaries(changes, message) -> None:
    values = {
        "execution_id": "exec",
        "runtime_status": "COMPLETED",
        "total_duration_ns": 10,
        "tool_duration_ns": 2,
        "runtime_overhead_ns": 8,
        "tool_call_count": 1,
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        RuntimeExecutionTiming(**values)


def _tool_measurement(execution_id: str = "exec") -> ToolExecutionMeasurement:
    return ToolExecutionMeasurement(
        execution_id=execution_id,
        invocation_id="inv1",
        node_id="read1",
        tool_id="TEST.ECHO",
        tool_version="1.0.0",
        tenant_id="tenant",
        outcome=ToolExecutionOutcome.RETURNED,
        duration_ns=3,
    )


def test_perf_003_runtime_probe_ignores_previous_and_other_execution_timings() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    request = ExecutionRequest(
        execution_id="exec-current",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )
    measurements = [_tool_measurement("exec-current")]

    async def operation():
        measurements.append(_tool_measurement("exec-other"))
        return await RuntimeEngine(catalog).execute(
            skill,
            request,
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )

    measured = asyncio.run(
        measure_runtime_execution(
            "exec-current",
            operation,
            measurements,
            clock=ManualClock([10, 20]),
        )
    )
    assert measured.timing.tool_call_count == 0
    assert measured.timing.tool_duration_ns == 0
    assert measured.timing.runtime_overhead_ns == 10


def test_perf_003_measured_execution_requires_matching_contracts() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    request = ExecutionRequest(
        execution_id="exec-contract",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill, request, build_mock_executor(catalog), InMemoryAuditSink()
        )
    )
    timing = RuntimeExecutionTiming("exec-contract", "COMPLETED", 1, 0, 1, 0)
    assert MeasuredRuntimeExecution(result, timing).result is result
    with pytest.raises(TypeError, match="ExecutionResult"):
        MeasuredRuntimeExecution(object(), timing)
    with pytest.raises(TypeError, match="RuntimeExecutionTiming"):
        MeasuredRuntimeExecution(result, object())
    with pytest.raises(ValueError, match="identities"):
        MeasuredRuntimeExecution(result, replace(timing, execution_id="other"))


def test_perf_003_runtime_probe_rejects_invalid_contracts() -> None:
    async def invalid_result():
        return object()

    with pytest.raises(ValueError, match="execution_id"):
        asyncio.run(measure_runtime_execution("", invalid_result, []))
    with pytest.raises(TypeError, match="operation"):
        asyncio.run(measure_runtime_execution("exec", None, []))
    with pytest.raises(TypeError, match="sequence"):
        asyncio.run(measure_runtime_execution("exec", invalid_result, object()))
    with pytest.raises(TypeError, match="clock"):
        asyncio.run(
            measure_runtime_execution("exec", invalid_result, [], clock=object())
        )
    with pytest.raises(TypeError, match="ExecutionResult"):
        asyncio.run(measure_runtime_execution("exec", invalid_result, []))


def test_perf_003_runtime_probe_rejects_mismatched_result_and_measurements() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    request = ExecutionRequest(
        execution_id="actual",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )

    async def valid_result():
        return await RuntimeEngine(catalog).execute(
            skill,
            request,
            build_mock_executor(catalog),
            InMemoryAuditSink(),
        )

    with pytest.raises(ValueError, match="identity"):
        asyncio.run(measure_runtime_execution("different", valid_result, []))

    measurements = []

    async def append_invalid_measurement():
        measurements.append(object())
        return await valid_result()

    with pytest.raises(TypeError, match="invalid item"):
        asyncio.run(
            measure_runtime_execution(
                "actual", append_invalid_measurement, measurements
            )
        )


def test_perf_004_tool_and_runtime_timings_are_separate() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    request = ExecutionRequest(
        execution_id="exec-perf-004",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )
    tool_timings = InMemoryToolMeasurementSink()
    runtime = RuntimeEngine(catalog, tool_measurement_sink=tool_timings)
    audit = InMemoryAuditSink()

    measured = asyncio.run(
        measure_runtime_execution(
            request.execution_id,
            lambda: runtime.execute(
                skill, request, build_mock_executor(catalog), audit
            ),
            tool_timings.measurements,
        )
    )
    tools = summarize_tool_timings(request.execution_id, tool_timings.measurements)

    assert tools.call_count == 2
    assert tools.total_duration_ns == measured.timing.tool_duration_ns
    assert measured.timing.runtime_overhead_ns == max(
        0, measured.timing.total_duration_ns - tools.total_duration_ns
    )
    tool_events = [
        event
        for event in audit.events
        if event.event_type in {"TOOL_STARTED", "TOOL_COMPLETED"}
    ]
    assert all("duration_ns" not in event.payload for event in tool_events)


def test_perf_004_tool_timing_summary_filters_execution_and_supports_zero_calls() -> None:
    first = _tool_measurement("first")
    second = replace(_tool_measurement("second"), duration_ns=7)

    summary = summarize_tool_timings("second", [first, second])
    empty = summarize_tool_timings("none", [first, second])

    assert summary.measurements == (second,)
    assert summary.call_count == 1
    assert summary.total_duration_ns == 7
    assert empty.call_count == 0
    assert empty.total_duration_ns == 0


def test_perf_004_tool_timing_contract_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="execution_id"):
        summarize_tool_timings("", [])
    with pytest.raises(TypeError, match="sequence"):
        summarize_tool_timings("exec", object())
    with pytest.raises(TypeError, match="invalid item"):
        summarize_tool_timings("exec", [object()])
    with pytest.raises(ValueError, match="different execution"):
        ToolTimingSummary("first", (_tool_measurement("second"),))
    with pytest.raises(TypeError, match="invalid measurement"):
        ToolTimingSummary("first", (object(),))
    with pytest.raises(ValueError, match="execution_id"):
        ToolTimingSummary("", ())


@pytest.mark.parametrize(
    ("item_count", "expected_status", "expected_iterations"),
    [
        (999, "COMPLETED", 999),
        (1000, "COMPLETED", 1000),
        (1001, "LIMIT_EXCEEDED", 0),
    ],
)
def test_perf_005_bounded_1000_loop_boundary(
    item_count, expected_status, expected_iterations
) -> None:
    catalog = ToolContractCatalog(())
    skill = NslCompiler(catalog).compile(LOOP_SOURCE).skill
    request = ExecutionRequest(
        execution_id=f"exec-loop-{item_count}",
        inputs={"items": list(range(item_count))},
        runtime_context={},
        principal=build_principal(),
    )

    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            request,
            MockToolExecutor(catalog, {}),
            InMemoryAuditSink(),
        )
    )

    assert result.status.value == expected_status
    assert result.resources.loop_iterations == expected_iterations
    if item_count == 1000:
        require_bounded_loop_support(result)
        assert result.resources.emitted_rows == 1000
        assert len(result.outputs) == 1000
    if item_count == 1001:
        assert result.error is not None
        assert result.error.code == "NSL-E6001"


def test_perf_005_bounded_loop_acceptance_rejects_incomplete_or_wrong_count() -> None:
    catalog = ToolContractCatalog(())
    skill = NslCompiler(catalog).compile(LOOP_SOURCE).skill
    base_request = ExecutionRequest(
        execution_id="exec-loop-acceptance",
        inputs={"items": [1]},
        runtime_context={},
        principal=build_principal(),
    )
    one_iteration = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            base_request,
            MockToolExecutor(catalog, {}),
            InMemoryAuditSink(),
        )
    )
    with pytest.raises(PerformanceAcceptanceError, match="count"):
        require_bounded_loop_support(one_iteration)

    over_limit = asyncio.run(
        RuntimeEngine(catalog).execute(
            skill,
            replace(base_request, execution_id="exec-loop-over", inputs={"items": list(range(1001))}),
            MockToolExecutor(catalog, {}),
            InMemoryAuditSink(),
        )
    )
    with pytest.raises(PerformanceAcceptanceError, match="did not complete"):
        require_bounded_loop_support(over_limit)
    with pytest.raises(TypeError, match="ExecutionResult"):
        require_bounded_loop_support(object())
    with pytest.raises(ValueError, match="positive"):
        require_bounded_loop_support(one_iteration, expected_iterations=0)


def test_rel_002_runtime_success_and_target_error_are_process_isolated() -> None:
    success = ProcessIsolatedRuntime(isolated_echo, timeout_ms=5_000).execute(
        b"runtime-job"
    )
    failure = ProcessIsolatedRuntime(isolated_error, timeout_ms=5_000).execute(
        b"secret"
    )

    assert success == IsolatedProcessResult(
        IsolatedProcessStatus.COMPLETED, b"boj-emitnur", 0, None
    )
    assert failure.status is IsolatedProcessStatus.TARGET_ERROR
    assert failure.payload is None
    assert failure.error_code == "ISOLATED_RUNTIME_TARGET_ERROR"
    assert "secret" not in repr(failure)


def test_rel_002_runtime_crash_does_not_terminate_parent_process() -> None:
    parent_pid = os.getpid()
    result = ProcessIsolatedRuntime(isolated_crash, timeout_ms=5_000).execute(
        b"crash"
    )

    assert os.getpid() == parent_pid
    assert result.status is IsolatedProcessStatus.CRASHED
    assert result.exit_code == 23
    assert result.error_code == "ISOLATED_RUNTIME_CRASH"


def test_rel_002_runtime_timeout_terminates_only_the_child() -> None:
    parent_pid = os.getpid()
    result = ProcessIsolatedRuntime(isolated_sleep, timeout_ms=100).execute(b"slow")

    assert os.getpid() == parent_pid
    assert result.status is IsolatedProcessStatus.TIMED_OUT
    assert result.payload is None
    assert result.error_code == "ISOLATED_RUNTIME_TIMEOUT"


class FakeSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.frames: list[bytes] = []
        self.closed = False
        self.fail = fail

    def send_bytes(self, frame: bytes) -> None:
        if self.fail:
            raise OSError("pipe failed")
        self.frames.append(frame)

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("target", "limit", "expected"),
    [
        (isolated_echo, 10, b"\x00cba"),
        (isolated_error, 10, b"\x01"),
        (isolated_invalid_result, 10, b"\x02"),
        (lambda payload: b"x" * 11, 10, b"\x02"),
    ],
)
def test_rel_002_child_entry_uses_bounded_redacted_frames(target, limit, expected) -> None:
    sender = FakeSender()
    _isolated_entry(target, b"abc", sender, limit)
    assert sender.frames == [expected]
    assert sender.closed


def test_rel_002_child_entry_closes_sender_when_delivery_fails() -> None:
    sender = FakeSender(fail=True)
    with pytest.raises(OSError, match="pipe failed"):
        _isolated_entry(isolated_echo, b"abc", sender, 10)
    assert sender.closed


@pytest.mark.parametrize(
    ("arguments", "error", "message"),
    [
        ({"target": None}, TypeError, "callable"),
        ({"target": isolated_echo, "timeout_ms": 0}, ValueError, "timeout_ms"),
        ({"target": isolated_echo, "timeout_ms": True}, ValueError, "timeout_ms"),
        ({"target": isolated_echo, "timeout_ms": 2_147_483_648}, ValueError, "timeout_ms"),
        ({"target": isolated_echo, "max_payload_bytes": 0}, ValueError, "max_payload_bytes"),
        ({"target": isolated_echo, "max_payload_bytes": True}, ValueError, "max_payload_bytes"),
        ({"target": isolated_echo, "max_payload_bytes": 8 * 1024 * 1024 + 1}, ValueError, "max_payload_bytes"),
    ],
)
def test_rel_002_process_boundary_rejects_invalid_configuration(
    arguments, error, message
) -> None:
    with pytest.raises(error, match=message):
        ProcessIsolatedRuntime(**arguments)


def test_rel_002_process_boundary_rejects_invalid_payloads() -> None:
    boundary = ProcessIsolatedRuntime(
        isolated_echo, timeout_ms=5_000, max_payload_bytes=3
    )
    with pytest.raises(TypeError, match="bytes"):
        boundary.execute("bad")
    with pytest.raises(ValueError, match="exceeds"):
        boundary.execute(b"1234")


@pytest.mark.parametrize(
    "arguments",
    [
        {"status": "COMPLETED", "payload": b"", "exit_code": 0, "error_code": None},
        {"status": IsolatedProcessStatus.COMPLETED, "payload": None, "exit_code": 0, "error_code": None},
        {"status": IsolatedProcessStatus.COMPLETED, "payload": b"", "exit_code": 1, "error_code": None},
        {"status": IsolatedProcessStatus.TARGET_ERROR, "payload": b"bad", "exit_code": 0, "error_code": "ERROR"},
        {"status": IsolatedProcessStatus.TARGET_ERROR, "payload": None, "exit_code": 0, "error_code": None},
        {"status": IsolatedProcessStatus.CRASHED, "payload": None, "exit_code": 0, "error_code": "CRASH"},
        {"status": IsolatedProcessStatus.CRASHED, "payload": None, "exit_code": True, "error_code": "CRASH"},
    ],
)
def test_rel_002_process_result_rejects_inconsistent_states(arguments) -> None:
    with pytest.raises(ValueError):
        IsolatedProcessResult(**arguments)


def test_rel_002_process_frame_decoder_covers_crash_and_protocol_paths() -> None:
    boundary = ProcessIsolatedRuntime(isolated_echo)

    assert boundary._without_frame(0).status is IsolatedProcessStatus.PROTOCOL_ERROR
    assert boundary._without_frame(None).status is IsolatedProcessStatus.PROTOCOL_ERROR
    assert boundary._from_frame(b"\x00ignored", 9).status is IsolatedProcessStatus.CRASHED
    assert boundary._from_frame(b"\x02", 0).status is IsolatedProcessStatus.PROTOCOL_ERROR


class FakeEndpoint:
    def __init__(self, *, poll_result: bool = False, receive_error: Exception | None = None) -> None:
        self.poll_result = poll_result
        self.receive_error = receive_error
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def poll(self, timeout: float) -> bool:
        return self.poll_result

    def recv_bytes(self, maxlength: int) -> bytes:
        if self.receive_error is not None:
            raise self.receive_error
        return b"\x02"


class FakeProcess:
    def __init__(
        self, *, start_error: Exception | None = None, alive: bool = False, exit_code: int = 0
    ) -> None:
        self.start_error = start_error
        self.alive = alive
        self.exitcode = exit_code
        self.joined = False

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.alive = False
        self.exitcode = 1

    def join(self) -> None:
        self.joined = True


class FakeProcessContext:
    def __init__(self, receiver: FakeEndpoint, sender: FakeEndpoint, process: FakeProcess) -> None:
        self.receiver = receiver
        self.sender = sender
        self.process = process

    def Pipe(self, *, duplex: bool):
        assert not duplex
        return self.receiver, self.sender

    def Process(self, *, target, args):
        assert target is _isolated_entry
        assert len(args) == 4
        return self.process


def test_rel_002_process_start_failure_is_controlled_and_closes_ports(monkeypatch) -> None:
    receiver = FakeEndpoint()
    sender = FakeEndpoint()
    process = FakeProcess(start_error=OSError("spawn denied"))
    context = FakeProcessContext(receiver, sender, process)
    monkeypatch.setattr(
        process_isolation_module.multiprocessing,
        "get_context",
        lambda method: context,
    )

    with pytest.raises(ProcessIsolationError, match="could not start"):
        ProcessIsolatedRuntime(isolated_echo).execute(b"job")
    assert receiver.closed
    assert sender.closed


@pytest.mark.parametrize(
    ("receiver", "exit_code", "expected"),
    [
        (FakeEndpoint(), 0, IsolatedProcessStatus.PROTOCOL_ERROR),
        (FakeEndpoint(poll_result=True, receive_error=EOFError()), 7, IsolatedProcessStatus.CRASHED),
        (FakeEndpoint(poll_result=True, receive_error=OSError()), 0, IsolatedProcessStatus.PROTOCOL_ERROR),
    ],
)
def test_rel_002_process_missing_frame_paths_are_structured(
    monkeypatch, receiver, exit_code, expected
) -> None:
    sender = FakeEndpoint()
    process = FakeProcess(exit_code=exit_code)
    context = FakeProcessContext(receiver, sender, process)
    monkeypatch.setattr(
        process_isolation_module.multiprocessing,
        "get_context",
        lambda method: context,
    )

    result = ProcessIsolatedRuntime(isolated_echo).execute(b"job")

    assert result.status is expected
    assert process.joined
    assert receiver.closed
    assert sender.closed
