from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, runtime_checkable

from .data_protection import CredentialMaterialError, ensure_no_credential_material
from .integration import IntegrationContractError, RuntimeResultEnvelope


def _digest(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


def _identifier(value: object, field: str) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or not value.isascii()
        or len(value) > 256
    ):
        raise IntegrationContractError(
            f"{field} must be non-empty ASCII up to 256 bytes"
        )
    try:
        ensure_no_credential_material(value, field)
    except CredentialMaterialError as error:
        raise IntegrationContractError(
            f"credential material is forbidden in {field}"
        ) from error
    return value


def _sha256(value: object, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise IntegrationContractError(f"{field} must be a lowercase sha256 digest")
    return value


@dataclass(frozen=True, slots=True)
class RuntimeResultRecord:
    record_id: str
    execution_id: str
    result_hash: str
    envelope: RuntimeResultEnvelope

    def __post_init__(self) -> None:
        _identifier(self.record_id, "runtime record_id")
        _identifier(self.execution_id, "runtime execution_id")
        _sha256(self.result_hash, "runtime result_hash")
        if not isinstance(self.envelope, RuntimeResultEnvelope):
            raise IntegrationContractError(
                "runtime result record requires RuntimeResultEnvelope"
            )
        if self.execution_id != self.envelope.runtime_result.execution_id:
            raise IntegrationContractError(
                "runtime result record execution_id mismatch"
            )
        if self.result_hash != _digest(self.envelope.to_bytes()):
            raise IntegrationContractError("runtime result record hash mismatch")

    @classmethod
    def create(cls, record_id: str, envelope: RuntimeResultEnvelope) -> RuntimeResultRecord:
        if not isinstance(envelope, RuntimeResultEnvelope):
            raise IntegrationContractError(
                "runtime result record requires RuntimeResultEnvelope"
            )
        return cls(
            record_id=record_id,
            execution_id=envelope.runtime_result.execution_id,
            result_hash=_digest(envelope.to_bytes()),
            envelope=envelope,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "record_type": "NSL_RUNTIME_RESULT",
            "record_id": self.record_id,
            "execution_id": self.execution_id,
            "result_hash": self.result_hash,
            "runtime_result": self.envelope.to_data(),
        }


@dataclass(frozen=True, slots=True)
class LlmExplanationRecord:
    explanation_id: str
    execution_id: str
    runtime_result_hash: str
    model_ref: str
    content: str

    def __post_init__(self) -> None:
        _identifier(self.explanation_id, "explanation_id")
        _identifier(self.execution_id, "explanation execution_id")
        _sha256(self.runtime_result_hash, "explanation runtime_result_hash")
        _identifier(self.model_ref, "explanation model_ref")
        if type(self.content) is not str or not self.content.strip():
            raise IntegrationContractError("explanation content must be non-empty")
        if len(self.content.encode("utf-8")) > 1_048_576:
            raise IntegrationContractError("explanation content exceeds 1 MiB")

    @classmethod
    def create(
        cls,
        explanation_id: str,
        runtime_record: RuntimeResultRecord,
        model_ref: str,
        content: str,
    ) -> LlmExplanationRecord:
        if not isinstance(runtime_record, RuntimeResultRecord):
            raise IntegrationContractError(
                "explanation requires a separate runtime result record"
            )
        return cls(
            explanation_id=explanation_id,
            execution_id=runtime_record.execution_id,
            runtime_result_hash=runtime_record.result_hash,
            model_ref=model_ref,
            content=content,
        )

    def to_data(self) -> dict[str, str]:
        return {
            "record_type": "LLM_EXPLANATION",
            "explanation_id": self.explanation_id,
            "execution_id": self.execution_id,
            "runtime_result_hash": self.runtime_result_hash,
            "model_ref": self.model_ref,
            "content": self.content,
        }


@runtime_checkable
class RuntimeResultStore(Protocol):
    async def put(self, record: RuntimeResultRecord) -> None:
        ...


@runtime_checkable
class LlmExplanationStore(Protocol):
    async def put(self, record: LlmExplanationRecord) -> None:
        ...


class InMemoryRuntimeResultStore:
    def __init__(self) -> None:
        self.records: dict[str, RuntimeResultRecord] = {}

    async def put(self, record: RuntimeResultRecord) -> None:
        if not isinstance(record, RuntimeResultRecord):
            raise IntegrationContractError(
                "runtime result store requires RuntimeResultRecord"
            )
        if record.record_id in self.records:
            raise IntegrationContractError("duplicate runtime result record_id")
        self.records[record.record_id] = record


class InMemoryLlmExplanationStore:
    def __init__(self) -> None:
        self.records: dict[str, LlmExplanationRecord] = {}

    async def put(self, record: LlmExplanationRecord) -> None:
        if not isinstance(record, LlmExplanationRecord):
            raise IntegrationContractError(
                "explanation store requires LlmExplanationRecord"
            )
        if record.explanation_id in self.records:
            raise IntegrationContractError("duplicate explanation_id")
        self.records[record.explanation_id] = record
