from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from ..core import (
    Completeness,
    Presence,
    decode_value,
    encode_value,
)
from ..data_protection import ensure_no_credential_material
from ..tools import (
    IncompatibleToolVersionError,
    ToolCallRequest,
    ToolContractValidator,
    ToolExecutionError,
    ToolRegistry,
    ToolResultEnvelope,
    UnknownToolContractError,
    tool_result_hash,
)


class MCPBindingNotFoundError(KeyError):
    pass


class DuplicateMCPBindingError(ValueError):
    pass


class MCPClientError(RuntimeError):
    """Expected protocol or transport failure from an MCP client bridge."""


@dataclass(frozen=True, slots=True)
class MCPToolBinding:
    binding_id: str
    tenant_id: str
    tool_id: str
    tool_version: str
    server_ref: str
    tool_name: str
    result_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        fields = {
            "binding_id": self.binding_id,
            "tenant_id": self.tenant_id,
            "tool_id": self.tool_id,
            "tool_version": self.tool_version,
            "server_ref": self.server_ref,
            "tool_name": self.tool_name,
        }
        if not all(isinstance(value, str) and value.strip() for value in fields.values()):
            raise ValueError("MCP binding identifiers must be non-empty strings")
        if not isinstance(self.result_path, tuple) or not all(
            isinstance(part, str) and part.strip() for part in self.result_path
        ):
            raise ValueError("MCP result_path must contain non-empty strings")
        ensure_no_credential_material(fields, "MCPToolBinding")


@runtime_checkable
class MCPBindingResolver(Protocol):
    def resolve(
        self,
        tenant_id: str,
        tool_id: str,
        tool_version: str,
    ) -> MCPToolBinding:
        ...


class MCPBindingRegistry:
    def __init__(self, bindings: tuple[MCPToolBinding, ...]) -> None:
        self._bindings: dict[tuple[str, str, str], MCPToolBinding] = {}
        for binding in bindings:
            if not isinstance(binding, MCPToolBinding):
                raise ValueError("MCP binding registry accepts MCPToolBinding values")
            key = (binding.tenant_id, binding.tool_id, binding.tool_version)
            if key in self._bindings:
                raise DuplicateMCPBindingError(
                    f"duplicate MCP binding: {binding.tool_id}@{binding.tool_version}"
                )
            self._bindings[key] = binding

    def resolve(
        self,
        tenant_id: str,
        tool_id: str,
        tool_version: str,
    ) -> MCPToolBinding:
        try:
            return self._bindings[(tenant_id, tool_id, tool_version)]
        except KeyError as error:
            raise MCPBindingNotFoundError(
                f"MCP binding not found: {tool_id}@{tool_version}"
            ) from error


@dataclass(frozen=True, slots=True)
class MCPCallContext:
    execution_id: str
    invocation_id: str
    tenant_id: str
    subject_id: str
    auth_context_ref: str
    authorization_decision_ref: str


@dataclass(frozen=True, slots=True)
class MCPCallResult:
    content: tuple[Mapping[str, Any], ...]
    structured_content: Mapping[str, Any] | None = None
    is_error: bool | None = None


@runtime_checkable
class MCPClientPort(Protocol):
    async def call_tool(
        self,
        *,
        server_ref: str,
        name: str,
        arguments: Mapping[str, Any],
        context: MCPCallContext,
    ) -> MCPCallResult:
        ...


