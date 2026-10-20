from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NSL_ROOT = ROOT / "nsl"

LLM_MODULES = {
    "anthropic",
    "google.generativeai",
    "langchain",
    "litellm",
    "ollama",
    "openai",
}
DIRECT_INFRASTRUCTURE_MODULES = {
    "aiohttp",
    "http",
    "httpx",
    "os",
    "pathlib",
    "psycopg",
    "pymssql",
    "pymysql",
    "pyodbc",
    "requests",
    "shutil",
    "socket",
    "sqlalchemy",
    "sqlite3",
    "tempfile",
    "urllib",
}
PROVIDER_MODULES = {
    "azure",
    "boto3",
    "botocore",
    "google.cloud",
    "salesforce",
    "stripe",
}
WORKFLOW_MODULES = {
    "airflow",
    "celery",
    "durabletask",
    "prefect",
    "temporalio",
}
HOST_FRAMEWORK_MODULES = {
    "django",
    "fastapi",
    "flask",
    "nex_ae",
    "starlette",
}
RUNTIME_KERNEL_MODULES = {
    "audit.py",
    "builtins.py",
    "checkpoints.py",
    "core.py",
    "data_protection.py",
    "idempotency.py",
    "integrity.py",
    "ir.py",
    "ir_schema.py",
    "preflight.py",
    "replay.py",
    "result_codec.py",
    "runtime.py",
    "runtime_models.py",
    "security.py",
    "trust.py",
    "validation.py",
    "verification.py",
    "write_execution.py",
    "write_intent.py",
}
MODULE_BOUNDARIES = {
    "core.py": {"compiler", "ir", "runtime", "syntax", "tools"},
    "bounds.py": {"audit", "compiler", "replay", "runtime", "syntax", "tools"},
    "syntax.py": {"audit", "compiler", "ir", "replay", "runtime", "tools"},
    "ir.py": {"audit", "compiler", "replay", "runtime", "syntax"},
    "ir_schema.py": {"compiler", "ir", "runtime", "syntax", "tools"},
    "integrity.py": {"compiler", "includes", "ir", "runtime", "syntax", "tools"},
    "idempotency.py": {"audit", "compiler", "replay", "runtime", "syntax"},
    "preflight.py": {
        "audit",
        "compiler",
        "nsp_verification",
        "replay",
        "runtime",
        "syntax",
        "tools",
    },
    "write_execution.py": {"audit", "compiler", "replay", "runtime", "syntax"},
    "compiler.py": {"audit", "replay", "runtime", "runtime_models"},
    "data_protection.py": {
        "audit",
        "compiler",
        "ir",
        "replay",
        "runtime",
        "syntax",
        "tools",
    },
    "semantic_diagnostics.py": {"compiler", "ir", "runtime", "syntax", "tools"},
    "symbols.py": {"compiler", "ir", "runtime", "syntax", "tools"},
    "type_system.py": {"compiler", "ir", "runtime", "syntax", "tools"},
    "runtime.py": {"compiler", "syntax"},
    "runtime_models.py": {"compiler", "syntax"},
    "result_codec.py": {"audit", "compiler", "replay", "runtime", "syntax", "tools"},
    "validation.py": {"audit", "compiler", "replay", "runtime", "syntax", "tools"},
    "verification.py": {
        "audit",
        "compiler",
        "nsp_verification",
        "replay",
        "runtime",
        "syntax",
    },
    "write_intent.py": {"audit", "compiler", "replay", "runtime", "syntax", "tools"},
    "trust.py": {
        "audit",
        "compiler",
        "nsp_verification",
        "replay",
        "runtime",
        "syntax",
        "tools",
    },
}


