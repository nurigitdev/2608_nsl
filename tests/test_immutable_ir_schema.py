from __future__ import annotations

import asyncio
from dataclasses import is_dataclass
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from nsl.audit import InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.core import BOOL, ExecutionStatus, TypeRef
from nsl.includes import MemoryIncludeResolver
from nsl.ir import (
    BuildMetadata,
    BuildSource,
    LetStatement,
    LiteralExpr,
    NsoCodec,
    SkillObject,
)
from nsl.ir_schema import NsoSchemaError, validate_nso_document
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.security import DataHandlingPolicy
from nsl.source import SourceFile
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")


def compile_with_empty_include():
    root = SourceFile.from_text(
        "skills/project_budget_check.ns",
        SOURCE.replace(
            "    risk READ_VALIDATE;",
            '    risk READ_VALIDATE;\n\n    include "shared/empty.ns";',
        ),
    )
    fragment = SourceFile.from_text("skills/shared/empty.ns", "")
    return NslCompiler(
        build_tool_catalog(), MemoryIncludeResolver((fragment,))
    ).compile(root)


def test_ir_001_ns_source_compiles_to_nso_artifact() -> None:
    compilation = NslCompiler(build_tool_catalog()).compile(SOURCE)

    assert isinstance(compilation.nso_bytes, bytes)
    assert json.loads(compilation.nso_bytes)["format"] == "NSO"
    assert isinstance(NsoCodec.decode(compilation.nso_bytes), SkillObject)


def test_ir_002_nso_executes_without_source_or_compiler_frontend() -> None:
    catalog = build_tool_catalog()
    artifact = NslCompiler(catalog).compile(SOURCE).nso_bytes
    skill = NsoCodec.decode(artifact)
    request = ExecutionRequest(
        execution_id="exec-ir-002",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
        data_policy=DataHandlingPolicy(),
    )

    with patch("nsl.syntax.Lexer.tokenize", side_effect=AssertionError("source used")):
        result = asyncio.run(
            RuntimeEngine(catalog).execute(
                skill,
                request,
                build_mock_executor(catalog),
                InMemoryAuditSink(),
            )
        )

    assert result.status is ExecutionStatus.COMPLETED
    assert len(result.outputs) == 1


def test_ir_003_nso_contains_language_name_and_version() -> None:
    compilation = NslCompiler(build_tool_catalog()).compile(SOURCE)
    payload = json.loads(compilation.nso_bytes)

    assert payload["language"] == {"name": "NSL", "version": "0.1"}
    assert NsoCodec.decode(compilation.nso_bytes).language_version == "0.1"


def test_ir_004_nso_contains_skill_identity_and_version() -> None:
    compilation = NslCompiler(build_tool_catalog()).compile(SOURCE)
    payload = json.loads(compilation.nso_bytes)
    loaded = NsoCodec.decode(compilation.nso_bytes)

    assert payload["skill"] == {
        "id": "FINANCE.PROJECT_BUDGET_CHECK",
        "version": "1.0.0",
        "risk": "READ_VALIDATE",
    }
    assert (loaded.skill_id, loaded.skill_version, loaded.risk) == (
        "FINANCE.PROJECT_BUDGET_CHECK",
        "1.0.0",
        "READ_VALIDATE",
    )


def test_ir_005_nso_contains_typed_statement_and_expression_tree() -> None:
    payload = json.loads(NslCompiler(build_tool_catalog()).compile(SOURCE).nso_bytes)
    statement_kinds = {"let", "foreach", "check", "emit"}
    expression_kinds = {"read", "symbol_ref", "field", "project", "call", "binary"}
    found_statements: set[str] = set()
    found_expressions: set[str] = set()

    def visit(value) -> None:
        if isinstance(value, dict):
            kind = value.get("kind")
            if kind in statement_kinds:
                found_statements.add(kind)
                assert "node_id" in value
            if kind in expression_kinds:
                found_expressions.add(kind)
                assert TypeRef.from_data(value["type"]).kind
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload["body"])

    assert found_statements == statement_kinds
    assert found_expressions == expression_kinds


def test_ir_006_nso_contains_resolved_required_tool_contracts() -> None:
    catalog = build_tool_catalog()
    compilation = NslCompiler(catalog).compile(SOURCE)
    payload = json.loads(compilation.nso_bytes)
    loaded = NsoCodec.decode(compilation.nso_bytes)

    assert [item["tool_ref"] for item in payload["requires"]] == [
        "tool0001",
        "tool0002",
    ]
    assert len(loaded.required_tools) == 2
    for encoded, required in zip(payload["requires"], loaded.required_tools):
        contract = catalog.get(required.tool_id, required.version)
        assert encoded["tool_id"] == contract.tool_id
        assert encoded["version"] == contract.version
        assert encoded["capability"] == "READ"
        assert encoded["contract_hash"] == contract.contract_hash
        assert encoded["required_scope"] == contract.required_scope
        assert encoded["output_classification"] == contract.output_classification


