from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import Any, TypeAlias

from .core import DataClassification, TypeRef, decode_value, encode_value
from .data_protection import ensure_no_credential_material
from .integrity import source_manifest_sha256
from .ir_schema import load_nso_json, validate_nso_document


@dataclass(frozen=True, slots=True)
class ResultPolicy:
    required: bool = True
    accept_partial: bool = False
    empty_is_valid: bool = True


@dataclass(frozen=True, slots=True)
class LiteralExpr:
    node_id: str
    value: Any
    type_info: TypeRef


@dataclass(frozen=True, slots=True)
class SymbolRefExpr:
    node_id: str
    symbol_id: str
    type_info: TypeRef


@dataclass(frozen=True, slots=True)
class FieldExpr:
    node_id: str
    source: Expression
    field: str
    type_info: TypeRef


@dataclass(frozen=True, slots=True)
class ProjectionExpr:
    node_id: str
    source: Expression
    field: str
    type_info: TypeRef


@dataclass(frozen=True, slots=True)
class CallExpr:
    node_id: str
    function: str
    arguments: tuple[Expression, ...]
    type_info: TypeRef


@dataclass(frozen=True, slots=True)
class BinaryExpr:
    node_id: str
    operator: str
    left: Expression
    right: Expression
    type_info: TypeRef


@dataclass(frozen=True, slots=True)
class ReadExpr:
    node_id: str
    tool_ref: str
    arguments: tuple[tuple[str, Expression], ...]
    type_info: TypeRef
    result_policy: ResultPolicy


Expression: TypeAlias = (
    LiteralExpr
    | SymbolRefExpr
    | FieldExpr
    | ProjectionExpr
    | CallExpr
    | BinaryExpr
    | ReadExpr
)


@dataclass(frozen=True, slots=True)
class LetStatement:
    node_id: str
    target_symbol_id: str
    value: Expression


@dataclass(frozen=True, slots=True)
class ForeachStatement:
    node_id: str
    iterator_symbol_id: str
    collection: Expression
    max_iterations: int
    body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class CheckStatement:
    node_id: str
    check_id: str
    condition: Expression
    severity: str
    on_fail: str
    message: str
    result_symbol_id: str


@dataclass(frozen=True, slots=True)
class EmitStatement:
    node_id: str
    fields: tuple[tuple[str, Expression], ...]


Statement: TypeAlias = LetStatement | ForeachStatement | CheckStatement | EmitStatement


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    symbol_id: str
    name: str
    category: str
    type_info: TypeRef
    classification: DataClassification


@dataclass(frozen=True, slots=True)
class InputSpec:
    symbol_id: str
    name: str
    type_info: TypeRef
    required: bool
    classification: DataClassification


@dataclass(frozen=True, slots=True)
class ContextSpec:
    symbol_id: str
    name: str
    type_info: TypeRef
    path: tuple[str, ...]
    classification: DataClassification


@dataclass(frozen=True, slots=True)
class OutputField:
    name: str
    type_info: TypeRef
    classification: DataClassification


@dataclass(frozen=True, slots=True)
class RequiredTool:
    tool_ref: str
    tool_id: str
    version: str
    capability: str
    contract_hash: str
    required_scope: str
    output_classification: DataClassification


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    tool_calls: int
    loop_iterations: int
    emitted_rows: int
    collection_size: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class StaticAnalysis:
    max_tool_calls: int
    max_loop_iterations: int
    max_emit_records: int
    bounded: bool


@dataclass(frozen=True, slots=True)
class BuildSource:
    logical_path: str
    content_hash: str
    size_bytes: int
    is_root: bool


@dataclass(frozen=True, slots=True)
class BuildMetadata:
    source_bundle_sha256: str
    root_source: str
    sources: tuple[BuildSource, ...]


class NsoIntegrityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SkillObject:
    ir_version: str
    language_version: str
    skill_id: str
    skill_version: str
    risk: str
    semantics_profile: str
    semantic_hash: str
    features: frozenset[str]
    symbols: tuple[SymbolSpec, ...]
    required_tools: tuple[RequiredTool, ...]
    limits: ResourceLimits
    inputs: tuple[InputSpec, ...]
    contexts: tuple[ContextSpec, ...]
    outputs: tuple[OutputField, ...]
    body: tuple[Statement, ...]
    analysis: StaticAnalysis
    build: BuildMetadata

    def with_computed_hash(self) -> SkillObject:
        payload = skill_to_data(self, include_hash=False, include_build=False)
        digest = sha256(canonical_json(payload)).hexdigest()
        return replace(self, semantic_hash="sha256:" + digest)


def _expr_to_data(expr: Expression) -> dict[str, Any]:
    base = {"node_id": expr.node_id, "type": expr.type_info.to_data()}
    if isinstance(expr, LiteralExpr):
        return {**base, "kind": "literal", "value": encode_value(expr.value)}
    if isinstance(expr, SymbolRefExpr):
        return {**base, "kind": "symbol_ref", "symbol_id": expr.symbol_id}
    if isinstance(expr, FieldExpr):
        return {
            **base,
            "kind": "field",
            "source": _expr_to_data(expr.source),
            "field": expr.field,
        }
    if isinstance(expr, ProjectionExpr):
        return {
            **base,
            "kind": "project",
            "source": _expr_to_data(expr.source),
            "field": expr.field,
        }
    if isinstance(expr, CallExpr):
        return {
            **base,
            "kind": "call",
            "function": expr.function,
            "arguments": [_expr_to_data(arg) for arg in expr.arguments],
        }
    if isinstance(expr, BinaryExpr):
        return {
            **base,
            "kind": "binary",
            "operator": expr.operator,
            "left": _expr_to_data(expr.left),
            "right": _expr_to_data(expr.right),
        }
    if isinstance(expr, ReadExpr):
        return {
            **base,
            "kind": "read",
            "tool_ref": expr.tool_ref,
            "arguments": [
                {"name": name, "value": _expr_to_data(value)}
                for name, value in expr.arguments
            ],
            "result_policy": {
                "required": expr.result_policy.required,
                "accept_partial": expr.result_policy.accept_partial,
                "empty_is_valid": expr.result_policy.empty_is_valid,
            },
        }
    raise TypeError(f"unsupported expression: {type(expr)!r}")


def _statement_to_data(statement: Statement) -> dict[str, Any]:
    if isinstance(statement, LetStatement):
        return {
            "node_id": statement.node_id,
            "kind": "let",
            "target_symbol_id": statement.target_symbol_id,
            "value": _expr_to_data(statement.value),
        }
    if isinstance(statement, ForeachStatement):
        return {
            "node_id": statement.node_id,
            "kind": "foreach",
            "iterator_symbol_id": statement.iterator_symbol_id,
            "collection": _expr_to_data(statement.collection),
            "max_iterations": statement.max_iterations,
            "body": [_statement_to_data(item) for item in statement.body],
        }
    if isinstance(statement, CheckStatement):
        return {
            "node_id": statement.node_id,
            "kind": "check",
            "check_id": statement.check_id,
            "condition": _expr_to_data(statement.condition),
            "severity": statement.severity,
            "on_fail": statement.on_fail,
            "message": statement.message,
            "result_symbol_id": statement.result_symbol_id,
            "data_policy": {
                "require_complete": True,
                "on_partial": "UNKNOWN",
                "on_unknown": "UNKNOWN",
            },
        }
    if isinstance(statement, EmitStatement):
        return {
            "node_id": statement.node_id,
            "kind": "emit",
            "fields": [
                {"name": name, "value": _expr_to_data(value)}
                for name, value in statement.fields
            ],
        }
    raise TypeError(f"unsupported statement: {type(statement)!r}")


