"""Verify the versioned compatibility corpus, its files, and canonical goldens."""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path, PureWindowsPath
import sys
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

from nsl.ir import NsoCodec, NsoIntegrityError, canonical_json
from nsl.ir_schema import load_nso_json


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "compatibility/v0_2_1"
LOCK = ROOT / "rust-transition/baseline/python-oracle-lock.json"


def read_descriptor(data: bytes, name: str) -> Any:
    if b"\r" in data or not data.endswith(b"\n"):
        raise ValueError(f"{name}: expected LF line endings and a final LF")
    return load_nso_json(data)


def read_reference(root: Path, reference: dict[str, str]) -> bytes:
    path = root
    for component in reference["path"].split("/"):
        if PureWindowsPath(component).is_reserved():
            raise ValueError(f"reserved path component: {component}")
        path /= component
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"linked corpus path: {path}")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError(f"missing or out-of-root corpus file: {path}")
    data = path.read_bytes()
    if sha256(data).hexdigest() != reference["sha256"]:
        raise ValueError(f"SHA-256 mismatch: {path}")
    return data


def verify(root: Path) -> tuple[int, str]:
    root = root.resolve()
    schema_path = CORPUS / "manifest.schema.json"
    schema = load_nso_json(schema_path.read_bytes())
    Draft202012Validator.check_schema(schema)
    schema_uri = schema_path.as_uri()
    registry = Registry().with_resource(schema_uri, Resource.from_contents(schema))
    case_validator = Draft202012Validator({"$ref": f"{schema_uri}#/$defs/case"}, registry=registry)

    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError(f"expected a regular corpus manifest: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = read_descriptor(manifest_bytes, "manifest.json")
    Draft202012Validator(schema).validate(manifest)
    if manifest["oracle_commit"] != load_nso_json(LOCK.read_bytes())["commit"]:
        raise ValueError("manifest Oracle commit differs from the lock")

    seen: set[str] = set()
    for entry in manifest["cases"]:
        case_id = entry["case_id"]
        category = entry["category"]
        if case_id in seen:
            raise ValueError(f"duplicate case ID: {case_id}")
        seen.add(case_id)
        if entry["path"] != f"{category}/{case_id}/case.json":
            raise ValueError(f"unexpected descriptor path for {case_id}")
        case_root = root / category / case_id
        case = read_descriptor(read_reference(root, entry), entry["path"])
        case_validator.validate(case)
        if (case["case_id"], case["category"]) != (case_id, category):
            raise ValueError(f"descriptor identity differs from manifest: {case_id}")

        artifacts = {}
        for group in ("inputs", "expected_artifacts"):
            for role, reference in case[group].items():
                data = read_reference(case_root, reference)
                if group == "inputs" and role == "untrusted_nso":
                    try:
                        NsoCodec.decode(data)
                    except NsoIntegrityError as error:
                        expected = {"code": "NSO_INTEGRITY_ERROR", "phase": "nso_decode"}
                        if expected != case["expected_error"] or str(error) != "semantic hash mismatch":
                            raise ValueError(f"unexpected NSO rejection in {case_id}") from error
                    else:
                        raise ValueError(f"untrusted NSO was accepted in {case_id}")
                elif reference["path"].endswith(".nso"):
                    if NsoCodec.encode(NsoCodec.decode(data)) != data:
                        raise ValueError(f"noncanonical NSO in {case_id}: {reference['path']}")
                if group == "expected_artifacts":
                    artifacts[role] = data

        hashes = case["expected_hashes"]
        if "nso" in artifacts:
            data = artifacts["nso"]
            skill = NsoCodec.decode(data)
            observed = {
                "artifact_hash": sha256(data).hexdigest(),
                "semantic_hash": skill.semantic_hash.removeprefix("sha256:"),
                "source_bundle_hash": skill.build.source_bundle_sha256.removeprefix("sha256:"),
            }
            if any(hashes.get(name) != value for name, value in observed.items()):
                raise ValueError(f"expected NSO hashes differ from the artifact: {case_id}")
        if "canonical_contract" in artifacts:
            data = artifacts["canonical_contract"]
            if (
                canonical_json(load_nso_json(data)) != data
                or hashes.get("tool_contract_hash") != sha256(data).hexdigest()
            ):
                raise ValueError(f"canonical Tool Contract bytes or hash mismatch: {case_id}")

    return len(seen), sha256(manifest_bytes).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path, default=CORPUS, nargs="?")
    try:
        cases, digest = verify(parser.parse_args().corpus)
    except (OSError, ValueError, ValidationError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Verified {cases} compatibility cases. Manifest SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
