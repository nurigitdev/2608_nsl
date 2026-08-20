from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import asyncio

import pytest

from nsl.adapters.mcp import (
    DuplicateMCPBindingError,
    MCPBindingNotFoundError,
    MCPBindingRegistry,
    MCPBindingResolver,
    MCPCallResult,
    MCPClientError,
    MCPClientPort,
    MCPToolBinding,
    MCPToolExecutor,
)
from nsl.core import (
    Completeness,
    DataClassification,
    Money,
    Presence,
    STRING,
    ValueEnvelope,
    encode_value,
    list_type,
    money_type,
    record_type,
)
from nsl.data_protection import CredentialMaterialError
from nsl.audit import InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.tools import (
    InMemoryToolMeasurementSink,
    MeasuringToolExecutor,
    NullToolMeasurementSink,
    ToolCallRequest,
    ToolContract,
    ToolContractCatalog,
    ToolExecutionError,
    ToolExecutionMeasurement,
    ToolExecutionOutcome,
    ToolExecutionPort,
    ToolMeasurementSink,
    ToolResultEnvelope,
    tool_result_hash,
)
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def build_contract(*, input_type=STRING, output_type=STRING) -> ToolContract:
    return ToolContract(
        tool_id="TEST.ECHO",
        version="1.0.0",
        capability="READ",
        input_types=(("value", input_type),),
        output_type=output_type,
        required_scope="test:echo:read",
        output_classification=DataClassification.CONFIDENTIAL,
    )


def build_request(contract: ToolContract, value="hello") -> ToolCallRequest:
    principal = replace(
        build_principal(),
        scopes=build_principal().scopes | {"test:echo:read"},
    )
    return ToolCallRequest(
        execution_id="exec-mcp-001",
        invocation_id="inv0001",
        node_id="node-read-001",
        tool_id=contract.tool_id,
        tool_version=contract.version,
        contract_hash=contract.contract_hash,
        arguments={
            "value": ValueEnvelope.complete(
                value,
                contract.input_type("value"),
                DataClassification.INTERNAL,
            )
        },
        principal=principal,
        authorization_decision_ref="authz-mcp-001",
    )


def build_binding(**overrides) -> MCPToolBinding:
    values = {
        "binding_id": "binding-echo-001",
        "tenant_id": "tenant-nex",
        "tool_id": "TEST.ECHO",
        "tool_version": "1.0.0",
        "server_ref": "mcp-server-finance",
        "tool_name": "echo_value",
        "result_path": ("value",),
    }
    values.update(overrides)
    return MCPToolBinding(**values)


