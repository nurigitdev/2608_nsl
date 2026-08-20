from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

from nsl.compiler import NslCompiler
from nsl.performance import (
    benchmark_nso_loading,
    benchmark_parsing,
    benchmark_source_compilation,
    require_immediate_small_source_parsing,
    require_nso_loading_faster,
)
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "examples/project_budget_check.ns"


@dataclass(frozen=True, slots=True)
class PerformanceAcceptanceReport:
    parse_p95_ns: int
    compile_median_ns: int
    nso_load_median_ns: int
    iterations: int

    def to_data(self) -> dict[str, int | str]:
        return {
            "status": "PASSED",
            "iterations": self.iterations,
            "parse_p95_ns": self.parse_p95_ns,
            "compile_median_ns": self.compile_median_ns,
            "nso_load_median_ns": self.nso_load_median_ns,
        }


def run_performance_acceptance(
    *, iterations: int = 25, warmups: int = 5
) -> PerformanceAcceptanceReport:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    compiler = NslCompiler(build_tool_catalog())
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
    return PerformanceAcceptanceReport(
        parse_p95_ns=parsing.p95_ns,
        compile_median_ns=compilation.median_ns,
        nso_load_median_ns=loading.median_ns,
        iterations=iterations,
    )


def main() -> int:
    try:
        report = run_performance_acceptance()
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"Performance acceptance failed: {error}", file=sys.stderr)
        return 1
    print(
        "Performance gate: "
        + json.dumps(report.to_data(), ensure_ascii=False, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
