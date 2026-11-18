"""Observe type rules and typed compiler expressions from the frozen Oracle."""

from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.compiler import NslCompiler, _Lowerer
from nsl.core import (
    BOOL, INT, DECIMAL, STRING, DATE, DATETIME, YEAR, CHECK_STATUS, CHECK_RESULT,
    DataClassification, TypeRef, domain, list_type, money_type, record_type,
)
from nsl.diagnostics import CompileError, DiagnosticCode
from nsl.ir import FieldExpr, ProjectionExpr
from nsl.source import SourceFile
from nsl.tools import (
    CommitSemantics, IdempotencySemantics, ToolContract, ToolContractCatalog, WriteSemantics,
)
from nsl.type_system import StaticTypeChecker


def describe(ty):
    if ty.kind == "primitive":
        return ty.name
    if ty.kind == "domain":
        return "$" + ty.name
    if ty.kind == "money":
        return f"Money<{ty.currency}>"
    if ty.kind in ("list", "optional"):
        return f"{ty.kind.title()}<{describe(ty.item)}>"
    if ty.kind == "enum":
        return ty.name + "[" + ",".join(ty.enum_values) + "]"
    return ty.name + "{" + ",".join(f"{name}:{describe(item)}" for name, item in ty.fields) + "}"


ROW = record_type("Row", number=INT, cost=money_type("KRW"),
                  nested=record_type("Meta", flag=BOOL), values=list_type(INT))
TYPES = [INT, DECIMAL, BOOL, STRING, DATE, DATETIME, YEAR, money_type("KRW"), money_type("USD"),
         domain("TeamId"), domain("EmployeeId"), list_type(INT), list_type(BOOL), ROW,
         list_type(ROW), CHECK_STATUS, CHECK_RESULT, TypeRef("optional", item=INT),
         TypeRef("optional", item=ROW), list_type(TypeRef("optional", item=INT)),
         list_type(list_type(ROW)), list_type(money_type("KRW")), list_type(DECIMAL),
         list_type(STRING), list_type(CHECK_STATUS)]


def encoded(value):
    return value.encode("utf-8").hex() or "-"


def result(action):
    try:
        value = action()
    except CompileError as error:
        return error.code + "\t" + encoded(error.public_message)
    if value is None:
        return "ok\t-"
    if isinstance(value, tuple):
        ty, projected = value
        return "ok\t" + describe(ty) + ":" + str(int(projected))
    return "ok\t" + describe(value)


def rules():
    checker = StaticTypeChecker(())
    for index, ty in enumerate(TYPES):
        print("type", index, describe(ty), sep="\t")
    for operator in ("==", "!=", "<", "<=", ">", ">=", "+", "-", "*", "/"):
        for left, first in enumerate(TYPES):
            for right, second in enumerate(TYPES):
                print("binary", operator, left, right,
                      result(lambda: checker.binary_result(operator, first, second, None)), sep="\t")
    for index, ty in enumerate(TYPES):
        print("bool", index, result(lambda: checker.require_bool(ty, None)), sep="\t")
        print("list", index, result(lambda: checker.require_list(
            ty, DiagnosticCode.SEM_FOREACH_COLLECTION_TYPE, "foreach collection must be List<T>", None)), sep="\t")
        for field in ("number", "cost", "nested", "values", "flag", "status", "missing"):
            print("field", index, field, result(lambda: checker.field_result(ty, field, None)), sep="\t")
        for name in ("count", "min", "max", "sum", "unknown", "SUM"):
            for size in (0, 1, 2):
                print("call", name, index, size,
                      result(lambda: checker.builtin_result(name, (ty,) * size, None).result_type), sep="\t")


def tool(name, inputs, output, classification=DataClassification.INTERNAL, capability="READ"):
    options = {}
    if capability == "WRITE":
        options = dict(operation_kind="CREATE", risk_level="R1", write_semantics=WriteSemantics(
            True, "ATOMIC", IdempotencySemantics(
                True, "TOOL_AND_TENANT", "PROVIDER_DEFINED", "RETURN_ORIGINAL_RESULT", "IDEMPOTENCY_CONFLICT",
            ), CommitSemantics("EXPLICIT"), True, True,
        ))
    return ToolContract(name, "1.0.0", capability, inputs, output, "test:scope", classification, **options)


