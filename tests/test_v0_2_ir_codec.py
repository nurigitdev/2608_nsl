from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from nsl.compiler import NslCompiler
from nsl.core import (
    DataClassification,
    RiskLevel,
    STRING,
    ToolCapability,
    domain,
)
from nsl.ir import (
    ApprovalPolicy,
    ApprovalStatement,
    LetStatement,
    LiteralExpr,
    NsoCodec,
    RequiredTool,
    WriteExpression,
)
from nsl.ir_schema import NsoSchemaError, validate_nso_document
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)
WRITE_RESULT = domain("VoucherDraft")
BASELINE_SEMANTIC_HASH = (
    "sha256:ba75f7b96d08f0a8bff6ff1917b127c36cf2a0c96f1d29b7dc883674a4678b56"
)
BASELINE_ARTIFACT_HASH = (
    "sha256:35c8201ff8e55f22cd55f3fa677182a800b5452099a79cfe9e450792bb33337c"
)


def v1_0_compilation():
    return NslCompiler(build_tool_catalog()).compile(SOURCE)


def v1_1_write_skill(*, ttl_seconds: int | None = 1):
    base = v1_0_compilation().skill
    required = RequiredTool(
        tool_ref="tool-write-0001",
        tool_id="FINANCE.CREATE_VOUCHER_DRAFT",
        version="1.0.0",
        capability=ToolCapability.WRITE,
        contract_hash="sha256:" + "a" * 64,
        required_scope="finance:voucher:write",
        output_classification=DataClassification.CONFIDENTIAL,
        risk_level=RiskLevel.R3,
    )
    approval = ApprovalStatement(
        node_id="approval0001",
        approval_id="VOUCHER_CREATE",
        message="Create the voucher draft?",
        policy=ApprovalPolicy(ttl_seconds=ttl_seconds),
    )
    write = WriteExpression(
        node_id="write0001",
        tool_ref=required.tool_ref,
        arguments=(("description", LiteralExpr("expr0001", "memo", STRING)),),
        approval_ref=approval.approval_id,
        type_info=WRITE_RESULT,
    )
    skill = replace(
        base,
        ir_version="1.1",
        language_version="0.2",
        semantics_profile="NSL-0.2-STRICT",
        features=frozenset({"APPROVAL", "WRITE"}),
        required_tools=(required,),
        body=(approval, LetStatement("stmt0001", "s-write-result", write)),
    )
    return skill.with_computed_hash()


def v1_1_payload() -> dict[str, object]:
    return json.loads(NsoCodec.encode(v1_1_write_skill()))


@pytest.mark.parametrize("ttl_seconds", (None, 1, 2_147_483_647))
def test_ir_201_ir_1_1_codec_round_trips_deterministically(
    ttl_seconds: int | None,
) -> None:
    skill = v1_1_write_skill(ttl_seconds=ttl_seconds)
    encoded = NsoCodec.encode(skill)
    loaded = NsoCodec.decode(encoded)

    assert loaded == skill
    assert NsoCodec.encode(loaded) == encoded
    assert loaded.with_computed_hash().semantic_hash == skill.semantic_hash
    assert loaded.required_tools[0].risk_level is RiskLevel.R3
    assert isinstance(loaded.body[0], ApprovalStatement)
    assert isinstance(loaded.body[1].value, WriteExpression)


def test_ir_201_ir_1_1_canonical_shape_contains_only_static_write_semantics() -> None:
    payload = v1_1_payload()
    required = payload["requires"][0]
    approval, statement = payload["body"]
    write = statement["value"]

    assert required["capability"] == "WRITE"
    assert required["risk_level"] == "R3"
    assert approval == {
        "approval_id": "VOUCHER_CREATE",
        "kind": "approval",
        "message": "Create the voucher draft?",
        "node_id": "approval0001",
        "policy": {"required": True, "ttl_seconds": 1},
    }
    assert write["kind"] == "write"
    assert write["approval_ref"] == "VOUCHER_CREATE"
    assert set(write).isdisjoint(
        {
            "business_operation_id",
            "idempotency_key",
            "payload_hash",
            "approval_request_id",
            "policy_decision",
            "commit_state",
            "verification_status",
        }
    )


def test_ir_201_write_less_ir_1_1_remains_a_supported_additive_shape() -> None:
    base = v1_0_compilation().skill
    skill = replace(
        base,
        ir_version="1.1",
        language_version="0.2",
        semantics_profile="NSL-0.2-STRICT",
        required_tools=tuple(
            replace(required, risk_level=RiskLevel.R0)
            for required in base.required_tools
        ),
    ).with_computed_hash()

    encoded = NsoCodec.encode(skill)
    loaded = NsoCodec.decode(encoded)

    assert loaded == skill
    assert all(item.risk_level is RiskLevel.R0 for item in loaded.required_tools)


@pytest.mark.parametrize("ir_version", ("", "0.9", "1.2", "2.0"))
def test_ir_201_loader_rejects_unsupported_ir_versions(ir_version: str) -> None:
    payload = v1_1_payload()
    payload["ir_version"] = ir_version

    with pytest.raises(NsoSchemaError) as captured:
        validate_nso_document(payload)

    assert captured.value.path == "$.ir_version"


@pytest.mark.parametrize("risk_level", (None, "", "R6", 3))
def test_ir_201_ir_1_1_requires_bounded_required_tool_risk(
    risk_level,
) -> None:
    payload = v1_1_payload()
    if risk_level is None:
        del payload["requires"][0]["risk_level"]
    else:
        payload["requires"][0]["risk_level"] = risk_level

    with pytest.raises(NsoSchemaError, match="risk_level|missing fields"):
        validate_nso_document(payload)


