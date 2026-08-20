from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter_ns
from typing import Awaitable, Callable, Protocol, Sequence, TypeVar

from .compiler import NslCompiler
from .ir import NsoCodec
from .runtime_models import ExecutionResult
from .source import SourceFile, coerce_source
from .syntax import Lexer, Parser
from .tools import ToolExecutionMeasurement


SMALL_SOURCE_MAX_BYTES = 64 * 1024
SMALL_SOURCE_PARSE_P95_BUDGET_NS = 100_000_000
MAX_BENCHMARK_ITERATIONS = 10_000
BOUNDED_LOOP_BENCHMARK_ITERATIONS = 1_000

T = TypeVar("T")


class PerformanceAcceptanceError(RuntimeError):
    pass


class BenchmarkClock(Protocol):
    def monotonic_ns(self) -> int:
        ...


class SystemBenchmarkClock:
    __slots__ = ()

    def monotonic_ns(self) -> int:
        return perf_counter_ns()


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    name: str
    samples_ns: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("benchmark name must be non-empty")
        if not self.samples_ns:
            raise ValueError("benchmark samples must not be empty")
        if not all(type(value) is int and value >= 0 for value in self.samples_ns):
            raise ValueError("benchmark samples must be non-negative integers")

    @property
    def count(self) -> int:
        return len(self.samples_ns)

    @property
    def minimum_ns(self) -> int:
        return min(self.samples_ns)

    @property
    def maximum_ns(self) -> int:
        return max(self.samples_ns)

    @property
    def median_ns(self) -> int:
        ordered = sorted(self.samples_ns)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) // 2

    @property
    def p95_ns(self) -> int:
        ordered = sorted(self.samples_ns)
        nearest_rank = (95 * len(ordered) + 99) // 100
        return ordered[nearest_rank - 1]


@dataclass(frozen=True, slots=True)
class RuntimeExecutionTiming:
    execution_id: str
    runtime_status: str
    total_duration_ns: int
    tool_duration_ns: int
    runtime_overhead_ns: int
    tool_call_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, str) or not self.execution_id.strip():
            raise ValueError("runtime timing execution_id must be non-empty")
        if not isinstance(self.runtime_status, str) or not self.runtime_status.strip():
            raise ValueError("runtime timing status must be non-empty")
        durations = (
            self.total_duration_ns,
            self.tool_duration_ns,
            self.runtime_overhead_ns,
        )
        if not all(type(value) is int and value >= 0 for value in durations):
            raise ValueError("runtime timing durations must be non-negative integers")
        if type(self.tool_call_count) is not int or self.tool_call_count < 0:
            raise ValueError("runtime timing tool_call_count must be non-negative")
        expected_overhead = max(0, self.total_duration_ns - self.tool_duration_ns)
        if self.runtime_overhead_ns != expected_overhead:
            raise ValueError("runtime timing overhead is inconsistent")


@dataclass(frozen=True, slots=True)
class ToolTimingSummary:
    execution_id: str
    measurements: tuple[ToolExecutionMeasurement, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.execution_id, str) or not self.execution_id.strip():
            raise ValueError("tool timing execution_id must be non-empty")
        if not all(
            isinstance(item, ToolExecutionMeasurement)
            for item in self.measurements
        ):
            raise TypeError("tool timing contains an invalid measurement")
        if any(item.execution_id != self.execution_id for item in self.measurements):
            raise ValueError("tool timing contains a different execution identity")

    @property
    def call_count(self) -> int:
        return len(self.measurements)

    @property
    def total_duration_ns(self) -> int:
        return sum(item.duration_ns for item in self.measurements)


@dataclass(frozen=True, slots=True)
class MeasuredRuntimeExecution:
    result: ExecutionResult
    timing: RuntimeExecutionTiming

    def __post_init__(self) -> None:
        if not isinstance(self.result, ExecutionResult):
            raise TypeError("measured runtime result must be ExecutionResult")
        if not isinstance(self.timing, RuntimeExecutionTiming):
            raise TypeError("measured runtime timing must be RuntimeExecutionTiming")
        if self.result.execution_id != self.timing.execution_id:
            raise ValueError("measured runtime execution identities differ")