CATALOG = ToolContractCatalog((
    tool("TEST.RECORD", (("z", INT), ("a", DECIMAL)), ROW, DataClassification.CONFIDENTIAL),
    tool("TEST.LIST", (), list_type(ROW)),
    tool("TEST.SCALAR", (("amount", INT),), DECIMAL, DataClassification.PUBLIC),
    tool("TEST.CREATE", (("amount", INT),), ROW, capability="WRITE"),
    tool("TEST.TRANSACTION", (), INT, capability="TRANSACTION"),
    tool("TEST.CUSTOM", (), INT, capability="CUSTOM"),
    tool("TEST.UNSPELLABLE", tuple((name, INT) for name in (
        "can't", 'both"\'', "back\\slash", "a\t", "b\n", "c\r", "d\x00", "e\u00a0",
        "f\u0378", "g\U000e0001", "h\U0001fae9", "한글😀", "e\u0301",
    )), INT),
))
ROOT = "types/skill.ns"
LIMITS = "limits { tool_calls 100; loop_iterations 100; emitted_rows 100; collection_size 100; }"
DECLARATIONS = """input {
    i: Int classification PUBLIC; d: Decimal classification INTERNAL;
    b: Bool classification PUBLIC; s: String classification PUBLIC;
    krw: Money<KRW> classification CONFIDENTIAL; usd: Money<USD> classification RESTRICTED;
    ints: List<Int> classification INTERNAL; bools: List<Bool> classification PUBLIC;
    nested: List<List<Int>> classification PUBLIC;
} context { ctx: Int from "prior" classification RESTRICTED; }"""
REQUIRES = 'tool TEST.RECORD version "1.0.0"; tool TEST.LIST version "1.0.0"; tool TEST.SCALAR version "1.0.0";'


def emit(label, version, body, *, declarations=DECLARATIONS, requires=REQUIRES, controlled=False):
    risk = "WRITE" if controlled else "READ_ONLY"
    text = (f'// 타입 😀\r\nlanguage NSL "{version}";\r\nskill TEST {{ version "1"; risk {risk};\n'
            f'requires {{ {requires} }}\n{LIMITS}\n{declarations}\n{body}\n}}')
    source = SourceFile.from_text(ROOT, text)
    observations = []
    lower_expr = _Lowerer._lower_expr

    def observe(lowerer, ast):
        expression, classification = lower_expr(lowerer, ast)
        steps = []
        cursor = expression
        while isinstance(cursor, (FieldExpr, ProjectionExpr)):
            steps.append(describe(cursor.type_info) + ":" + str(int(isinstance(cursor, ProjectionExpr))))
            cursor = cursor.source
        observations.append((ast.span.start.offset, ast.span.end.offset, describe(expression.type_info),
                             classification.value, ";".join(reversed(steps)) or "-"))
        return expression, classification

    print("case", label, version, encoded(text), sep="\t")
    with patch.object(_Lowerer, "_lower_expr", observe):
        try:
            compiled = NslCompiler(CATALOG).compile(source)
        except CompileError as error:
            print("error", error.code, f"{error.location.line},{error.location.column}",
                  encoded(error.logical_path), encoded(error.snippet), encoded(error.public_message), sep="\t")
        else:
            for binding in compiled.skill.symbols:
                print("binding", binding.symbol_id, binding.name, describe(binding.type_info), binding.classification.value, sep="\t")
            for item in sorted(observations):
                print("expression", *item, sep="\t")