def test_ir_201_ir_1_1_rejects_missing_approval_link() -> None:
    payload = v1_1_payload()
    payload["body"] = payload["body"][1:]

    with pytest.raises(NsoSchemaError, match="declared approval_id"):
        validate_nso_document(payload)


def test_ir_201_ir_1_1_rejects_approval_after_write() -> None:
    payload = v1_1_payload()
    payload["body"].reverse()

    with pytest.raises(NsoSchemaError, match="before WRITE"):
        validate_nso_document(payload)


def test_ir_201_ir_1_1_rejects_duplicate_or_unused_approval() -> None:
    duplicate = v1_1_payload()
    duplicate["body"].insert(1, deepcopy(duplicate["body"][0]))
    with pytest.raises(NsoSchemaError, match="duplicate approval_id"):
        validate_nso_document(duplicate)

    unused = v1_1_payload()
    extra = deepcopy(unused["body"][0])
    extra["node_id"] = "approval0002"
    extra["approval_id"] = "UNUSED"
    unused["body"].insert(1, extra)
    with pytest.raises(NsoSchemaError, match="exactly one WRITE"):
        validate_nso_document(unused)


def test_ir_201_ir_1_1_rejects_non_write_or_missing_tool_reference() -> None:
    wrong_capability = v1_1_payload()
    wrong_capability["requires"][0]["capability"] = "READ"
    with pytest.raises(NsoSchemaError, match="WRITE capability"):
        validate_nso_document(wrong_capability)

    missing_tool = v1_1_payload()
    missing_tool["body"][1]["value"]["tool_ref"] = "tool-missing"
    with pytest.raises(NsoSchemaError, match="required tool"):
        validate_nso_document(missing_tool)


def test_ir_201_ir_1_1_rejects_write_inside_foreach_and_multiple_writes() -> None:
    inside_loop = v1_1_payload()
    write_statement = inside_loop["body"].pop()
    inside_loop["body"].append(
        {
            "node_id": "loop0001",
            "kind": "foreach",
            "iterator_symbol_id": "s-iterator",
            "collection": {
                "node_id": "expr-collection",
                "kind": "literal",
                "type": {"kind": "primitive", "name": "Int"},
                "value": 1,
            },
            "max_iterations": 1,
            "body": [write_statement],
        }
    )
    with pytest.raises(NsoSchemaError, match="inside foreach"):
        validate_nso_document(inside_loop)

    multiple = v1_1_payload()
    second = deepcopy(multiple["body"][1])
    second["node_id"] = "stmt0002"
    second["value"]["node_id"] = "write0002"
    multiple["body"].append(second)
    with pytest.raises(NsoSchemaError, match="at most one WRITE"):
        validate_nso_document(multiple)


@pytest.mark.parametrize(
    ("policy", "message"),
    (
        ([], "expected object"),
        ({}, "missing fields"),
        ({"required": 1, "ttl_seconds": 1}, "expected boolean"),
        ({"required": False, "ttl_seconds": 1}, "expected True"),
        ({"required": True, "ttl_seconds": 0}, "at least 1"),
        ({"required": True, "routing": "finance"}, "unexpected fields"),
    ),
)
def test_ir_201_ir_1_1_rejects_malformed_approval_policy(
    policy: object, message: str
) -> None:
    payload = v1_1_payload()
    payload["body"][0]["policy"] = policy

    with pytest.raises(NsoSchemaError, match=message):
        validate_nso_document(payload)


def test_ir_201_ir_1_1_rejects_duplicate_required_tool_reference() -> None:
    payload = v1_1_payload()
    payload["requires"].append(deepcopy(payload["requires"][0]))

    with pytest.raises(NsoSchemaError, match="duplicate tool_ref"):
        validate_nso_document(payload)


def test_ir_201_ir_1_1_rejects_runtime_state_in_static_write_node() -> None:
    payload = v1_1_payload()
    payload["body"][1]["value"]["commit_state"] = "COMMITTED"

    with pytest.raises(NsoSchemaError, match="unexpected fields"):
        validate_nso_document(payload)


def test_ir_208_v1_0_artifact_bytes_hash_and_load_are_unchanged() -> None:
    compilation = v1_0_compilation()

    assert compilation.skill.semantic_hash == BASELINE_SEMANTIC_HASH
    assert "sha256:" + sha256(compilation.nso_bytes).hexdigest() == (
        BASELINE_ARTIFACT_HASH
    )
    loaded = NsoCodec.decode(compilation.nso_bytes)
    assert loaded == compilation.skill
    assert NsoCodec.encode(loaded) == compilation.nso_bytes
    assert all(item.risk_level is None for item in loaded.required_tools)


def test_ir_208_ir_1_0_rejects_v1_1_additions_and_non_read_tools() -> None:
    payload = json.loads(v1_0_compilation().nso_bytes)
    payload["requires"][0]["risk_level"] = "R0"
    with pytest.raises(NsoSchemaError, match="unexpected fields"):
        validate_nso_document(payload)

    payload = json.loads(v1_0_compilation().nso_bytes)
    payload["requires"][0]["capability"] = "WRITE"
    with pytest.raises(NsoSchemaError, match="READ capability only"):
        validate_nso_document(payload)

    payload = json.loads(v1_0_compilation().nso_bytes)
    payload["body"].insert(
        0,
        {
            "node_id": "approval0001",
            "kind": "approval",
            "approval_id": "INVALID",
            "message": "invalid in 1.0",
            "policy": {"required": True},
        },
    )
    with pytest.raises(NsoSchemaError, match="unknown statement kind"):
        validate_nso_document(payload)
