from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from nsl.compiler import NslCompiler
from nsl.includes import MemoryIncludeResolver
from nsl.integrity import source_manifest_sha256
from nsl.ir import NsoCodec, NsoIntegrityError, skill_to_data
from nsl.ir_schema import NsoSchemaError
from nsl.source import SourceFile
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")


def encode_payload(payload) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def collect_node_ids(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        current = (value["node_id"],) if "node_id" in value else ()
        return current + tuple(
            node_id for item in value.values() for node_id in collect_node_ids(item)
        )
    if isinstance(value, list):
        return tuple(
            node_id for item in value for node_id in collect_node_ids(item)
        )
    return ()


def test_ir_015_source_bundle_and_semantic_hashes_are_separate_and_verified() -> None:
    compilation = NslCompiler(build_tool_catalog()).compile(SOURCE)
    payload = json.loads(compilation.nso_bytes)

    assert payload["hashes"] == {
        "semantic_sha256": compilation.semantic_hash,
        "source_bundle_sha256": compilation.source_bundle_hash,
    }
    assert compilation.source_bundle_hash != compilation.semantic_hash
    assert payload["build"]["source_bundle_sha256"] == compilation.source_bundle_hash
    assert source_manifest_sha256(compilation.skill.build.sources) == compilation.source_bundle_hash
    assert NsoCodec.decode(compilation.nso_bytes) == compilation.skill

    header_tamper = deepcopy(payload)
    header_tamper["hashes"]["source_bundle_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(NsoIntegrityError, match="differs between hashes and build"):
        NsoCodec.decode(encode_payload(header_tamper))

    manifest_tamper = deepcopy(payload)
    manifest_tamper["build"]["sources"][0]["sha256"] = "sha256:" + "0" * 64
    with pytest.raises(NsoIntegrityError, match="manifest hash mismatch"):
        NsoCodec.decode(encode_payload(manifest_tamper))


def test_ir_016_include_partition_does_not_change_semantic_identity() -> None:
    catalog = build_tool_catalog()
    monolithic = NslCompiler(catalog).compile(SOURCE)
    root = SourceFile.from_text(
        "skills/project_budget_check.ns",
        SOURCE.replace(
            "    risk READ_VALIDATE;",
            '    risk READ_VALIDATE;\n\n    include "shared/empty.ns";',
        ),
    )
    fragment = SourceFile.from_text("skills/shared/empty.ns", "")
    partitioned = NslCompiler(
        catalog,
        MemoryIncludeResolver((fragment,)),
    ).compile(root)

    assert partitioned.semantic_hash == monolithic.semantic_hash
    assert skill_to_data(
        partitioned.skill, include_hash=False, include_build=False
    ) == skill_to_data(monolithic.skill, include_hash=False, include_build=False)
    assert partitioned.source_bundle_hash != monolithic.source_bundle_hash
    assert partitioned.nso_bytes != monolithic.nso_bytes


@pytest.mark.parametrize(
    ("artifact", "reason"),
    [
        (bytearray(b"{}"), "expected NSO bytes"),
        (b"\xff", "expected UTF-8 JSON"),
        (b'{"format":', "invalid JSON"),
        (
            b'{"format":"NSO","format":"NSO"}',
            "duplicate object field: format",
        ),
        (b'{"value":NaN}', "non-finite JSON number: NaN"),
        (b'{"value":Infinity}', "non-finite JSON number: Infinity"),
        (b'{"value":-Infinity}', "non-finite JSON number: -Infinity"),
    ],
)
def test_sec_009_untrusted_nso_rejects_unsafe_serialization(
    artifact: object,
    reason: str,
) -> None:
    with pytest.raises(NsoSchemaError, match=reason):
        NsoCodec.decode(artifact)  # type: ignore[arg-type]


def test_sec_009_untrusted_nso_requires_schema_and_semantic_integrity() -> None:
    compilation = NslCompiler(build_tool_catalog()).compile(SOURCE)
    payload = json.loads(compilation.nso_bytes)

    schema_tamper = deepcopy(payload)
    schema_tamper["limits"]["tool_calls"] = True
    with pytest.raises(NsoSchemaError) as captured:
        NsoCodec.decode(encode_payload(schema_tamper))
    assert captured.value.path == "$.limits.tool_calls"
    assert captured.value.reason == "expected integer"

    semantic_tamper = deepcopy(payload)
    semantic_tamper["limits"]["tool_calls"] += 1
    with pytest.raises(NsoIntegrityError, match="semantic hash mismatch"):
        NsoCodec.decode(encode_payload(semantic_tamper))


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("symbols", "classification"),
        ("requires", "output_classification"),
        ("inputs", "classification"),
        ("contexts", "classification"),
        ("output", "classification"),
    ],
)
def test_sec_009_rejects_unknown_classification_before_ir_construction(
    section: str,
    field: str,
) -> None:
    payload = json.loads(NslCompiler(build_tool_catalog()).compile(SOURCE).nso_bytes)
    payload[section][0][field] = "SECRET"

    with pytest.raises(NsoSchemaError) as captured:
        NsoCodec.decode(encode_payload(payload))
    assert captured.value.path == f"$.{section}[0].{field}"
    assert "expected one of" in captured.value.reason


