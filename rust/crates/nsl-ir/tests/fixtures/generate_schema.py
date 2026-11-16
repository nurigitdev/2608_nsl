"""Generate structured NSO mutations and their frozen Oracle admission verdicts."""

from copy import deepcopy
import json
from pathlib import Path
import sys


def set_field(path, value):
    return "set", path, value


def remove(path):
    return "remove", path, None


def copy(source, target):
    return "copy", target, source


def lookup(root, path):
    for part in path.split("/") if path else ():
        root = root[int(part) if isinstance(root, list) else part]
    return root


def apply(doc, edit):
    operation, path, value = edit
    parent, _, field = path.rpartition("/")
    item = lookup(doc, parent)
    field = int(field) if isinstance(item, list) else field
    if operation == "remove":
        del item[field]
        return
    if operation == "copy":
        value = lookup(doc, value)
    if isinstance(item, list) and field == len(item):
        item.append(deepcopy(value))
    else:
        item[field] = deepcopy(value)


def cases():
    yield "1.0", "frozen_read_only", []
    yield "1.1", "frozen_controlled_write", []

    shared = (
        ("unknown_ir", set_field("ir_version", "1.2")),
        ("wrong_format", set_field("format", "NSL")),
        ("missing_language", remove("language")),
        ("wrong_language_name", set_field("language/name", "nsl")),
        ("extra_skill_field", set_field("skill/extra", True)),
        ("unknown_profile_is_metadata", set_field("semantics_profile", "demo-profile")),
        ("empty_feature", set_field("features/0", "")),
        ("classification_case", set_field("symbols/0/classification", "internal")),
        ("bad_digest", set_field("requires/0/contract_hash", "sha256:" + "A" * 64)),
        ("unknown_capability", set_field("requires/0/capability", "read")),
        ("limit_zero", set_field("limits/tool_calls", 0)),
        ("limit_bool", set_field("limits/tool_calls", True)),
        ("limit_wide_integer", set_field("limits/tool_calls", 2**128)),
        ("analysis_zero", set_field("analysis/max_tool_calls", 0)),
        ("analysis_negative", set_field("analysis/max_tool_calls", -1)),
        ("wrong_bounded_type", set_field("analysis/bounded", "true")),
        ("bad_input_required", set_field("inputs/0/required", 1)),
        ("output_is_array", set_field("output", {})),
        ("missing_hash", remove("hashes/semantic_sha256")),
        ("bad_source_size", set_field("build/sources/0/size_bytes", -1)),
        ("no_sources", set_field("build/sources", [])),
        ("no_root", set_field("build/sources/0/is_root", False)),
        ("wrong_root", set_field("build/root_source", "different.ns")),
        ("duplicate_source_path", copy("build/sources/0", "build/sources/1")),
        ("credential_text", set_field("skill/id", "password=demo-placeholder")),
        ("credential_nested_key_before_shape", set_field("x", [{"API-Key": "demo-placeholder"}])),
        ("credential_key_before_shape", set_field("Bearer DEMO", None)),
        ("empty_credential_key_still_unknown_field", set_field("x", {"password": ""})),
    )
    for name, edit in shared:
        yield "1.0", name, [edit]

    for name, ty in (
        ("open_primitive_name", {"kind": "primitive", "name": "FutureType"}),
        ("missing_record_fields", {"kind": "record", "name": "Record"}),
        ("empty_record_fields", {"kind": "record", "name": "Record", "fields": []}),
        ("missing_enum_values", {"kind": "enum", "name": "Status"}),
        ("empty_enum_member", {"kind": "enum", "name": "Status", "values": [""]}),
        ("type_extra", {"kind": "primitive", "name": "Int", "x": 1}),
        ("nested_optional", {"kind": "list", "item": {"kind": "optional", "item": {"kind": "primitive", "name": "Int"}}}),
        ("bad_type_currency", {"kind": "money", "currency": "krw"}),
    ):
        yield "1.0", name, [set_field("output/0/type", ty)]

    read_cases = (
        ("read_with_risk", set_field("requires/0/risk_level", "R0")),
        ("write_capability_in_1_0", set_field("requires/0/capability", "WRITE")),
        ("duplicate_read_tools_in_1_0", copy("requires/0", "requires/2")),
        ("result_policy_missing", remove("body/0/value/result_policy/required")),
        ("read_arg_extra", set_field("body/0/value/arguments/0/extra", "x")),
        ("unknown_expression", set_field("body/1/body/1/value/arguments/0/kind", "projection")),
        ("unknown_statement", set_field("body/1/body/0/kind", "while")),
        ("write_node_in_1_0", set_field("body/0/value/kind", "write")),
        ("approval_node_in_1_0", set_field("body/0/kind", "approval")),
        ("foreach_zero", set_field("body/1/max_iterations", 0)),
        ("check_bad_policy_enum", set_field("body/1/body/3/data_policy/on_partial", "FAIL")),
        ("check_false_complete_is_schema_valid", set_field("body/1/body/3/data_policy/require_complete", False)),
        ("emit_bad_name", set_field("body/1/body/4/fields/0/name", "")),
        ("context_bad_path", set_field("contexts/0/path/0", 1)),
    )
    for name, edit in read_cases:
        yield "1.0", name, [edit]
    yield "1.0", "read_only_ir_1_1_has_risk", [set_field("ir_version", "1.1"),
        set_field("language/version", "0.2"), set_field("semantics_profile", "NSL-0.2-STRICT"),
        set_field("requires/0/risk_level", "R0"), set_field("requires/1/risk_level", "R1")]

    for name, value in (
        ("wide_literal", 2**128), ("finite_decimal", {"$type": "Decimal", "value": "-0E-2"}),
        ("nonfinite_literal", {"$type": "Decimal", "value": "NaN"}),
        ("null_literal", None), ("unsupported_literal_tag", {"$type": "Date", "value": "2026-01-01"}),
        ("invalid_money_currency", {"$type": "Money", "amount": "1", "currency": "K0W"}),
        ("extra_money_field", {"$type": "Money", "amount": "1", "currency": "KRW", "extra": False}),
    ):
        literal = {"kind": "literal", "node_id": "fixture", "type": {"kind": "primitive", "name": "Int"}, "value": value}
        yield "1.0", name, [set_field("body/0/value", literal)]

    for name, edit in (
        ("risk_missing", remove("requires/0/risk_level")),
        ("risk_case", set_field("requires/0/risk_level", "r3")),
        ("unknown_risk", set_field("requires/0/risk_level", "R6")),
        ("duplicate_tool_ref", copy("requires/0", "requires/1")),
        ("missing_approval_ref", remove("body/1/value/approval_ref")),
        ("unknown_approval_ref", set_field("body/1/value/approval_ref", "UNKNOWN_APPROVAL")),
        ("unknown_write_tool_ref", set_field("body/1/value/tool_ref", "missing-tool")),
        ("read_tool_for_write", set_field("requires/0/capability", "READ")),
        ("transaction_tool_for_write", set_field("requires/0/capability", "TRANSACTION")),
        ("write_has_result_policy", set_field("body/1/value/result_policy", {})),
        ("approval_unused", remove("body/1")),
        ("approval_duplicate", copy("body/0", "body/3")),
        ("second_strict_write", copy("body/1", "body/3")),
        ("approval_not_required", set_field("body/0/policy/required", False)),
        ("approval_null_ttl", set_field("body/0/policy/ttl_seconds", None)),
        ("approval_zero_ttl", set_field("body/0/policy/ttl_seconds", 0)),
        ("approval_positive_ttl", set_field("body/0/policy/ttl_seconds", 1)),
        ("approval_wide_ttl", set_field("body/0/policy/ttl_seconds", 2**128)),
        ("credential_in_approval_message", set_field("body/0/message", "Bearer DEMO.STUB")),
        ("write_blank_node", set_field("body/1/value/node_id", "\u00a0")),
        ("write_blank_arg_name", set_field("body/1/value/arguments/0/name", "  ")),
        ("duplicate_write_args", copy("body/1/value/arguments/0", "body/1/value/arguments/1")),
        ("blank_approval_node", set_field("body/0/node_id", "\u3000")),
    ):
        yield "1.1", name, [edit]

    yield "1.1", "approval_after_write", [copy("body/0", "body/3"), remove("body/0")]
    yield "1.1", "approval_reused", [set_field("semantics_profile", "demo-profile"), copy("body/1", "body/3")]
    loop = {"kind": "foreach", "node_id": "loop", "iterator_symbol_id": "iter", "max_iterations": 1,
            "collection": {"kind": "symbol_ref", "node_id": "collection", "symbol_id": "items",
                           "type": {"kind": "list", "item": {"kind": "primitive", "name": "Int"}}}, "body": []}
    yield "1.1", "write_nested_in_foreach", [set_field("body/3", loop), copy("body/1", "body/3/body/0"), remove("body/1")]
    yield "1.1", "write_in_nested_argument", [set_field("body/3", loop), copy("body/1", "body/3/body/0"),
        set_field("body/3/body/0/value", {"kind": "call", "node_id": "call", "function": "example",
            "type": {"kind": "primitive", "name": "Int"}, "arguments": []}),
        copy("body/1/value", "body/3/body/0/value/arguments/0"), remove("body/1")]


