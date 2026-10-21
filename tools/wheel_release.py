from __future__ import annotations

import argparse
import base64
import configparser
import csv
from dataclasses import asdict, dataclass
from email.parser import BytesParser
from email.policy import default
import hashlib
from io import StringIO
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from zipfile import BadZipFile, ZipFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nsl._version import __version__


DISTRIBUTION_NAME = "nex-skill-language"
NORMALIZED_NAME = "nex_skill_language"
EXPECTED_DEPENDENCY = "cryptography>=46.0.7,<47.0.0"
EXPECTED_PYTHON = ">=3.12"
EXPECTED_TAG = "py3-none-any"
REPRODUCIBLE_BUILD_EPOCH = "315532800"
REQUIRED_FILES = frozenset(
    {
        "nsl/__init__.py",
        "nsl/_version.py",
        "nsl/api/__init__.py",
        "nsl/cli.py",
    }
)


class WheelReleaseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WheelInspection:
    distribution: str
    version: str
    filename: str
    sha256: str
    size_bytes: int
    python_requirement: str
    runtime_dependencies: tuple[str, ...]
    tags: tuple[str, ...]
    console_scripts: tuple[str, ...]
    archive_file_count: int


def inspect_wheel(path: Path, *, expected_version: str = __version__) -> WheelInspection:
    if not isinstance(path, Path):
        raise WheelReleaseError("wheel path must be a Path")
    if not path.is_file():
        raise WheelReleaseError(f"wheel does not exist: {path}")
    expected_filename_prefix = f"{NORMALIZED_NAME}-{expected_version}-"
    if path.suffix != ".whl" or not path.name.startswith(expected_filename_prefix):
        raise WheelReleaseError("wheel filename does not match the NSL release version")

    try:
        with ZipFile(path, "r") as archive:
            names = tuple(archive.namelist())
            if not names:
                raise WheelReleaseError("wheel archive is empty")
            if len(names) != len(set(names)):
                raise WheelReleaseError("wheel archive contains duplicate paths")
            _validate_archive_paths(names)

            dist_info = f"{NORMALIZED_NAME}-{expected_version}.dist-info"
            metadata_path = f"{dist_info}/METADATA"
            wheel_path = f"{dist_info}/WHEEL"
            entry_points_path = f"{dist_info}/entry_points.txt"
            record_path = f"{dist_info}/RECORD"
            required = REQUIRED_FILES | {
                metadata_path,
                wheel_path,
                entry_points_path,
                record_path,
            }
            missing = sorted(required.difference(names))
            if missing:
                raise WheelReleaseError(
                    "wheel is missing required files: " + ", ".join(missing)
                )

            _validate_top_level_content(names, dist_info)
            metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_path))
            _validate_metadata(metadata, expected_version)
            tags = _validate_wheel_metadata(archive.read(wheel_path))
            scripts = _validate_entry_points(archive.read(entry_points_path))
            _validate_record(archive, record_path, names)
    except BadZipFile as error:
        raise WheelReleaseError("wheel is not a valid ZIP archive") from error

    dependencies = tuple(metadata.get_all("Requires-Dist", []))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return WheelInspection(
        distribution=metadata["Name"],
        version=metadata["Version"],
        filename=path.name,
        sha256=digest,
        size_bytes=path.stat().st_size,
        python_requirement=metadata["Requires-Python"],
        runtime_dependencies=dependencies,
        tags=tags,
        console_scripts=scripts,
        archive_file_count=len(names),
    )


def build_wheel(project_root: Path, output_dir: Path) -> Path:
    root = project_root.resolve()
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        raise WheelReleaseError(f"pyproject.toml not found under {root}")
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    build_temp = root / "test" / "wheel-build-temp"
    build_temp.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["TEMP"] = str(build_temp)
    environment["TMP"] = str(build_temp)
    environment["SOURCE_DATE_EPOCH"] = REPRODUCIBLE_BUILD_EPOCH

    expected_pattern = f"{NORMALIZED_NAME}-{__version__}-*.whl"
    for stale in destination.glob(expected_pattern):
        stale.unlink()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(destination),
        ],
        cwd=root,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise WheelReleaseError(f"wheel build failed with exit code {result.returncode}")
    candidates = sorted(destination.glob(expected_pattern))
    if len(candidates) != 1:
        raise WheelReleaseError(
            f"wheel build produced {len(candidates)} matching artifacts; expected exactly one"
        )
    return candidates[0]


def write_checksum(path: Path, sha256: str) -> Path:
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise WheelReleaseError("sha256 must be 64 lowercase hexadecimal characters")
    checksum_path = path.with_name(f"{path.name}.sha256")
    checksum_path.write_text(f"{sha256}  {path.name}\n", encoding="ascii", newline="\n")
    return checksum_path


