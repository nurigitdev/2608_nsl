from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Mapping, Protocol

from .core import (
    Completeness,
    DataClassification,
    Presence,
    TypeRef,
    ValueEnvelope,
    encode_value,
)
from .ir import canonical_json
from .security import ExecutionPrincipal


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolContract:
    tool_id: str
    version: str
    capability: str
    input_types: tuple[tuple[str, TypeRef], ...]
    output_type: TypeRef
    required_scope: str
    output_classification: DataClassification
    empty_is_valid: bool = True

    @property
    def contract_hash(self) -> str:
        payload = {
            "tool_id": self.tool_id,
            "version": self.version,
            "capability": self.capability,
            "inputs": [
                {"name": name, "type": type_info.to_data()}
                for name, type_info in self.input_types
            ],
            "output": self.output_type.to_data(),
            "required_scope": self.required_scope,
            "output_classification": self.output_classification.value,
            "empty_is_valid": self.empty_is_valid,
        }
        return "sha256:" + sha256(canonical_json(payload)).hexdigest()

    def input_type(self, name: str) -> TypeRef:
        for input_name, type_info in self.input_types:
            if input_name == name:
                return type_info
        raise KeyError(name)


class ToolContractCatalog:
    def __init__(self, contracts: tuple[ToolContract, ...]) -> None:
        self._contracts = {
            (contract.tool_id, contract.version): contract for contract in contracts
        }

    def get(self, tool_id: str, version: str) -> ToolContract:
        try:
            return self._contracts[(tool_id, version)]
        except KeyError as error:
            raise KeyError(f"unknown tool contract: {tool_id}@{version}") from error


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    execution_id: str
    invocation_id: str
    node_id: str
    tool_id: str
    tool_version: str
    contract_hash: str
    arguments: Mapping[str, ValueEnvelope]
    principal: ExecutionPrincipal
    authorization_decision_ref: str


@dataclass(frozen=True, slots=True)
class ToolResultEnvelope:
    invocation_id: str
    tool_id: str
    tool_version: str
    value: Any
    type_info: TypeRef
    presence: Presence
    completeness: Completeness
    classification: DataClassification
    result_hash: str
    snapshot_ref: str | None = None

    def to_value(self, provenance_ref: str) -> ValueEnvelope:
        return ValueEnvelope(
            value=self.value,
            type_info=self.type_info,
            presence=self.presence,
            completeness=self.completeness,
            classification=self.classification,
            provenance_refs=(provenance_ref,),
        )


class ToolExecutionPort(Protocol):
    async def execute(self, request: ToolCallRequest) -> ToolResultEnvelope:
        ...


FixtureHandler = Callable[[Mapping[str, Any]], Any]


class MockToolExecutor:
    def __init__(
        self,
        catalog: ToolContractCatalog,
        handlers: Mapping[str, FixtureHandler],
    ) -> None:
        self.catalog = catalog
        self.handlers = dict(handlers)
        self.call_count = 0

    async def execute(self, request: ToolCallRequest) -> ToolResultEnvelope:
        self.call_count += 1
        contract = self.catalog.get(request.tool_id, request.tool_version)
        if request.contract_hash != contract.contract_hash:
            raise ToolExecutionError(
                "TOOL_CONTRACT_MISMATCH", f"contract changed: {request.tool_id}"
            )
        if request.tool_id not in self.handlers:
            raise ToolExecutionError(
                "MOCK_FIXTURE_NOT_FOUND", f"no fixture for {request.tool_id}"
            )
        raw_arguments = {
            name: envelope.value for name, envelope in request.arguments.items()
        }
        value = self.handlers[request.tool_id](raw_arguments)
        presence = Presence.EMPTY if value == [] or value is None else Presence.PRESENT
        result_hash = "sha256:" + sha256(
            canonical_json(encode_value(value))
        ).hexdigest()
        return ToolResultEnvelope(
            invocation_id=request.invocation_id,
            tool_id=request.tool_id,
            tool_version=request.tool_version,
            value=value,
            type_info=contract.output_type,
            presence=presence,
            completeness=Completeness.COMPLETE,
            classification=contract.output_classification,
            result_hash=result_hash,
            snapshot_ref=None,
        )
