"""Check the resolved Cargo graph and the workspace unsafe-code policy."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import subprocess
import sys
import tomllib


WORKSPACE = Path(__file__).resolve().parents[1]

# A lower layer may only reach the listed workspace crates. Record reviewed
# external packages by (package name, Cargo source) when a Slice needs them.
LOWER_LAYERS: dict[str, set[str]] = {"nsl-core": set(), "nsl-ir": {"nsl-core"}}
APPROVED_EXTERNAL: dict[str, set[tuple[str, str]]] = {
    "nsl-core": {
        (name, "registry+https://github.com/rust-lang/crates.io-index")
        for name in ("autocfg", "bigdecimal", "libm", "num-bigint", "num-integer", "num-traits")
    },
    "nsl-ir": {
        (name, "registry+https://github.com/rust-lang/crates.io-index")
        for name in (
            "block-buffer", "cfg-if", "cpufeatures", "crypto-common", "digest",
            "hybrid-array", "libc", "sha2", "typenum",
        )
    },
}
FORBIDDEN_WORKSPACE = {
    "nsl-compiler": {"nsl-runtime"},
    "nsl-runtime": {"nsl-compiler", "nsl-source"},
}


def read_toml(path: Path) -> dict:
    with path.open("rb") as manifest:
        return tomllib.load(manifest)


def dependency_violations(metadata: dict) -> list[str]:
    packages = {package["id"]: package for package in metadata["packages"]}
    members = {packages[key]["name"]: key for key in metadata["workspace_members"]}
    member_ids = set(members.values())
    graph = {
        node["id"]: [edge["pkg"] for edge in node["deps"]]
        for node in metadata["resolve"]["nodes"]
    }
    violations = []
    for owner in (*LOWER_LAYERS, *FORBIDDEN_WORKSPACE):
        if owner not in members:
            violations.append(f"required workspace crate is missing: {owner}")
            continue
        root = members[owner]
        pending = deque([(root, (owner,), owner)])
        seen = {(root, owner)}
        while pending:
            current, chain, external_owner = pending.popleft()
            for key in graph[current]:
                package = packages[key]
                name = package["name"]
                source = package["source"]
                path = (*chain, name)
                next_owner = name if key in member_ids and name in LOWER_LAYERS else external_owner
                visit = (key, next_owner)
                if visit in seen:
                    continue
                seen.add(visit)
                forbidden = False
                if owner in LOWER_LAYERS:
                    if key in member_ids:
                        forbidden = name not in LOWER_LAYERS[owner]
                    else:
                        forbidden = source is None or (name, source) not in APPROVED_EXTERNAL[external_owner]
                elif key in member_ids:
                    forbidden = name in FORBIDDEN_WORKSPACE[owner]
                if forbidden:
                    description = " -> ".join(path)
                    if key not in member_ids:
                        description += f" ({source or 'non-workspace path'})"
                    violations.append(f"forbidden dependency: {description}")
                pending.append((key, path, next_owner))
    return violations


def unsafe_policy_violations(metadata: dict) -> list[str]:
    root = Path(metadata["workspace_root"])
    lints = read_toml(root / "Cargo.toml")["workspace"].get("lints", {})
    violations = []
    if lints.get("rust", {}).get("unsafe_code") != "forbid":
        violations.append("workspace must forbid unsafe_code")
    members = set(metadata["workspace_members"])
    for package in metadata["packages"]:
        if package["id"] in members:
            manifest = read_toml(Path(package["manifest_path"]))
            if manifest.get("lints", {}).get("workspace") is not True:
                violations.append(f"{package['name']} must inherit the workspace lints")
    return violations


def check(workspace: Path) -> list[str]:
    # Cargo includes dependencies for all target platforms by default. Enable
    # all features to include optional edges, and inspect resolved package IDs.
    output = subprocess.check_output(
        ["cargo", "metadata", "--format-version", "1", "--locked", "--all-features"],
        cwd=workspace,
    )
    metadata = json.loads(output)
    return sorted(dependency_violations(metadata) + unsafe_policy_violations(metadata))


def main() -> int:
    try:
        violations = check(WORKSPACE)
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as error:
        print(f"Could not inspect Rust architecture: {error}", file=sys.stderr)
        return 1
    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        return 1
    print("Rust architecture dependency guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
