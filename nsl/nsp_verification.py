from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from enum import StrEnum
from typing import Any
from zipfile import BadZipFile, LargeZipFile, ZIP_STORED, ZipFile, ZipInfo

from .ir import NsoCodec, SkillObject, canonical_json
from .json_boundary import load_strict_json
from .nsp import (
    NSP_FORMAT,
    NSP_FORMAT_VERSION,
    NspManifest,
    NspSkillManifest,
)
from .nsp_signatures import (
    NspSignatureError,
    NspSignatureMetadata,
    NspSignatureVerifier,
    decode_signature_metadata,
    signature_message,
)
from .security import RuntimeEnvironment


class NspVerificationCode(StrEnum):
    INVALID_ARCHIVE = "NSP_INVALID_ARCHIVE"
    PACKAGE_LIMIT_EXCEEDED = "NSP_PACKAGE_LIMIT_EXCEEDED"
    UNSAFE_MEMBER_PATH = "NSP_UNSAFE_MEMBER_PATH"
    INVALID_MANIFEST = "NSP_INVALID_MANIFEST"
    CONTENT_MISMATCH = "NSP_CONTENT_MISMATCH"
    INVALID_SIGNATURE_METADATA = "NSP_INVALID_SIGNATURE_METADATA"
    SIGNATURE_REQUIRED = "NSP_SIGNATURE_REQUIRED"
    SIGNATURE_VERIFIER_REQUIRED = "NSP_SIGNATURE_VERIFIER_REQUIRED"
    INVALID_SIGNATURE = "NSP_INVALID_SIGNATURE"


class NspVerificationError(ValueError):
    def __init__(self, code: NspVerificationCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class NspVerificationLimits:
    max_package_bytes: int = 64 * 1024 * 1024
    max_members: int = 1024
    max_member_bytes: int = 16 * 1024 * 1024
    max_total_uncompressed_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        values = (
            self.max_package_bytes,
            self.max_members,
            self.max_member_bytes,
            self.max_total_uncompressed_bytes,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in values
        ):
            raise ValueError("NSP verification limits must be positive integers")


@dataclass(frozen=True, slots=True)
class NspVerificationPolicy:
    environment: RuntimeEnvironment
    allow_unsigned_development: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.environment, RuntimeEnvironment):
            raise ValueError("NSP verification environment is invalid")
        if type(self.allow_unsigned_development) is not bool:
            raise ValueError("allow_unsigned_development must be boolean")
        if (
            self.environment is not RuntimeEnvironment.DEVELOPMENT
            and self.allow_unsigned_development
        ):
            raise ValueError(
                "unsigned packages can only be allowed in DEVELOPMENT"
            )

    def allows_unsigned(self) -> bool:
        return (
            self.environment is RuntimeEnvironment.DEVELOPMENT
            and self.allow_unsigned_development
        )


@dataclass(frozen=True, slots=True)
class VerifiedNspSkill:
    manifest: NspSkillManifest
    skill: SkillObject
    artifact: bytes


@dataclass(frozen=True, slots=True)
class VerifiedNspPackage:
    manifest: NspManifest
    skills: tuple[VerifiedNspSkill, ...]
    signature: NspSignatureMetadata | None

    @property
    def package_hash(self) -> str:
        return self.manifest.package_sha256


