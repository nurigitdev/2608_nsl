from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Sequence
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from .ir import NsoCodec, canonical_json


NSP_FORMAT = "NSP"
NSP_FORMAT_VERSION = "1.0"
_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_REGULAR_FILE_MODE = 0o100644 << 16
_PACKAGE_HASH_DOMAIN = b"NSP-CONTENT-V1\x00"


class NspBuildError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NspPackage:
    data: bytes
    artifact_count: int
    manifest: NspManifest

    @property
    def package_hash(self) -> str:
        return self.manifest.package_sha256


@dataclass(frozen=True, slots=True)
class NspSkillManifest:
    path: str
    skill_id: str
    skill_version: str
    semantic_sha256: str
    artifact_sha256: str
    size_bytes: int

    def to_data(self) -> dict[str, object]:
        return {
            "path": self.path,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "semantic_sha256": self.semantic_sha256,
            "artifact_sha256": self.artifact_sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class NspManifest:
    format: str
    schema_version: str
    skills: tuple[NspSkillManifest, ...]
    package_sha256: str

    @classmethod
    def create(cls, skills: tuple[NspSkillManifest, ...]) -> NspManifest:
        manifest = cls(
            format=NSP_FORMAT,
            schema_version=NSP_FORMAT_VERSION,
            skills=skills,
            package_sha256="",
        )
        return cls(
            format=manifest.format,
            schema_version=manifest.schema_version,
            skills=manifest.skills,
            package_sha256=manifest.computed_package_hash(),
        )

    def to_data(self) -> dict[str, object]:
        return {**self.content_data(), "package_sha256": self.package_sha256}

    def content_data(self) -> dict[str, object]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "skills": [skill.to_data() for skill in self.skills],
        }

    def computed_package_hash(self) -> str:
        return "sha256:" + sha256(
            _PACKAGE_HASH_DOMAIN + canonical_json(self.content_data())
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class _PreparedArtifact:
    skill_id: str
    skill_version: str
    semantic_hash: str
    data: bytes


class NspBuilder:
    """Builds deterministic NSP archives from validated NSO artifacts."""

    def build(self, artifacts: Sequence[bytes]) -> NspPackage:
        prepared = self._prepare_artifacts(artifacts)
        skill_members = tuple(
            (f"skills/{index:04d}.nso", artifact)
            for index, artifact in enumerate(prepared, start=1)
        )
        skills = tuple(
            NspSkillManifest(
                path=path,
                skill_id=artifact.skill_id,
                skill_version=artifact.skill_version,
                semantic_sha256=artifact.semantic_hash,
                artifact_sha256="sha256:" + sha256(artifact.data).hexdigest(),
                size_bytes=len(artifact.data),
            )
            for path, artifact in skill_members
        )
        manifest = NspManifest.create(skills)
        stream = BytesIO()
        with ZipFile(stream, mode="w", compression=ZIP_STORED) as archive:
            self._write_member(
                archive,
                "manifest.json",
                canonical_json(manifest.to_data()),
            )
            for path, artifact in skill_members:
                self._write_member(
                    archive,
                    path,
                    artifact.data,
                )
        return NspPackage(
            data=stream.getvalue(),
            artifact_count=len(prepared),
            manifest=manifest,
        )

    @staticmethod
    def _prepare_artifacts(artifacts: Sequence[bytes]) -> tuple[_PreparedArtifact, ...]:
        if isinstance(artifacts, (bytes, bytearray, str)) or not isinstance(
            artifacts, Sequence
        ):
            raise NspBuildError("NSP artifacts must be a sequence of NSO bytes")
        if not artifacts:
            raise NspBuildError("NSP package requires at least one NSO artifact")
        prepared: list[_PreparedArtifact] = []
        for index, artifact in enumerate(artifacts):
            if type(artifact) is not bytes:
                raise NspBuildError(
                    f"NSP artifact at index {index} must be NSO bytes"
                )
            try:
                skill = NsoCodec.decode(artifact)
                canonical_artifact = NsoCodec.encode(skill)
            except ValueError as error:
                raise NspBuildError(
                    f"invalid NSO artifact at index {index}"
                ) from error
            prepared.append(
                _PreparedArtifact(
                    skill_id=skill.skill_id,
                    skill_version=skill.skill_version,
                    semantic_hash=skill.semantic_hash,
                    data=canonical_artifact,
                )
            )
        ordered = tuple(
            sorted(
                prepared,
                key=lambda item: (item.skill_id, item.skill_version),
            )
        )
        identities = tuple(
            (artifact.skill_id, artifact.skill_version) for artifact in ordered
        )
        if len(identities) != len(set(identities)):
            raise NspBuildError("NSP package contains a duplicate Skill ID/Version")
        return ordered

    @staticmethod
    def _write_member(archive: ZipFile, path: str, data: bytes) -> None:
        member = ZipInfo(path, date_time=_ARCHIVE_TIMESTAMP)
        member.compress_type = ZIP_STORED
        member.create_system = 3
        member.external_attr = _REGULAR_FILE_MODE
        archive.writestr(member, data)
