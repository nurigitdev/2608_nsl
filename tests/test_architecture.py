from __future__ import annotations

import ast
from pathlib import Path
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
NSL = ROOT / "nsl"
LLM_MODULES = {
    "anthropic",
    "google.generativeai",
    "langchain",
    "litellm",
    "ollama",
    "openai",
}
RUNTIME_KERNEL_MODULES = {
    "audit.py",
    "builtins.py",
    "core.py",
    "data_protection.py",
    "ir.py",
    "ir_schema.py",
    "integrity.py",
    "replay.py",
    "result_codec.py",
    "runtime.py",
    "runtime_models.py",
    "security.py",
    "validation.py",
}
LANGUAGE_CORE_MODULES = {
    "bounds.py",
    "builtins.py",
    "compiler.py",
    "core.py",
    "data_protection.py",
    "diagnostics.py",
    "integrity.py",
    "ir.py",
    "includes.py",
    "semantic_diagnostics.py",
    "source.py",
    "symbols.py",
    "syntax.py",
    "type_system.py",
}
DIRECT_INFRASTRUCTURE_MODULES = {
    "aiohttp",
    "http",
    "httpx",
    "os",
    "pathlib",
    "psycopg",
    "requests",
    "shutil",
    "socket",
    "sqlalchemy",
    "sqlite3",
    "tempfile",
    "urllib",
}
FILESYSTEM_MODULES = {"os", "pathlib", "shutil", "tempfile"}
NETWORK_MODULES = {"aiohttp", "http", "httpx", "requests", "socket", "urllib"}
DATABASE_MODULES = {"psycopg", "sqlalchemy", "sqlite3"}
HOST_FRAMEWORK_MODULES = {
    "django",
    "fastapi",
    "flask",
    "nex_ae",
    "starlette",
}
ALLOWED_EXTERNAL_MODULES = {
    "__future__",
    "argparse",
    "asyncio",
    "base64",
    "copy",
    "cryptography",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "hashlib",
    "io",
    "json",
    "pathlib",
    "re",
    "sys",
    "time",
    "traceback",
    "types",
    "typing",
    "zipfile",
}
AST_NODE_NAMES = {
    "AstBinary",
    "AstCall",
    "AstCheck",
    "AstEmit",
    "AstFieldSpec",
    "AstForeach",
    "AstIncludeDeclaration",
    "AstIncludeFragment",
    "AstLet",
    "AstLimits",
    "AstLiteral",
    "AstNode",
    "AstPath",
    "AstRead",
    "AstRequiredTool",
    "AstSkill",
}


def local_imports(module_name: str) -> set[str]:
    tree = ast.parse((NSL / f"{module_name}.py").read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def relative_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level >= 1 and node.module
    }


def absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def direct_function_calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def called_attributes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


@pytest.mark.parametrize(
    ("module_name", "forbidden"),
    [
        ("core", {"ir", "syntax", "compiler", "runtime", "tools"}),
        ("bounds", {"syntax", "compiler", "runtime", "tools", "audit", "replay"}),
        ("syntax", {"ir", "compiler", "runtime", "tools", "audit", "replay"}),
        ("ir", {"syntax", "compiler", "runtime", "audit", "replay"}),
        ("ir_schema", {"ir", "syntax", "compiler", "runtime", "tools"}),
        ("integrity", {"ir", "includes", "syntax", "compiler", "runtime", "tools"}),
        ("compiler", {"runtime", "runtime_models", "audit", "replay"}),
        ("data_protection", {"ir", "syntax", "compiler", "runtime", "tools", "audit", "replay"}),
        ("semantic_diagnostics", {"ir", "syntax", "compiler", "runtime", "tools"}),
        ("symbols", {"ir", "syntax", "compiler", "runtime", "tools"}),
        ("type_system", {"ir", "syntax", "compiler", "runtime", "tools"}),
        ("runtime", {"syntax", "compiler"}),
        ("runtime_models", {"syntax", "compiler"}),
        ("result_codec", {"syntax", "compiler", "runtime", "tools", "audit", "replay"}),
        ("validation", {"syntax", "compiler", "runtime", "tools", "audit", "replay"}),
    ],
)
def test_module_dependency_boundaries(module_name, forbidden) -> None:
    violations = local_imports(module_name) & forbidden
    assert not violations, f"{module_name} imports forbidden modules: {sorted(violations)}"


