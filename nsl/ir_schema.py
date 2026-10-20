from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .json_boundary import load_strict_json


class NsoSchemaError(ValueError):
    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"invalid NSO schema at {path}: {reason}")


CLASSIFICATIONS = frozenset(
    {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}
)
IR_VERSIONS = frozenset({"1.0", "1.1"})
TOOL_CAPABILITIES = frozenset({"READ", "WRITE", "TRANSACTION"})
RISK_LEVELS = frozenset({"R0", "R1", "R2", "R3", "R4", "R5"})


def load_nso_json(data: bytes) -> Any:
    return load_strict_json(
        data,
        document_name="NSO",
        error_factory=NsoSchemaError,
    )


def _object(value: Any, path: str, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected object")
    actual = frozenset(value)
    missing = fields - actual
    if missing:
        raise NsoSchemaError(path, f"missing fields: {sorted(missing)}")
    unexpected = actual - fields
    if unexpected:
        raise NsoSchemaError(path, f"unexpected fields: {sorted(unexpected)}")
    return value


def _object_with_optional(
    value: Any,
    path: str,
    required: frozenset[str],
    optional: frozenset[str],
) -> dict[str, Any]:
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected object")
    actual = frozenset(value)
    missing = required - actual
    if missing:
        raise NsoSchemaError(path, f"missing fields: {sorted(missing)}")
    unexpected = actual - required - optional
    if unexpected:
        raise NsoSchemaError(path, f"unexpected fields: {sorted(unexpected)}")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if type(value) is not list:
        raise NsoSchemaError(path, "expected array")
    return value


def _string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise NsoSchemaError(path, "expected string")
    if not value and not allow_empty:
        raise NsoSchemaError(path, "must not be empty")
    return value


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if type(value) is not int:
        raise NsoSchemaError(path, "expected integer")
    if value < minimum:
        raise NsoSchemaError(path, f"must be at least {minimum}")
    return value


def _boolean(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise NsoSchemaError(path, "expected boolean")
    return value


def _sha256(value: Any, path: str) -> str:
    digest = _string(value, path)
    if (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise NsoSchemaError(path, "expected lowercase sha256 digest")
    return digest


def _choice(value: Any, path: str, choices: frozenset[str]) -> str:
    item = _string(value, path)
    if item not in choices:
        raise NsoSchemaError(path, f"expected one of: {sorted(choices)}")
    return item


def _currency(value: Any, path: str) -> str:
    currency = _string(value, path)
    if not (
        len(currency) == 3
        and currency.isascii()
        and currency.isalpha()
        and currency.isupper()
    ):
        raise NsoSchemaError(path, "expected 3-letter uppercase currency code")
    return currency


def _finite_decimal(value: Any, path: str) -> str:
    encoded = _string(value, path)
    try:
        decimal = Decimal(encoded)
    except InvalidOperation as error:
        raise NsoSchemaError(path, "expected decimal string") from error
    if not decimal.is_finite():
        raise NsoSchemaError(path, "expected finite decimal string")
    return encoded


def _constant(value: Any, expected: str, path: str) -> None:
    if value != expected:
        raise NsoSchemaError(path, f"expected {expected!r}")


def _validate_type(value: Any, path: str) -> None:
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected type object")
    kind = _string(value.get("kind"), f"{path}.kind")
    fields_by_kind = {
        "primitive": frozenset({"kind", "name"}),
        "domain": frozenset({"kind", "name"}),
        "money": frozenset({"kind", "currency"}),
        "list": frozenset({"kind", "item"}),
        "record": frozenset({"kind", "name", "fields"}),
        "enum": frozenset({"kind", "name", "values"}),
    }
    try:
        fields = fields_by_kind[kind]
    except KeyError as error:
        raise NsoSchemaError(f"{path}.kind", f"unknown type kind: {kind}") from error
    item = _object(value, path, fields)
    if kind in {"primitive", "domain"}:
        _string(item["name"], f"{path}.name")
    elif kind == "money":
        _currency(item["currency"], f"{path}.currency")
    elif kind == "list":
        _validate_type(item["item"], f"{path}.item")
    elif kind == "record":
        _string(item["name"], f"{path}.name")
        for index, field in enumerate(_array(item["fields"], f"{path}.fields")):
            field_path = f"{path}.fields[{index}]"
            field = _object(field, field_path, frozenset({"name", "type"}))
            _string(field["name"], f"{field_path}.name")
            _validate_type(field["type"], f"{field_path}.type")
    else:
        _string(item["name"], f"{path}.name")
        for index, enum_value in enumerate(
            _array(item["values"], f"{path}.values")
        ):
            _string(enum_value, f"{path}.values[{index}]")


def validate_type_document(value: Any, path: str = "$") -> None:
    """Validate the canonical JSON representation of a TypeRef."""

    _validate_type(value, path)


def _validate_literal_value(value: Any, path: str) -> None:
    if type(value) in {bool, int, str}:
        return
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected immutable literal value")
    tag = value.get("$type")
    if tag == "Decimal":
        item = _object(value, path, frozenset({"$type", "value"}))
        _finite_decimal(item["value"], f"{path}.value")
        return
    if tag == "Money":
        item = _object(
            value, path, frozenset({"$type", "amount", "currency"})
        )
        _finite_decimal(item["amount"], f"{path}.amount")
        _currency(item["currency"], f"{path}.currency")
        return
    raise NsoSchemaError(path, "unknown encoded literal value")


def _validate_expression(value: Any, path: str, ir_version: str) -> None:
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected expression object")
    kind = _string(value.get("kind"), f"{path}.kind")
    common = {"node_id", "kind", "type"}
    fields_by_kind = {
        "literal": frozenset(common | {"value"}),
        "symbol_ref": frozenset(common | {"symbol_id"}),
        "field": frozenset(common | {"source", "field"}),
        "project": frozenset(common | {"source", "field"}),
        "call": frozenset(common | {"function", "arguments"}),
        "binary": frozenset(common | {"operator", "left", "right"}),
        "read": frozenset(common | {"tool_ref", "arguments", "result_policy"}),
    }
    if ir_version == "1.1":
        fields_by_kind["write"] = frozenset(
            common | {"tool_ref", "approval_ref", "arguments"}
        )
    try:
        fields = fields_by_kind[kind]
    except KeyError as error:
        raise NsoSchemaError(
            f"{path}.kind", f"unknown expression kind: {kind}"
        ) from error
    item = _object(value, path, fields)
    _string(item["node_id"], f"{path}.node_id")
    _validate_type(item["type"], f"{path}.type")
    if kind == "literal":
        _validate_literal_value(item["value"], f"{path}.value")
    elif kind == "symbol_ref":
        _string(item["symbol_id"], f"{path}.symbol_id")
    elif kind in {"field", "project"}:
        _validate_expression(item["source"], f"{path}.source", ir_version)
        _string(item["field"], f"{path}.field")
    elif kind == "call":
        _string(item["function"], f"{path}.function")
        for index, argument in enumerate(
            _array(item["arguments"], f"{path}.arguments")
        ):
            _validate_expression(
                argument,
                f"{path}.arguments[{index}]",
                ir_version,
            )
    elif kind == "binary":
        _string(item["operator"], f"{path}.operator")
        _validate_expression(item["left"], f"{path}.left", ir_version)
        _validate_expression(item["right"], f"{path}.right", ir_version)
    elif kind in {"read", "write"}:
        _string(item["tool_ref"], f"{path}.tool_ref")
        if kind == "write":
            _string(item["approval_ref"], f"{path}.approval_ref")
        for index, argument in enumerate(
            _array(item["arguments"], f"{path}.arguments")
        ):
            argument_path = f"{path}.arguments[{index}]"
            argument = _object(
                argument, argument_path, frozenset({"name", "value"})
            )
            _string(argument["name"], f"{argument_path}.name")
            _validate_expression(
                argument["value"],
                f"{argument_path}.value",
                ir_version,
            )
        if kind == "write":
            return
        policy_path = f"{path}.result_policy"
        policy = _object(
            item["result_policy"],
            policy_path,
            frozenset({"required", "accept_partial", "empty_is_valid"}),
        )
        for name in ("required", "accept_partial", "empty_is_valid"):
            _boolean(policy[name], f"{policy_path}.{name}")


def _validate_statement(value: Any, path: str, ir_version: str) -> None:
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected statement object")
    kind = _string(value.get("kind"), f"{path}.kind")
    fields_by_kind = {
        "let": frozenset({"node_id", "kind", "target_symbol_id", "value"}),
        "foreach": frozenset(
            {
                "node_id",
                "kind",
                "iterator_symbol_id",
                "collection",
                "max_iterations",
                "body",
            }
        ),
        "check": frozenset(
            {
                "node_id",
                "kind",
                "check_id",
                "condition",
                "severity",
                "on_fail",
                "message",
                "result_symbol_id",
                "data_policy",
            }
        ),
        "emit": frozenset({"node_id", "kind", "fields"}),
    }
    if ir_version == "1.1":
        fields_by_kind["approval"] = frozenset(
            {"node_id", "kind", "approval_id", "message", "policy"}
        )
    try:
        fields = fields_by_kind[kind]
    except KeyError as error:
        raise NsoSchemaError(
            f"{path}.kind", f"unknown statement kind: {kind}"
        ) from error
    item = _object(value, path, fields)
    _string(item["node_id"], f"{path}.node_id")
    if kind == "let":
        _string(item["target_symbol_id"], f"{path}.target_symbol_id")
        _validate_expression(item["value"], f"{path}.value", ir_version)
    elif kind == "foreach":
        _string(item["iterator_symbol_id"], f"{path}.iterator_symbol_id")
        _validate_expression(
            item["collection"], f"{path}.collection", ir_version
        )
        _integer(item["max_iterations"], f"{path}.max_iterations", 1)
        for index, statement in enumerate(_array(item["body"], f"{path}.body")):
            _validate_statement(
                statement,
                f"{path}.body[{index}]",
                ir_version,
            )
    elif kind == "check":
        for name in (
            "check_id",
            "severity",
            "on_fail",
            "result_symbol_id",
        ):
            _string(item[name], f"{path}.{name}")
        _string(item["message"], f"{path}.message", allow_empty=True)
        _validate_expression(
            item["condition"], f"{path}.condition", ir_version
        )
        policy_path = f"{path}.data_policy"
        policy = _object(
            item["data_policy"],
            policy_path,
            frozenset({"require_complete", "on_partial", "on_unknown"}),
        )
        _boolean(policy["require_complete"], f"{policy_path}.require_complete")
        _constant(policy["on_partial"], "UNKNOWN", f"{policy_path}.on_partial")
        _constant(policy["on_unknown"], "UNKNOWN", f"{policy_path}.on_unknown")
    elif kind == "emit":
        for index, field in enumerate(_array(item["fields"], f"{path}.fields")):
            field_path = f"{path}.fields[{index}]"
            field = _object(field, field_path, frozenset({"name", "value"}))
            _string(field["name"], f"{field_path}.name")
            _validate_expression(
                field["value"], f"{field_path}.value", ir_version
            )
    else:
        _string(item["approval_id"], f"{path}.approval_id")
        _string(item["message"], f"{path}.message", allow_empty=True)
        policy_path = f"{path}.policy"
        policy = _object_with_optional(
            item["policy"],
            policy_path,
            frozenset({"required"}),
            frozenset({"ttl_seconds"}),
        )
        _boolean(policy["required"], f"{policy_path}.required")
        _constant(policy["required"], True, f"{policy_path}.required")
        if "ttl_seconds" in policy:
            _integer(policy["ttl_seconds"], f"{policy_path}.ttl_seconds", 1)


def _collect_write_links(
    expression: dict[str, Any],
    path: str,
    order: int,
    inside_foreach: bool,
    writes: list[tuple[str, str, str, int, bool]],
) -> None:
    kind = expression["kind"]
    if kind == "write":
        writes.append(
            (
                path,
                expression["approval_ref"],
                expression["tool_ref"],
                order,
                inside_foreach,
            )
        )
    if kind in {"field", "project"}:
        _collect_write_links(
            expression["source"],
            f"{path}.source",
            order,
            inside_foreach,
            writes,
        )
    elif kind == "call":
        for index, argument in enumerate(expression["arguments"]):
            _collect_write_links(
                argument,
                f"{path}.arguments[{index}]",
                order,
                inside_foreach,
                writes,
            )
    elif kind == "binary":
        for name in ("left", "right"):
            _collect_write_links(
                expression[name],
                f"{path}.{name}",
                order,
                inside_foreach,
                writes,
            )
    elif kind in {"read", "write"}:
        for index, argument in enumerate(expression["arguments"]):
            _collect_write_links(
                argument["value"],
                f"{path}.arguments[{index}].value",
                order,
                inside_foreach,
                writes,
            )


def _collect_ir_1_1_links(
    statements: list[Any],
    path: str,
    counter: list[int],
    approvals: dict[str, tuple[str, int]],
    writes: list[tuple[str, str, str, int, bool]],
    *,
    inside_foreach: bool = False,
) -> None:
    for index, statement in enumerate(statements):
        statement_path = f"{path}[{index}]"
        counter[0] += 1
        order = counter[0]
        kind = statement["kind"]
        if kind == "approval":
            approval_id = statement["approval_id"]
            if approval_id in approvals:
                raise NsoSchemaError(
                    f"{statement_path}.approval_id",
                    "duplicate approval_id",
                )
            approvals[approval_id] = (statement_path, order)
        elif kind == "let":
            _collect_write_links(
                statement["value"],
                f"{statement_path}.value",
                order,
                inside_foreach,
                writes,
            )
        elif kind == "foreach":
            _collect_write_links(
                statement["collection"],
                f"{statement_path}.collection",
                order,
                inside_foreach,
                writes,
            )
            _collect_ir_1_1_links(
                statement["body"],
                f"{statement_path}.body",
                counter,
                approvals,
                writes,
                inside_foreach=True,
            )
        elif kind == "check":
            _collect_write_links(
                statement["condition"],
                f"{statement_path}.condition",
                order,
                inside_foreach,
                writes,
            )
        elif kind == "emit":
            for field_index, field in enumerate(statement["fields"]):
                _collect_write_links(
                    field["value"],
                    f"{statement_path}.fields[{field_index}].value",
                    order,
                    inside_foreach,
                    writes,
                )


def _validate_ir_1_1_links(root: dict[str, Any]) -> None:
    tools: dict[str, tuple[str, str]] = {}
    for index, required in enumerate(root["requires"]):
        tool_ref = required["tool_ref"]
        path = f"$.requires[{index}].tool_ref"
        if tool_ref in tools:
            raise NsoSchemaError(path, "duplicate tool_ref")
        tools[tool_ref] = (required["capability"], path)

    approvals: dict[str, tuple[str, int]] = {}
    writes: list[tuple[str, str, str, int, bool]] = []
    _collect_ir_1_1_links(
        root["body"],
        "$.body",
        [0],
        approvals,
        writes,
    )
    if root["semantics_profile"] == "NSL-0.2-STRICT" and len(writes) > 1:
        raise NsoSchemaError(writes[1][0], "strict profile allows at most one WRITE")

    approval_use: dict[str, int] = {}
    for path, approval_ref, tool_ref, order, inside_foreach in writes:
        if inside_foreach:
            raise NsoSchemaError(path, "WRITE must not be nested inside foreach")
        approval = approvals.get(approval_ref)
        if approval is None:
            raise NsoSchemaError(
                f"{path}.approval_ref",
                "must reference a declared approval_id",
            )
        if approval[1] >= order:
            raise NsoSchemaError(
                f"{path}.approval_ref",
                "approval must occur before WRITE",
            )
        tool = tools.get(tool_ref)
        if tool is None:
            raise NsoSchemaError(
                f"{path}.tool_ref",
                "must reference a required tool",
            )
        if tool[0] != "WRITE":
            raise NsoSchemaError(
                f"{path}.tool_ref",
                "must reference a WRITE capability tool",
            )
        approval_use[approval_ref] = approval_use.get(approval_ref, 0) + 1

    for approval_id, (path, _) in approvals.items():
        if approval_use.get(approval_id, 0) != 1:
            raise NsoSchemaError(
                f"{path}.approval_id",
                "approval must be linked to exactly one WRITE",
            )


def validate_nso_document(value: Any) -> None:
    root_fields = frozenset(
        {
            "format",
            "ir_version",
            "language",
            "skill",
            "semantics_profile",
            "features",
            "symbols",
            "requires",
            "limits",
            "inputs",
            "contexts",
            "output",
            "body",
            "analysis",
            "hashes",
            "build",
        }
    )
    root = _object(value, "$", root_fields)
    _constant(root["format"], "NSO", "$.format")
    ir_version = _choice(root["ir_version"], "$.ir_version", IR_VERSIONS)

    language = _object(
        root["language"], "$.language", frozenset({"name", "version"})
    )
    _constant(language["name"], "NSL", "$.language.name")
    _string(language["version"], "$.language.version")

    skill = _object(
        root["skill"], "$.skill", frozenset({"id", "version", "risk"})
    )
    for name in ("id", "version", "risk"):
        _string(skill[name], f"$.skill.{name}")
    _string(root["semantics_profile"], "$.semantics_profile")
    for index, feature in enumerate(_array(root["features"], "$.features")):
        _string(feature, f"$.features[{index}]")

    for index, symbol in enumerate(_array(root["symbols"], "$.symbols")):
        path = f"$.symbols[{index}]"
        symbol = _object(
            symbol,
            path,
            frozenset({"symbol_id", "name", "category", "type", "classification"}),
        )
        for name in ("symbol_id", "name", "category"):
            _string(symbol[name], f"{path}.{name}")
        _choice(
            symbol["classification"], f"{path}.classification", CLASSIFICATIONS
        )
        _validate_type(symbol["type"], f"{path}.type")

    for index, required in enumerate(_array(root["requires"], "$.requires")):
        path = f"$.requires[{index}]"
        required_fields = frozenset(
            {
                "tool_ref",
                "tool_id",
                "version",
                "capability",
                "contract_hash",
                "required_scope",
                "output_classification",
            }
        )
        if ir_version == "1.1":
            required_fields |= frozenset({"risk_level"})
        required = _object(
            required,
            path,
            required_fields,
        )
        for name in (
            "tool_ref",
            "tool_id",
            "version",
            "required_scope",
        ):
            _string(required[name], f"{path}.{name}")
        capability = _choice(
            required["capability"],
            f"{path}.capability",
            TOOL_CAPABILITIES,
        )
        if ir_version == "1.0" and capability != "READ":
            raise NsoSchemaError(
                f"{path}.capability",
                "IR 1.0 supports READ capability only",
            )
        if ir_version == "1.1":
            _choice(
                required["risk_level"],
                f"{path}.risk_level",
                RISK_LEVELS,
            )
        _choice(
            required["output_classification"],
            f"{path}.output_classification",
            CLASSIFICATIONS,
        )
        _sha256(required["contract_hash"], f"{path}.contract_hash")

    limits = _object(
        root["limits"],
        "$.limits",
        frozenset(
            {
                "tool_calls",
                "loop_iterations",
                "emitted_rows",
                "collection_size",
                "duration_ms",
            }
        ),
    )
    for name in (
        "tool_calls",
        "loop_iterations",
        "emitted_rows",
        "collection_size",
        "duration_ms",
    ):
        _integer(limits[name], f"$.limits.{name}", 1)

    for index, input_spec in enumerate(_array(root["inputs"], "$.inputs")):
        path = f"$.inputs[{index}]"
        input_spec = _object(
            input_spec,
            path,
            frozenset(
                {"symbol_id", "name", "type", "required", "classification"}
            ),
        )
        for name in ("symbol_id", "name"):
            _string(input_spec[name], f"{path}.{name}")
        _choice(
            input_spec["classification"], f"{path}.classification", CLASSIFICATIONS
        )
        _validate_type(input_spec["type"], f"{path}.type")
        _boolean(input_spec["required"], f"{path}.required")

    for index, context in enumerate(_array(root["contexts"], "$.contexts")):
        path = f"$.contexts[{index}]"
        context = _object(
            context,
            path,
            frozenset({"symbol_id", "name", "type", "path", "classification"}),
        )
        for name in ("symbol_id", "name"):
            _string(context[name], f"{path}.{name}")
        _choice(
            context["classification"], f"{path}.classification", CLASSIFICATIONS
        )
        _validate_type(context["type"], f"{path}.type")
        for part_index, part in enumerate(_array(context["path"], f"{path}.path")):
            _string(part, f"{path}.path[{part_index}]")

    for index, output in enumerate(_array(root["output"], "$.output")):
        path = f"$.output[{index}]"
        output = _object(
            output,
            path,
            frozenset({"name", "type", "classification"}),
        )
        _string(output["name"], f"{path}.name")
        _validate_type(output["type"], f"{path}.type")
        _choice(output["classification"], f"{path}.classification", CLASSIFICATIONS)

    for index, statement in enumerate(_array(root["body"], "$.body")):
        _validate_statement(statement, f"$.body[{index}]", ir_version)
    if ir_version == "1.1":
        _validate_ir_1_1_links(root)

    analysis = _object(
        root["analysis"],
        "$.analysis",
        frozenset(
            {
                "max_tool_calls",
                "max_loop_iterations",
                "max_emit_records",
                "bounded",
            }
        ),
    )
    for name in ("max_tool_calls", "max_loop_iterations", "max_emit_records"):
        _integer(analysis[name], f"$.analysis.{name}")
    _boolean(analysis["bounded"], "$.analysis.bounded")

    hashes = _object(
        root["hashes"],
        "$.hashes",
        frozenset({"source_bundle_sha256", "semantic_sha256"}),
    )
    _sha256(hashes["source_bundle_sha256"], "$.hashes.source_bundle_sha256")
    _sha256(hashes["semantic_sha256"], "$.hashes.semantic_sha256")

    build = _object(
        root["build"],
        "$.build",
        frozenset({"source_bundle_sha256", "root_source", "sources"}),
    )
    _sha256(build["source_bundle_sha256"], "$.build.source_bundle_sha256")
    _string(build["root_source"], "$.build.root_source")
    sources = _array(build["sources"], "$.build.sources")
    if not sources:
        raise NsoSchemaError("$.build.sources", "must not be empty")
    root_count = 0
    logical_paths: set[str] = set()
    for index, source in enumerate(sources):
        path = f"$.build.sources[{index}]"
        source = _object(
            source,
            path,
            frozenset({"logical_path", "sha256", "size_bytes", "is_root"}),
        )
        logical_path = _string(source["logical_path"], f"{path}.logical_path")
        if logical_path in logical_paths:
            raise NsoSchemaError(
                f"{path}.logical_path", "duplicate source logical path"
            )
        logical_paths.add(logical_path)
        _sha256(source["sha256"], f"{path}.sha256")
        _integer(source["size_bytes"], f"{path}.size_bytes")
        if _boolean(source["is_root"], f"{path}.is_root"):
            root_count += 1
    if root_count != 1:
        raise NsoSchemaError("$.build.sources", "must contain exactly one root")
    root_entry = next(source for source in sources if source["is_root"])
    if root_entry["logical_path"] != build["root_source"]:
        raise NsoSchemaError(
            "$.build.root_source", "must identify the root manifest entry"
        )
