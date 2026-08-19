from __future__ import annotations

import asyncio
import ast
from dataclasses import fields, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from nsl.audit import InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.core import (
    BOOL,
    DATETIME,
    DATE,
    DECIMAL,
    INT,
    STRING,
    YEAR,
    ExecutionStatus,
    Money,
    TypeRef,
    ValueEnvelope,
    domain,
    enum_type,
    list_type,
    money_type,
    record_type,
)
from nsl.diagnostics import DiagnosticCode
from nsl.ir import LetStatement, LiteralExpr, ReadExpr
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.tools import (
    DuplicateToolContractError,
    ToolContract,
    ToolContractCatalog,
    ToolCallRequest,
    ToolExecutor,
    ToolExecutionError,
    MockToolExecutor,
    value_conforms_to_type,
    ToolRegistry,
)
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")


def execution_request(execution_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )


def tool_request(**overrides) -> ToolCallRequest:
    catalog = build_tool_catalog()
    contract = catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    values = {
        "execution_id": "exec-tool",
        "invocation_id": "inv0001",
        "node_id": "read0001",
        "tool_id": contract.tool_id,
        "tool_version": contract.version,
        "contract_hash": contract.contract_hash,
        "arguments": {
            "year": ValueEnvelope.complete(2026, YEAR),
            "team_id": ValueEnvelope.complete(
                "TEAM-FINANCE", contract.input_type("team_id")
            ),
        },
        "principal": build_principal(),
        "authorization_decision_ref": "authz-tool",
    }
    values.update(overrides)
    return ToolCallRequest(**values)


def test_tol_001_tool_registry_is_an_explicit_runtime_contract() -> None:
    registry = build_tool_catalog()

    assert isinstance(registry, ToolRegistry)
    contract = registry.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    assert registry.resolve(contract.tool_id, contract.version) is contract


def test_tol_002_tool_identity_is_unique_per_global_id_and_version() -> None:
    catalog = build_tool_catalog()
    contract = catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    next_version = replace(contract, version="1.0.1")
    versioned = ToolContractCatalog((contract, next_version))

    assert versioned.resolve(contract.tool_id, "1.0.0") is contract
    assert versioned.resolve(contract.tool_id, "1.0.1") is next_version
    with pytest.raises(DuplicateToolContractError, match="duplicate tool contract"):
        ToolContractCatalog((contract, contract))


@pytest.mark.parametrize("invalid", (None, "", " "))
def test_tol_003_registry_manages_required_tool_capability(invalid) -> None:
    catalog = build_tool_catalog()
    contract = catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    write_contract = replace(contract, version="1.0.1", capability="WRITE")
    registry = ToolContractCatalog((contract, write_contract))

    assert registry.resolve(contract.tool_id, contract.version).capability == "READ"
    assert registry.resolve(contract.tool_id, write_contract.version).capability == "WRITE"
    assert write_contract.contract_hash != contract.contract_hash
    with pytest.raises(ValueError, match="capability"):
        replace(contract, capability=invalid)


@pytest.mark.parametrize(
    ("input_types", "message"),
    (
        ((('', YEAR),), "non-empty"),
        ((("year", YEAR), ("year", STRING)), "unique"),
        ((("year", "Year"),), "TypeRef"),
    ),
)
def test_tol_004_registry_manages_input_and_output_schemas(
    input_types, message: str
) -> None:
    contract = build_tool_catalog().get(
        "PROJECT.LIST_PARENT_PROJECTS", "1.0.0"
    )
    changed_output = replace(contract, output_type=STRING)

    assert contract.input_type("year") == YEAR
    assert changed_output.contract_hash != contract.contract_hash
    with pytest.raises(ValueError, match=message):
        replace(contract, input_types=input_types)
    with pytest.raises(ValueError, match="output schema"):
        replace(contract, output_type="List<ParentProject>")


@pytest.mark.parametrize("version", ("0.0.0", "999.999.999"))
def test_tol_005_registry_manages_canonical_exact_versions(version: str) -> None:
    contract = build_tool_catalog().get(
        "PROJECT.LIST_PARENT_PROJECTS", "1.0.0"
    )
    versioned = replace(contract, version=version)
    registry = ToolContractCatalog((versioned,))

    assert registry.resolve(contract.tool_id, version) is versioned


@pytest.mark.parametrize(
    "invalid", (None, "", "1", "1.0", "1.0.x", "01.0.0", "1.-1.0")
)
def test_tol_005_registry_rejects_noncanonical_versions(invalid) -> None:
    contract = build_tool_catalog().get(
        "PROJECT.LIST_PARENT_PROJECTS", "1.0.0"
    )

    with pytest.raises(ValueError, match="major.minor.patch"):
        replace(contract, version=invalid)