def benchmark(
    name: str,
    operation: Callable[[], T],
    *,
    iterations: int = 25,
    warmups: int = 3,
    clock: BenchmarkClock | None = None,
) -> BenchmarkSummary:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("benchmark name must be non-empty")
    if not callable(operation):
        raise TypeError("benchmark operation must be callable")
    if (
        type(iterations) is not int
        or iterations < 1
        or iterations > MAX_BENCHMARK_ITERATIONS
    ):
        raise ValueError(
            f"benchmark iterations must be between 1 and {MAX_BENCHMARK_ITERATIONS}"
        )
    if type(warmups) is not int or warmups < 0 or warmups > iterations:
        raise ValueError("benchmark warmups must be between 0 and iterations")
    resolved_clock = clock if clock is not None else SystemBenchmarkClock()
    if not callable(getattr(resolved_clock, "monotonic_ns", None)):
        raise TypeError("benchmark clock must provide monotonic_ns")

    for _ in range(warmups):
        operation()

    samples: list[int] = []
    for _ in range(iterations):
        started_ns = resolved_clock.monotonic_ns()
        operation()
        elapsed_ns = resolved_clock.monotonic_ns() - started_ns
        samples.append(max(0, elapsed_ns))
    return BenchmarkSummary(name, tuple(samples))


def benchmark_parsing(
    source: str | SourceFile,
    *,
    iterations: int = 25,
    warmups: int = 3,
    clock: BenchmarkClock | None = None,
) -> BenchmarkSummary:
    source_file = coerce_source(source)
    source_size = len(source_file.text.encode("utf-8"))
    if source_size > SMALL_SOURCE_MAX_BYTES:
        raise ValueError(
            f"small source benchmark is limited to {SMALL_SOURCE_MAX_BYTES} bytes"
        )

    def parse() -> object:
        tokens = Lexer().tokenize(source_file)
        return Parser(tokens, source_file).parse()

    return benchmark(
        "nsl.source.parse",
        parse,
        iterations=iterations,
        warmups=warmups,
        clock=clock,
    )


def require_immediate_small_source_parsing(
    summary: BenchmarkSummary,
    *,
    p95_budget_ns: int = SMALL_SOURCE_PARSE_P95_BUDGET_NS,
) -> None:
    if not isinstance(summary, BenchmarkSummary):
        raise TypeError("parsing acceptance requires BenchmarkSummary")
    if type(p95_budget_ns) is not int or p95_budget_ns <= 0:
        raise ValueError("parsing p95 budget must be a positive integer")
    if summary.name != "nsl.source.parse":
        raise ValueError("parsing acceptance requires the parsing benchmark")
    if summary.p95_ns > p95_budget_ns:
        raise PerformanceAcceptanceError(
            f"small source parsing p95 {summary.p95_ns}ns exceeds {p95_budget_ns}ns"
        )


def benchmark_source_compilation(
    compiler: NslCompiler,
    source: str | SourceFile,
    *,
    iterations: int = 25,
    warmups: int = 3,
    clock: BenchmarkClock | None = None,
) -> BenchmarkSummary:
    if not isinstance(compiler, NslCompiler):
        raise TypeError("source compilation benchmark requires NslCompiler")
    source_file = coerce_source(source)
    return benchmark(
        "nsl.source.compile",
        lambda: compiler.compile(source_file),
        iterations=iterations,
        warmups=warmups,
        clock=clock,
    )


def benchmark_nso_loading(
    nso_bytes: bytes,
    *,
    iterations: int = 25,
    warmups: int = 3,
    clock: BenchmarkClock | None = None,
) -> BenchmarkSummary:
    if type(nso_bytes) is not bytes or not nso_bytes:
        raise TypeError("NSO loading benchmark requires non-empty bytes")
    return benchmark(
        "nsl.nso.load",
        lambda: NsoCodec.decode(nso_bytes),
        iterations=iterations,
        warmups=warmups,
        clock=clock,
    )


