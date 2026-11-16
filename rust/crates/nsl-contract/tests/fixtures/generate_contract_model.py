"""Record constructor, field, and lookup behavior from the frozen Tool Contract Oracle."""

from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.core import DataClassification, STRING, ToolCapability, TypeRef, domain, list_type, money_type
from nsl.tools import (
    CommitSemantics,
    IdempotencySemantics,
    PostWriteVerification,
    ToolContract,
    ToolContractCatalog,
    WriteSemantics,
)


def field(text):
    if text is None:
        return "~"
    return text.encode("utf-8").hex() or "-"


def word(value):
    return str(value).lower()


def typ(value):
    if value is None:
        return "~"
    if value.kind in ("list", "optional"):
        return f"{value.kind}:{typ(value.item)}"
    return f"{value.kind}:{value.currency or value.name}"


def record(*fields):
    print(*fields, sep="\t")


def parse_value(data, key, default):
    value = data.get(key, default)
    return None if value == "~" else value


def idempotency(data):
    return IdempotencySemantics(
        required=data.get("required", "true") == "true",
        scope=data.get("scope", "TOOL_AND_TENANT"),
        retention=data.get("retention", "PROVIDER_DEFINED"),
        duplicate_behavior=data.get("duplicate_behavior", "RETURN_ORIGINAL_RESULT"),
        payload_conflict_behavior=data.get("payload_conflict_behavior", "IDEMPOTENCY_CONFLICT"),
    )


def verification(data):
    label = data.get("reference_schema", "~")
    schema = {"~": None, "domain": domain("Draft"), "optional": TypeRef("optional", item=STRING)}[label]
    return PostWriteVerification(
        tool_id=parse_value(data, "tool_id", "~"),
        reference_schema=schema,
        consistency_hint=parse_value(data, "consistency_hint", "~"),
    )


def write_semantics(data):
    return WriteSemantics(
        side_effect=data.get("side_effect", "true") == "true",
        atomicity=data.get("atomicity", "ATOMIC"),
        idempotency=None if data.get("idempotency") == "~" else idempotency({}),
        commit=None if data.get("commit") == "~" else CommitSemantics("EXPLICIT"),
        approval_required=data.get("approval_required", "true") == "true",
        preconditions_supported=data.get("preconditions_supported", "true") == "true",
        verification=verification(VERIFICATION) if data.get("verification") == "full" else None,
    )


VERIFICATION = {"tool_id": "TOOL.VERIFY", "reference_schema": "domain", "consistency_hint": " READ_AFTER_WRITE "}


def contract(data):
    inputs = (("z_label", STRING), ("a_amounts", list_type(money_type("KRW"))))
    schemas = {
        "ordered": inputs,
        "empty": (),
        "duplicate": inputs + (("z_label", domain("Duplicate")),),
        "blank": (("\u00a0\x1f", STRING),),
        "duplicate_and_blank": (("same", STRING), ("same", STRING), ("", STRING)),
        "padded": (("name", STRING), (" name ", STRING)),
    }
    options = {}
    for key in ("risk", "operation_kind", "risk_level"):
        if key in data:
            options[key] = parse_value(data, key, "~")
    if "timeout_ms" in data:
        options["timeout_ms"] = int(data["timeout_ms"])
    if "empty_is_valid" in data:
        options["empty_is_valid"] = data["empty_is_valid"] == "true"
    if data.get("write") in ("complete", "full"):
        options["write_semantics"] = write_semantics({"verification": data["write"]})
    return ToolContract(
        tool_id=data.get("tool_id", "TOOL.FETCH"),
        version=data.get("version", "1.0.0"),
        capability=data.get("capability", "READ"),
        input_types=schemas[data.get("inputs", "ordered")],
        output_type=domain("Draft"),
        required_scope=data.get("required_scope", "tool:read"),
        output_classification=DataClassification(data.get("classification", "INTERNAL")),
        **options,
    )


