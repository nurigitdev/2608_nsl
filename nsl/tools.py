from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .core import (
    Completeness,
    DataClassification,
    Money,
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


class UnknownToolContractError(KeyError):
    pass


class IncompatibleToolVersionError(KeyError):
    pass


class DuplicateToolContractError(ValueError):
    pass


TOOL_VERSION_COMPATIBILITY_POLICY = "EXACT"
MAX_TOOL_TIMEOUT_MS = 2_147_483_647


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Canonical business contract without customer-specific binding data."""

    tool_id: str
    version: str
    capability: str
    input_types: tuple[tuple[str, TypeRef], ...]
    output_type: TypeRef
    required_scope: str
    output_classification: DataClassification
    timeout_ms: int = 30_000
    risk: str = "READ_ONLY"
    empty_is_valid: bool = True

    def __post_init__(self) -> None:
        version_parts = (
            self.version.split(".") if isinstance(self.version, str) else []
        )
        valid_version = len(version_parts) == 3 and all(
            part.isascii()
            and part.isdigit()
            and (part == "0" or not part.startswith("0"))
            for part in version_parts
        )
        if not valid_version:
            raise ValueError("tool version must use canonical major.minor.patch form")
        if not isinstance(self.capability, str) or not self.capability.strip():
            raise ValueError("tool capability must be a non-empty string")
        names = tuple(name for name, _ in self.input_types)
        if not all(isinstance(name, str) and name.strip() for name in names):
            raise ValueError("tool input names must be non-empty strings")
        if len(names) != len(set(names)):
            raise ValueError("tool input names must be unique")
        if not all(isinstance(type_info, TypeRef) for _, type_info in self.input_types):
            raise ValueError("tool input schema must contain TypeRef values")
        if not isinstance(self.output_type, TypeRef):
            raise ValueError("tool output schema must be a TypeRef")
        if (
            type(self.timeout_ms) is not int
            or self.timeout_ms < 1
            or self.timeout_ms > MAX_TOOL_TIMEOUT_MS
        ):
            raise ValueError(
                f"tool timeout_ms must be between 1 and {MAX_TOOL_TIMEOUT_MS}"
            )

    @property
    def contract_hash(self) -> str:
        payload = {
            "tool_id": self.tool_id,
            "version": self.version,
            "capability": self.capability,
            "risk": self.risk,
            "inputs": [
                {"name": name, "type": type_info.to_data()}
                for name, type_info in self.input_types
            ],
            "output": self.output_type.to_data(),
            "required_scope": self.required_scope,
            "output_classification": self.output_classification.value,
            "timeout_ms": self.timeout_ms,
            "empty_is_valid": self.empty_is_valid,
        }
        return "sha256:" + sha256(canonical_json(payload)).hexdigest()

    def input_type(self, name: str) -> TypeRef:
        for input_name, type_info in self.input_types:
            if input_name == name:
                return type_info
        raise KeyError(name)


@runtime_checkable
class ToolRegistry(Protocol):
    def get(self, tool_id: str, version: str) -> ToolContract:
        ...

    def resolve(self, tool_id: str, requested_version: str) -> ToolContract:
        ...


class ToolContractCatalog:
    def __init__(self, contracts: tuple[ToolContract, ...]) -> None:
        self._contracts: dict[tuple[str, str], ToolContract] = {}
        for contract in contracts:
            key = (contract.tool_id, contract.version)
            if key in self._contracts:
                raise DuplicateToolContractError(
                    f"duplicate tool contract: {contract.tool_id}@{contract.version}"
                )
            self._contracts[key] = contract

    def get(self, tool_id: str, version: str) -> ToolContract:
        try:
            return self._contracts[(tool_id, version)]
        except KeyError as error:
            raise KeyError(f"unknown tool contract: {tool_id}@{version}") from error

    def resolve(self, tool_id: str, requested_version: str) -> ToolContract:
        available_versions = tuple(
            version
            for registered_tool_id, version in self._contracts
            if registered_tool_id == tool_id
        )
        if not available_versions:
            raise UnknownToolContractError(
                f"unknown tool contract: {tool_id}@{requested_version}"
            )
        try:
            return self._contracts[(tool_id, requested_version)]
        except KeyError as error:
            raise IncompatibleToolVersionError(
                f"incompatible tool version: {tool_id}@{requested_version}"
            ) from error


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


def tool_result_hash(value: Any) -> str:
    return "sha256:" + sha256(canonical_json(encode_value(value))).hexdigest()


def value_conforms_to_type(value: Any, type_info: TypeRef) -> bool:
    if type_info.kind == "primitive":
        expected = {
            "Bool": bool,
            "Date": date,
            "DateTime": datetime,
            "Decimal": Decimal,
            "Int": int,
            "String": str,
            "Year": int,
        }.get(type_info.name)
        if expected is None:
            return False
        if type_info.name == "Date":
            return type(value) is date
        if type_info.name in {"Int", "Year"}:
            return type(value) is int
        return isinstance(value, expected)
    if type_info.kind == "domain":
        return isinstance(value, str)
    if type_info.kind == "money":
        return isinstance(value, Money) and value.currency == type_info.currency
    if type_info.kind == "list":
        return (
            isinstance(value, list)
            and type_info.item is not None
            and all(value_conforms_to_type(item, type_info.item) for item in value)
        )
    if type_info.kind == "record":
        expected_fields = dict(type_info.fields)
        return (
            isinstance(value, Mapping)
            and set(value) == set(expected_fields)
            and all(
                value_conforms_to_type(value[name], field_type)
                for name, field_type in expected_fields.items()
            )
        )
    if type_info.kind == "enum":
        return isinstance(value, str) and value in type_info.enum_values
    return False


class ToolContractValidator:
    __slots__ = ()

    def validate_request(
        self, request: ToolCallRequest, contract: ToolContract
    ) -> None:
        if request.contract_hash != contract.contract_hash:
            raise ToolExecutionError(
                "TOOL_CONTRACT_MISMATCH", f"contract changed: {request.tool_id}"
            )
        expected_arguments = dict(contract.input_types)
        if set(request.arguments) != set(expected_arguments):
            raise ToolExecutionError(
                "TOOL_ARGUMENT_MISMATCH",
                f"argument names do not match contract: {request.tool_id}",
            )
        for name, expected_type in expected_arguments.items():
            argument = request.arguments[name]
            if (
                not isinstance(argument, ValueEnvelope)
                or argument.type_info != expected_type
                or not value_conforms_to_type(argument.value, expected_type)
            ):
                raise ToolExecutionError(
                    "TOOL_ARGUMENT_TYPE_MISMATCH",
                    f"argument type does not match contract: {request.tool_id}.{name}",
                )

    def validate_result_structure(
        self, result: Any, request: ToolCallRequest
    ) -> ToolResultEnvelope:
        if not isinstance(result, ToolResultEnvelope):
            raise ToolExecutionError(
                "MALFORMED_TOOL_RESULT",
                f"tool result is not a structured envelope: {request.tool_id}",
            )
        if (
            result.invocation_id != request.invocation_id
            or result.tool_id != request.tool_id
            or result.tool_version != request.tool_version
        ):
            raise ToolExecutionError(
                "TOOL_RESULT_IDENTITY_MISMATCH",
                f"tool result identity does not match request: {request.tool_id}",
            )
        if (
            not isinstance(result.type_info, TypeRef)
            or not isinstance(result.presence, Presence)
            or not isinstance(result.completeness, Completeness)
            or not isinstance(result.classification, DataClassification)
            or (
                result.snapshot_ref is not None
                and (
                    not isinstance(result.snapshot_ref, str)
                    or not result.snapshot_ref
                )
            )
        ):
            raise ToolExecutionError(
                "MALFORMED_TOOL_RESULT",
                f"tool result metadata is malformed: {request.tool_id}",
            )
        return result

    def validate_result_value(self, value: Any, contract: ToolContract) -> None:
        if not value_conforms_to_type(value, contract.output_type):
            raise ToolExecutionError(
                "OUTPUT_CONTRACT_VIOLATION",
                f"result does not match contract: {contract.tool_id}",
            )

    def validate_result(
        self, result: ToolResultEnvelope, contract: ToolContract
    ) -> None:
        if (
            result.type_info != contract.output_type
            or result.classification != contract.output_classification
        ):
            raise ToolExecutionError(
                "OUTPUT_CONTRACT_VIOLATION",
                f"result metadata does not match contract: {contract.tool_id}",
            )
        self.validate_result_value(result.value, contract)

    def validate_result_hash(self, result: ToolResultEnvelope) -> None:
        if result.result_hash != tool_result_hash(result.value):
            raise ToolExecutionError(
                "TOOL_RESULT_HASH_MISMATCH",
                f"tool result hash does not match value: {result.tool_id}",
            )


@runtime_checkable
class ToolExecutor(Protocol):
    async def execute(self, request: ToolCallRequest) -> ToolResultEnvelope:
        ...


ToolExecutionPort = ToolExecutor


async def execute_with_timeout(
    executor: ToolExecutor,
    request: ToolCallRequest,
    timeout_ms: int,
) -> ToolResultEnvelope:
    try:
        return await asyncio.wait_for(
            executor.execute(request), timeout=timeout_ms / 1000
        )
    except TimeoutError as error:
        raise ToolExecutionError(
            "TOOL_TIMEOUT",
            f"tool timed out after {timeout_ms} ms: {request.tool_id}",
        ) from error


FixtureHandler = Callable[[Mapping[str, Any]], Any]


class MockToolExecutor:
    def __init__(
        self,
        catalog: ToolRegistry,
        handlers: Mapping[str, FixtureHandler],
    ) -> None:
        self.catalog = catalog
        self.handlers = dict(handlers)
        self.validator = ToolContractValidator()
        self.call_count = 0

    async def execute(self, request: ToolCallRequest) -> ToolResultEnvelope:
        try:
            contract = self.catalog.resolve(request.tool_id, request.tool_version)
        except (UnknownToolContractError, IncompatibleToolVersionError) as error:
            raise ToolExecutionError(
                "TOOL_CONTRACT_MISMATCH", f"contract unavailable: {request.tool_id}"
            ) from error
        self.validator.validate_request(request, contract)
        if request.tool_id not in self.handlers:
            raise ToolExecutionError(
                "MOCK_FIXTURE_NOT_FOUND", f"no fixture for {request.tool_id}"
            )
        raw_arguments = {
            name: envelope.value for name, envelope in request.arguments.items()
        }
        self.call_count += 1
        value = self.handlers[request.tool_id](raw_arguments)
        self.validator.validate_result_value(value, contract)
        presence = Presence.EMPTY if value == [] or value is None else Presence.PRESENT
        result_hash = tool_result_hash(value)
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
