from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .approval import ApprovalEvidence
from .core import CommitState
from .security import ExecutionPrincipal
from .verification import VerificationResult
from .write_execution import (
    WriteAttemptRecord,
    WriteCallRequest,
    WriteOutcomeIdentity,
    WriteOutcomeRecord,
)
from .write_replay import (
    ProtectedWriteReplayBundle,
    RehydratedWriteReplayEvidence,
    WriteReplayEvidenceLoader,
)


MAX_REPLAY_WRITE_CALLS = 64


class ReplaySideEffectError(RuntimeError):
    """Replay failed closed before any Live WRITE capability was available."""


class ReplayWriteSource(StrEnum):
    RECORDED = "RECORDED"
    MOCK = "MOCK"


def _outcome_matches_request(
    outcome: WriteOutcomeIdentity,
    request: WriteAttemptRecord,
) -> bool:
    return (
        outcome.tenant_id == request.tenant_id
        and outcome.business_operation_id == request.business_operation_id
        and outcome.invocation_id == request.invocation_id
        and outcome.node_id == request.node_id
        and outcome.tool_id == request.tool_id
        and outcome.idempotency_key == request.idempotency_key
        and outcome.payload_hash == request.payload_hash
        and outcome.approval_request_id == request.approval_request_id
        and outcome.write_intent_hash == request.write_intent_hash
    )


@dataclass(frozen=True, slots=True)
class RecordedWriteExchange:
    request: WriteAttemptRecord
    outcome: WriteOutcomeRecord

    def __post_init__(self) -> None:
        if not isinstance(self.request, WriteAttemptRecord):
            raise TypeError("request must be a WriteAttemptRecord")
        if not isinstance(self.outcome, WriteOutcomeRecord):
            raise TypeError("outcome must be a WriteOutcomeRecord")
        if self.outcome.requested_record_hash != self.request.record_hash:
            raise ReplaySideEffectError(
                "recorded outcome does not bind the WRITE request"
            )
        if not _outcome_matches_request(self.outcome.identity, self.request):
            raise ReplaySideEffectError(
                "recorded WRITE request and outcome identities differ"
            )

    def matches(self, request: WriteCallRequest) -> bool:
        if not isinstance(request, WriteCallRequest):
            raise TypeError("request must be a WriteCallRequest")
        candidate = WriteAttemptRecord.from_request(
            request,
            self.request.requested_at,
        )
        return candidate == self.request


@dataclass(frozen=True, slots=True)
class ReplayWriteDecision:
    outcome: WriteOutcomeRecord
    source: ReplayWriteSource
    automatic_retry_allowed: bool = False
    side_effect_performed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, WriteOutcomeRecord):
            raise TypeError("outcome must be a WriteOutcomeRecord")
        if not isinstance(self.source, ReplayWriteSource):
            raise TypeError("source must be a ReplayWriteSource")
        if self.automatic_retry_allowed is not False:
            raise ReplaySideEffectError("Replay cannot allow automatic WRITE retry")
        if self.side_effect_performed is not False:
            raise ReplaySideEffectError("Replay cannot report a WRITE side effect")

    @property
    def reconciliation_required(self) -> bool:
        return self.outcome.commit_state is CommitState.UNKNOWN_COMMIT_STATE