def rust(value):
    if value is None:
        return "ValueData::Null"
    if type(value) is bool:
        return f"ValueData::Bool({str(value).lower()})"
    if type(value) is int:
        return f'ValueData::Number("{value}".parse().unwrap())'
    if isinstance(value, str):
        return f'{string(value)}.into()'
    if isinstance(value, list):
        return "ValueData::Array(vec![" + ",".join(map(rust, value)) + "])"
    return "ValueData::Object(BTreeMap::from([" + ",".join(
        f"({string(key)}.into(),{rust(item)})" for key, item in value.items()
    ) + "]))"


def string(value):
    return json.dumps(value, ensure_ascii=False)


def rust_edit(edit):
    operation, target, value = edit
    if operation == "set":
        return f'Edit::Set({string(target)},{rust(value)})'
    if operation == "copy":
        return f'Edit::Copy({string(value)},{string(target)})'
    return f'Edit::Remove({string(target)})'


def main(oracle_root, corpus_root):
    sys.path.insert(0, str(oracle_root.resolve()))
    from nsl.data_protection import CredentialMaterialError, ensure_no_credential_material
    from nsl.ir import _statement_from_data
    from nsl.ir_schema import NsoSchemaError, load_nso_json, validate_nso_document

    docs = {version: load_nso_json((corpus_root / f"ir-{version.replace('.', '-')}-canonical" / "expected.nso").read_bytes())
            for version in ("1.0", "1.1")}
    print("// Generated by fixtures/generate_schema.py from Python Oracle 28a949e.")
    print("fn cases() -> Vec<Case> { vec![")
    for version, name, edits in cases():
        doc = deepcopy(docs[version])
        for edit in edits:
            apply(doc, edit)
        try:
            ensure_no_credential_material(doc, ".nso")
            validate_nso_document(doc)
            for node in doc["body"]:
                _statement_from_data(node)
        except CredentialMaterialError:
            expected = "Outcome::Credential"
        except NsoSchemaError as error:
            expected = f"Outcome::Schema(Some({string(error.path)}))"
        except ValueError:
            expected = "Outcome::Schema(None)"
        else:
            expected = "Outcome::Accept"
        mutations = ",".join(map(rust_edit, edits))
        print(f'Case {{ name:{string(name)},version:{string(version)},edits:vec![{mutations}],expected:{expected} }},')
    print("] }")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