def test_sec_009_rejects_invalid_money_currency_before_ir_construction() -> None:
    payload = json.loads(NslCompiler(build_tool_catalog()).compile(SOURCE).nso_bytes)
    payload["output"][1]["type"]["currency"] = "krw"

    with pytest.raises(NsoSchemaError) as captured:
        NsoCodec.decode(encode_payload(payload))
    assert captured.value.path == "$.output[1].type.currency"
    assert captured.value.reason == "expected 3-letter uppercase currency code"


@pytest.mark.parametrize(
    ("encoded", "reason"),
    [("not-a-decimal", "expected decimal string"), ("NaN", "expected finite")],
)
def test_sec_009_rejects_invalid_encoded_decimal_before_ir_construction(
    encoded: str,
    reason: str,
) -> None:
    source = SOURCE.replace(
        "    let parents =",
        "    let approved = true;\n\n    let parents =",
    )
    payload = json.loads(NslCompiler(build_tool_catalog()).compile(source).nso_bytes)
    payload["body"][0]["value"]["value"] = {
        "$type": "Decimal",
        "value": encoded,
    }

    with pytest.raises(NsoSchemaError) as captured:
        NsoCodec.decode(encode_payload(payload))
    assert captured.value.path == "$.body[0].value.value.value"
    assert reason in captured.value.reason


def test_tst_017_repeated_compile_preserves_ids_hashes_and_artifact() -> None:
    root = SourceFile.from_text(
        "skills/project_budget_check.ns",
        SOURCE.replace(
            "    risk READ_VALIDATE;",
            '    risk READ_VALIDATE;\n\n    include "shared/empty.ns";',
        ),
    )
    fragment = SourceFile.from_text("skills/shared/empty.ns", "")
    compiler = NslCompiler(
        build_tool_catalog(),
        MemoryIncludeResolver((fragment,)),
    )
    compilations = tuple(compiler.compile(root) for _ in range(10))
    payloads = tuple(json.loads(item.nso_bytes) for item in compilations)

    symbol_ids = tuple(
        tuple(symbol["symbol_id"] for symbol in payload["symbols"])
        for payload in payloads
    )
    node_ids = tuple(collect_node_ids(payload["body"]) for payload in payloads)

    assert len(set(symbol_ids)) == 1
    assert len(set(node_ids)) == 1
    assert node_ids[0]
    assert len({item.semantic_hash for item in compilations}) == 1
    assert len({item.source_bundle_hash for item in compilations}) == 1
    assert len({item.nso_bytes for item in compilations}) == 1