class _StrictRecordedWriteExecutor:
    __slots__ = ("_exchanges", "_failed", "_index", "_source")

    def __init__(
        self,
        exchanges: tuple[RecordedWriteExchange, ...],
        source: ReplayWriteSource,
    ) -> None:
        if not isinstance(exchanges, tuple) or not all(
            isinstance(exchange, RecordedWriteExchange)
            for exchange in exchanges
        ):
            raise TypeError("exchanges must be a tuple of RecordedWriteExchange")
        if len(exchanges) > MAX_REPLAY_WRITE_CALLS:
            raise ReplaySideEffectError(
                f"Replay cannot exceed {MAX_REPLAY_WRITE_CALLS} WRITE calls"
            )
        identities = tuple(
            (
                exchange.request.execution_id,
                exchange.request.business_operation_id,
                exchange.request.invocation_id,
                exchange.request.node_id,
            )
            for exchange in exchanges
        )
        if len(set(identities)) != len(identities):
            raise ReplaySideEffectError(
                "Replay WRITE invocation identities must be unique"
            )
        if not isinstance(source, ReplayWriteSource):
            raise TypeError("source must be a ReplayWriteSource")
        self._exchanges = exchanges
        self._source = source
        self._index = 0
        self._failed = False

    async def execute(self, request: WriteCallRequest) -> ReplayWriteDecision:
        if not isinstance(request, WriteCallRequest):
            raise TypeError("request must be a WriteCallRequest")
        if self._failed:
            raise ReplaySideEffectError("Replay WRITE executor is failed closed")
        if self._index >= len(self._exchanges):
            self._failed = True
            raise ReplaySideEffectError(
                "Replay requested more WRITE calls than recorded"
            )
        expected = self._exchanges[self._index]
        if not expected.matches(request):
            self._failed = True
            raise ReplaySideEffectError(
                "Replay WRITE call does not match recorded invocation order"
            )
        self._index += 1
        return ReplayWriteDecision(expected.outcome, self._source)

    @property
    def call_count(self) -> int:
        return self._index

    def assert_consumed(self) -> None:
        if self._failed:
            raise ReplaySideEffectError("Replay WRITE executor is failed closed")
        if self._index != len(self._exchanges):
            raise ReplaySideEffectError(
                "Replay did not consume every recorded WRITE call"
            )


class RecordedWriteReplayExecutor(_StrictRecordedWriteExecutor):
    """WRITE port substitute backed only by verified recorded evidence."""

    def __init__(self, evidence: RehydratedWriteReplayEvidence) -> None:
        if not isinstance(evidence, RehydratedWriteReplayEvidence):
            raise TypeError("evidence must be RehydratedWriteReplayEvidence")
        if evidence.write_request is None and evidence.commit_outcome is None:
            exchanges: tuple[RecordedWriteExchange, ...] = ()
        elif evidence.write_request is not None and evidence.commit_outcome is not None:
            exchanges = (
                RecordedWriteExchange(
                    evidence.write_request,
                    evidence.commit_outcome,
                ),
            )
        else:
            raise ReplaySideEffectError(
                "Replay evidence has a partial WRITE exchange"
            )
        super().__init__(exchanges, ReplayWriteSource.RECORDED)


class MockWriteExecutor(_StrictRecordedWriteExecutor):
    """Explicit deterministic test double; it has no Live WRITE delegate."""

    def __init__(self, exchanges: tuple[RecordedWriteExchange, ...]) -> None:
        super().__init__(exchanges, ReplayWriteSource.MOCK)


class ReplayControlEvidenceProvider:
    __slots__ = ("_approval", "_verification")

    def __init__(self, evidence: RehydratedWriteReplayEvidence) -> None:
        if not isinstance(evidence, RehydratedWriteReplayEvidence):
            raise TypeError("evidence must be RehydratedWriteReplayEvidence")
        self._approval = evidence.approval
        self._verification = evidence.verification_outcome

    def approval(self) -> ApprovalEvidence:
        return self._approval

    def verification(self) -> VerificationResult | None:
        return self._verification


class ReplayWriteSession:
    """Verified Replay boundary with no field or parameter for a Live provider."""

    __slots__ = ("_controls", "_writes")

    def __init__(self, evidence: RehydratedWriteReplayEvidence) -> None:
        if not isinstance(evidence, RehydratedWriteReplayEvidence):
            raise TypeError("evidence must be RehydratedWriteReplayEvidence")
        self._controls = ReplayControlEvidenceProvider(evidence)
        self._writes = RecordedWriteReplayExecutor(evidence)

    @classmethod
    def open(
        cls,
        bundle: ProtectedWriteReplayBundle,
        loader: WriteReplayEvidenceLoader,
        principal: ExecutionPrincipal,
    ) -> ReplayWriteSession:
        if not isinstance(loader, WriteReplayEvidenceLoader):
            raise TypeError("loader must be a WriteReplayEvidenceLoader")
        return cls(loader.load(bundle, principal))

    def approval(self) -> ApprovalEvidence:
        return self._controls.approval()

    async def execute_write(
        self,
        request: WriteCallRequest,
    ) -> ReplayWriteDecision:
        return await self._writes.execute(request)

    def verification(self) -> VerificationResult | None:
        return self._controls.verification()

    @property
    def write_call_count(self) -> int:
        return self._writes.call_count

    def assert_complete(self) -> None:
        self._writes.assert_consumed()