class StubMCPClient:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls = []

    async def call_tool(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def build_executor(
    outcome,
    *,
    contract: ToolContract | None = None,
    binding: MCPToolBinding | None = None,
):
    selected_contract = contract or build_contract()
    selected_binding = binding or build_binding()
    client = StubMCPClient(outcome)
    executor = MCPToolExecutor(
        ToolContractCatalog((selected_contract,)),
        MCPBindingRegistry((selected_binding,)),
        client,
    )
    return selected_contract, client, executor


def test_exe_003_mcp_executor_maps_canonical_request_and_result() -> None:
    outcome = MCPCallResult(
        content=({"type": "text", "text": '"echo"'},),
        structured_content={"value": "echo"},
        is_error=False,
    )
    contract, client, executor = build_executor(outcome)
    request = build_request(contract)

    result = asyncio.run(executor.execute(request))

    assert isinstance(executor, ToolExecutionPort)
    assert isinstance(executor.bindings, MCPBindingResolver)
    assert isinstance(client, MCPClientPort)
    assert result.value == "echo"
    assert result.presence is Presence.PRESENT
    assert result.completeness is Completeness.COMPLETE
    assert result.classification is DataClassification.CONFIDENTIAL
    assert result.result_hash == tool_result_hash("echo")
    assert result.snapshot_ref is None
    call = client.calls[0]
    assert call["server_ref"] == "mcp-server-finance"
    assert call["name"] == "echo_value"
    assert call["arguments"] == {"value": "hello"}
    assert call["context"].execution_id == request.execution_id
    assert call["context"].invocation_id == request.invocation_id
    assert call["context"].tenant_id == request.principal.tenant_id
    assert call["context"].subject_id == request.principal.subject_id
    assert call["context"].auth_context_ref == request.principal.auth_context_ref
    assert (
        call["context"].authorization_decision_ref
        == request.authorization_decision_ref
    )


def test_mcp_executor_encodes_arguments_and_decodes_structured_values() -> None:
    krw = money_type("KRW")
    amount = Money(Decimal("1234.50"), "KRW")
    contract = build_contract(input_type=krw, output_type=krw)
    outcome = MCPCallResult(
        content=(),
        structured_content={
            "value": {
                "$type": "Money",
                "amount": "1234.50",
                "currency": "KRW",
            }
        },
    )
    _, client, executor = build_executor(outcome, contract=contract)

    result = asyncio.run(executor.execute(build_request(contract, amount)))

    assert client.calls[0]["arguments"] == {
        "value": {
            "$type": "Money",
            "amount": "1234.50",
            "currency": "KRW",
        }
    }
    assert result.value == amount


def test_mcp_executor_supports_root_result_and_empty_collection() -> None:
    record_contract = build_contract(
        output_type=record_type("EchoResult", value=STRING)
    )
    root_outcome = MCPCallResult(
        content=(),
        structured_content={"value": "echo"},
    )
    _, _, root_executor = build_executor(
        root_outcome,
        contract=record_contract,
        binding=build_binding(result_path=()),
    )
    root_result = asyncio.run(root_executor.execute(build_request(record_contract)))
    assert root_result.value == {"value": "echo"}

    list_contract = build_contract(output_type=list_type(STRING))
    empty_outcome = MCPCallResult(content=(), structured_content={"items": []})
    _, _, empty_executor = build_executor(
        empty_outcome,
        contract=list_contract,
        binding=build_binding(result_path=("items",)),
    )
    empty_result = asyncio.run(empty_executor.execute(build_request(list_contract)))
    assert empty_result.value == []
    assert empty_result.presence is Presence.EMPTY


def test_mcp_tool_error_is_fail_closed_without_exposing_content() -> None:
    outcome = MCPCallResult(
        content=({"type": "text", "text": "account secret 123"},),
        is_error=True,
    )
    contract, client, executor = build_executor(outcome)

    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(executor.execute(build_request(contract)))

    assert captured.value.code == "MCP_TOOL_ERROR"
    assert "account secret" not in str(captured.value)
    assert len(client.calls) == 1


def test_mcp_client_failure_is_normalized() -> None:
    contract, client, executor = build_executor(MCPClientError("transport down"))

    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(executor.execute(build_request(contract)))

    assert captured.value.code == "MCP_CLIENT_ERROR"
    assert "transport down" not in str(captured.value)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("outcome", "expected_code"),
    [
        (object(), "MCP_MALFORMED_RESULT"),
        (
            MCPCallResult(content=[], structured_content={"value": "echo"}),
            "MCP_MALFORMED_RESULT",
        ),
        (
            MCPCallResult(content=("not-a-block",), structured_content={"value": "echo"}),
            "MCP_MALFORMED_RESULT",
        ),
        (
            MCPCallResult(
                content=(), structured_content={"value": "echo"}, is_error="false"
            ),
            "MCP_MALFORMED_RESULT",
        ),
        (MCPCallResult(content=()), "MCP_STRUCTURED_CONTENT_REQUIRED"),
        (
            MCPCallResult(content=(), structured_content=["echo"]),
            "MCP_STRUCTURED_CONTENT_REQUIRED",
        ),
    ],
)
def test_mcp_executor_rejects_malformed_call_results(outcome, expected_code) -> None:
    contract, _, executor = build_executor(outcome)

    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(executor.execute(build_request(contract)))

    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    "structured_content",
    [
        {},
        {"value": {"other": "echo"}},
    ],
)
def test_mcp_executor_rejects_missing_result_path(structured_content) -> None:
    binding = build_binding(result_path=("value", "nested"))
    outcome = MCPCallResult(content=(), structured_content=structured_content)
    contract, _, executor = build_executor(outcome, binding=binding)

    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(executor.execute(build_request(contract)))

    assert captured.value.code == "MCP_RESULT_PATH_MISSING"


