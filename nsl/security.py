from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from .core import DataClassification


class AuthorizationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionPrincipal:
    tenant_id: str
    subject_id: str
    actor_type: str
    roles: frozenset[str]
    scopes: frozenset[str]
    auth_context_ref: str
    on_behalf_of: str | None = None

    def validate(self) -> None:
        if not self.tenant_id or not self.subject_id:
            raise AuthorizationError("tenant_id and subject_id are required")
        if self.actor_type not in {"USER", "SERVICE"}:
            raise AuthorizationError("actor_type must be USER or SERVICE")
        if not self.auth_context_ref:
            raise AuthorizationError("auth_context_ref is required")


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

