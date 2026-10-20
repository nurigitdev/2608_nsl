from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256

from .data_protection import ensure_no_credential_material
from .ir import NsoCodec, SkillObject


class PackageTrustError(ValueError):
    """Invalid or unavailable package trust evidence."""


class TrustStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    REVOKED = "REVOKED"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackageTrustError(f"{name} must be a non-empty string")
    ensure_no_credential_material(value, name)
    return value


def _sha256(value: object, name: str) -> str:
    digest = _required_text(value, name)
    if (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise PackageTrustError(f"{name} must be a lowercase SHA-256 value")
    return digest


def package_id_for(package_hash: str) -> str:
    return "nsp:" + _sha256(package_hash, "package_hash")


@dataclass(frozen=True, slots=True)
class PackageTrustEvidence:
    package_id: str
    package_hash: str
    publisher_id: str
    signing_key_id: str
    verified_at: datetime
    trust_status: TrustStatus
    trust_policy_id: str

    def __post_init__(self) -> None:
        package_hash = _sha256(self.package_hash, "package_hash")
        if self.package_id != package_id_for(package_hash):
            raise PackageTrustError("package_id does not match package_hash")
        _required_text(self.publisher_id, "publisher_id")
        _required_text(self.signing_key_id, "signing_key_id")
        _required_text(self.trust_policy_id, "trust_policy_id")
        if not isinstance(self.verified_at, datetime):
            raise TypeError("verified_at must be a datetime")
        if (
            self.verified_at.tzinfo is None
            or self.verified_at.utcoffset() != UTC.utcoffset(self.verified_at)
        ):
            raise PackageTrustError("verified_at must use timezone-aware UTC")
        if not isinstance(self.trust_status, TrustStatus):
            raise TypeError("trust_status must be a TrustStatus")

    @classmethod
    def verified(
        cls,
        *,
        package_hash: str,
        publisher_id: str,
        signing_key_id: str,
        verified_at: datetime,
        trust_policy_id: str,
    ) -> PackageTrustEvidence:
        return cls(
            package_id=package_id_for(package_hash),
            package_hash=package_hash,
            publisher_id=publisher_id,
            signing_key_id=signing_key_id,
            verified_at=verified_at,
            trust_status=TrustStatus.VERIFIED,
            trust_policy_id=trust_policy_id,
        )


@dataclass(frozen=True, slots=True)
class VerifiedSkillArtifact:
    skill: SkillObject
    artifact_hash: str
    trust: PackageTrustEvidence | None

    def __post_init__(self) -> None:
        if not isinstance(self.skill, SkillObject):
            raise TypeError("skill must be a SkillObject")
        artifact_hash = _sha256(self.artifact_hash, "artifact_hash")
        try:
            canonical_artifact = NsoCodec.encode(self.skill)
        except ValueError as error:
            raise PackageTrustError(
                "skill is not a valid canonical Skill artifact"
            ) from error
        expected = "sha256:" + sha256(canonical_artifact).hexdigest()
        if artifact_hash != expected:
            raise PackageTrustError(
                "artifact_hash does not match the canonical Skill artifact"
            )
        if self.trust is not None and not isinstance(
            self.trust,
            PackageTrustEvidence,
        ):
            raise TypeError("trust must be PackageTrustEvidence or None")

    @property
    def semantic_hash(self) -> str:
        return self.skill.semantic_hash