@dataclass(frozen=True, slots=True, order=True)
class ArchitectureViolation:
    rule_id: str
    module: str
    detail: str

    def to_data(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "module": self.module,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ArchitectureCertificationReport:
    checked_file_count: int
    violations: tuple[ArchitectureViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_data(self) -> dict[str, Any]:
        return {
            "status": "CERTIFIED" if self.passed else "FAILED",
            "checked_file_count": self.checked_file_count,
            "violation_count": len(self.violations),
            "violations": [item.to_data() for item in self.violations],
        }


def _matches_module(module: str, candidates: set[str]) -> bool:
    return any(module == item or module.startswith(f"{item}.") for item in candidates)


def _relative_imports(tree: ast.AST) -> set[str]:
    return {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level >= 1 and node.module
    }


def _absolute_imports(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def _imported_names(tree: ast.AST) -> set[str]:
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


def _called_names(tree: ast.AST) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _violation(
    rule_id: str, module: str, detail: str
) -> ArchitectureViolation:
    return ArchitectureViolation(rule_id, module, detail)


def _scan_import_set(
    *,
    rule_id: str,
    module: str,
    imported: set[str],
    forbidden: set[str],
    label: str,
) -> list[ArchitectureViolation]:
    matches = sorted(item for item in imported if _matches_module(item, forbidden))
    if not matches:
        return []
    return [_violation(rule_id, module, f"{label}: {', '.join(matches)}")]


def certify_architecture(
    nsl_root: Path = DEFAULT_NSL_ROOT,
) -> ArchitectureCertificationReport:
    try:
        paths = tuple(sorted(nsl_root.rglob("*.py")))
    except OSError as error:
        return ArchitectureCertificationReport(
            0,
            (_violation("ARCH-GATE-ROOT", str(nsl_root), str(error)),),
        )
    if not nsl_root.is_dir() or not paths:
        return ArchitectureCertificationReport(
            0,
            (_violation("ARCH-GATE-ROOT", str(nsl_root), "NSL package is missing or empty"),),
        )

    trees: dict[Path, ast.AST] = {}
    violations: list[ArchitectureViolation] = []
    for path in paths:
        module = path.relative_to(nsl_root).as_posix()
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError) as error:
            violations.append(_violation("ARCH-GATE-PARSE", module, str(error)))

    for required in ("core.py", "ir.py", "compiler.py", "runtime.py"):
        if nsl_root / required not in trees:
            violations.append(
                _violation("NSL-ARC-201", required, "required shared architecture module is missing")
            )

    for owner in ("compiler.py", "runtime.py"):
        tree = trees.get(nsl_root / owner)
        if tree is None:
            continue
        missing = {"core", "ir"} - _relative_imports(tree)
        if missing:
            violations.append(
                _violation(
                    "NSL-ARC-201",
                    owner,
                    f"shared semantic imports missing: {', '.join(sorted(missing))}",
                )
            )

    for path, tree in trees.items():
        module = path.relative_to(nsl_root).as_posix()
        filename = path.name
        relative = _relative_imports(tree)
        absolute = _absolute_imports(tree)
        imported_names = _imported_names(tree)

        forbidden_boundary = MODULE_BOUNDARIES.get(filename, set())
        boundary_matches = sorted(relative & forbidden_boundary)
        if boundary_matches:
            violations.append(
                _violation(
                    "ARCH-MODULE-BOUNDARY",
                    module,
                    f"forbidden local imports: {', '.join(boundary_matches)}",
                )
            )

        violations.extend(
            _scan_import_set(
                rule_id="NSL-ARC-203",
                module=module,
                imported=absolute,
                forbidden=LLM_MODULES,
                label="LLM dependency",
            )
        )
        violations.extend(
            _scan_import_set(
                rule_id="ARCH-HOST-FRAMEWORK",
                module=module,
                imported=absolute,
                forbidden=HOST_FRAMEWORK_MODULES,
                label="host or web-framework dependency",
            )
        )

        dynamic_calls = sorted(_called_names(tree) & {"__import__", "eval", "exec"})
        if dynamic_calls:
            violations.append(
                _violation(
                    "ARCH-DYNAMIC-EXECUTION",
                    module,
                    f"forbidden calls: {', '.join(dynamic_calls)}",
                )
            )

        if filename not in RUNTIME_KERNEL_MODULES:
            continue
        absolute_local = {
            item.removeprefix("nsl.").split(".", 1)[0]
            for item in absolute
            if item.startswith("nsl.")
        }
        ast_dependencies = sorted(
            (relative | absolute_local) & {"compiler", "source", "syntax"}
        )
        ast_names = sorted(name for name in imported_names if name.startswith("Ast"))
        if ast_dependencies or ast_names:
            details = ast_dependencies + ast_names
            violations.append(
                _violation(
                    "NSL-ARC-202",
                    module,
                    f"Compiler or Source AST dependency: {', '.join(details)}",
                )
            )

        violations.extend(
            _scan_import_set(
                rule_id="ARCH-DIRECT-INFRASTRUCTURE",
                module=module,
                imported=absolute,
                forbidden=DIRECT_INFRASTRUCTURE_MODULES,
                label="direct filesystem, network, or SQL dependency",
            )
        )
        violations.extend(
            _scan_import_set(
                rule_id="ARCH-PROVIDER-BOUNDARY",
                module=module,
                imported=absolute,
                forbidden=PROVIDER_MODULES,
                label="provider SDK dependency",
            )
        )
        violations.extend(
            _scan_import_set(
                rule_id="ARCH-WORKFLOW-BOUNDARY",
                module=module,
                imported=absolute,
                forbidden=WORKFLOW_MODULES,
                label="workflow orchestration dependency",
            )
        )

    runtime_tree = trees.get(nsl_root / "runtime.py")
    if runtime_tree is not None:
        tool_names = {
            alias.name
            for node in ast.walk(runtime_tree)
            if isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module == "tools"
            for alias in node.names
        }
        if "ToolExecutionPort" not in tool_names:
            violations.append(
                _violation(
                    "ARCH-TOOL-PORT",
                    "runtime.py",
                    "ToolExecutionPort import is required",
                )
            )
        if "MockToolExecutor" in tool_names:
            violations.append(
                _violation(
                    "ARCH-TOOL-PORT",
                    "runtime.py",
                    "MockToolExecutor cannot enter Runtime",
                )
            )
        if "execute_with_timeout" not in _called_names(runtime_tree):
            violations.append(
                _violation(
                    "ARCH-TOOL-PORT",
                    "runtime.py",
                    "Runtime has no ToolExecutionPort dispatch path",
                )
            )

    return ArchitectureCertificationReport(
        checked_file_count=len(paths),
        violations=tuple(sorted(set(violations))),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Certify NSL architecture boundaries")
    parser.add_argument("--nsl-root", type=Path, default=DEFAULT_NSL_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = certify_architecture(args.nsl_root)
    payload = json.dumps(
        report.to_data(), ensure_ascii=False, indent=2, sort_keys=True
    )
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        except OSError as error:
            print(f"cannot write architecture report: {error}", file=sys.stderr)
            return 2
    print(payload)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