def test_mcp_executor_rejects_malformed_encoded_and_contract_values() -> None:
    krw = money_type("KRW")
    money_contract = build_contract(output_type=krw)
    malformed = MCPCallResult(
        content=(),
        structured_content={
            "value": {"$type": "Money", "amount": 1, "currency": "KRW"}
        },
    )
    _, _, malformed_executor = build_executor(malformed, contract=money_contract)
    with pytest.raises(ToolExecutionError) as malformed_error:
        asyncio.run(malformed_executor.execute(build_request(money_contract)))
    assert malformed_error.value.code == "MCP_MALFORMED_RESULT"

    wrong_type = MCPCallResult(
        content=(), structured_content={"value": 123}
    )
    string_contract, _, wrong_type_executor = build_executor(wrong_type)
    with pytest.raises(ToolExecutionError) as contract_error:
        asyncio.run(wrong_type_executor.execute(build_request(string_contract)))
    assert contract_error.value.code == "OUTPUT_CONTRACT_VIOLATION"


@pytest.mark.parametrize("catalog_kind", ["unknown", "incompatible"])
def test_mcp_executor_rejects_unavailable_contract_before_client(catalog_kind) -> None:
    contract = build_contract()
    registered = ()
    if catalog_kind == "incompatible":
        registered = (replace(contract, version="2.0.0"),)
    client = StubMCPClient(
        MCPCallResult(content=(), structured_content={"value": "echo"})
    )
    executor = MCPToolExecutor(
        ToolContractCatalog(registered),
        MCPBindingRegistry((build_binding(),)),
        client,
    )

    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(executor.execute(build_request(contract)))

    assert captured.value.code == "TOOL_CONTRACT_MISMATCH"
    assert client.calls == []


def test_mcp_executor_validates_contract_before_binding_and_client() -> None:
    outcome = MCPCallResult(content=(), structured_content={"value": "echo"})
    contract, client, executor = build_executor(outcome)
    forged = replace(build_request(contract), contract_hash="sha256:forged")

    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(executor.execute(forged))

    assert captured.value.code == "TOOL_CONTRACT_MISMATCH"
    assert client.calls == []


def test_mcp_executor_normalizes_missing_tenant_binding_before_client() -> None:
    contract = build_contract()
    client = StubMCPClient(
        MCPCallResult(content=(), structured_content={"value": "echo"})
    )
    executor = MCPToolExecutor(
        ToolContractCatalog((contract,)),
        MCPBindingRegistry(()),
        client,
    )

    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(executor.execute(build_request(contract)))

    assert captured.value.code == "MCP_BINDING_NOT_FOUND"
    assert client.calls == []


def test_mcp_binding_registry_is_exact_tenant_scoped_and_duplicate_safe() -> None:
    binding = build_binding()
    registry = MCPBindingRegistry((binding,))
    assert registry.resolve("tenant-nex", "TEST.ECHO", "1.0.0") is binding

    with pytest.raises(MCPBindingNotFoundError, match="binding not found"):
        registry.resolve("tenant-other", "TEST.ECHO", "1.0.0")
    with pytest.raises(DuplicateMCPBindingError, match="duplicate MCP binding"):
        MCPBindingRegistry((binding, binding))
    with pytest.raises(ValueError, match="MCPToolBinding"):
        MCPBindingRegistry(("not-a-binding",))


@pytest.mark.parametrize(
    "field_name",
    [
        "binding_id",
        "tenant_id",
        "tool_id",
        "tool_version",
        "server_ref",
        "tool_name",
    ],
)
def test_mcp_binding_rejects_empty_identifiers(field_name) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        build_binding(**{field_name: ""})


@pytest.mark.parametrize("result_path", [["value"], ("",), (1,)])
def test_mcp_binding_rejects_invalid_result_path(result_path) -> None:
    with pytest.raises(ValueError, match="result_path"):
        build_binding(result_path=result_path)


def test_mcp_binding_rejects_embedded_credential_material() -> None:
    with pytest.raises(CredentialMaterialError, match="MCPToolBinding"):
        build_binding(server_ref="Authorization: Bearer abc123")


