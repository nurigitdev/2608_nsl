"""Record the frozen Oracle's outcomes at the public NSO byte boundary."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys


def digest(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


def splice(before: bytes, after: bytes) -> tuple[int, int, str]:
    start = next((i for i, pair in enumerate(zip(before, after)) if pair[0] != pair[1]),
                 min(len(before), len(after)))
    tail = next((i for i, pair in enumerate(zip(reversed(before[start:]), reversed(after[start:])))
                 if pair[0] != pair[1]), min(len(before), len(after)) - start)
    return start, len(before) - start - tail, after[start:len(after) - tail].hex() or "-"


def main(oracle_root: Path, corpus: Path) -> None:
    sys.path.insert(0, str(oracle_root.resolve()))
    from nsl.data_protection import CredentialMaterialError
    from nsl.integrity import source_manifest_sha256
    from nsl.ir import NsoCodec, NsoIntegrityError, canonical_json
    from nsl.ir_schema import NsoSchemaError, load_nso_json

    reason_to_code = {
        "semantic hash mismatch": "semantic",
        "source bundle manifest hash mismatch": "manifest",
        "source bundle hash differs between hashes and build": "bundle",
    }

    def reject(label: str, path: str, before: bytes, after: bytes, error: ValueError) -> None:
        path_detail = "-"
        if isinstance(error, NsoSchemaError):
            code, path_detail = "schema", error.path
        elif isinstance(error, CredentialMaterialError):
            code = "credential"
        elif isinstance(error, NsoIntegrityError):
            code = reason_to_code[str(error)]
        else:
            raise error
        print("reject", label, path, code, path_detail, digest(after), *splice(before, after), sep="\t")

    print("# Public NsoCodec from Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    for file in sorted(corpus.rglob("*.nso")):
        path = file.relative_to(corpus).as_posix()
        raw = file.read_bytes()
        try:
            skill = NsoCodec.decode(raw)
        except NsoIntegrityError as error:
            reject("frozen_tamper", path, raw, raw, error)
        else:
            encoded = NsoCodec.encode(skill)
            manifest = source_manifest_sha256(skill.build.sources)
            assert encoded == raw
            assert manifest == skill.build.source_bundle_sha256
            print("roundtrip", path, skill.ir_version, digest(encoded), manifest,
                  skill.with_computed_hash().semantic_hash, sep="\t")

    for version in ("1.0", "1.1"):
        path = f"nso/ir-{version.replace('.', '-')}-canonical/expected.nso"
        before = (corpus / path).read_bytes()
        base = load_nso_json(before)
        bad_digest = "sha256:" + "0" * 64
        mutations = [
            ("changed_limit", ("limits", "tool_calls"), base["limits"]["tool_calls"] + 1),
            ("wrong_semantic", ("hashes", "semantic_sha256"), bad_digest),
            ("malformed_digest", ("hashes", "semantic_sha256"), "sha256:uppercase"),
            ("wrong_bundle_header", ("hashes", "source_bundle_sha256"), bad_digest),
            ("changed_manifest", ("build", "sources", 0, "sha256"), bad_digest),
            ("credential_nested_key", ("debug_fixture",), [{"API-Key": "demo-placeholder"}]),
            ("unsupported_ir", ("ir_version",), "1.2" if version == "1.0" else "2.0"),
        ]
        if version == "1.1":
            mutations.extend([
                ("unknown_write_risk", ("requires", 0, "risk_level"), "R6"),
                ("read_only_tool_for_write", ("requires", 0, "capability"), "READ"),
                ("missing_approval_target", ("body", 1, "value", "approval_ref"), "MISSING"),
                ("disabled_approval", ("body", 0, "policy", "required"), False),
                ("credential_in_approval", ("body", 0, "message"), "Bearer DEMO.STUB"),
            ])
        for label, fields, value in mutations:
            document = deepcopy(base)
            item = document
            for field in fields[:-1]:
                item = item[field]
            item[fields[-1]] = value
            after = canonical_json(document)
            try:
                NsoCodec.decode(after)
            except (NsoSchemaError, CredentialMaterialError, NsoIntegrityError) as error:
                reject(f"{version}_{label}", path, before, after, error)
            else:
                raise AssertionError(f"Oracle unexpectedly admitted {label}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