def skill_to_data(
    skill: SkillObject,
    include_hash: bool = True,
    include_build: bool = True,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": "NSO",
        "ir_version": skill.ir_version,
        "language": {"name": "NSL", "version": skill.language_version},
        "skill": {
            "id": skill.skill_id,
            "version": skill.skill_version,
            "risk": skill.risk,
        },
        "semantics_profile": skill.semantics_profile,
        "features": sorted(skill.features),
        "symbols": [
            {
                "symbol_id": item.symbol_id,
                "name": item.name,
                "category": item.category,
                "type": item.type_info.to_data(),
                "classification": item.classification.value,
            }
            for item in skill.symbols
        ],
        "requires": [
            {
                "tool_ref": item.tool_ref,
                "tool_id": item.tool_id,
                "version": item.version,
                "capability": item.capability,
                "contract_hash": item.contract_hash,
                "required_scope": item.required_scope,
                "output_classification": item.output_classification.value,
            }
            for item in skill.required_tools
        ],
        "limits": {
            "tool_calls": skill.limits.tool_calls,
            "loop_iterations": skill.limits.loop_iterations,
            "emitted_rows": skill.limits.emitted_rows,
            "collection_size": skill.limits.collection_size,
            "duration_ms": skill.limits.duration_ms,
        },
        "inputs": [
            {
                "symbol_id": item.symbol_id,
                "name": item.name,
                "type": item.type_info.to_data(),
                "required": item.required,
                "classification": item.classification.value,
            }
            for item in skill.inputs
        ],
        "contexts": [
            {
                "symbol_id": item.symbol_id,
                "name": item.name,
                "type": item.type_info.to_data(),
                "path": list(item.path),
                "classification": item.classification.value,
            }
            for item in skill.contexts
        ],
        "output": [
            {
                "name": item.name,
                "type": item.type_info.to_data(),
                "classification": item.classification.value,
            }
            for item in skill.outputs
        ],
        "body": [_statement_to_data(item) for item in skill.body],
        "analysis": {
            "max_tool_calls": skill.analysis.max_tool_calls,
            "max_loop_iterations": skill.analysis.max_loop_iterations,
            "max_emit_records": skill.analysis.max_emit_records,
            "bounded": skill.analysis.bounded,
        },
    }
    if include_build:
        data["build"] = {
            "source_bundle_sha256": skill.build.source_bundle_sha256,
            "root_source": skill.build.root_source,
            "sources": [
                {
                    "logical_path": item.logical_path,
                    "sha256": item.content_hash,
                    "size_bytes": item.size_bytes,
                    "is_root": item.is_root,
                }
                for item in skill.build.sources
            ],
        }
    if include_hash:
        data["hashes"] = {
            "source_bundle_sha256": skill.build.source_bundle_sha256,
            "semantic_sha256": skill.semantic_hash,
        }
    return data


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _expr_from_data(data: dict[str, Any]) -> Expression:
    kind = data["kind"]
    common = {"node_id": data["node_id"], "type_info": TypeRef.from_data(data["type"])}
    if kind == "literal":
        return LiteralExpr(value=decode_value(data["value"]), **common)
    if kind == "symbol_ref":
        return SymbolRefExpr(symbol_id=data["symbol_id"], **common)
    if kind == "field":
        return FieldExpr(
            source=_expr_from_data(data["source"]), field=data["field"], **common
        )
    if kind == "project":
        return ProjectionExpr(
            source=_expr_from_data(data["source"]), field=data["field"], **common
        )
    if kind == "call":
        return CallExpr(
            function=data["function"],
            arguments=tuple(_expr_from_data(item) for item in data["arguments"]),
            **common,
        )
    if kind == "binary":
        return BinaryExpr(
            operator=data["operator"],
            left=_expr_from_data(data["left"]),
            right=_expr_from_data(data["right"]),
            **common,
        )
    if kind == "read":
        policy = data["result_policy"]
        return ReadExpr(
            tool_ref=data["tool_ref"],
            arguments=tuple(
                (item["name"], _expr_from_data(item["value"]))
                for item in data["arguments"]
            ),
            result_policy=ResultPolicy(**policy),
            **common,
        )
    raise ValueError(f"unknown expression kind: {kind}")


