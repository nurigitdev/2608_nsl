"""Generate R3-S04 hash expectations from the frozen Python Oracle."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sys


def artifact(encoded: bytes) -> str:
    return "sha256:" + sha256(encoded).hexdigest()


def main(oracle_root: Path, corpus_root: Path) -> None:
    sys.path.insert(0, str(oracle_root.resolve()))
    from nsl.integrity import source_manifest_sha256
    from nsl.ir import BuildSource, NsoCodec
    from nsl.trust import VerifiedSkillArtifact

    print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    for version in ("1.0", "1.1"):
        raw = (corpus_root / f"ir-{version.replace('.', '-')}-canonical" / "expected.nso").read_bytes()
        skill = NsoCodec.decode(raw)
        assert NsoCodec.encode(skill) == raw
        manifest = source_manifest_sha256(skill.build.sources)
        assert manifest == skill.build.source_bundle_sha256
        verified = VerifiedSkillArtifact(skill, artifact(raw), None)
        print(version, manifest, skill.with_computed_hash().semantic_hash, verified.artifact_hash, sep="\t")
        if version == "1.0":
            read = skill
        else:
            write = skill

    extra = BuildSource('fragment/예산 "α".ns', "sha256:" + "07" * 32, 2**64, False)
    entries = (*read.build.sources, extra)
    for label, sources in (("ordered", entries), ("reversed", tuple(reversed(entries))), ("empty", ())):
        print(label, source_manifest_sha256(sources), sep="\t")

    build = replace(read.build, sources=entries, source_bundle_sha256=source_manifest_sha256(entries))
    rebuilt = replace(read, build=build)
    assert NsoCodec.decode(NsoCodec.encode(rebuilt)) == rebuilt
    print("rebuilt", artifact(NsoCodec.encode(rebuilt)), sep="\t")

    approval, *body = write.body
    approval = replace(approval, policy=replace(approval.policy, ttl_seconds=1))
    approved = replace(write, body=(approval, *body)).with_computed_hash()
    print("ttl", approved.semantic_hash, artifact(NsoCodec.encode(approved)), sep="\t")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