class MCPToolExecutor:
    """Maps canonical NSL tool calls to an SDK-neutral MCP client port."""

    def __init__(
        self,
        catalog: ToolRegistry,
        bindings: MCPBindingResolver,
        client: MCPClientPort,
    ) -> None:
        self.catalog = catalog
        self.bindings = bindings
        self.client = client
        self.validator = ToolContractValidator()

    async def execute(self, request: ToolCallRequest) -> ToolResultEnvelope:
        try:
            contract = self.catalog.resolve(request.tool_id, request.tool_version)
        except (UnknownToolContractError, IncompatibleToolVersionError) as error:
            raise ToolExecutionError(
                "TOOL_CONTRACT_MISMATCH",
                f"contract unavailable: {request.tool_id}",
            ) from error
        self.validator.validate_request(request, contract)

        try:
            binding = self.bindings.resolve(
                request.principal.tenant_id,
                request.tool_id,
                request.tool_version,
            )
        except MCPBindingNotFoundError as error:
            raise ToolExecutionError(
                "MCP_BINDING_NOT_FOUND",
                f"MCP binding unavailable: {request.tool_id}",
            ) from error
        if (
            not isinstance(binding, MCPToolBinding)
            or binding.tenant_id != request.principal.tenant_id
            or binding.tool_id != request.tool_id
            or binding.tool_version != request.tool_version
        ):
            raise ToolExecutionError(
                "MCP_BINDING_MISMATCH",
                f"MCP binding identity mismatch: {request.tool_id}",
            )

        arguments = {
            name: encode_value(envelope.value)
            for name, envelope in request.arguments.items()
        }
        context = MCPCallContext(
            execution_id=request.execution_id,
            invocation_id=request.invocation_id,
            tenant_id=request.principal.tenant_id,
            subject_id=request.principal.subject_id,
            auth_context_ref=request.principal.auth_context_ref,
            authorization_decision_ref=request.authorization_decision_ref,
        )
        try:
            outcome = await self.client.call_tool(
                server_ref=binding.server_ref,
                name=binding.tool_name,
                arguments=arguments,
                context=context,
            )
        except MCPClientError as error:
            raise ToolExecutionError(
                "MCP_CLIENT_ERROR",
                f"MCP client failed: {request.tool_id}",
            ) from error

        self._validate_call_result(outcome, request)
        if outcome.is_error is True:
            raise ToolExecutionError(
                "MCP_TOOL_ERROR",
                f"MCP tool reported failure: {request.tool_id}",
            )
        assert outcome.structured_content is not None
        encoded_value: Any = outcome.structured_content
        for part in binding.result_path:
            if not isinstance(encoded_value, Mapping) or part not in encoded_value:
                raise ToolExecutionError(
                    "MCP_RESULT_PATH_MISSING",
                    f"MCP result path is missing: {request.tool_id}",
                )
            encoded_value = encoded_value[part]
        try:
            value = decode_value(encoded_value)
        except ValueError as error:
            raise ToolExecutionError(
                "MCP_MALFORMED_RESULT",
                f"MCP structured result is malformed: {request.tool_id}",
            ) from error
        self.validator.validate_result_value(value, contract)
        return ToolResultEnvelope(
            invocation_id=request.invocation_id,
            tool_id=request.tool_id,
            tool_version=request.tool_version,
            value=value,
            type_info=contract.output_type,
            presence=(
                Presence.EMPTY if value is None or value == [] else Presence.PRESENT
            ),
            completeness=Completeness.COMPLETE,
            classification=contract.output_classification,
            result_hash=tool_result_hash(value),
        )

    @staticmethod
    def _validate_call_result(
        outcome: Any,
        request: ToolCallRequest,
    ) -> None:
        valid = (
            isinstance(outcome, MCPCallResult)
            and isinstance(outcome.content, tuple)
            and all(isinstance(block, Mapping) for block in outcome.content)
            and (outcome.is_error is None or type(outcome.is_error) is bool)
        )
        if not valid:
            raise ToolExecutionError(
                "MCP_MALFORMED_RESULT",
                f"MCP result envelope is malformed: {request.tool_id}",
            )
        if outcome.is_error is not True and not isinstance(
            outcome.structured_content, Mapping
        ):
            raise ToolExecutionError(
                "MCP_STRUCTURED_CONTENT_REQUIRED",
                f"MCP structuredContent is required: {request.tool_id}",
            )
