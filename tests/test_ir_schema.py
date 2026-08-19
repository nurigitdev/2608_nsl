from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from typing import Any

import pytest

from nsl.compiler import NslCompiler
from nsl.ir import NsoCodec
from nsl.ir_schema import NsoSchemaError, validate_nso_document
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")
DELETE = object()


def compiled_payload(*, bool_literal: bool = False) -> dict[str, Any]:
    source = SOURCE
    if bool_literal:
        source = source.replace(
            "    let parents =",
            "    let approved = true;\n\n    let parents =",
        )
    return json.loads(NslCompiler(build_tool_catalog()).compile(source).nso_bytes)


def mutate(
    document: dict[str, Any], path: tuple[str | int, ...], value: Any
) -> Any:
    if not path:
        return value
    changed = deepcopy(document)
    target: Any = changed
    for part in path[:-1]:
        target = target[part]
    final = path[-1]
    if value is DELETE:
        del target[final]
    else:
        target[final] = value
    return changed


@pytest.mark.parametrize(
    ("path", "value", "error_path", "reason"),
    [
        ((), [], "$", "expected object"),
        (("build",), DELETE, "$", "missing fields"),
        (("unexpected",), True, "$", "unexpected fields"),
        (("format",), "BAD", "$.format", "expected 'NSO'"),
        (("features",), {}, "$.features", "expected array"),
        (("features", 0), 1, "$.features[0]", "expected string"),
        (("skill", "id"), "", "$.skill.id", "must not be empty"),
        (("limits", "tool_calls"), True, "$.limits.tool_calls", "expected integer"),
        (("limits", "tool_calls"), 0, "$.limits.tool_calls", "at least 1"),
        (("inputs", 0, "required"), 1, "$.inputs[0].required", "expected boolean"),
        (("hashes", "semantic_sha256"), "bad", "$.hashes.semantic_sha256", "sha256"),
        (("hashes", "semantic_sha256"), "sha256:abc", "$.hashes.semantic_sha256", "sha256"),
        (("hashes", "semantic_sha256"), "sha256:" + "g" * 64, "$.hashes.semantic_sha256", "sha256"),
        (("symbols", 0, "type"), [], "$.symbols[0].type", "type object"),
        (("symbols", 0, "type", "kind"), "unknown", "$.symbols[0].type.kind", "unknown type kind"),
        (("body", 0), [], "$.body[0]", "statement object"),
        (("body", 0, "kind"), "unknown", "$.body[0].kind", "unknown statement kind"),
        (("body", 0, "value"), [], "$.body[0].value", "expression object"),
        (("body", 0, "value", "kind"), "unknown", "$.body[0].value.kind", "unknown expression kind"),
        (("build", "sources"), [], "$.build.sources", "must not be empty"),
        (("build", "sources", 0, "is_root"), False, "$.build.sources", "exactly one root"),
        (("build", "root_source"), "other.ns", "$.build.root_source", "root manifest"),
    ],
)
def test_schema_validator_rejects_malformed_documents_with_paths(
    path, value, error_path, reason
) -> None:
    malformed = mutate(compiled_payload(), path, value)

    with pytest.raises(NsoSchemaError) as captured:
        validate_nso_document(malformed)

    assert captured.value.path == error_path
    assert reason in captured.value.reason


@pytest.mark.parametrize(
    "encoded",
    [
        {"$type": "Decimal", "value": "1.25"},
        {"$type": "Money", "amount": "1000", "currency": "KRW"},
    ],
)
def test_schema_validator_accepts_canonical_immutable_literal_wrappers(encoded) -> None:
    payload = compiled_payload(bool_literal=True)
    payload["body"][0]["value"]["value"] = encoded

    validate_nso_document(payload)


def test_schema_validator_accepts_native_immutable_literal_value() -> None:
    validate_nso_document(compiled_payload(bool_literal=True))


def test_schema_and_codec_accept_empty_check_message_boundary() -> None:
    source = SOURCE.replace(
        'message "자 프로젝트 지출 합계가 모 프로젝트 예산을 초과했습니다.";',
        'message "";',
    )
    artifact = NslCompiler(build_tool_catalog()).compile(source).nso_bytes

    assert NsoCodec.decode(artifact).body


@pytest.mark.parametrize("encoded", [[], {"$type": "Unknown"}])
def test_schema_validator_rejects_mutable_or_unknown_literal_values(encoded) -> None:
    payload = compiled_payload(bool_literal=True)
    payload["body"][0]["value"]["value"] = encoded

    with pytest.raises(NsoSchemaError, match="literal value"):
        validate_nso_document(payload)


def test_decoded_ir_is_frozen_and_uses_immutable_collections() -> None:
    artifact = NslCompiler(build_tool_catalog()).compile(SOURCE).nso_bytes
    skill = NsoCodec.decode(artifact)

    assert isinstance(skill.body, tuple)
    assert isinstance(skill.features, frozenset)
    assert isinstance(skill.build.sources, tuple)
    with pytest.raises(FrozenInstanceError):
        skill.skill_id = "CHANGED"  # type: ignore[misc]


def test_schema_validator_rejects_duplicate_build_manifest_path() -> None:
    payload = compiled_payload()
    duplicate = deepcopy(payload["build"]["sources"][0])
    duplicate["is_root"] = False
    payload["build"]["sources"].append(duplicate)

    with pytest.raises(NsoSchemaError) as captured:
        validate_nso_document(payload)

    assert captured.value.path == "$.build.sources[1].logical_path"
    assert captured.value.reason == "duplicate source logical path"
