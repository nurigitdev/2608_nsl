from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256

from .core import DataClassification
from .data_protection import CredentialMaterialError, ensure_no_credential_material


class AuthorizationError(RuntimeError):
    pass


class RuntimeEnvironment(StrEnum):
    PRODUCTION = "PRODUCTION"
    DEVELOPMENT = "DEVELOPMENT"


class PrincipalVerification(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


@dataclass(frozen=True, slots=True)
class ExecutionPrincipal:
    tenant_id: str
    subject_id: str
    actor_type: str
    roles: frozenset[str]
    scopes: frozenset[str]
    auth_context_ref: str
    verification: PrincipalVerification
    on_behalf_of: str | None = None

    def validate(self, *, require_verified: bool = False) -> None:
        if (
            not isinstance(self.tenant_id, str)
            or not self.tenant_id.strip()
            or not isinstance(self.subject_id, str)
            or not self.subject_id.strip()
        ):
            raise AuthorizationError("tenant_id and subject_id are required")
        if self.actor_type not in {"USER", "SERVICE"}:
            raise AuthorizationError("actor_type must be USER or SERVICE")
        if (
            not isinstance(self.auth_context_ref, str)
            or not self.auth_context_ref.strip()
        ):
            raise AuthorizationError("auth_context_ref is required")
        if not isinstance(self.roles, frozenset) or not all(
            isinstance(role, str) and bool(role.strip()) for role in self.roles
        ):
            raise AuthorizationError("roles must be a set of non-empty strings")
        if not isinstance(self.scopes, frozenset) or not all(
            isinstance(scope, str) and bool(scope.strip()) for scope in self.scopes
        ):
            raise AuthorizationError("scopes must be a set of non-empty strings")
        if self.on_behalf_of is not None and (
            not isinstance(self.on_behalf_of, str) or not self.on_behalf_of.strip()
        ):
            raise AuthorizationError("on_behalf_of must be a non-empty string")
        if not isinstance(self.verification, PrincipalVerification):
            raise AuthorizationError("principal verification state is invalid")
        if require_verified and self.verification is not PrincipalVerification.VERIFIED:
            raise AuthorizationError("verified execution principal is required")
        try:
            ensure_no_credential_material(
                {
                    "tenant_id": self.tenant_id,
                    "subject_id": self.subject_id,
                    "actor_type": self.actor_type,
                    "roles": tuple(self.roles),
                    "scopes": tuple(self.scopes),
                    "auth_context_ref": self.auth_context_ref,
                    "on_behalf_of": self.on_behalf_of,
                },
                "ExecutionPrincipal",
            )
        except CredentialMaterialError as error:
            raise AuthorizationError(
                "credential material is forbidden in ExecutionPrincipal"
            ) from error


@dataclass(frozen=True, slots=True)
class DataHandlingPolicy:
    max_trace_classification: DataClassification = DataClassification.INTERNAL
    snapshot_retention_days: int = 30

    def __post_init__(self) -> None:
        if self.snapshot_retention_days <= 0:
            raise ValueError("snapshot_retention_days must be positive")


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    decision_id: str
    policy_id: str
    policy_version: str
    effect: str
    granted_scopes: frozenset[str]
    decided_at: str


class StaticAuthorizer:
    """Default-deny authorizer used by the vertical slice."""

    def __init__(
        self,
        policy_id: str = "NSL-VS-DEFAULT-DENY",
        policy_version: str = "1",
    ) -> None:
        self.policy_id = policy_id
        self.policy_version = policy_version

    def authorize(
        self,
        principal: ExecutionPrincipal,
        action: str,
        required_scopes: frozenset[str],
    ) -> AuthorizationDecision:
        principal.validate()
        if not isinstance(action, str) or not action.strip():
            raise AuthorizationError("authorization action is required")
        if not isinstance(required_scopes, frozenset) or not required_scopes:
            raise AuthorizationError("explicit required scopes are required")
        if not all(
            isinstance(scope, str) and bool(scope.strip()) for scope in required_scopes
        ):
            raise AuthorizationError("required scopes must be non-empty strings")
        missing = required_scopes - principal.scopes
        effect = "DENY" if missing else "ALLOW"
        material = "|".join(
            [
                self.policy_id,
                self.policy_version,
                principal.tenant_id,
                principal.subject_id,
                action,
                ",".join(sorted(required_scopes)),
                effect,
            ]
        )
        decision = AuthorizationDecision(
            decision_id="authz-" + sha256(material.encode("utf-8")).hexdigest()[:16],
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            effect=effect,
            granted_scopes=principal.scopes & required_scopes,
            decided_at=datetime.now(UTC).isoformat(),
        )
        if missing:
            raise AuthorizationError(
                f"missing required scope(s) for {action}: {', '.join(sorted(missing))}"
            )
        return decision