def require_nso_loading_faster(
    compilation: BenchmarkSummary,
    loading: BenchmarkSummary,
) -> None:
    if not isinstance(compilation, BenchmarkSummary) or not isinstance(
        loading, BenchmarkSummary
    ):
        raise TypeError("NSO loading acceptance requires BenchmarkSummary values")
    if compilation.name != "nsl.source.compile":
        raise ValueError("compilation evidence has an invalid benchmark name")
    if loading.name != "nsl.nso.load":
        raise ValueError("loading evidence has an invalid benchmark name")
    if loading.median_ns >= compilation.median_ns:
        raise PerformanceAcceptanceError(
            "NSO loading median must be lower than source compilation median"
        )


def summarize_tool_timings(
    execution_id: str,
    measurements: Sequence[ToolExecutionMeasurement],
) -> ToolTimingSummary:
    if not isinstance(execution_id, str) or not execution_id.strip():
        raise ValueError("tool timing execution_id must be non-empty")
    if not isinstance(measurements, Sequence):
        raise TypeError("tool timing source must be a sequence")
    if not all(isinstance(item, ToolExecutionMeasurement) for item in measurements):
        raise TypeError("tool timing source contains an invalid item")
    return ToolTimingSummary(
        execution_id,
        tuple(item for item in measurements if item.execution_id == execution_id),
    )


async def measure_runtime_execution(
    execution_id: str,
    operation: Callable[[], Awaitable[ExecutionResult]],
    tool_measurements: Sequence[ToolExecutionMeasurement],
    *,
    clock: BenchmarkClock | None = None,
) -> MeasuredRuntimeExecution:
    if not isinstance(execution_id, str) or not execution_id.strip():
        raise ValueError("runtime measurement execution_id must be non-empty")
    if not callable(operation):
        raise TypeError("runtime measurement operation must be callable")
    if not isinstance(tool_measurements, Sequence):
        raise TypeError("runtime measurement source must be a sequence")
    resolved_clock = clock if clock is not None else SystemBenchmarkClock()
    if not callable(getattr(resolved_clock, "monotonic_ns", None)):
        raise TypeError("runtime measurement clock must provide monotonic_ns")

    measurement_offset = len(tool_measurements)
    started_ns = resolved_clock.monotonic_ns()
    result = await operation()
    elapsed_ns = max(0, resolved_clock.monotonic_ns() - started_ns)
    if not isinstance(result, ExecutionResult):
        raise TypeError("runtime measurement operation must return ExecutionResult")
    if result.execution_id != execution_id:
        raise ValueError("runtime measurement execution identity differs from result")

    current_measurements = tool_measurements[measurement_offset:]
    if not all(isinstance(item, ToolExecutionMeasurement) for item in current_measurements):
        raise TypeError("runtime measurement source contains an invalid item")
    execution_tools = tuple(
        item for item in current_measurements if item.execution_id == execution_id
    )
    tool_duration_ns = sum(item.duration_ns for item in execution_tools)
    timing = RuntimeExecutionTiming(
        execution_id=execution_id,
        runtime_status=result.status.value,
        total_duration_ns=elapsed_ns,
        tool_duration_ns=tool_duration_ns,
        runtime_overhead_ns=max(0, elapsed_ns - tool_duration_ns),
        tool_call_count=len(execution_tools),
    )
    return MeasuredRuntimeExecution(result, timing)


def require_bounded_loop_support(
    result: ExecutionResult,
    *,
    expected_iterations: int = BOUNDED_LOOP_BENCHMARK_ITERATIONS,
) -> None:
    if not isinstance(result, ExecutionResult):
        raise TypeError("bounded loop acceptance requires ExecutionResult")
    if type(expected_iterations) is not int or expected_iterations <= 0:
        raise ValueError("expected loop iterations must be a positive integer")
    if result.status.value != "COMPLETED":
        raise PerformanceAcceptanceError(
            f"bounded loop execution did not complete: {result.status.value}"
        )
    if result.resources.loop_iterations != expected_iterations:
        raise PerformanceAcceptanceError(
            "bounded loop execution count differs from the expected iterations"
        )