def test_arc_013_include_is_resolved_before_runtime_ir() -> None:
    assert "includes" in local_imports("compiler")
    for runtime_module in (
        "ir",
        "runtime",
        "runtime_models",
        "audit",
        "replay",
    ):
        assert "includes" not in local_imports(runtime_module)

    tree = ast.parse((NSL / "ir.py").read_text(encoding="utf-8"))
    skill_object = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SkillObject"
    )
    fields = {
        node.target.id
        for node in skill_object.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    assert not any("include" in field for field in fields)


def test_arc_004_runtime_has_no_llm_dependency() -> None:
    violations: dict[str, list[str]] = {}
    for path in NSL.rglob("*.py"):
        imported = absolute_imports(path)
        forbidden = sorted(
            module
            for module in imported
            if any(
                module == llm or module.startswith(f"{llm}.")
                for llm in LLM_MODULES
            )
        )
        if forbidden:
            violations[path.name] = forbidden
    assert not violations, f"NSL imports LLM dependencies: {violations}"


def test_arc_005_external_access_uses_tool_execution_port() -> None:
    tree = ast.parse((NSL / "runtime.py").read_text(encoding="utf-8"))
    imported_tool_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "tools"
        for alias in node.names
    }
    port_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "tools"
        and node.func.attr == "execute"
    ]
    timeout_port_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "execute_with_timeout"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "tools"
    ]

    assert "ToolExecutionPort" in imported_tool_names
    assert "MockToolExecutor" not in imported_tool_names
    assert port_calls or timeout_port_calls, (
        "Runtime must route business-system access through the ToolExecutor port"
    )


def test_arc_006_runtime_has_no_direct_infrastructure_dependency() -> None:
    violations: dict[str, list[str]] = {}
    for module_name in sorted(RUNTIME_KERNEL_MODULES):
        imported = absolute_imports(NSL / module_name)
        forbidden = sorted(
            module
            for module in imported
            if module.split(".", 1)[0] in DIRECT_INFRASTRUCTURE_MODULES
        )
        if forbidden:
            violations[module_name] = forbidden
    assert not violations, f"Runtime imports direct infrastructure: {violations}"


def test_persistent_audit_adapter_stays_outside_runtime_kernel() -> None:
    assert "audit_persistence" not in local_imports("runtime")
    assert "audit_persistence" not in local_imports("audit")
    assert "audit" in local_imports("audit_persistence")
    assert "pathlib" in absolute_imports(NSL / "audit_persistence.py")


def test_py_007_mcp_adapter_stays_outside_runtime_core() -> None:
    adapter = NSL / "adapters" / "mcp.py"
    assert adapter.is_file()
    assert relative_imports(adapter) == {"core", "data_protection", "tools"}

    forbidden_adapter_dependencies = {
        "audit",
        "compiler",
        "ir",
        "replay",
        "runtime",
        "runtime_models",
        "syntax",
        "vertical_slice",
    }
    assert not relative_imports(adapter) & forbidden_adapter_dependencies
    assert "adapters" not in local_imports("runtime")
    assert "adapters" not in local_imports("tools")

    runtime_source = (NSL / "runtime.py").read_text(encoding="utf-8")
    tools_source = (NSL / "tools.py").read_text(encoding="utf-8")
    assert "MCPToolExecutor" not in runtime_source
    assert "MCPToolExecutor" not in tools_source


def test_protected_snapshot_adapter_stays_behind_snapshot_store_port() -> None:
    assert "protected_snapshots" not in local_imports("runtime")
    assert "protected_snapshots" not in local_imports("replay")
    assert "audit" in local_imports("protected_snapshots")

    replay_source = (NSL / "replay.py").read_text(encoding="utf-8")
    runtime_source = (NSL / "runtime.py").read_text(encoding="utf-8")
    assert "InMemorySnapshotStore" not in replay_source
    assert "ProtectedSnapshotStore" not in replay_source
    assert "ProtectedSnapshotStore" not in runtime_source


def test_arc_007_nsl_is_an_independent_python_package() -> None:
    violations: dict[str, list[str]] = {}
    for path in NSL.rglob("*.py"):
        imported = absolute_imports(path)
        forbidden = sorted(
            module
            for module in imported
            if module.split(".", 1)[0] in HOST_FRAMEWORK_MODULES
        )
        if forbidden:
            violations[path.name] = forbidden

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.12"
    assert project["project"].get("dependencies", []) == [
        "cryptography>=49.0.0,<50.0.0"
    ]
    assert not violations, f"NSL depends on NeX-AE or web frameworks: {violations}"