@pytest.mark.parametrize(
    "resolved_binding",
    [build_binding(tool_id="TEST.OTHER"), object()],
)
def test_mcp_executor_rejects_resolver_identity_mismatch(resolved_binding) -> None:
    contract = build_contract()
    client = StubMCPClient(
        MCPCallResult(content=(), structured_content={"value": "echo"})
    )

    class MismatchedResolver:
        def resolve(self, tenant_id, tool_id, tool_version):
            return resolved_binding

    executor = MCPToolExecutor(
        ToolContractCatalog((contract,)), MismatchedResolver(), client
    )
    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(executor.execute(build_request(contract)))

    assert captured.value.code == "MCP_BINDING_MISMATCH"
    assert client.calls == []


def test_runtime_executes_project_skill_through_mcp_adapter() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    bindings = MCPBindingRegistry(
        (
            MCPToolBinding(
                "binding-parent",
                "tenant-nex",
                "PROJECT.LIST_PARENT_PROJECTS",
                "1.0.0",
                "mcp-project-server",
                "list_parent_projects",
                ("items",),
            ),
            MCPToolBinding(
                "binding-child",
                "tenant-nex",
                "PROJECT.LIST_CHILD_PROJECTS",
                "1.0.0",
                "mcp-project-server",
                "list_child_projects",
                ("items",),
            ),
        )
    )

    class ProjectMCPClient:
        def __init__(self) -> None:
            self.calls = []

        async def call_tool(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["name"] == "list_parent_projects":
                value = [
                    {
                        "project_code": "PARENT-001",
                        "budget": Money(Decimal("100000000"), "KRW"),
                    }
                ]
            else:
                assert kwargs["arguments"] == {
                    "parent_project": "PARENT-001",
                    "year": 2026,
                }
                value = [
                    {
                        "project_code": "CHILD-001",
                        "parent_project": "PARENT-001",
                        "expense_amount": Money(Decimal("87500000"), "KRW"),
                    }
                ]
            return MCPCallResult(
                content=(),
                structured_content={"items": encode_value(value)},
            )

    client = ProjectMCPClient()
    sink = InMemoryToolMeasurementSink()
    request = ExecutionRequest(
        execution_id="exec-mcp-runtime",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )
    result = asyncio.run(
        RuntimeEngine(catalog, tool_measurement_sink=sink).execute(
            skill,
            request,
            MCPToolExecutor(catalog, bindings, client),
            InMemoryAuditSink(),
        )
    )

    assert result.status.value == "COMPLETED"
    assert [call["name"] for call in client.calls] == [
        "list_parent_projects",
        "list_child_projects",
    ]
    assert result.outputs[0].values["remaining"] == Money(
        Decimal("12500000"), "KRW"
    )
    assert len(sink.measurements) == 2


class ManualMonotonicClock:
    def __init__(self) -> None:
        self.now_ns = 0

    def monotonic_ns(self) -> int:
        return self.now_ns

    def advance_ns(self, value: int) -> None:
        self.now_ns += value


def build_tool_result(request: ToolCallRequest, value="echo") -> ToolResultEnvelope:
    return ToolResultEnvelope(
        invocation_id=request.invocation_id,
        tool_id=request.tool_id,
        tool_version=request.tool_version,
        value=value,
        type_info=STRING,
        presence=Presence.PRESENT,
        completeness=Completeness.COMPLETE,
        classification=DataClassification.CONFIDENTIAL,
        result_hash=tool_result_hash(value),
    )


class AdvancingToolExecutor:
    def __init__(self, clock, deltas, delegate=None, error=None) -> None:
        self.clock = clock
        self.deltas = iter(deltas)
        self.delegate = delegate
        self.error = error

    async def execute(self, request):
        self.clock.advance_ns(next(self.deltas))
        if self.error is not None:
            raise self.error
        if self.delegate is not None:
            return await self.delegate.execute(request)
        return build_tool_result(request)


@pytest.mark.parametrize(
    ("elapsed_ns", "expected_ns", "expected_ms"),
    [
        (-1, 0, 0),
        (0, 0, 0),
        (999_999, 999_999, 0),
        (1_000_000, 1_000_000, 1),
        (1_500_000, 1_500_000, 1),
    ],
)
def test_exe_006_measuring_executor_records_duration_boundaries(
    elapsed_ns, expected_ns, expected_ms
) -> None:
    contract = build_contract()
    request = build_request(contract)
    clock = ManualMonotonicClock()
    sink = InMemoryToolMeasurementSink()
    executor = MeasuringToolExecutor(
        AdvancingToolExecutor(clock, [elapsed_ns]), clock, sink
    )

    result = asyncio.run(executor.execute(request))

    assert result.value == "echo"
    assert isinstance(sink, ToolMeasurementSink)
    measurement = sink.measurements[0]
    assert measurement.execution_id == request.execution_id
    assert measurement.invocation_id == request.invocation_id
    assert measurement.node_id == request.node_id
    assert measurement.tool_id == request.tool_id
    assert measurement.tool_version == request.tool_version
    assert measurement.tenant_id == request.principal.tenant_id
    assert measurement.outcome is ToolExecutionOutcome.RETURNED
    assert measurement.duration_ns == expected_ns
    assert measurement.duration_ms == expected_ms


@pytest.mark.parametrize(
    ("error", "expected_outcome"),
    [
        (RuntimeError("provider failed"), ToolExecutionOutcome.ERROR),
        (asyncio.CancelledError(), ToolExecutionOutcome.CANCELLED),
    ],
)
def test_measuring_executor_records_error_and_cancellation(error, expected_outcome) -> None:
    contract = build_contract()
    request = build_request(contract)
    clock = ManualMonotonicClock()
    sink = InMemoryToolMeasurementSink()
    executor = MeasuringToolExecutor(
        AdvancingToolExecutor(clock, [2_000_000], error=error),
        clock,
        sink,
    )

    with pytest.raises(type(error)):
        asyncio.run(executor.execute(request))

    assert sink.measurements[0].outcome is expected_outcome
    assert sink.measurements[0].duration_ns == 2_000_000


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"execution_id": ""}, "identifiers"),
        ({"outcome": "RETURNED"}, "outcome"),
        ({"duration_ns": -1}, "duration_ns"),
        ({"duration_ns": True}, "duration_ns"),
    ],
)
def test_tool_execution_measurement_rejects_invalid_boundaries(changes, message) -> None:
    values = {
        "execution_id": "exec-1",
        "invocation_id": "inv1",
        "node_id": "node1",
        "tool_id": "TEST.ECHO",
        "tool_version": "1.0.0",
        "tenant_id": "tenant-nex",
        "outcome": ToolExecutionOutcome.RETURNED,
        "duration_ns": 0,
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        ToolExecutionMeasurement(**values)


def test_runtime_measures_all_tool_ports_without_changing_audit_payloads() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    clock = ManualMonotonicClock()
    sink = InMemoryToolMeasurementSink()
    delegate = build_mock_executor(catalog)
    tools = AdvancingToolExecutor(
        clock,
        [1_500_000, 2_999_999],
        delegate=delegate,
    )
    audit = InMemoryAuditSink()
    request = ExecutionRequest(
        execution_id="exec-timed-runtime",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )

    result = asyncio.run(
        RuntimeEngine(
            catalog,
            runtime_clock=clock,
            tool_measurement_sink=sink,
        ).execute(skill, request, tools, audit)
    )

    assert result.status.value == "COMPLETED"
    assert [item.duration_ns for item in sink.measurements] == [
        1_500_000,
        2_999_999,
    ]
    assert [item.duration_ms for item in sink.measurements] == [1, 2]
    assert all(
        item.outcome is ToolExecutionOutcome.RETURNED
        for item in sink.measurements
    )
    tool_events = [
        event
        for event in audit.events
        if event.event_type in {"TOOL_STARTED", "TOOL_COMPLETED"}
    ]
    assert all("duration_ns" not in event.payload for event in tool_events)


def test_null_measurement_sink_is_non_persistent() -> None:
    measurement = ToolExecutionMeasurement(
        execution_id="exec-1",
        invocation_id="inv1",
        node_id="node1",
        tool_id="TEST.ECHO",
        tool_version="1.0.0",
        tenant_id="tenant-nex",
        outcome=ToolExecutionOutcome.RETURNED,
        duration_ns=1,
    )
    assert NullToolMeasurementSink().record(measurement) is None