class NspVerifier:
    def __init__(
        self,
        *,
        policy: NspVerificationPolicy,
        limits: NspVerificationLimits | None = None,
        signature_verifier: NspSignatureVerifier | None = None,
    ) -> None:
        if not isinstance(policy, NspVerificationPolicy):
            raise ValueError("NSP verification policy is required")
        self.policy = policy
        self.limits = limits or NspVerificationLimits()
        if signature_verifier is not None and not isinstance(
            signature_verifier, NspSignatureVerifier
        ):
            raise ValueError("NSP signature_verifier does not implement the port")
        self.signature_verifier = signature_verifier

    def verify(self, data: bytes) -> VerifiedNspPackage:
        if type(data) is not bytes:
            raise NspVerificationError(
                NspVerificationCode.INVALID_ARCHIVE,
                "NSP package must be bytes",
            )
        if len(data) > self.limits.max_package_bytes:
            raise NspVerificationError(
                NspVerificationCode.PACKAGE_LIMIT_EXCEEDED,
                "NSP package byte limit exceeded",
            )
        try:
            with ZipFile(BytesIO(data), mode="r") as archive:
                infos = tuple(archive.infolist())
                self._validate_archive_members(infos)
                manifest_bytes = self._read_member(archive, "manifest.json")
                manifest = self._parse_manifest(manifest_bytes)
                actual_names = {info.filename for info in infos}
                expected_names = {"manifest.json"}
                expected_names.update(skill.path for skill in manifest.skills)
                signature = None
                if "signature.json" in actual_names:
                    try:
                        signature = decode_signature_metadata(
                            self._read_member(archive, "signature.json")
                        )
                    except NspSignatureError as error:
                        raise NspVerificationError(
                            NspVerificationCode.INVALID_SIGNATURE_METADATA,
                            "invalid NSP signature metadata",
                        ) from error
                    if signature.package_sha256 != manifest.package_sha256:
                        raise NspVerificationError(
                            NspVerificationCode.CONTENT_MISMATCH,
                            "NSP signature metadata package hash mismatch",
                        )
                    expected_names.add("signature.json")
                if signature is None and not self.policy.allows_unsigned():
                    raise NspVerificationError(
                        NspVerificationCode.SIGNATURE_REQUIRED,
                        "NSP package signature is required by policy",
                    )
                if actual_names != expected_names:
                    raise NspVerificationError(
                        NspVerificationCode.CONTENT_MISMATCH,
                        "NSP archive members differ from the manifest",
                    )
                skills = tuple(
                    self._verify_skill(
                        entry,
                        self._read_member(archive, entry.path),
                    )
                    for entry in manifest.skills
                )
                self._verify_signature(signature)
        except NspVerificationError:
            raise
        except (BadZipFile, LargeZipFile, RuntimeError, ValueError) as error:
            raise NspVerificationError(
                NspVerificationCode.INVALID_ARCHIVE,
                "invalid NSP ZIP archive",
            ) from error
        return VerifiedNspPackage(
            manifest=manifest,
            skills=skills,
            signature=signature,
        )

    def _verify_signature(
        self,
        signature: NspSignatureMetadata | None,
    ) -> None:
        if signature is None:
            return
        if self.signature_verifier is None:
            raise NspVerificationError(
                NspVerificationCode.SIGNATURE_VERIFIER_REQUIRED,
                "NSP signature verifier is required for signed packages",
            )
        try:
            verified = self.signature_verifier.verify(
                algorithm=signature.algorithm,
                key_id=signature.key_id,
                message=signature_message(
                    signature.package_sha256,
                    signature.algorithm,
                    signature.key_id,
                ),
                signature=signature.signature_bytes(),
            )
        except Exception as error:
            raise NspVerificationError(
                NspVerificationCode.INVALID_SIGNATURE,
                "NSP package signature verification failed",
            ) from error
        if verified is not True:
            raise NspVerificationError(
                NspVerificationCode.INVALID_SIGNATURE,
                "NSP package signature is invalid or untrusted",
            )

    def _validate_archive_members(self, infos: tuple[ZipInfo, ...]) -> None:
        if len(infos) > self.limits.max_members:
            raise NspVerificationError(
                NspVerificationCode.PACKAGE_LIMIT_EXCEEDED,
                "NSP member count limit exceeded",
            )
        names = tuple(info.filename for info in infos)
        if len(names) != len(set(names)):
            raise NspVerificationError(
                NspVerificationCode.INVALID_ARCHIVE,
                "NSP archive contains duplicate member names",
            )
        total_size = 0
        for info in infos:
            if info.orig_filename != info.filename:
                raise NspVerificationError(
                    NspVerificationCode.UNSAFE_MEMBER_PATH,
                    "NSP archive member path was normalized by ZIP decoding",
                )
            self._validate_member_path(info.orig_filename)
            self._validate_member_path(info.filename)
            if info.is_dir() or self._is_non_regular_unix_member(info):
                raise NspVerificationError(
                    NspVerificationCode.UNSAFE_MEMBER_PATH,
                    "NSP archive member must be a regular file",
                )
            if info.flag_bits & 0x1:
                raise NspVerificationError(
                    NspVerificationCode.INVALID_ARCHIVE,
                    "encrypted NSP members are forbidden",
                )
            if info.compress_type != ZIP_STORED:
                raise NspVerificationError(
                    NspVerificationCode.INVALID_ARCHIVE,
                    "compressed NSP members are forbidden in schema 1.0",
                )
            if info.file_size > self.limits.max_member_bytes:
                raise NspVerificationError(
                    NspVerificationCode.PACKAGE_LIMIT_EXCEEDED,
                    "NSP member byte limit exceeded",
                )
            total_size += info.file_size
            if total_size > self.limits.max_total_uncompressed_bytes:
                raise NspVerificationError(
                    NspVerificationCode.PACKAGE_LIMIT_EXCEEDED,
                    "NSP total uncompressed byte limit exceeded",
                )

    @staticmethod
    def _validate_member_path(path: str) -> None:
        if (
            not path
            or not path.isascii()
            or "\\" in path
            or "\x00" in path
            or ":" in path
        ):
            raise NspVerificationError(
                NspVerificationCode.UNSAFE_MEMBER_PATH,
                "NSP archive contains an unsafe member path",
            )
        parts = path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise NspVerificationError(
                NspVerificationCode.UNSAFE_MEMBER_PATH,
                "NSP archive contains an unsafe member path",
            )

    @staticmethod
    def _is_non_regular_unix_member(info: ZipInfo) -> bool:
        if info.create_system != 3:
            return False
        file_type = (info.external_attr >> 16) & 0o170000
        return file_type not in {0, 0o100000}

    @staticmethod
    def _read_member(archive: ZipFile, path: str) -> bytes:
        try:
            return archive.read(path)
        except (BadZipFile, KeyError, RuntimeError) as error:
            raise NspVerificationError(
                NspVerificationCode.INVALID_ARCHIVE,
                "NSP archive member cannot be read",
            ) from error

    def _parse_manifest(self, data: bytes) -> NspManifest:
        raw = load_strict_json(
            data,
            document_name="NSP manifest",
            error_factory=self._manifest_error,
        )
        root = self._object(
            raw,
            "$",
            {"format", "schema_version", "skills", "package_sha256"},
        )
        self._constant(root["format"], NSP_FORMAT, "$.format")
        self._constant(
            root["schema_version"],
            NSP_FORMAT_VERSION,
            "$.schema_version",
        )
        skills_raw = self._array(root["skills"], "$.skills")
        if not skills_raw:
            raise self._manifest_error("$.skills", "must not be empty")
        if len(skills_raw) > self.limits.max_members - 1:
            raise NspVerificationError(
                NspVerificationCode.PACKAGE_LIMIT_EXCEEDED,
                "NSP manifest skill count limit exceeded",
            )
        skills = tuple(
            self._parse_skill_entry(item, index)
            for index, item in enumerate(skills_raw, start=1)
        )
        identities = tuple(
            (skill.skill_id, skill.skill_version) for skill in skills
        )
        if len(identities) != len(set(identities)):
            raise self._manifest_error("$.skills", "duplicate Skill ID/Version")
        manifest = NspManifest(
            format=NSP_FORMAT,
            schema_version=NSP_FORMAT_VERSION,
            skills=skills,
            package_sha256=self._sha256(root["package_sha256"], "$.package_sha256"),
        )
        if manifest.package_sha256 != manifest.computed_package_hash():
            raise NspVerificationError(
                NspVerificationCode.CONTENT_MISMATCH,
                "NSP package hash mismatch",
            )
        if data != canonical_json(raw):
            raise self._manifest_error("$", "manifest must use canonical JSON")
        return manifest

    def _parse_skill_entry(self, value: Any, index: int) -> NspSkillManifest:
        path = f"$.skills[{index - 1}]"
        item = self._object(
            value,
            path,
            {
                "path",
                "skill_id",
                "skill_version",
                "semantic_sha256",
                "artifact_sha256",
                "size_bytes",
            },
        )
        member_path = self._string(item["path"], f"{path}.path")
        self._validate_member_path(member_path)
        expected_path = f"skills/{index:04d}.nso"
        if member_path != expected_path:
            raise self._manifest_error(
                f"{path}.path",
                f"expected canonical path {expected_path}",
            )
        return NspSkillManifest(
            path=member_path,
            skill_id=self._string(item["skill_id"], f"{path}.skill_id"),
            skill_version=self._string(
                item["skill_version"], f"{path}.skill_version"
            ),
            semantic_sha256=self._sha256(
                item["semantic_sha256"], f"{path}.semantic_sha256"
            ),
            artifact_sha256=self._sha256(
                item["artifact_sha256"], f"{path}.artifact_sha256"
            ),
            size_bytes=self._integer(
                item["size_bytes"], f"{path}.size_bytes", minimum=1
            ),
        )

    @staticmethod
    def _verify_skill(
        entry: NspSkillManifest,
        artifact: bytes,
    ) -> VerifiedNspSkill:
        if len(artifact) != entry.size_bytes:
            raise NspVerificationError(
                NspVerificationCode.CONTENT_MISMATCH,
                "NSP skill artifact size mismatch",
            )
        digest = "sha256:" + sha256(artifact).hexdigest()
        if digest != entry.artifact_sha256:
            raise NspVerificationError(
                NspVerificationCode.CONTENT_MISMATCH,
                "NSP skill artifact hash mismatch",
            )
        try:
            skill = NsoCodec.decode(artifact)
        except ValueError as error:
            raise NspVerificationError(
                NspVerificationCode.CONTENT_MISMATCH,
                "NSP contains an invalid NSO artifact",
            ) from error
        if NsoCodec.encode(skill) != artifact:
            raise NspVerificationError(
                NspVerificationCode.CONTENT_MISMATCH,
                "NSP skill artifact must use canonical NSO encoding",
            )
        if (
            skill.skill_id != entry.skill_id
            or skill.skill_version != entry.skill_version
            or skill.semantic_hash != entry.semantic_sha256
        ):
            raise NspVerificationError(
                NspVerificationCode.CONTENT_MISMATCH,
                "NSP skill identity differs from the manifest",
            )
        return VerifiedNspSkill(entry, skill, artifact)

    @staticmethod
    def _manifest_error(path: str, reason: str) -> NspVerificationError:
        return NspVerificationError(
            NspVerificationCode.INVALID_MANIFEST,
            f"invalid NSP manifest at {path}: {reason}",
        )

    @classmethod
    def _object(cls, value: Any, path: str, fields: set[str]) -> dict[str, Any]:
        if type(value) is not dict:
            raise cls._manifest_error(path, "expected object")
        actual = set(value)
        missing = fields - actual
        if missing:
            raise cls._manifest_error(path, f"missing fields: {sorted(missing)}")
        unexpected = actual - fields
        if unexpected:
            raise cls._manifest_error(
                path, f"unexpected fields: {sorted(unexpected)}"
            )
        return value

    @classmethod
    def _array(cls, value: Any, path: str) -> list[Any]:
        if type(value) is not list:
            raise cls._manifest_error(path, "expected array")
        return value

    @classmethod
    def _string(cls, value: Any, path: str) -> str:
        if type(value) is not str:
            raise cls._manifest_error(path, "expected string")
        if not value:
            raise cls._manifest_error(path, "must not be empty")
        return value

    @classmethod
    def _integer(
        cls,
        value: Any,
        path: str,
        *,
        minimum: int,
    ) -> int:
        if type(value) is not int:
            raise cls._manifest_error(path, "expected integer")
        if value < minimum:
            raise cls._manifest_error(path, f"must be at least {minimum}")
        return value

    @classmethod
    def _sha256(cls, value: Any, path: str) -> str:
        digest = cls._string(value, path)
        if (
            not digest.startswith("sha256:")
            or len(digest) != 71
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            raise cls._manifest_error(path, "expected lowercase sha256 digest")
        return digest

    @classmethod
    def _constant(cls, value: Any, expected: str, path: str) -> None:
        if value != expected:
            raise cls._manifest_error(path, f"expected {expected!r}")