def observe(data):
    if isinstance(data, ToolContract):
        variant = "known" if isinstance(data.capability, ToolCapability) else "unregistered"
        record("contract", field(data.tool_id), field(data.version), field(data.capability), variant,
               field(data.required_scope), data.output_classification, data.timeout_ms,
               field(data.risk), word(data.empty_is_valid), data.operation_kind or "~", data.risk_level or "~")
        for name, item in data.input_types:
            record("input", field(name), typ(item))
        record("output", typ(data.output_type))
        if data.write_semantics is not None:
            observe(data.write_semantics)
    elif isinstance(data, WriteSemantics):
        record("write", word(data.side_effect), data.atomicity, word(data.approval_required),
               word(data.preconditions_supported))
        observe(data.idempotency)
        observe(data.commit)
        if data.verification is not None:
            observe(data.verification)
    elif isinstance(data, CommitSemantics):
        record("commit", data.confirmation)
    elif isinstance(data, IdempotencySemantics):
        record("idempotency", word(data.required), *(field(getattr(data, name)) for name in
                ("scope", "retention", "duplicate_behavior", "payload_conflict_behavior")))
    else:
        assert isinstance(data, PostWriteVerification)
        record("verification", field(data.tool_id), typ(data.reference_schema), field(data.consistency_hint))


def emit(kind, label, **data):
    record("case", kind, label)
    for key, value in data.items():
        record("set", key, field(value))
    record("expected")
    build = {
        "contract": contract, "idempotency": idempotency, "verification": verification,
        "write": write_semantics, "commit": lambda values: CommitSemantics(values["confirmation"]),
    }
    if kind in build:
        try:
            result = build[kind](data)
        except ValueError as error:
            record("error", str(error))
        else:
            observe(result)
        return

    assert kind == "catalog"
    first = contract({"tool_id": "" if data.get("layout") == "empty_id" else "TOOL.FETCH"})
    second = replace(first, version="2.0.1")
    other = contract({"tool_id": "TOOL.OTHER", "capability": "Legacy.Read"})
    variants = {"empty": (), "basic": (first, second, other), "duplicate": (first, second, first),
                "empty_id": (first,)}
    try:
        catalog = ToolContractCatalog(variants[data.get("layout", "basic")])
        result = getattr(catalog, data.get("method", "resolve"))(
            data.get("tool_id", "TOOL.FETCH"), data.get("version", "1.0.0")
        )
    except (KeyError, ValueError) as error:
        record("catalog_error", type(error).__name__)
    else:
        record("lookup", field(result.tool_id), field(result.version), field(result.capability))


