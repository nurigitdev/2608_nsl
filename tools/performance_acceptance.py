from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import platform
import sys

from nsl.audit import InMemoryAuditSink
from nsl.checkpoints import ExecutionCheckpoint
from nsl.compiler import NslCompiler
from nsl.core import BusinessOperationId
from nsl.performance import (
    benchmark,
    benchmark_nso_loading,
    benchmark_parsing,
    benchmark_source_compilation,
    measure_runtime_execution,
    require_bounded_loop_support,
    require_immediate_small_source_parsing,
    require_nso_loading_faster,
    summarize_tool_timings,
)
from nsl.runtime import RuntimeEngine
from nsl.runtime_models import ExecutionRequest
from nsl.tools import (
    InMemoryToolMeasurementSink,
    MockToolExecutor,
    ToolContractCatalog,
)
from nsl.vertical_slice import build_mock_executor, build_principal, build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "examples/project_budget_check.ns"
LOOP_SOURCE = '''language NSL "0.1";
skill TEST.RELEASE_LOOP_1000 {
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


@dataclass(frozen=True, slots=True)
class PerformanceAcceptanceReport:
    parse_p95_ns: int
    compile_median_ns: int
    nso_load_median_ns: int
    runtime_total_ns: int
    runtime_overhead_ns: int
    tool_duration_ns: int
    tool_call_count: int
    protected_evidence_median_ns: int
    bounded_loop_duration_ns: int
    bounded_loop_iterations: int
    bounded_loop_emitted_rows: int
    iterations: int
    python_version: str
    platform: str

    def to_data(self) -> dict[str, int | str]:
        return {
            "status": "PASSED",
            "iterations": self.iterations,
            "python_version": self.python_version,
            "platform": self.platform,
            "parse_p95_ns": self.parse_p95_ns,
            "compile_median_ns": self.compile_median_ns,
            "nso_load_median_ns": self.nso_load_median_ns,
            "runtime_total_ns": self.runtime_total_ns,
            "runtime_overhead_ns": self.runtime_overhead_ns,
            "tool_duration_ns": self.tool_duration_ns,
            "tool_call_count": self.tool_call_count,
            "protected_evidence_median_ns": self.protected_evidence_median_ns,
            "bounded_loop_duration_ns": self.bounded_loop_duration_ns,
            "bounded_loop_iterations": self.bounded_loop_iterations,
            "bounded_loop_emitted_rows": self.bounded_loop_emitted_rows,
        }


def run_performance_acceptance(
    *, iterations: int = 25, warmups: int = 5
) -> PerformanceAcceptanceReport:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    catalog = build_tool_catalog()
    compiler = NslCompiler(catalog)
    nso_bytes = compiler.compile(source).nso_bytes
    parsing = benchmark_parsing(
        source, iterations=iterations, warmups=warmups
    )
    compilation = benchmark_source_compilation(
        compiler, source, iterations=iterations, warmups=warmups
    )
    loading = benchmark_nso_loading(
        nso_bytes, iterations=iterations, warmups=warmups
    )
    require_immediate_small_source_parsing(parsing)
    require_nso_loading_faster(compilation, loading)

    skill = compiler.compile(source).skill
    request = ExecutionRequest(
        execution_id="performance-release-runtime",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )
    tool_measurements = InMemoryToolMeasurementSink()
    runtime = RuntimeEngine(
        catalog,
        tool_measurement_sink=tool_measurements,
    )
    measured = asyncio.run(
        measure_runtime_execution(
            request.execution_id,
            lambda: runtime.execute(
                skill,
                request,
                build_mock_executor(catalog),
                InMemoryAuditSink(),
            ),
            tool_measurements.measurements,
        )
    )
    tools = summarize_tool_timings(
        request.execution_id, tool_measurements.measurements
    )

    checkpoint = ExecutionCheckpoint.create(
        execution_id="performance-protected-evidence",
        business_operation_id=BusinessOperationId(
            "performance-protected-evidence-operation"
        ),
        checkpoint_version=1,
        skill_id=skill.skill_id,
        skill_version=skill.skill_version,
        semantic_hash=skill.semantic_hash,
        ir_version=skill.ir_version,
        runtime_version="nsl-performance-acceptance",
        state_payload=b'{"protected_reference":"snapshot:benchmark"}',
    )
    checkpoint_bytes = checkpoint.to_bytes()
    evidence = benchmark(
        "nsl.protected_evidence.load",
        lambda: ExecutionCheckpoint.from_bytes(checkpoint_bytes),
        iterations=iterations,
        warmups=warmups,
    )

    loop_catalog = ToolContractCatalog(())
    loop_skill = NslCompiler(loop_catalog).compile(LOOP_SOURCE).skill
    loop_request = ExecutionRequest(
        execution_id="performance-release-loop-1000",
        inputs={"items": list(range(1000))},
        runtime_context={},
        principal=build_principal(),
    )
    loop_results = []

    def execute_loop():
        result = asyncio.run(
            RuntimeEngine(loop_catalog).execute(
                loop_skill,
                loop_request,
                MockToolExecutor(loop_catalog, {}),
                InMemoryAuditSink(),
            )
        )
        loop_results.append(result)
        return result

    loop_timing = benchmark(
        "nsl.runtime.loop_1000",
        execute_loop,
        iterations=1,
        warmups=0,
    )
    loop_result = loop_results[-1]
    require_bounded_loop_support(loop_result)
    return PerformanceAcceptanceReport(
        parse_p95_ns=parsing.p95_ns,
        compile_median_ns=compilation.median_ns,
        nso_load_median_ns=loading.median_ns,
        runtime_total_ns=measured.timing.total_duration_ns,
        runtime_overhead_ns=measured.timing.runtime_overhead_ns,
        tool_duration_ns=tools.total_duration_ns,
        tool_call_count=tools.call_count,
        protected_evidence_median_ns=evidence.median_ns,
        bounded_loop_duration_ns=loop_timing.median_ns,
        bounded_loop_iterations=loop_result.resources.loop_iterations,
        bounded_loop_emitted_rows=loop_result.resources.emitted_rows,
        iterations=iterations,
        python_version=platform.python_version(),
        platform=platform.platform(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run NSL performance acceptance")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_performance_acceptance()
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"Performance acceptance failed: {error}", file=sys.stderr)
        return 1
    payload = json.dumps(
        report.to_data(), ensure_ascii=False, indent=2, sort_keys=True
    )
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        except OSError as error:
            print(f"cannot write performance report: {error}", file=sys.stderr)
            return 2
    print("Performance gate: " + payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