def test_py_001_runtime_is_implemented_in_python() -> None:
    runtime_path = NSL / "runtime.py"
    tree = ast.parse(runtime_path.read_text(encoding="utf-8"))
    runtime_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RuntimeEngine"
    ]
    assert runtime_path.suffix == ".py"
    assert len(runtime_classes) == 1
    assert any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "execute"
        for node in runtime_classes[0].body
    )

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.12"


def test_py_002_language_core_has_no_web_framework_dependency() -> None:
    violations: dict[str, list[str]] = {}
    for module_name in sorted(LANGUAGE_CORE_MODULES):
        imported = absolute_imports(NSL / module_name)
        forbidden = sorted(
            module
            for module in imported
            if module.split(".", 1)[0] in HOST_FRAMEWORK_MODULES
        )
        if forbidden:
            violations[module_name] = forbidden
    assert not violations, f"Language Core imports web frameworks: {violations}"


def test_py_003_ast_nodes_are_explicit_dataclasses() -> None:
    tree = ast.parse((NSL / "syntax.py").read_text(encoding="utf-8"))
    ast_classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.startswith("Ast")
    }
    assert set(ast_classes) == AST_NODE_NAMES

    for name, node in ast_classes.items():
        is_dataclass = any(
            (isinstance(decorator, ast.Name) and decorator.id == "dataclass")
            or (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "dataclass"
            )
            for decorator in node.decorator_list
        )
        fields = [item for item in node.body if isinstance(item, ast.AnnAssign)]
        assert is_dataclass, f"{name} must be a dataclass"
        assert fields, f"{name} must declare typed fields"


def test_py_006_runtime_core_minimizes_async_framework_dependencies() -> None:
    async_frameworks = {
        "anyio",
        "asyncio",
        "curio",
        "trio",
        "twisted",
        "uvloop",
    }
    violations: dict[str, list[str]] = {}
    for module_name in ("runtime.py", "runtime_models.py"):
        imported = absolute_imports(NSL / module_name)
        forbidden = sorted(
            module
            for module in imported
            if module.split(".", 1)[0] in async_frameworks
        )
        if forbidden:
            violations[module_name] = forbidden

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"].get("dependencies", [])
    forbidden_dependencies = sorted(
        dependency
        for dependency in dependencies
        if dependency.split("[", 1)[0].split("=", 1)[0].lower() in async_frameworks
    )
    assert not violations, f"Runtime imports async frameworks: {violations}"
    assert not forbidden_dependencies


def test_sec_001_eval_is_forbidden() -> None:
    violations = sorted(
        str(path.relative_to(NSL))
        for path in NSL.rglob("*.py")
        if "eval" in direct_function_calls(path)
    )
    assert not violations, f"eval() is forbidden in NSL modules: {violations}"


def test_rt_007_runtime_kernel_never_calls_python_eval() -> None:
    violations = sorted(
        module_name
        for module_name in RUNTIME_KERNEL_MODULES
        if "eval" in direct_function_calls(NSL / module_name)
    )
    assert not violations, f"Runtime kernel calls eval(): {violations}"


def test_sec_002_exec_is_forbidden() -> None:
    violations = sorted(
        str(path.relative_to(NSL))
        for path in NSL.rglob("*.py")
        if "exec" in direct_function_calls(path)
    )
    assert not violations, f"exec() is forbidden in NSL modules: {violations}"


def test_rt_008_runtime_kernel_never_calls_python_exec() -> None:
    violations = sorted(
        module_name
        for module_name in RUNTIME_KERNEL_MODULES
        if "exec" in direct_function_calls(NSL / module_name)
    )
    assert not violations, f"Runtime kernel calls exec(): {violations}"


def test_rt_009_runtime_forbids_arbitrary_python_object_reflection() -> None:
    forbidden_functions = {
        "__import__",
        "compile",
        "delattr",
        "dir",
        "getattr",
        "globals",
        "hasattr",
        "locals",
        "setattr",
        "vars",
    }
    forbidden_methods = {"__getattribute__", "__subclasses__"}
    violations: dict[str, list[str]] = {}
    for module_name in ("runtime.py", "runtime_models.py"):
        path = NSL / module_name
        found = sorted(
            (direct_function_calls(path) & forbidden_functions)
            | (called_attributes(path) & forbidden_methods)
        )
        if found:
            violations[module_name] = found
    assert not violations, f"Runtime uses reflection APIs: {violations}"


