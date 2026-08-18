from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NSL = ROOT / "nsl"


def local_imports(module_name: str) -> set[str]:
    tree = ast.parse((NSL / f"{module_name}.py").read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


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