def _validate_archive_paths(names: tuple[str, ...]) -> None:
    for name in names:
        pure = PurePosixPath(name)
        if not name or name.startswith("/") or "\\" in name or ".." in pure.parts:
            raise WheelReleaseError(f"wheel contains unsafe archive path: {name!r}")
        if name.endswith(".pyc") or "__pycache__" in pure.parts:
            raise WheelReleaseError(f"wheel contains generated Python bytecode: {name}")


def _validate_top_level_content(names: tuple[str, ...], dist_info: str) -> None:
    allowed_roots = {"nsl", dist_info}
    roots = {PurePosixPath(name).parts[0] for name in names}
    unexpected = sorted(roots.difference(allowed_roots))
    if unexpected:
        raise WheelReleaseError(
            "wheel contains unexpected top-level content: " + ", ".join(unexpected)
        )


def _normalize_requirement(value: str) -> str:
    compact = "".join(value.lower().split())
    name_end = min(
        (index for index, character in enumerate(compact) if character in "<>=!~[;"),
        default=len(compact),
    )
    name = compact[:name_end]
    specifiers = compact[name_end:]
    if not specifiers:
        return name
    return name + ",".join(sorted(specifiers.split(",")))


def _validate_metadata(metadata, expected_version: str) -> None:
    if metadata["Name"] != DISTRIBUTION_NAME:
        raise WheelReleaseError("wheel distribution name is not nex-skill-language")
    if metadata["Version"] != expected_version:
        raise WheelReleaseError("wheel metadata version does not match the release version")
    if metadata["Requires-Python"] != EXPECTED_PYTHON:
        raise WheelReleaseError("wheel Python requirement must be >=3.12")
    dependencies = metadata.get_all("Requires-Dist", [])
    normalized = {_normalize_requirement(item) for item in dependencies}
    if normalized != {_normalize_requirement(EXPECTED_DEPENDENCY)}:
        raise WheelReleaseError("wheel runtime dependency set is not the approved release set")


def _validate_wheel_metadata(data: bytes) -> tuple[str, ...]:
    metadata = BytesParser(policy=default).parsebytes(data)
    if metadata["Wheel-Version"] != "1.0":
        raise WheelReleaseError("unsupported wheel metadata version")
    if metadata["Root-Is-Purelib"] != "true":
        raise WheelReleaseError("NSL release wheel must be pure Python")
    tags = tuple(metadata.get_all("Tag", []))
    if tags != (EXPECTED_TAG,):
        raise WheelReleaseError("NSL release wheel must use the py3-none-any tag")
    return tags


def _validate_entry_points(data: bytes) -> tuple[str, ...]:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(data.decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error) as error:
        raise WheelReleaseError("wheel entry points are malformed") from error
    scripts = dict(parser.items("console_scripts")) if parser.has_section("console_scripts") else {}
    if scripts != {"nsl": "nsl.cli:main"}:
        raise WheelReleaseError("wheel must expose only the approved nsl console script")
    return tuple(f"{name}={target}" for name, target in sorted(scripts.items()))


def _validate_record(archive: ZipFile, record_path: str, names: tuple[str, ...]) -> None:
    try:
        rows = list(csv.reader(StringIO(archive.read(record_path).decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as error:
        raise WheelReleaseError("wheel RECORD is malformed") from error
    if any(len(row) != 3 for row in rows):
        raise WheelReleaseError("wheel RECORD rows must contain path, hash, and size")
    records = {row[0]: (row[1], row[2]) for row in rows}
    if len(records) != len(rows) or set(records) != set(names):
        raise WheelReleaseError("wheel RECORD does not exactly cover archive files")
    for name in names:
        digest, size = records[name]
        if name == record_path:
            if digest or size:
                raise WheelReleaseError("wheel RECORD must not hash itself")
            continue
        content = archive.read(name)
        expected = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
        if digest != f"sha256={expected}" or size != str(len(content)):
            raise WheelReleaseError(f"wheel RECORD integrity mismatch: {name}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and verify the NSL v0.2 wheel")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--verify", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        wheel = arguments.verify or build_wheel(PROJECT_ROOT, arguments.output_dir)
        inspection = inspect_wheel(wheel)
        checksum = write_checksum(wheel, inspection.sha256)
        payload = asdict(inspection)
        payload["checksum_file"] = str(checksum)
        payload["status"] = "VERIFIED"
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, WheelReleaseError) as error:
        print(
            json.dumps(
                {"error": {"code": "WHEEL_RELEASE_ERROR", "message": str(error)}},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
