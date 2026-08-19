from __future__ import annotations

import asyncio
from dataclasses import fields, replace
from decimal import Decimal
from inspect import signature
from pathlib import Path

import pytest

from nsl import (
    CompileError,
    DiagnosticCode,
    NslCompiler,
    SourceFile,
    SourceLocation,
)
from nsl.audit import InMemoryAuditSink
from nsl.core import ExecutionStatus, Money
from nsl.ir import LetStatement, ReadExpr, RequiredTool
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.security import DataHandlingPolicy
from nsl.tools import (
    TOOL_VERSION_COMPATIBILITY_POLICY,
    ToolContract,
    ToolContractCatalog,
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


def _request(execution_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=execution_id,
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
        data_policy=DataHandlingPolicy(),
    )


def test_arc_012_compiler_resolves_canonical_contract_not_customer_binding() -> None:
    catalog = build_tool_catalog()
    compilation = NslCompiler(catalog).compile(SOURCE)
    default_binding = build_mock_executor(catalog)
    alternate_binding = build_mock_executor(catalog)
    alternate_binding.handlers["PROJECT.LIST_CHILD_PROJECTS"] = (
        lambda arguments: []
    )

    default_result = asyncio.run(
        RuntimeEngine(catalog).execute(
            compilation.skill,
            _request("exec-canonical-default"),
            default_binding,
            InMemoryAuditSink(),
        )
    )
    alternate_result = asyncio.run(
        RuntimeEngine(catalog).execute(
            compilation.skill,
            _request("exec-canonical-alternate"),
            alternate_binding,
            InMemoryAuditSink(),
        )
    )

    assert default_result.status is ExecutionStatus.COMPLETED
    assert alternate_result.status is ExecutionStatus.COMPLETED
    assert default_result.outputs[0].values["spent"] == Money(
        Decimal("87500000"), "KRW"
    )
    assert alternate_result.outputs[0].values["spent"] == Money(
        Decimal("0"), "KRW"
    )

    forbidden = {"binding", "credential", "endpoint", "mcp", "url"}
    canonical_fields = {item.name.lower() for item in fields(ToolContract)}
    required_fields = {item.name.lower() for item in fields(RequiredTool)}
    assert not any(
        marker in field_name
        for marker in forbidden
        for field_name in canonical_fields | required_fields
    )
    encoded = compilation.nso_bytes.lower()
    assert all(marker.encode("ascii") not in encoded for marker in forbidden)


def test_req_001_every_read_requires_an_explicit_tool_declaration() -> None:
    source = SourceFile.from_text(
        "skills/missing_requirement.ns",
        SOURCE.replace(
            '        tool PROJECT.LIST_PARENT_PROJECTS version "1.0.0";\n',
            "\n",
        ),
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == DiagnosticCode.SEM_UNDECLARED_TOOL
    assert captured.value.location == SourceLocation(35, 19)
    assert captured.value.logical_path == "skills/missing_requirement.ns"
    assert captured.value.snippet == (
        "    let parents = read PROJECT.LIST_PARENT_PROJECTS("
    )


def test_req_003_compile_checks_canonical_tool_existence() -> None:
    source = SourceFile.from_text(
        "skills/unknown_tool.ns",
        SOURCE.replace(
            "PROJECT.LIST_PARENT_PROJECTS",
            "PROJECT.UNKNOWN_TOOL",
            1,
        ),
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == DiagnosticCode.SEM_UNKNOWN_TOOL_CONTRACT
    assert captured.value.location == SourceLocation(8, 9)
    assert captured.value.logical_path == "skills/unknown_tool.ns"
    assert captured.value.snippet == (
        '        tool PROJECT.UNKNOWN_TOOL version "1.0.0";'
    )


@pytest.mark.parametrize("version", ("0.9.9", "1.0.1", "1.1.0", "2.0.0"))
def test_req_004_tool_version_compatibility_is_exact(version: str) -> None:
    assert TOOL_VERSION_COMPATIBILITY_POLICY == "EXACT"
    source = SourceFile.from_text(
        "skills/incompatible_tool_version.ns",
        SOURCE.replace(
            'tool PROJECT.LIST_PARENT_PROJECTS version "1.0.0";',
            f'tool PROJECT.LIST_PARENT_PROJECTS version "{version}";',
            1,
        ),
    )

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == DiagnosticCode.SEM_INCOMPATIBLE_TOOL_VERSION
    assert captured.value.location == SourceLocation(8, 9)
    assert captured.value.logical_path == "skills/incompatible_tool_version.ns"
    assert captured.value.snippet == (
        f'        tool PROJECT.LIST_PARENT_PROJECTS version "{version}";'
    )


@pytest.mark.parametrize("capability", ("WRITE", "APPROVAL", "EXECUTE"))
def test_req_005_v0_1_accepts_only_read_capability_tools(
    capability: str,
) -> None:
    base_catalog = build_tool_catalog()
    parent = base_catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    child = base_catalog.get("PROJECT.LIST_CHILD_PROJECTS", "1.0.0")
    catalog = ToolContractCatalog((replace(parent, capability=capability), child))
    source = SourceFile.from_text("skills/non_read_tool.ns", SOURCE)

    with pytest.raises(CompileError) as captured:
        NslCompiler(catalog).compile(source)

    assert captured.value.code == DiagnosticCode.SEM_WRITE_TOOL_FORBIDDEN
    assert captured.value.location == SourceLocation(8, 9)
    assert captured.value.logical_path == "skills/non_read_tool.ns"
    assert captured.value.snippet == (
        '        tool PROJECT.LIST_PARENT_PROJECTS version "1.0.0";'
    )
    assert captured.value.public_message.startswith(f"{capability} tool")


def test_req_007_compiler_resolves_complete_canonical_business_contract() -> None:
    catalog = build_tool_catalog()
    contract = catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    compilation = NslCompiler(catalog).compile(SOURCE)
    required = compilation.skill.required_tools[0]
    statement = compilation.skill.body[0]

    assert contract.risk == "READ_ONLY"
    assert required.tool_id == contract.tool_id
    assert required.version == contract.version
    assert required.capability == contract.capability
    assert required.contract_hash == contract.contract_hash
    assert required.required_scope == contract.required_scope
    assert required.output_classification == contract.output_classification
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, ReadExpr)
    assert statement.value.type_info == contract.output_type
    assert tuple(name for name, _ in statement.value.arguments) == tuple(
        name for name, _ in contract.input_types
    )
    assert contract.contract_hash.encode("ascii") in compilation.nso_bytes

    changed_contracts = (
        replace(contract, risk="READ_VALIDATE"),
        replace(contract, input_types=tuple(reversed(contract.input_types))),
        replace(contract, output_type=contract.input_types[0][1]),
    )
    assert all(
        changed.contract_hash != contract.contract_hash
        for changed in changed_contracts
    )


def test_req_008_compile_stage_rejects_customer_mcp_binding_and_endpoint() -> None:
    parameters = signature(NslCompiler.__init__).parameters
    assert tuple(parameters) == (
        "self",
        "tool_catalog",
        "include_resolver",
        "include_options",
    )

    source = SourceFile.from_text(
        "skills/customer_endpoint_forbidden.ns",
        SOURCE.replace(
            '        tool PROJECT.LIST_PARENT_PROJECTS version "1.0.0";',
            '        tool PROJECT.LIST_PARENT_PROJECTS version "1.0.0";\n'
            '        endpoint "https://customer.example/mcp";',
            1,
        ),
    )
    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    assert captured.value.code == DiagnosticCode.PAR_EXPECTED_TOKEN
    assert captured.value.location == SourceLocation(9, 9)
    assert captured.value.logical_path == "skills/customer_endpoint_forbidden.ns"
    assert captured.value.snippet == (
        '        endpoint "https://customer.example/mcp";'
    )


def test_slice_0003_tool_diagnostics_preserve_source_context() -> None:
    parent_declaration = (
        '        tool PROJECT.LIST_PARENT_PROJECTS version "1.0.0";'
    )
    duplicate_source = SourceFile.from_text(
        "skills/duplicate_requirement.ns",
        SOURCE.replace(
            parent_declaration,
            f"{parent_declaration}\n{parent_declaration}",
            1,
        ),
    )
    with pytest.raises(CompileError) as duplicate:
        NslCompiler(build_tool_catalog()).compile(duplicate_source)
    assert duplicate.value.code == DiagnosticCode.SEM_DUPLICATE_TOOL
    assert duplicate.value.location == SourceLocation(9, 9)
    assert duplicate.value.snippet == parent_declaration
    assert duplicate.value.logical_path == "skills/duplicate_requirement.ns"

    arguments_source = SourceFile.from_text(
        "skills/tool_argument_names.ns",
        SOURCE.replace(
            "        team_id: team_id", "        wrong_name: team_id", 1
        ),
    )
    with pytest.raises(CompileError) as arguments:
        NslCompiler(build_tool_catalog()).compile(arguments_source)
    assert arguments.value.code == DiagnosticCode.SEM_TOOL_ARGUMENTS
    assert arguments.value.location == SourceLocation(35, 19)
    assert arguments.value.snippet == (
        "    let parents = read PROJECT.LIST_PARENT_PROJECTS("
    )
    assert arguments.value.logical_path == "skills/tool_argument_names.ns"