def test_ir_007_nso_contains_explicit_resource_limits() -> None:
    compilation = NslCompiler(build_tool_catalog()).compile(SOURCE)
    payload = json.loads(compilation.nso_bytes)
    loaded = NsoCodec.decode(compilation.nso_bytes)

    assert payload["limits"] == {
        "tool_calls": 11,
        "loop_iterations": 10,
        "emitted_rows": 10,
        "collection_size": 1000,
    }
    assert payload["limits"] == {
        "tool_calls": loaded.limits.tool_calls,
        "loop_iterations": loaded.limits.loop_iterations,
        "emitted_rows": loaded.limits.emitted_rows,
        "collection_size": loaded.limits.collection_size,
    }


def test_ir_008_nso_contains_typed_output_schema() -> None:
    compilation = NslCompiler(build_tool_catalog()).compile(SOURCE)
    payload = json.loads(compilation.nso_bytes)
    loaded = NsoCodec.decode(compilation.nso_bytes)

    assert [field["name"] for field in payload["output"]] == [
        "parent_project",
        "budget",
        "spent",
        "remaining",
        "status",
    ]
    assert [field.name for field in loaded.outputs] == [
        field["name"] for field in payload["output"]
    ]
    for encoded, field in zip(payload["output"], loaded.outputs):
        assert TypeRef.from_data(encoded["type"]) == field.type_info
        assert encoded["classification"] == field.classification


def test_ir_013_bool_source_literal_is_normalized_to_typed_ir_literal() -> None:
    source = SOURCE.replace(
        "    let parents =",
        "    let approved = true;\n\n    let parents =",
    )
    compilation = NslCompiler(build_tool_catalog()).compile(source)
    payload = json.loads(compilation.nso_bytes)
    encoded = payload["body"][0]["value"]
    loaded = NsoCodec.decode(compilation.nso_bytes)

    assert encoded == {
        "kind": "literal",
        "node_id": "expr0001",
        "type": {"kind": "primitive", "name": "Bool"},
        "value": True,
    }
    assert type(encoded["value"]) is bool
    assert isinstance(loaded.body[0], LetStatement)
    assert isinstance(loaded.body[0].value, LiteralExpr)
    assert loaded.body[0].value.type_info == BOOL
    assert loaded.body[0].value.value is True


def test_ir_012_nso_contains_no_include_keyword_feature_or_ast_node() -> None:
    compilation = compile_with_empty_include()
    payload = json.loads(compilation.nso_bytes)

    assert "INCLUDE" not in payload["features"]
    assert b"include" not in compilation.nso_bytes.lower()
    assert all("include" not in type(statement).__name__.lower() for statement in compilation.skill.body)


def test_ir_014_nso_build_metadata_contains_root_and_include_manifest() -> None:
    compilation = compile_with_empty_include()
    payload = json.loads(compilation.nso_bytes)
    build = payload["build"]

    assert build["source_bundle_sha256"] == compilation.source_bundle_hash
    assert build["root_source"] == "skills/project_budget_check.ns"
    assert build["sources"] == [
        {
            "logical_path": item.logical_path,
            "sha256": item.content_hash,
            "size_bytes": item.size_bytes,
            "is_root": item.is_root,
        }
        for item in compilation.source_manifest
    ]
    assert [item["is_root"] for item in build["sources"]] == [True, False]
    loaded = NsoCodec.decode(compilation.nso_bytes)
    assert loaded.build == compilation.skill.build
    assert isinstance(loaded.build.sources, tuple)


def test_py_004_ir_model_has_strict_schema_validation_before_immutable_load() -> None:
    compilation = NslCompiler(build_tool_catalog()).compile(SOURCE)
    payload = json.loads(compilation.nso_bytes)
    validate_nso_document(payload)

    del payload["output"][0]["type"]
    malformed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(NsoSchemaError) as captured:
        NsoCodec.decode(malformed)

    assert captured.value.path == "$.output[0]"
    assert "missing fields: ['type']" in captured.value.reason
    for model in (SkillObject, BuildMetadata, BuildSource, LetStatement, LiteralExpr):
        assert is_dataclass(model)
        assert model.__dataclass_params__.frozen is True
