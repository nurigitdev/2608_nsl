from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = (
    ROOT / "examples" / "project_operations" / "artifact_manifest.json",
    ROOT / "examples" / "project_operations" / "write_artifact_manifest.json",
    ROOT / "examples" / "voucher_operations" / "artifact_manifest.json",
)


def test_git_attributes_pin_manifest_source_types_to_lf() -> None:
    rules = {
        line.strip()
        for line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "*.json text eol=lf" in rules
    assert "*.ns text eol=lf" in rules


@pytest.mark.parametrize("manifest_path", MANIFESTS, ids=lambda path: path.parent.name)
def test_manifest_bound_sources_have_platform_stable_lf_bytes(
    manifest_path: Path,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bound_paths = [manifest_path.parent / manifest["tool_catalog"]["path"]]
    bound_paths.extend(
        manifest_path.parent / artifact["path"] for artifact in manifest["skills"]
    )

    assert bound_paths
    for path in bound_paths:
        content = path.read_bytes()
        assert content
        assert b"\r" not in content, f"{path} must use LF-only source bytes"
        assert content.endswith(b"\n"), f"{path} must end with one LF newline"
