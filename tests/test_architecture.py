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
    "core.py",
    "ir.py",
    "replay.py",
    "runtime.py",
    "runtime_models.py",
    "security.py",
}
LANGUAGE_CORE_MODULES = {
    "compiler.py",
    "core.py",
    "diagnostics.py",
    "ir.py",
    "source.py",
    "syntax.py",
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
    "asyncio",
    "copy",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "hashlib",
    "json",
    "pathlib",
    "traceback",
    "typing",
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
    "AstSkill",
}


def local_imports(module_name: str) -> set[str]:
    tree = ast.parse((NSL / f"{module_name}.py").read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


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
        ("syntax", {"ir", "compiler", "runtime", "tools", "audit", "replay"}),
        ("ir", {"syntax", "compiler", "runtime", "audit", "replay"}),
        ("compiler", {"runtime", "runtime_models", "audit", "replay"}),
        ("runtime", {"syntax", "compiler"}),
        ("runtime_models", {"syntax", "compiler"}),
    ],
)
def test_module_dependency_boundaries(module_name, forbidden) -> None:
    violations = local_imports(module_name) & forbidden
    assert not violations, f"{module_name} imports forbidden modules: {sorted(violations)}"


def test_arc_004_runtime_has_no_llm_dependency() -> None:
    violations: dict[str, list[str]] = {}
    for path in NSL.glob("*.py"):
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

    assert "ToolExecutionPort" in imported_tool_names
    assert "MockToolExecutor" not in imported_tool_names
    assert port_calls, "Runtime must route business-system access through tools.execute"


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


def test_arc_007_nsl_is_an_independent_python_package() -> None:
    violations: dict[str, list[str]] = {}
    for path in NSL.glob("*.py"):
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
    assert project["project"].get("dependencies", []) == []
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


def test_sec_001_eval_is_forbidden() -> None:
    violations = sorted(
        path.name
        for path in NSL.glob("*.py")
        if "eval" in direct_function_calls(path)
    )
    assert not violations, f"eval() is forbidden in NSL modules: {violations}"


def test_sec_002_exec_is_forbidden() -> None:
    violations = sorted(
        path.name
        for path in NSL.glob("*.py")
        if "exec" in direct_function_calls(path)
    )
    assert not violations, f"exec() is forbidden in NSL modules: {violations}"


def test_sec_003_arbitrary_module_import_is_forbidden() -> None:
    violations: dict[str, list[str]] = {}
    for path in NSL.glob("*.py"):
        forbidden = sorted(
            module
            for module in absolute_imports(path)
            if module.split(".", 1)[0] not in ALLOWED_EXTERNAL_MODULES
        )
        calls = direct_function_calls(path)
        if "__import__" in calls:
            forbidden.append("__import__()")
        if forbidden:
            violations[path.name] = forbidden
    assert not violations, f"NSL imports modules outside the allowlist: {violations}"


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
