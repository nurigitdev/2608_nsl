from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from nsl.audit import InMemorySnapshotStore
from nsl.core import (
    Completeness,
    DataClassification,
    Presence,
    ValueEnvelope,
    YEAR,
)
from nsl.ir import (
    NsoCodec,
    _expr_from_data,
    _expr_to_data,
    _statement_from_data,
    _statement_to_data,
)
from nsl.replay import RecordedToolCall, ReplayToolExecutor
from nsl.tools import (
    MockToolExecutor,
    ToolCallRequest,
    ToolExecutionError,
    ToolResultEnvelope,
)
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


def tool_request(**overrides) -> ToolCallRequest:
    catalog = build_tool_catalog()
    contract = catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    values = {
        "execution_id": "exec",
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
        "authorization_decision_ref": "authz",
    }
    values.update(overrides)
    return ToolCallRequest(**values)


def test_tool_contract_and_catalog_missing_items() -> None:
    catalog = build_tool_catalog()
    contract = catalog.get("PROJECT.LIST_PARENT_PROJECTS", "1.0.0")
    with pytest.raises(KeyError):
        contract.input_type("missing")
    with pytest.raises(KeyError, match="unknown tool contract"):
        catalog.get("UNKNOWN", "1")


def test_mock_tool_contract_mismatch_and_missing_fixture() -> None:
    catalog = build_tool_catalog()
    mock = build_mock_executor(catalog)
    bad_hash = replace(tool_request(), contract_hash="sha256:bad")
    with pytest.raises(ToolExecutionError, match="contract changed"):
        asyncio.run(mock.execute(bad_hash))

    empty_mock = MockToolExecutor(catalog, {})
    with pytest.raises(ToolExecutionError, match="no fixture"):
        asyncio.run(empty_mock.execute(tool_request()))


def test_tool_result_to_value_preserves_state() -> None:
    result = ToolResultEnvelope(
        invocation_id="inv",
        tool_id="tool",
        tool_version="1",
        value=[],
        type_info=YEAR,
        presence=Presence.EMPTY,
        completeness=Completeness.UNKNOWN,
        classification=DataClassification.RESTRICTED,
        result_hash="sha256:value",
    )
    value = result.to_value("prov")
    assert value.presence is Presence.EMPTY
    assert value.completeness is Completeness.UNKNOWN
    assert value.provenance_refs == ("prov",)


def test_ir_defensive_unknown_kinds_and_format() -> None:
    type_data = YEAR.to_data()
    class UnknownExpression:
        node_id = "unknown"
        type_info = YEAR

    with pytest.raises(TypeError, match="unsupported expression"):
        _expr_to_data(UnknownExpression())
    with pytest.raises(TypeError, match="unsupported statement"):
        _statement_to_data(object())
    with pytest.raises(ValueError, match="unknown expression kind"):
        _expr_from_data({"kind": "unknown", "node_id": "x", "type": type_data})
    with pytest.raises(ValueError, match="unknown statement kind"):
        _statement_from_data({"kind": "unknown", "node_id": "x"})
    with pytest.raises(ValueError, match="invalid NSO format"):
        NsoCodec.decode(b'{"format":"BAD"}')


def test_replay_call_count_mismatch_argument_mismatch_and_invalid_snapshot() -> None:
    store = InMemorySnapshotStore()
    request = tool_request()
    empty_replay = ReplayToolExecutor((), store)
    with pytest.raises(RuntimeError, match="more tool calls"):
        asyncio.run(empty_replay.execute(request))

    bad_ref = store.put(
        request.principal.tenant_id,
        "not-a-tool-result",
        DataClassification.INTERNAL,
        retention_days=1,
    )
    call = RecordedToolCall(
        request.tool_id,
        request.tool_version,
        "sha256:not-the-argument-hash",
        bad_ref,
    )
    mismatch = ReplayToolExecutor((call,), store)
    with pytest.raises(RuntimeError, match="does not match"):
        asyncio.run(mismatch.execute(request))

    from nsl.replay import _argument_hash

    valid_call = replace(call, argument_hash=_argument_hash(request))
    invalid_result = ReplayToolExecutor((valid_call,), store)
    with pytest.raises(TypeError, match="invalid tool result snapshot"):
        asyncio.run(invalid_result.execute(request))


def test_replay_assert_consumed_detects_too_few_calls() -> None:
    store = InMemorySnapshotStore()
    request = tool_request()
    reference = store.put(
        request.principal.tenant_id,
        "unused",
        DataClassification.INTERNAL,
        retention_days=1,
    )
    call = RecordedToolCall(request.tool_id, request.tool_version, "hash", reference)
    replay = ReplayToolExecutor((call,), store)
    with pytest.raises(RuntimeError, match="did not consume"):
        replay.assert_consumed()
