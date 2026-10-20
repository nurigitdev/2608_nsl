from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol, runtime_checkable

from .core import CommitState, ToolCapability, TypeRef, VerificationStatus
from .data_protection import ensure_no_credential_material
from .ir import canonical_json
from .runtime_models import ExecutionContext, VerificationSummary
from .tools import PostWriteVerification, ToolContract
from .write_execution import WriteOutcomeIdentity, WriteResultEnvelope


class VerificationError(RuntimeError):
    """Invalid post-WRITE verification contract or binding."""


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VerificationError(f"{name} must be a non-empty string")
    ensure_no_credential_material(value, name)
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, name)


def _sha256_text(value: object, name: str) -> str:
    text = _required_text(value, name)
    if (
        not text.startswith("sha256:")
        or len(text) != 71
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise VerificationError(f"{name} must be a lowercase SHA-256 value")
    return text


def _utc_instant(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise VerificationError(f"{name} must use timezone-aware UTC")
    return value


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    identity: WriteOutcomeIdentity
    verification_tool_id: str
    business_reference: str
    provider_transaction_id: str | None
    reference_schema: TypeRef | None
    consistency_hint: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, WriteOutcomeIdentity):
            raise TypeError("identity must be a WriteOutcomeIdentity")
        _required_text(self.verification_tool_id, "verification_tool_id")
        _required_text(self.business_reference, "business_reference")
        _optional_text(
            self.provider_transaction_id,
            "provider_transaction_id",
        )
        if self.reference_schema is not None and not isinstance(
            self.reference_schema,
            TypeRef,
        ):
            raise TypeError("reference_schema must be a TypeRef or None")
        _optional_text(self.consistency_hint, "consistency_hint")

    @property
    def request_hash(self) -> str:
        payload = {
            "tenant_id": self.identity.tenant_id,
            "business_operation_id": self.identity.business_operation_id,
            "invocation_id": self.identity.invocation_id,
            "node_id": self.identity.node_id,
            "write_tool_id": self.identity.tool_id,
            "idempotency_key": self.identity.idempotency_key,
            "payload_hash": self.identity.payload_hash,
            "approval_request_id": self.identity.approval_request_id,
            "write_intent_hash": self.identity.write_intent_hash,
            "verification_tool_id": self.verification_tool_id,
            "business_reference": self.business_reference,
            "provider_transaction_id": self.provider_transaction_id,
            "reference_schema": (
                None
                if self.reference_schema is None
                else self.reference_schema.to_data()
            ),
            "consistency_hint": self.consistency_hint,
        }
        return "sha256:" + sha256(
            b"NSL-POST-WRITE-VERIFY-V1\0" + canonical_json(payload)
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class VerificationObservation:
    request_hash: str
    business_reference: str
    matched: bool
    evidence_ref: str
    observed_at: datetime
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _sha256_text(self.request_hash, "request_hash")
        _required_text(self.business_reference, "business_reference")
        if type(self.matched) is not bool:
            raise TypeError("matched must be Bool")
        _required_text(self.evidence_ref, "evidence_ref")
        _utc_instant(self.observed_at, "observed_at")
        _optional_text(self.reason_code, "reason_code")
        if self.matched and self.reason_code is not None:
            raise VerificationError(
                "matched verification cannot contain a failure reason"
            )
        if not self.matched and self.reason_code is None:
            raise VerificationError(
                "failed verification must contain a reason_code"
            )


@runtime_checkable
class PostWriteVerificationPort(Protocol):
    async def read(
        self,
        request: VerificationRequest,
    ) -> VerificationObservation: ...


@dataclass(frozen=True, slots=True)
class VerificationResult:
    identity: WriteOutcomeIdentity
    commit_state: CommitState
    status: VerificationStatus
    business_reference: str
    verification_tool_id: str | None
    reason_code: str | None
    evidence_ref: str | None
    observed_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, WriteOutcomeIdentity):
            raise TypeError("identity must be a WriteOutcomeIdentity")
        if self.commit_state is not CommitState.COMMITTED:
            raise VerificationError(
                "post-WRITE verification cannot change COMMITTED state"
            )
        if not isinstance(self.status, VerificationStatus):
            raise TypeError("status must be a VerificationStatus")
        _required_text(self.business_reference, "business_reference")
        _optional_text(self.verification_tool_id, "verification_tool_id")
        _optional_text(self.reason_code, "reason_code")
        _optional_text(self.evidence_ref, "evidence_ref")
        if self.observed_at is not None:
            _utc_instant(self.observed_at, "observed_at")

        if self.status is VerificationStatus.NOT_REQUESTED:
            if any(
                value is not None
                for value in (
                    self.verification_tool_id,
                    self.reason_code,
                    self.evidence_ref,
                    self.observed_at,
                )
            ):
                raise VerificationError(
                    "NOT_REQUESTED verification cannot contain outcome evidence"
                )
            return
        if self.verification_tool_id is None:
            raise VerificationError(
                "requested verification requires verification_tool_id"
            )
        if self.status is VerificationStatus.VERIFIED:
            if (
                self.reason_code is not None
                or self.evidence_ref is None
                or self.observed_at is None
            ):
                raise VerificationError(
                    "VERIFIED requires evidence and no failure reason"
                )
            return
        if self.reason_code is None:
            raise VerificationError(
                "FAILED or UNKNOWN verification requires reason_code"
            )

    def to_summary(self) -> VerificationSummary:
        return VerificationSummary(
            status=self.status,
            reason_code=self.reason_code,
            evidence_ref=self.evidence_ref,
        )


class PostWriteVerifier:
    __slots__ = ()

    async def verify(
        self,
        result: WriteResultEnvelope,
        contract: ToolContract,
        context: ExecutionContext,
        port: PostWriteVerificationPort | None,
    ) -> VerificationResult:
        self._validate_inputs(result, contract, context)
        verification = contract.write_semantics.verification
        if verification is None or verification.tool_id is None:
            return VerificationResult(
                identity=result.identity,
                commit_state=CommitState.COMMITTED,
                status=VerificationStatus.NOT_REQUESTED,
                business_reference=result.business_reference,
                verification_tool_id=None,
                reason_code=None,
                evidence_ref=None,
                observed_at=None,
            )
        if not isinstance(port, PostWriteVerificationPort):
            raise TypeError(
                "port must provide the read-only PostWriteVerificationPort"
            )

        request = self._request(result, verification)
        try:
            observation = await port.read(request)
        except TimeoutError:
            return self._unknown(
                result,
                request,
                "VERIFICATION_TIMEOUT",
            )
        except Exception:
            return self._unknown(
                result,
                request,
                "VERIFICATION_DEPENDENCY_FAILURE",
            )
        if not isinstance(observation, VerificationObservation):
            return self._unknown(
                result,
                request,
                "VERIFICATION_RESPONSE_MALFORMED",
            )
        if (
            observation.request_hash != request.request_hash
            or observation.business_reference != result.business_reference
        ):
            return self._unknown(
                result,
                request,
                "VERIFICATION_BINDING_MISMATCH",
            )
        if observation.matched:
            return VerificationResult(
                identity=result.identity,
                commit_state=CommitState.COMMITTED,
                status=VerificationStatus.VERIFIED,
                business_reference=result.business_reference,
                verification_tool_id=request.verification_tool_id,
                reason_code=None,
                evidence_ref=observation.evidence_ref,
                observed_at=observation.observed_at,
            )
        return VerificationResult(
            identity=result.identity,
            commit_state=CommitState.COMMITTED,
            status=VerificationStatus.FAILED,
            business_reference=result.business_reference,
            verification_tool_id=request.verification_tool_id,
            reason_code=observation.reason_code,
            evidence_ref=observation.evidence_ref,
            observed_at=observation.observed_at,
        )

    @staticmethod
    def _validate_inputs(
        result: WriteResultEnvelope,
        contract: ToolContract,
        context: ExecutionContext,
    ) -> None:
        if not isinstance(result, WriteResultEnvelope):
            raise TypeError("result must be a committed WriteResultEnvelope")
        if not isinstance(contract, ToolContract):
            raise TypeError("contract must be a ToolContract")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if (
            contract.capability is not ToolCapability.WRITE
            or contract.write_semantics is None
            or contract.tool_id != result.identity.tool_id
        ):
            raise VerificationError(
                "verification requires the exact canonical WRITE contract"
            )
        if (
            context.tenant_id != result.identity.tenant_id
            or context.identity.business_operation_id
            != result.identity.business_operation_id
        ):
            raise VerificationError(
                "verification context does not match committed WRITE identity"
            )
        if (
            context.idempotency.keys_by_write_node.get(result.identity.node_id)
            != result.identity.idempotency_key
            or context.idempotency.payload_hashes_by_write_node.get(
                result.identity.node_id
            )
            != result.identity.payload_hash
        ):
            raise VerificationError(
                "verification idempotency state does not match committed WRITE"
            )

    @staticmethod
    def _request(
        result: WriteResultEnvelope,
        verification: PostWriteVerification,
    ) -> VerificationRequest:
        assert verification.tool_id is not None
        return VerificationRequest(
            identity=result.identity,
            verification_tool_id=verification.tool_id,
            business_reference=result.business_reference,
            provider_transaction_id=result.provider_transaction_id,
            reference_schema=verification.reference_schema,
            consistency_hint=verification.consistency_hint,
        )

    @staticmethod
    def _unknown(
        result: WriteResultEnvelope,
        request: VerificationRequest,
        reason_code: str,
    ) -> VerificationResult:
        return VerificationResult(
            identity=result.identity,
            commit_state=CommitState.COMMITTED,
            status=VerificationStatus.UNKNOWN,
            business_reference=result.business_reference,
            verification_tool_id=request.verification_tool_id,
            reason_code=reason_code,
            evidence_ref=None,
            observed_at=None,
        )