def _statement_from_data(data: dict[str, Any]) -> Statement:
    kind = data["kind"]
    if kind == "let":
        return LetStatement(
            data["node_id"], data["target_symbol_id"], _expr_from_data(data["value"])
        )
    if kind == "foreach":
        return ForeachStatement(
            data["node_id"],
            data["iterator_symbol_id"],
            _expr_from_data(data["collection"]),
            data["max_iterations"],
            tuple(_statement_from_data(item) for item in data["body"]),
        )
    if kind == "check":
        return CheckStatement(
            data["node_id"],
            data["check_id"],
            _expr_from_data(data["condition"]),
            data["severity"],
            data["on_fail"],
            data["message"],
            data["result_symbol_id"],
        )
    if kind == "emit":
        return EmitStatement(
            data["node_id"],
            tuple(
                (item["name"], _expr_from_data(item["value"]))
                for item in data["fields"]
            ),
        )
    raise ValueError(f"unknown statement kind: {kind}")


class NsoCodec:
    @staticmethod
    def encode(skill: SkillObject) -> bytes:
        payload = skill_to_data(skill)
        ensure_no_credential_material(payload, ".nso")
        return canonical_json(payload)

    @staticmethod
    def decode(data: bytes) -> SkillObject:
        raw = load_nso_json(data)
        ensure_no_credential_material(raw, ".nso")
        validate_nso_document(raw)
        symbols = tuple(
            SymbolSpec(
                item["symbol_id"],
                item["name"],
                item["category"],
                TypeRef.from_data(item["type"]),
                DataClassification(item["classification"]),
            )
            for item in raw["symbols"]
        )
        requires = tuple(
            RequiredTool(
                item["tool_ref"],
                item["tool_id"],
                item["version"],
                item["capability"],
                item["contract_hash"],
                item["required_scope"],
                DataClassification(item["output_classification"]),
            )
            for item in raw["requires"]
        )
        limits = ResourceLimits(**raw["limits"])
        inputs = tuple(
            InputSpec(
                item["symbol_id"],
                item["name"],
                TypeRef.from_data(item["type"]),
                item["required"],
                DataClassification(item["classification"]),
            )
            for item in raw["inputs"]
        )
        contexts = tuple(
            ContextSpec(
                item["symbol_id"],
                item["name"],
                TypeRef.from_data(item["type"]),
                tuple(item["path"]),
                DataClassification(item["classification"]),
            )
            for item in raw["contexts"]
        )
        outputs = tuple(
            OutputField(
                item["name"],
                TypeRef.from_data(item["type"]),
                DataClassification(item["classification"]),
            )
            for item in raw["output"]
        )
        analysis = StaticAnalysis(**raw["analysis"])
        build = BuildMetadata(
            source_bundle_sha256=raw["build"]["source_bundle_sha256"],
            root_source=raw["build"]["root_source"],
            sources=tuple(
                BuildSource(
                    logical_path=item["logical_path"],
                    content_hash=item["sha256"],
                    size_bytes=item["size_bytes"],
                    is_root=item["is_root"],
                )
                for item in raw["build"]["sources"]
            ),
        )
        source_bundle_hash = raw["hashes"]["source_bundle_sha256"]
        if source_bundle_hash != build.source_bundle_sha256:
            raise NsoIntegrityError("source bundle hash differs between hashes and build")
        expected_source_bundle_hash = source_manifest_sha256(build.sources)
        if source_bundle_hash != expected_source_bundle_hash:
            raise NsoIntegrityError("source bundle manifest hash mismatch")
        skill = SkillObject(
            ir_version=raw["ir_version"],
            language_version=raw["language"]["version"],
            skill_id=raw["skill"]["id"],
            skill_version=raw["skill"]["version"],
            risk=raw["skill"]["risk"],
            semantics_profile=raw["semantics_profile"],
            semantic_hash=raw["hashes"]["semantic_sha256"],
            features=frozenset(raw["features"]),
            symbols=symbols,
            required_tools=requires,
            limits=limits,
            inputs=inputs,
            contexts=contexts,
            outputs=outputs,
            body=tuple(_statement_from_data(item) for item in raw["body"]),
            analysis=analysis,
            build=build,
        )
        expected = skill.with_computed_hash().semantic_hash
        if skill.semantic_hash != expected:
            raise NsoIntegrityError("semantic hash mismatch")
        return skill
