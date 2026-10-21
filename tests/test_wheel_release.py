from __future__ import annotations

import base64
import csv
import hashlib
from io import StringIO
from pathlib import Path
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from nsl import __version__
from nsl.api import __version__ as api_version
from tools.wheel_release import WheelReleaseError, inspect_wheel, write_checksum


DIST_INFO = "nex_skill_language-0.2.0.dist-info"
ROOT = Path(__file__).resolve().parents[1]


def _record(contents: dict[str, bytes]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, data in contents.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        writer.writerow((name, f"sha256={digest}", str(len(data))))
    writer.writerow((f"{DIST_INFO}/RECORD", "", ""))
    return output.getvalue().encode("utf-8")


def _wheel(
    tmp_path: Path,
    *,
    metadata_version: str = "0.2.0",
    dependency: str = "cryptography<47.0.0,>=46.0.7",
    extra: tuple[tuple[str, bytes], ...] = (),
    omit: str | None = None,
    tamper_record_for: str | None = None,
) -> Path:
    metadata = (
        "Metadata-Version: 2.4\n"
        "Name: nex-skill-language\n"
        f"Version: {metadata_version}\n"
        "Requires-Python: >=3.12\n"
        f"Requires-Dist: {dependency}\n\n"
    ).encode("utf-8")
    contents = {
        "nsl/__init__.py": b"",
        "nsl/_version.py": b'__version__ = "0.2.0"\n',
        "nsl/api/__init__.py": b"",
        "nsl/cli.py": b"",
        f"{DIST_INFO}/METADATA": metadata,
        f"{DIST_INFO}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
        ),
        f"{DIST_INFO}/entry_points.txt": b"[console_scripts]\nnsl = nsl.cli:main\n",
    }
    contents.update(extra)
    if omit is not None:
        contents.pop(omit)
    record = _record(contents)
    if tamper_record_for is not None:
        target = f"sha256={base64.urlsafe_b64encode(hashlib.sha256(contents[tamper_record_for]).digest()).rstrip(b'=').decode('ascii')}"
        record = record.replace(target.encode("ascii"), b"sha256=invalid", 1)
    contents[f"{DIST_INFO}/RECORD"] = record

    path = tmp_path / "nex_skill_language-0.2.0-py3-none-any.whl"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return path


def test_distribution_version_is_shared_by_root_and_nex_api() -> None:
    assert __version__ == "0.2.0"
    assert api_version == __version__


def test_pyproject_uses_the_v020_single_version_source() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["name"] == "nex-skill-language"
    assert project["project"]["dynamic"] == ["version"]
    assert "version" not in project["project"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "nsl._version.__version__"
    }


def test_ci_uses_v020_identity_and_publishes_verified_wheel_evidence() -> None:
    workflow = (ROOT / ".github" / "workflows" / "nsl-quality.yml").read_text(
        encoding="utf-8"
    )

    assert "NSL v0.2 Quality and Wheel Gate" in workflow
    assert "nsl-v0.2-quality-${{ github.ref }}" in workflow
    assert "nsl-v0.2-release-evidence-${{ github.sha }}" in workflow
    assert "tools\\wheel_release.py" in workflow
    assert "Verify NeX Platform cryptography baseline" in workflow
    assert "cryptography.__version__ == '46.0.7'" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "./.venv/bin/python tools/run_quality.py" in workflow
    assert "dist/nex_skill_language-0.2.0-py3-none-any.whl" in workflow
    assert "nsl-v0.1-quality" not in workflow


def test_valid_release_wheel_has_approved_identity_and_integrity(tmp_path: Path) -> None:
    inspection = inspect_wheel(_wheel(tmp_path))

    assert inspection.distribution == "nex-skill-language"
    assert inspection.version == "0.2.0"
    assert inspection.python_requirement == ">=3.12"
    assert inspection.tags == ("py3-none-any",)
    assert inspection.console_scripts == ("nsl=nsl.cli:main",)
    assert inspection.runtime_dependencies == ("cryptography<47.0.0,>=46.0.7",)
    assert len(inspection.sha256) == 64


@pytest.mark.parametrize(
    "dependency",
    [
        "cryptography>=46.0.6,<47.0.0",
        "cryptography>=46.0.7,<46.0.8",
        "cryptography>=46.0.7,<47.0.1",
        "cryptography>=47.0.0,<48.0.0",
    ],
)
def test_release_wheel_rejects_dependency_range_boundaries(
    tmp_path: Path,
    dependency: str,
) -> None:
    with pytest.raises(WheelReleaseError, match="dependency set"):
        inspect_wheel(_wheel(tmp_path, dependency=dependency))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"metadata_version": "0.2.1"}, "metadata version"),
        ({"dependency": "cryptography>=46.0.7"}, "dependency set"),
        ({"omit": "nsl/api/__init__.py"}, "missing required files"),
        ({"extra": (("tests/test_leak.py", b""),)}, "unexpected top-level content"),
        ({"extra": (("../escape.py", b""),)}, "unsafe archive path"),
        ({"tamper_record_for": "nsl/cli.py"}, "RECORD integrity mismatch"),
    ],
)
def test_release_wheel_rejects_wrong_metadata_content_and_record(
    tmp_path: Path,
    change: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(WheelReleaseError, match=message):
        inspect_wheel(_wheel(tmp_path, **change))


def test_release_wheel_rejects_missing_and_non_path_inputs(tmp_path: Path) -> None:
    with pytest.raises(WheelReleaseError, match="must be a Path"):
        inspect_wheel("missing.whl")  # type: ignore[arg-type]
    with pytest.raises(WheelReleaseError, match="does not exist"):
        inspect_wheel(tmp_path / "missing.whl")


@pytest.mark.parametrize("digest", ["", "0" * 63, "0" * 65, "G" * 64])
def test_checksum_rejects_invalid_digest_boundaries(tmp_path: Path, digest: str) -> None:
    with pytest.raises(WheelReleaseError, match="64 lowercase hexadecimal"):
        write_checksum(tmp_path / "release.whl", digest)


def test_checksum_is_ascii_and_names_exact_wheel(tmp_path: Path) -> None:
    wheel = tmp_path / "release.whl"
    checksum = write_checksum(wheel, "a" * 64)

    assert checksum.name == "release.whl.sha256"
    assert checksum.read_bytes() == ("a" * 64 + "  release.whl\n").encode("ascii")
