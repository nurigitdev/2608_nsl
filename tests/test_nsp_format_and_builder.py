from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest

from nsl.compiler import NslCompiler
from nsl.nsp import NspBuildError, NspBuilder
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def _nso() -> bytes:
    return NslCompiler(build_tool_catalog()).compile(SOURCE).nso_bytes


def _renamed_nso(skill_id: str) -> bytes:
    source = SOURCE.replace(
        "skill FINANCE.PROJECT_BUDGET_CHECK",
        f"skill {skill_id}",
        1,
    )
    return NslCompiler(build_tool_catalog()).compile(source).nso_bytes


def test_pkg_001_nsp_builder_creates_deterministic_archive() -> None:
    builder = NspBuilder()

    first = builder.build((_nso(),))
    second = builder.build((_nso(),))

    assert first == second
    assert first.artifact_count == 1
    with ZipFile(BytesIO(first.data)) as archive:
        assert archive.namelist() == ["manifest.json", "skills/0001.nso"]
        member = archive.infolist()[1]
        assert member.date_time == (1980, 1, 1, 0, 0, 0)
        assert member.compress_type == ZIP_STORED
        assert archive.read(member) == _nso()


def test_pkg_001_builder_canonicalizes_valid_noncanonical_nso() -> None:
    canonical = _nso()
    noncanonical = json.dumps(json.loads(canonical), indent=2).encode("utf-8")

    canonical_package = NspBuilder().build((canonical,))
    normalized_package = NspBuilder().build((noncanonical,))

    assert normalized_package == canonical_package
    with ZipFile(BytesIO(normalized_package.data)) as archive:
        assert archive.read("skills/0001.nso") == canonical


@pytest.mark.parametrize("artifacts", [b"not-a-sequence", bytearray(), "bad", None])
def test_pkg_001_builder_rejects_non_sequence_artifact_inputs(artifacts) -> None:
    with pytest.raises(NspBuildError, match="sequence of NSO bytes"):
        NspBuilder().build(artifacts)  # type: ignore[arg-type]


@pytest.mark.parametrize("artifact", [b"", b"not-json", bytearray(), None])
def test_pkg_001_builder_rejects_malformed_or_non_byte_nso(artifact) -> None:
    message = "invalid NSO artifact" if type(artifact) is bytes else "must be NSO bytes"
    with pytest.raises(NspBuildError, match=message):
        NspBuilder().build((artifact,))  # type: ignore[arg-type]


@pytest.mark.parametrize("artifacts", [(), []])
def test_pkg_002_package_rejects_zero_nso_artifacts(artifacts) -> None:
    with pytest.raises(NspBuildError, match="at least one NSO artifact"):
        NspBuilder().build(artifacts)


def test_pkg_002_package_contains_every_nso_and_has_stable_order() -> None:
    first = _nso()
    second = _renamed_nso("FINANCE.ANNUAL_BUDGET_CHECK")

    forward = NspBuilder().build((first, second))
    reverse = NspBuilder().build((second, first))

    assert forward == reverse
    assert forward.artifact_count == 2
    with ZipFile(BytesIO(forward.data)) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "skills/0001.nso",
            "skills/0002.nso",
        ]
        assert {
            archive.read(path) for path in archive.namelist() if path.endswith(".nso")
        } == {first, second}


def test_pkg_003_package_contains_canonical_manifest() -> None:
    package = NspBuilder().build((_nso(),))

    expected = package.manifest.to_data()
    assert expected["format"] == "NSP"
    assert expected["schema_version"] == "1.0"
    assert package.manifest.to_data() == expected
    with ZipFile(BytesIO(package.data)) as archive:
        manifest_data = archive.read("manifest.json")
    assert json.loads(manifest_data) == expected
    assert manifest_data == json.dumps(
        expected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_pkg_004_manifest_stores_each_skill_id_and_version() -> None:
    annual = _renamed_nso("FINANCE.ANNUAL_BUDGET_CHECK")
    package = NspBuilder().build((_nso(), annual))

    assert [
        (entry.path, entry.skill_id, entry.skill_version)
        for entry in package.manifest.skills
    ] == [
        ("skills/0001.nso", "FINANCE.ANNUAL_BUDGET_CHECK", "1.0.0"),
        ("skills/0002.nso", "FINANCE.PROJECT_BUDGET_CHECK", "1.0.0"),
    ]


def test_pkg_004_duplicate_skill_identity_is_rejected() -> None:
    artifact = _nso()

    with pytest.raises(NspBuildError, match="duplicate Skill ID/Version"):
        NspBuilder().build((artifact, artifact))


def test_pkg_005_package_hash_covers_manifest_and_canonical_nso_bytes() -> None:
    artifact = _nso()
    package = NspBuilder().build((artifact,))
    entry = package.manifest.skills[0]

    expected_hash = "sha256:" + sha256(
        b"NSP-CONTENT-V1\x00"
        + json.dumps(
            package.manifest.content_data(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert entry.semantic_sha256.startswith("sha256:")
    assert entry.artifact_sha256 == "sha256:" + sha256(artifact).hexdigest()
    assert entry.size_bytes == len(artifact)
    assert package.package_hash == expected_hash
    assert package.manifest.computed_package_hash() == expected_hash
    assert package.manifest.to_data()["package_sha256"] == expected_hash


def test_pkg_005_package_hash_is_order_independent_and_content_sensitive() -> None:
    first = _nso()
    second = _renamed_nso("FINANCE.ANNUAL_BUDGET_CHECK")

    forward = NspBuilder().build((first, second))
    reverse = NspBuilder().build((second, first))
    changed = NspBuilder().build((first, _renamed_nso("FINANCE.MONTHLY_BUDGET_CHECK")))

    assert forward.package_hash == reverse.package_hash
    assert forward.data == reverse.data
    assert changed.package_hash != forward.package_hash