def test_rt_010_runtime_evaluator_directly_dispatches_every_ir_expression() -> None:
    tree = ast.parse((NSL / "runtime.py").read_text(encoding="utf-8"))
    runtime = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RuntimeEngine"
    )
    evaluators = [
        node
        for node in runtime.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in {"_evaluate", "_evaluate_ir"}
    ]
    dispatched: set[str] = set()
    for evaluator in evaluators:
        for call in ast.walk(evaluator):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "isinstance"
                and len(call.args) >= 2
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == "expression"
            ):
                continue
            type_argument = call.args[1]
            if isinstance(type_argument, ast.Name):
                dispatched.add(type_argument.id)
            elif isinstance(type_argument, ast.Tuple):
                dispatched.update(
                    item.id for item in type_argument.elts if isinstance(item, ast.Name)
                )
    expected = {
        "BinaryExpr",
        "CallExpr",
        "FieldExpr",
        "LiteralExpr",
        "ProjectionExpr",
        "ReadExpr",
        "SymbolRefExpr",
    }
    assert dispatched == expected
    assert "eval" not in direct_function_calls(NSL / "runtime.py")
    assert "exec" not in direct_function_calls(NSL / "runtime.py")


def test_rt_012_runtime_has_no_source_ast_interpreter_path() -> None:
    forbidden_modules = {"compiler", "source", "syntax"}
    violations: dict[str, list[str]] = {}
    for module_name in ("runtime", "runtime_models"):
        path = NSL / f"{module_name}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = sorted(local_imports(module_name) & forbidden_modules)
        ast_names = sorted(
            {
                node.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Name) and node.id.startswith("Ast")
            }
        )
        if found or ast_names:
            violations[module_name] = found + ast_names
    assert not violations, f"Runtime exposes a Source AST path: {violations}"


def test_sec_003_arbitrary_module_import_is_forbidden() -> None:
    violations: dict[str, list[str]] = {}
    for path in NSL.rglob("*.py"):
        forbidden = sorted(
            module
            for module in absolute_imports(path)
            if module.split(".", 1)[0] not in ALLOWED_EXTERNAL_MODULES
        )
        calls = direct_function_calls(path)
        if "__import__" in calls:
            forbidden.append("__import__()")
        if forbidden:
            violations[str(path.relative_to(NSL))] = forbidden
    assert not violations, f"NSL imports modules outside the allowlist: {violations}"


def test_sec_011_cryptography_is_isolated_to_ed25519_adapter() -> None:
    adapter = NSL / "adapters" / "ed25519.py"
    assert "cryptography" in {
        module.split(".", 1)[0] for module in absolute_imports(adapter)
    }
    violations = {
        str(path.relative_to(NSL))
        for path in NSL.rglob("*.py")
        if path != adapter
        and "cryptography"
        in {module.split(".", 1)[0] for module in absolute_imports(path)}
    }
    assert not violations

    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["dependencies"]
    assert dependencies == ["cryptography>=49.0.0,<50.0.0"]


def test_sec_004_runtime_has_no_arbitrary_filesystem_access() -> None:
    violations: dict[str, list[str]] = {}
    for module_name in sorted(RUNTIME_KERNEL_MODULES):
        path = NSL / module_name
        forbidden = sorted(
            module
            for module in absolute_imports(path)
            if module.split(".", 1)[0] in FILESYSTEM_MODULES
        )
        if "open" in direct_function_calls(path):
            forbidden.append("open()")
        if forbidden:
            violations[module_name] = forbidden
    assert not violations, f"Runtime accesses filesystem directly: {violations}"


def test_sec_005_runtime_has_no_arbitrary_network_access() -> None:
    violations: dict[str, list[str]] = {}
    for module_name in sorted(RUNTIME_KERNEL_MODULES):
        forbidden = sorted(
            module
            for module in absolute_imports(NSL / module_name)
            if module.split(".", 1)[0] in NETWORK_MODULES
        )
        if forbidden:
            violations[module_name] = forbidden
    assert not violations, f"Runtime accesses network directly: {violations}"


def test_sec_006_runtime_has_no_arbitrary_sql_execution() -> None:
    sql_api_names = {"cursor", "executemany", "executescript"}
    violations: dict[str, list[str]] = {}
    for module_name in sorted(RUNTIME_KERNEL_MODULES):
        path = NSL / module_name
        forbidden = sorted(
            module
            for module in absolute_imports(path)
            if module.split(".", 1)[0] in DATABASE_MODULES
        )
        forbidden.extend(sorted(called_attributes(path) & sql_api_names))
        if forbidden:
            violations[module_name] = forbidden
    assert not violations, f"Runtime executes SQL directly: {violations}"