def skills():
    for version in ("0.1", "0.2"):
        emit("literals_and_arithmetic", version, '''
let n = 1; let d0 = 1.25; let b0 = true; let s0 = "text";
let quotient = i / i; let mixed_class = i + ctx; let decimal = d * d;
let money = krw + krw; let same = b == b; let different = s != s;
let aggregate = sum(ints); let length = count(bools); let smallest = min(bools);
foreach item in nested max 2 { foreach number in item max 2 { let local = number + i; } }
check FLAG { assert i >= i; severity WARN; on_fail CONTINUE; message "ok"; }
let status = FLAG.status;
''')
        emit("record_projection_and_tool_output", version, '''
let row = read TEST.RECORD(z: i, a: d);
let rows = read TEST.LIST();
let flag = row.nested.flag; let flags = rows.nested.flag;
let values = rows.values; let costs = rows.cost; let total = sum(costs);
foreach entry in rows max 2 { let cost = entry.cost; }
let result = read TEST.SCALAR(amount: ctx);
emit { result: result; }
''', declarations=DECLARATIONS + "output { result: Decimal classification PUBLIC; }")
        for label, body in (
            ("bool_check_int", 'check C { assert i; severity WARN; on_fail CONTINUE; message "x"; }'),
            ("bool_check_string", 'check C { assert "true"; severity WARN; on_fail CONTINUE; message "x"; }'),
            ("bool_order", "let bad = b < b;"), ("bool_arithmetic", "let bad = b + b;"),
            ("mixed_numeric", "let bad = i + d;"), ("mixed_currency", "let bad = krw == usd;"),
            ("money_multiply", "let bad = krw * krw;"), ("string_order", "let bad = s < s;"),
            ("not_list", "foreach item in i max 1 {}"),
            ("sum_bool", "let bad = sum(bools);"), ("sum_nested", "let bad = sum(nested);"),
            ("sum_scalar", "let bad = sum(i);"), ("count_empty", "let bad = count();"),
            ("min_two", "let bad = min(ints, ints);"), ("unknown_builtin", "let bad = other(ints);"),
            ("missing_field", "let rows = read TEST.LIST(); let bad = rows.unknown;"),
            ("scalar_field", "let bad = i.unknown;"),
            ("nested_list_field", "let rows = read TEST.LIST(); let bad = rows.values.unknown;"),
            ("argument_type", "let bad = read TEST.SCALAR(amount: d);"),
            ("argument_missing", "let bad = read TEST.SCALAR();"),
            ("argument_extra", "let bad = read TEST.SCALAR(amount: i, extra: i);"),
            ("argument_duplicate", "let bad = read TEST.SCALAR(amount: i, amount: i);"),
            ("argument_shape_before_field", "let bad = read TEST.SCALAR(extra: i.unknown);"),
            ("first_type_before_later_field", "let bad = read TEST.RECORD(z: d, a: i.unknown);"),
            ("argument_order", "let good = read TEST.RECORD(a: d, z: i);"),
            ("undeclared_read", "let bad = read TEST.OTHER();"),
            ("unicode_snippet", "// a\u2028b\nlet bad = i + b;"),
            ("bare_cr_snippet", "// a\rb\nlet bad = i + b;"),
        ):
            emit(label, version, body)
        for label, outputs, body in (
            ("empty_emit", "", "emit {}"),
            ("empty_emit_missing", "a: Int;", "emit {}"),
            ("extra_emit", "", "emit { a: i; }"),
            ("duplicate_emit", "a: Int;", "emit { a: i; a: i; }"),
            ("duplicate_output", "a: Int; b: Int; b: Int; a: Int;", ""),
            ("output_type", "a: Decimal;", "emit { a: i; }"),
            ("output_classification", "a: Int classification PUBLIC;", "emit { a: ctx; }"),
            ("output_allows_higher", "a: Int classification RESTRICTED;", "emit { a: ctx; }"),
            ("emit_shape_before_expression", "a: Int;", "emit { b: i.unknown; }"),
        ):
            emit(label, version, body, declarations=DECLARATIONS + "output {" + outputs + "}")
        for label, required in (
            ("unknown_contract", 'tool TEST.MISSING version "1.0.0";'),
            ("incompatible_version", 'tool TEST.SCALAR version "2.0.0";'),
            ("duplicate_required", 'tool TEST.SCALAR version "1.0.0"; tool TEST.SCALAR version "1.0.0";'),
            ("transaction_contract", 'tool TEST.TRANSACTION version "1.0.0";'),
            ("custom_capability", 'tool TEST.CUSTOM version "1.0.0";'),
        ):
            body = "let bad = read TEST.CUSTOM();" if label == "custom_capability" else ""
            emit(label, version, body, requires=required)
        emit("read_transaction_rejected", version, "let bad = read TEST.TRANSACTION();",
             requires='tool TEST.TRANSACTION version "1.0.0";')
        emit("unspellable_contract_argument_names", version, "let bad = read TEST.UNSPELLABLE();",
             requires='tool TEST.UNSPELLABLE version "1.0.0";')
    required = 'tool TEST.CREATE version "1.0.0";'
    for label, argument in (("write_output", "i"), ("write_argument_type", "d")):
        emit(label, "0.2", 'approve A { message "go"; }\n'
             f'let result = write TEST.CREATE(amount = {argument}) approved_by A;\n'
             'let number = result.number;', requires=required, controlled=True)
    emit("read_write_contract_rejected", "0.2", "let bad = read TEST.CREATE(amount: i);", requires=required)


def unicode_table():
    import unicodedata
    assert unicodedata.unidata_version == "15.0.0"
    boundaries = []
    previous = False
    for code in range(0x110001):
        current = code < 0x110000 and not chr(code).isprintable()
        if current != previous:
            boundaries.append(code)
            previous = current
    print("// Generated by tests/fixtures/generate_types.py (Python 3.12, Unicode 15.0).")
    print("// Half-open ranges of code points escaped by Python repr().")
    print("#[rustfmt::skip]\nconst NONPRINTABLE_BOUNDARIES: &[u32] = &[")
    for offset in range(0, len(boundaries), 10):
        print("    " + ", ".join(hex(code) for code in boundaries[offset:offset + 10]) + ",")
    print("];")


if sys.argv[2] == "unicode":
    unicode_table()
    sys.exit()
print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
if sys.argv[2] == "rules":
    rules()
else:
    skills()
