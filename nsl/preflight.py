from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256

from .core import ToolCapability
from .data_protection import ensure_no_credential_material
from .ir import ForeachStatement, LetStatement, NsoCodec, SkillObject, WriteExpression
from .security import RuntimeEnvironment
from .trust import (
    PackageTrustEvidence,
    TrustStatus,
    VerifiedSkillArtifact,
    package_id_for,
)


MAX_TRUST_AGE_SECONDS = 31_536_000


class TrustPreflightError(RuntimeError):
    """A fail-closed Production WRITE trust decision."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class TrustPreflightStatus(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_REQUIRED = "NOT_REQUIRED"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    ensure_no_credential_material(value, name)
    return value


def _sha256(value: object, name: str) -> str:
    digest = _required_text(value, name)
    if (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 value")
    return digest


def _utc_instant(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must use timezone-aware UTC")
    return value


def _identity_set(value: object, name: str) -> frozenset[str]:
    if not isinstance(value, frozenset) or not all(
        isinstance(item, str) and bool(item.strip()) for item in value
    ):
        raise TypeError(f"{name} must be a frozenset of non-empty strings")
    ensure_no_credential_material(value, name)
    return value


@dataclass(frozen=True, slots=True)
class ExecutionTrustProfile:
    tenant_id: str
    environment: RuntimeEnvironment
    trust_policy_id: str | None
    expected_package_id: str | None
    expected_artifact_hash: str | None
    expected_semantic_hash: str | None
    trusted_publisher_ids: frozenset[str]
    trusted_signing_key_ids: frozenset[str]
    max_trust_age_seconds: int | None
    allow_unsigned_development: bool = False

    def __post_init__(self) -> None:
        _required_text(self.tenant_id, "tenant_id")
        if not isinstance(self.environment, RuntimeEnvironment):
            raise TypeError("environment must be a RuntimeEnvironment")
        publishers = _identity_set(
            self.trusted_publisher_ids,
            "trusted_publisher_ids",
        )
        signing_keys = _identity_set(
            self.trusted_signing_key_ids,
            "trusted_signing_key_ids",
        )
        if type(self.allow_unsigned_development) is not bool:
            raise TypeError("allow_unsigned_development must be Bool")
        if self.max_trust_age_seconds is not None and (
            type(self.max_trust_age_seconds) is not int
            or not 1
            <= self.max_trust_age_seconds
            <= MAX_TRUST_AGE_SECONDS
        ):
            raise ValueError(
                "max_trust_age_seconds must be between 1 and "
                f"{MAX_TRUST_AGE_SECONDS}"
            )

        if self.environment is RuntimeEnvironment.PRODUCTION:
            _required_text(self.trust_policy_id, "trust_policy_id")
            _required_text(self.expected_package_id, "expected_package_id")
            _sha256(self.expected_artifact_hash, "expected_artifact_hash")
            _sha256(self.expected_semantic_hash, "expected_semantic_hash")
            if not publishers:
                raise ValueError("Production trusted_publisher_ids cannot be empty")
            if not signing_keys:
                raise ValueError(
                    "Production trusted_signing_key_ids cannot be empty"
                )
            if self.max_trust_age_seconds is None:
                raise ValueError(
                    "Production max_trust_age_seconds must be explicit"
                )
            if self.allow_unsigned_development:
                raise ValueError(
                    "Production cannot allow unsigned development artifacts"
                )
        else:
            for value, name in (
                (self.trust_policy_id, "trust_policy_id"),
                (self.expected_package_id, "expected_package_id"),
            ):
                if value is not None:
                    _required_text(value, name)
            for value, name in (
                (self.expected_artifact_hash, "expected_artifact_hash"),
                (self.expected_semantic_hash, "expected_semantic_hash"),
            ):
                if value is not None:
                    _sha256(value, name)

    @classmethod
    def production(
        cls,
        *,
        tenant_id: str,
        trust_policy_id: str,
        expected_package_id: str,
        expected_artifact_hash: str,
        expected_semantic_hash: str,
        trusted_publisher_ids: frozenset[str],
        trusted_signing_key_ids: frozenset[str],
        max_trust_age_seconds: int,
    ) -> ExecutionTrustProfile:
        return cls(
            tenant_id=tenant_id,
            environment=RuntimeEnvironment.PRODUCTION,
            trust_policy_id=trust_policy_id,
            expected_package_id=expected_package_id,
            expected_artifact_hash=expected_artifact_hash,
            expected_semantic_hash=expected_semantic_hash,
            trusted_publisher_ids=trusted_publisher_ids,
            trusted_signing_key_ids=trusted_signing_key_ids,
            max_trust_age_seconds=max_trust_age_seconds,
        )

    @classmethod
    def development(
        cls,
        *,
        tenant_id: str,
        allow_unsigned: bool,
    ) -> ExecutionTrustProfile:
        return cls(
            tenant_id=tenant_id,
            environment=RuntimeEnvironment.DEVELOPMENT,
            trust_policy_id=None,
            expected_package_id=None,
            expected_artifact_hash=None,
            expected_semantic_hash=None,
            trusted_publisher_ids=frozenset(),
            trusted_signing_key_ids=frozenset(),
            max_trust_age_seconds=None,
            allow_unsigned_development=allow_unsigned,
        )


@dataclass(frozen=True, slots=True)
class TrustPreflightEvidence:
    tenant_id: str
    environment: RuntimeEnvironment
    status: TrustPreflightStatus
    checked_at: datetime
    artifact_hash: str
    skill_semantic_hash: str
    package_id: str | None
    trust_policy_id: str | None

    def __post_init__(self) -> None:
        _required_text(self.tenant_id, "tenant_id")
        if not isinstance(self.environment, RuntimeEnvironment):
            raise TypeError("environment must be a RuntimeEnvironment")
        if not isinstance(self.status, TrustPreflightStatus):
            raise TypeError("status must be a TrustPreflightStatus")
        _utc_instant(self.checked_at, "checked_at")
        _sha256(self.artifact_hash, "artifact_hash")
        _sha256(self.skill_semantic_hash, "skill_semantic_hash")
        if self.package_id is not None:
            _required_text(self.package_id, "package_id")
        if self.trust_policy_id is not None:
            _required_text(self.trust_policy_id, "trust_policy_id")


class RuntimePreflightValidator:
    __slots__ = ()

    def validate(
        self,
        artifact: VerifiedSkillArtifact,
        profile: ExecutionTrustProfile,
        *,
        now: datetime,
    ) -> TrustPreflightEvidence:
        if not isinstance(artifact, VerifiedSkillArtifact):
            raise TypeError("artifact must be a VerifiedSkillArtifact")
        if not isinstance(profile, ExecutionTrustProfile):
            raise TypeError("profile must be an ExecutionTrustProfile")
        checked_at = _utc_instant(now, "now")
        self._validate_artifact_identity(artifact)
        requires_trust = _contains_write(artifact.skill)
        trust = artifact.trust

        if trust is None:
            if profile.environment is RuntimeEnvironment.PRODUCTION and requires_trust:
                self._blocked(
                    "TRUST_EVIDENCE_MISSING",
                    "Production WRITE requires verified package trust evidence",
                )
            if (
                profile.environment is RuntimeEnvironment.DEVELOPMENT
                and requires_trust
                and not profile.allow_unsigned_development
            ):
                self._blocked(
                    "UNSIGNED_DEVELOPMENT_NOT_ALLOWED",
                    "unsigned development WRITE is not allowed by the profile",
                )
            return TrustPreflightEvidence(
                tenant_id=profile.tenant_id,
                environment=profile.environment,
                status=TrustPreflightStatus.NOT_REQUIRED,
                checked_at=checked_at,
                artifact_hash=artifact.artifact_hash,
                skill_semantic_hash=artifact.semantic_hash,
                package_id=None,
                trust_policy_id=None,
            )

        self._validate_trust_identity(artifact, trust, profile, checked_at)
        return TrustPreflightEvidence(
            tenant_id=profile.tenant_id,
            environment=profile.environment,
            status=TrustPreflightStatus.VERIFIED,
            checked_at=checked_at,
            artifact_hash=artifact.artifact_hash,
            skill_semantic_hash=artifact.semantic_hash,
            package_id=trust.package_id,
            trust_policy_id=trust.trust_policy_id,
        )

    @staticmethod
    def _validate_artifact_identity(artifact: VerifiedSkillArtifact) -> None:
        try:
            canonical = NsoCodec.encode(artifact.skill)
            semantic_hash = artifact.skill.with_computed_hash().semantic_hash
        except ValueError as error:
            raise TrustPreflightError(
                "ARTIFACT_INVALID",
                "Skill artifact is not canonical",
            ) from error
        expected_artifact_hash = "sha256:" + sha256(canonical).hexdigest()
        if artifact.artifact_hash != expected_artifact_hash:
            RuntimePreflightValidator._blocked(
                "ARTIFACT_HASH_MISMATCH",
                "Skill artifact hash does not match canonical content",
            )
        if artifact.semantic_hash != semantic_hash:
            RuntimePreflightValidator._blocked(
                "SEMANTIC_HASH_MISMATCH",
                "Skill semantic hash does not match canonical semantics",
            )

    @staticmethod
    def _validate_trust_identity(
        artifact: VerifiedSkillArtifact,
        trust: PackageTrustEvidence,
        profile: ExecutionTrustProfile,
        checked_at: datetime,
    ) -> None:
        if trust.trust_status is not TrustStatus.VERIFIED:
            RuntimePreflightValidator._blocked(
                "TRUST_NOT_VERIFIED",
                "package trust evidence is not verified",
            )
        if trust.package_id != package_id_for(trust.package_hash):
            RuntimePreflightValidator._blocked(
                "PACKAGE_ID_MISMATCH",
                "package trust identity is inconsistent",
            )
        if profile.expected_package_id is not None and (
            trust.package_id != profile.expected_package_id
        ):
            RuntimePreflightValidator._blocked(
                "PACKAGE_BINDING_MISMATCH",
                "package trust evidence does not match the execution profile",
            )
        if profile.expected_artifact_hash is not None and (
            artifact.artifact_hash != profile.expected_artifact_hash
        ):
            RuntimePreflightValidator._blocked(
                "ARTIFACT_BINDING_MISMATCH",
                "artifact does not match the execution profile",
            )
        if profile.expected_semantic_hash is not None and (
            artifact.semantic_hash != profile.expected_semantic_hash
        ):
            RuntimePreflightValidator._blocked(
                "SEMANTIC_BINDING_MISMATCH",
                "semantic identity does not match the execution profile",
            )
        if profile.trust_policy_id is not None and (
            trust.trust_policy_id != profile.trust_policy_id
        ):
            RuntimePreflightValidator._blocked(
                "TRUST_POLICY_MISMATCH",
                "trust policy does not match the execution profile",
            )
        if profile.trusted_publisher_ids and (
            trust.publisher_id not in profile.trusted_publisher_ids
        ):
            RuntimePreflightValidator._blocked(
                "PUBLISHER_NOT_TRUSTED",
                "publisher is not trusted by the execution profile",
            )
        if profile.trusted_signing_key_ids and (
            trust.signing_key_id not in profile.trusted_signing_key_ids
        ):
            RuntimePreflightValidator._blocked(
                "SIGNING_KEY_NOT_TRUSTED",
                "signing key is not trusted by the execution profile",
            )
        if checked_at < trust.verified_at:
            RuntimePreflightValidator._blocked(
                "TRUST_EVIDENCE_FROM_FUTURE",
                "trust evidence verification time is in the future",
            )
        if profile.max_trust_age_seconds is not None and checked_at >= (
            trust.verified_at
            + timedelta(seconds=profile.max_trust_age_seconds)
        ):
            RuntimePreflightValidator._blocked(
                "TRUST_EVIDENCE_STALE",
                "trust evidence is no longer current",
            )

    @staticmethod
    def _blocked(reason_code: str, message: str) -> None:
        raise TrustPreflightError(reason_code, message)


def _contains_write(skill: SkillObject) -> bool:
    if any(
        required.capability is ToolCapability.WRITE
        for required in skill.required_tools
    ):
        return True

    pending = list(skill.body)
    while pending:
        statement = pending.pop()
        if isinstance(statement, LetStatement) and isinstance(
            statement.value,
            WriteExpression,
        ):
            return True
        if isinstance(statement, ForeachStatement):
            pending.extend(statement.body)
    return False