def main():
    record("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    emit("contract", "read_default")
    emit("contract", "nondefault_exact_fields", tool_id=" TOÖL.조회 ", required_scope=" scope:읽기 ",
         inputs="padded", classification="RESTRICTED", timeout_ms="987", risk=" CUSTOM ", empty_is_valid="false")
    emit("contract", "no_inputs", inputs="empty", classification="PUBLIC")
    emit("contract", "blank_tool_id", tool_id="")
    emit("contract", "blank_scope", required_scope="")
    emit("contract", "padded_tool_id_and_scope", tool_id="\u00a0", required_scope=" ")
    for index, version in enumerate(("0.0.0", "1." + "9" * 100 + ".0", "", "1.0", "0.0.1.0",
                                     "1..0", "00.0.0", "1.02.0", "1.0.03", "１.0.0", "-1.0.0", "1.0.0\n")):
        emit("contract", f"version_{index}", version=version)
    for index, capability in enumerate(("TRANSACTION", "CUSTOM_READ", "write", " WRITE ", "관찰",
                                        "", "\t\x1f", "\u00a0", "WRIte")):
        emit("contract", f"capability_{index}", capability=capability)
    for index, timeout in enumerate((-1, 0, 1, 2, 2147483646, 2147483647, 2147483648, 9223372036854775807)):
        emit("contract", f"timeout_{index}", timeout_ms=str(timeout))
    for inputs in ("duplicate", "blank", "duplicate_and_blank"):
        emit("contract", f"input_{inputs}", inputs=inputs)
    emit("contract", "risk_blank", risk="\u00a0\x1f")
    emit("contract", "blank_before_timeout", risk="", timeout_ms="0")
    emit("contract", "invalid_version_first", version="", capability="")
    for risk in ("R0", "R1", "R2", "R3", "R4", "R5", "R6", "r3", ""):
        emit("contract", f"read_risk_{risk}", risk_level=risk)

    def write(label, **values):
        spec = {"capability": "WRITE", "operation_kind": "CREATE_DRAFT", "risk_level": "R3", "write": "complete"}
        emit("contract", label, **(spec | values))

    for operation in ("CREATE", "CREATE_DRAFT", "UPDATE", "DELETE", "UPSERT", "create", "", "~"):
        write(f"operation_{operation}", operation_kind=operation)
    write("write_no_risk", risk_level="~")
    write("write_invalid_risk", risk_level="R6")
    write("write_no_semantics", write="~")
    write("write_endpoints_of_risk", risk_level="R5", write="full")
    write("case_sensitive_write", capability="write")
    write("write_space", capability=" WRITE ")
    write("transaction_with_write", capability="TRANSACTION")
    write("read_with_operation", capability="READ", write="~")
    write("read_with_semantics", capability="READ", operation_kind="~")
    write("invalid_op_before_non_write_fields", capability="TRANSACTION", operation_kind="DELETE")
    write("risk_checked_before_operation", risk_level="R6", operation_kind="DELETE")

    emit("idempotency", "all_fields")
    emit("idempotency", "preserve_raw_fields", scope=" 범위 ", retention=" 24h ",
         duplicate_behavior="RETURN", payload_conflict_behavior=" DENY ")
    emit("idempotency", "not_required", required="false", scope="")
    for key in ("scope", "retention", "duplicate_behavior", "payload_conflict_behavior"):
        emit("idempotency", "blank_" + key, **{key: " \u2003\x1f"})
    for confirmation in ("EXPLICIT", "IMPLICIT", "explicit", ""):
        emit("commit", f"confirmation_{confirmation}", confirmation=confirmation)
    emit("verification", "no_hints")
    emit("verification", "three_hints", **VERIFICATION)
    emit("verification", "tool_only", tool_id=" VERIFY ")
    emit("verification", "reference_only", reference_schema="domain")
    emit("verification", "internal_optional_ref", reference_schema="optional")
    emit("verification", "hint_only", consistency_hint="EVENTUAL")
    emit("verification", "tool_blank", tool_id="\x1f", consistency_hint="keep")
    emit("verification", "hint_blank", tool_id="tool", consistency_hint=" ")
    emit("write", "full", verification="full")
    for key, invalid in (("side_effect", "false"), ("atomicity", "BEST_EFFORT"), ("atomicity", "atomic"),
                         ("idempotency", "~"), ("commit", "~"), ("approval_required", "false"),
                         ("preconditions_supported", "false")):
        emit("write", f"{key}_{invalid}", **{key: invalid})
    emit("write", "side_effect_first", side_effect="false", atomicity="NO")

    emit("catalog", "exact")
    emit("catalog", "later_exact", version="2.0.1")
    emit("catalog", "no_compatible_fallback", version="1.0.1")
    emit("catalog", "unknown_case", tool_id="tool.fetch")
    emit("catalog", "missing_exact_version", version="2.0.0", method="get")
    emit("catalog", "missing_exact_id", tool_id="MISSING", method="get")
    emit("catalog", "get_exact", method="get")
    emit("catalog", "unregistered_capability", tool_id="TOOL.OTHER")
    emit("catalog", "empty", layout="empty")
    emit("catalog", "duplicate", layout="duplicate")
    emit("catalog", "blank_identity", layout="empty_id", tool_id="")


if __name__ == "__main__":
    main()