@pytest.mark.parametrize(
    ("mode", "message"),
    (("missing", "unknown tool contract"), ("incompatible", "incompatible tool version")),
)
def test_tol_006_runtime_resolves_every_required_tool_before_execution(
    mode: str, message: str
) -> None:
    canonical = build_tool_catalog()
    skill = NslCompiler(canonical).compile(SOURCE).skill
    parent = canonical.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    registry = (
        ToolContractCatalog(())
        if mode == "missing"
        else ToolContractCatalog((replace(parent, version="1.0.1"),))
    )
    executor = build_mock_executor(canonical)

    result = asyncio.run(
        RuntimeEngine(registry).execute(
            skill,
            execution_request(f"exec-tol-006-{mode}"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code == DiagnosticCode.RUNTIME_EVALUATION
    assert message in result.error.message
    assert executor.call_count == 0


def test_tol_007_runtime_rejects_resolved_contract_mismatch_before_call() -> None:
    canonical = build_tool_catalog()
    skill = NslCompiler(canonical).compile(SOURCE).skill
    parent = canonical.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    child = canonical.get("PROJECT.LIST_CHILD_PROJECTS", "1.0.0")
    changed = replace(parent, required_scope="project:changed:read")
    registry = ToolContractCatalog((changed, child))
    executor = build_mock_executor(canonical)

    result = asyncio.run(
        RuntimeEngine(registry).execute(
            skill,
            execution_request("exec-tol-007"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert changed.contract_hash != parent.contract_hash
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code == DiagnosticCode.RUNTIME_EVALUATION
    assert "tool contract mismatch" in result.error.message
    assert executor.call_count == 0


def test_tol_008_registry_and_nso_hide_mcp_implementation_details() -> None:
    catalog = build_tool_catalog()
    compilation = NslCompiler(catalog).compile(SOURCE)
    forbidden = {"binding", "credential", "endpoint", "mcp", "provider", "url"}
    contract_fields = {field.name.lower() for field in fields(ToolContract)}
    catalog_state = {name.lower() for name in catalog.__dict__}
    source = SOURCE.lower()
    artifact = compilation.nso_bytes.lower()

    assert forbidden.isdisjoint(contract_fields | catalog_state)
    assert all(marker not in source for marker in forbidden)
    assert all(marker.encode("ascii") not in artifact for marker in forbidden)


def test_exe_001_tool_executor_is_an_explicit_async_port() -> None:
    executor = build_mock_executor(build_tool_catalog())

    assert isinstance(executor, ToolExecutor)
    assert callable(executor.execute)


def test_exe_002_mock_executor_is_deterministic_and_fixture_isolated() -> None:
    catalog = build_tool_catalog()
    fixture = {"PROJECT.LIST_PARENT_PROJECTS": lambda arguments: []}
    executor = MockToolExecutor(catalog, fixture)
    fixture.clear()
    request = tool_request()

    first = asyncio.run(executor.execute(request))
    second = asyncio.run(executor.execute(request))

    assert first == second
    assert first.result_hash.startswith("sha256:")
    assert executor.call_count == 2


def test_exe_004_runtime_depends_only_on_the_tool_executor_port() -> None:
    runtime_path = ROOT / "nsl" / "runtime.py"
    tree = ast.parse(runtime_path.read_text(encoding="utf-8"))
    imported_tools = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "tools"
        and node.level == 1
        for alias in node.names
    }
    runtime_source = runtime_path.read_text(encoding="utf-8")

    assert "ToolExecutionPort" in imported_tools
    assert "MockToolExecutor" not in imported_tools
    assert "MCPToolExecutor" not in runtime_source


@pytest.mark.parametrize(
    ("value", "type_info", "expected"),
    (
        (True, BOOL, True),
        (1, BOOL, False),
        (date(2026, 1, 1), DATE, True),
        (datetime(2026, 1, 1), DATE, False),
        (datetime(2026, 1, 1), DATETIME, True),
        (Decimal("0.1"), DECIMAL, True),
        (0.1, DECIMAL, False),
        (-1, INT, True),
        (True, INT, False),
        ("text", STRING, True),
        (0, YEAR, True),
        ("TEAM", domain("TeamId"), True),
        (1, domain("TeamId"), False),
        (Money(Decimal("0"), "KRW"), money_type("KRW"), True),
        (Money(Decimal("0"), "USD"), money_type("KRW"), False),
        ([0, 1], list_type(INT), True),
        ([0, True], list_type(INT), False),
        ({"value": 1}, record_type("Item", value=INT), True),
        ({"value": 1, "extra": 2}, record_type("Item", value=INT), False),
        ("PASS", enum_type("Status", "PASS", "FAIL"), True),
        ("UNKNOWN", enum_type("Status", "PASS", "FAIL"), False),
        (1.0, TypeRef(kind="primitive", name="Float"), False),
        ("value", TypeRef(kind="unsupported"), False),
    ),
)
def test_exe_005_contract_validator_checks_runtime_value_schema(
    value, type_info, expected: bool
) -> None:
    assert value_conforms_to_type(value, type_info) is expected


@pytest.mark.parametrize(
    "case",
    (
        "unavailable",
        "hash",
        "names",
        "not_envelope",
        "envelope_type",
        "value_type",
    ),
)
def test_exe_005_mock_validates_contract_before_fixture_call(case: str) -> None:
    catalog = build_tool_catalog()
    calls: list[dict] = []
    executor = MockToolExecutor(
        catalog,
        {"PROJECT.LIST_PARENT_PROJECTS": lambda arguments: calls.append(arguments) or []},
    )
    request = tool_request()
    if case == "unavailable":
        request = replace(request, tool_id="PROJECT.UNKNOWN")
    elif case == "hash":
        request = replace(request, contract_hash="sha256:changed")
    elif case == "names":
        request = replace(request, arguments={"year": request.arguments["year"]})
    elif case == "not_envelope":
        arguments = dict(request.arguments)
        arguments["year"] = 2026
        request = replace(request, arguments=arguments)
    elif case == "envelope_type":
        arguments = dict(request.arguments)
        arguments["year"] = ValueEnvelope.complete(2026, STRING)
        request = replace(request, arguments=arguments)
    else:
        arguments = dict(request.arguments)
        arguments["year"] = ValueEnvelope.complete(True, YEAR)
        request = replace(request, arguments=arguments)

    with pytest.raises(ToolExecutionError):
        asyncio.run(executor.execute(request))
    assert calls == []
    assert executor.call_count == 0


def test_exe_005_mock_validates_output_contract_after_fixture_call() -> None:
    catalog = build_tool_catalog()
    executor = MockToolExecutor(
        catalog,
        {"PROJECT.LIST_PARENT_PROJECTS": lambda arguments: "not-a-project-list"},
    )

    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(executor.execute(tool_request()))

    assert captured.value.code == "OUTPUT_CONTRACT_VIOLATION"
    assert executor.call_count == 1


def test_exe_005_runtime_validates_contract_before_executor_call() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    first = skill.body[0]
    assert isinstance(first, LetStatement)
    assert isinstance(first.value, ReadExpr)
    arguments = dict(first.value.arguments)
    arguments["year"] = LiteralExpr("expr-forged-year", "2026", STRING)
    forged_read = replace(first.value, arguments=tuple(arguments.items()))
    forged_skill = replace(
        skill,
        body=(replace(first, value=forged_read), *skill.body[1:]),
    ).with_computed_hash()

    class UnvalidatedExecutor:
        def __init__(self) -> None:
            self.call_count = 0

        async def execute(self, request):
            self.call_count += 1
            raise AssertionError("invalid request reached executor")

    executor = UnvalidatedExecutor()
    result = asyncio.run(
        RuntimeEngine(catalog).execute(
            forged_skill,
            execution_request("exec-exe-005-runtime"),
            executor,
            InMemoryAuditSink(),
        )
    )

    assert result.status is ExecutionStatus.TOOL_ERROR
    assert result.error is not None
    assert result.error.detail_code == "TOOL_ARGUMENT_TYPE_MISMATCH"
    assert executor.call_count == 0


def test_exe_007_runtime_generates_ordered_per_execution_invocation_ids() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    streams: list[tuple[list[str], list[str]]] = []

    for index in range(2):
        audit = InMemoryAuditSink()
        result = asyncio.run(
            RuntimeEngine(catalog).execute(
                skill,
                execution_request(f"exec-exe-007-{index}"),
                build_mock_executor(catalog),
                audit,
            )
        )
        started = [
            event.payload["invocation_id"]
            for event in audit.events
            if event.event_type == "TOOL_STARTED"
        ]
        completed = [
            event.payload["invocation_id"]
            for event in audit.events
            if event.event_type == "TOOL_COMPLETED"
        ]
        assert result.status is ExecutionStatus.COMPLETED
        streams.append((started, completed))

    assert streams == [
        (["inv0001", "inv0002"], ["inv0001", "inv0002"]),
        (["inv0001", "inv0002"], ["inv0001", "inv0002"]),
    ]
