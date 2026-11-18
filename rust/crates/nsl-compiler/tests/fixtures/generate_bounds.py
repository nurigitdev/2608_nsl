"""Observe exact resource counts and compiler diagnostics from Oracle 28a949e.

The fixture's postfix format retains only IR structure relevant to resource
counts. Counts wider than Python's formatting limit are observed in small
chunks, without changing the Oracle's integer conversion setting.
"""

from dataclasses import replace
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.bounds import StaticBoundAnalyzer, UnboundedStructureError
from nsl.compiler import NslCompiler
from nsl.core import INT, DataClassification, list_type
from nsl.diagnostics import CompileError
from nsl.includes import MemoryIncludeResolver
from nsl.ir import (
    ApprovalPolicy, ApprovalStatement, BinaryExpr, CallExpr, CheckStatement,
    EmitStatement, FieldExpr, ForeachStatement, LetStatement, LiteralExpr,
    ProjectionExpr, ReadExpr, ResultPolicy, SymbolRefExpr, WriteExpression,
)
from nsl.source import SourceFile
from nsl.tools import (
    CommitSemantics, IdempotencySemantics, ToolContract, ToolContractCatalog, WriteSemantics,
)


def encoded(value):
    return value.encode().hex() or "-"


def integer_text(value):
    if not value:
        return "0"
    chunks = []
    while value:
        value, chunk = divmod(value, 10 ** 1000)
        chunks.append(chunk)
    return str(chunks[-1]) + "".join(f"{chunk:01000d}" for chunk in reversed(chunks[:-1]))


def analysis_row(value):
    return "\t".join(("analysis", integer_text(value.max_tool_calls),
                      integer_text(value.max_loop_iterations), integer_text(value.max_emit_records),
                      str(value.bounded).lower()))


def program(statements):
    result = []

    def expression(value):
        if isinstance(value, LiteralExpr):
            result.append("literal")
        elif isinstance(value, SymbolRefExpr):
            result.append("symbol")
        elif isinstance(value, (ReadExpr, WriteExpression, CallExpr)):
            args = value.arguments
            if not isinstance(value, CallExpr):
                args = tuple(item for _, item in args)
            for item in args:
                expression(item)
            name = "call" if isinstance(value, CallExpr) else "read" if isinstance(value, ReadExpr) else "write"
            result.append(f"{name} {len(args)}")
        elif isinstance(value, (FieldExpr, ProjectionExpr)):
            expression(value.source)
            result.append("field" if isinstance(value, FieldExpr) else "projection")
        elif isinstance(value, BinaryExpr):
            expression(value.left)
            expression(value.right)
            result.append("binary")
        else:
            raise TypeError(value)

    def block(body):
        for statement in body:
            if isinstance(statement, LetStatement):
                expression(statement.value)
                result.append(f"let {statement.node_id}")
            elif isinstance(statement, ForeachStatement):
                expression(statement.collection)
                block(statement.body)
                result.append(f"foreach {statement.node_id} {statement.max_iterations} {len(statement.body)}")
            elif isinstance(statement, CheckStatement):
                expression(statement.condition)
                result.append(f"check {statement.node_id}")
            elif isinstance(statement, EmitStatement):
                for _, value in statement.fields:
                    expression(value)
                result.append(f"emit {statement.node_id} {len(statement.fields)}")
            elif isinstance(statement, ApprovalStatement):
                result.append(f"approval {statement.node_id}")
            else:
                raise TypeError(statement)
    block(statements)
    return "\n".join(result)


LITERAL = LiteralExpr("expr0001", 1, INT)
SYMBOL = SymbolRefExpr("expr0002", "s0001", list_type(INT))


def read(*args):
    return ReadExpr("read0001", "tool0001", tuple((f"a{i}", value) for i, value in enumerate(args)), INT, ResultPolicy())


def write(*args):
    return WriteExpression("write0001", "tool0001", tuple((f"a{i}", value) for i, value in enumerate(args)), "A", INT)


def emit(*values):
    return EmitStatement("emit0001", tuple((f"a{i}", value) for i, value in enumerate(values)))


def loop(maximum, body, collection=SYMBOL, node_id="stmt0001"):
    return ForeachStatement(node_id, "s0001", collection, maximum, tuple(body))


def model(label, body):
    print("case", label, encoded(program(body)), sep="\t")
    try:
        analysis = StaticBoundAnalyzer().analyze(tuple(body))
    except UnboundedStructureError as error:
        print("unbounded", encoded(str(error)), sep="\t")
    else:
        print(analysis_row(analysis))


def models():
    model("empty", ())
    expression_cases = (LITERAL, SYMBOL, read(), read(LITERAL, read()), write(), write(read(), write()),
                        FieldExpr("field0001", read(), "number", INT),
                        ProjectionExpr("projection0001", write(read()), "number", list_type(INT)),
                        CallExpr("call0001", "sum", (), INT),
                        CallExpr("call0001", "sum", (read(), write()), INT),
                        BinaryExpr("binary0001", "ADD", read(), write(read()), INT))
    for index, value in enumerate(expression_cases):
        model(f"let_expression_{index}", (LetStatement("stmt0001", "s0001", value),))
        model(f"check_expression_{index}", (CheckStatement("check0001", "C", value, "WARN", "CONTINUE", "note", "s0001"),))
    for size in (0, 1, 2, 128):
        model(f"emit_{size}", (emit(*(read() for _ in range(size))),))
    model("approval_only", (ApprovalStatement("approval0001", "A", "go", ApprovalPolicy()),))
    model("approval_between_tools", (LetStatement("stmt0001", "s0001", read()),
                                    ApprovalStatement("approval0001", "A", "go", ApprovalPolicy()),
                                    LetStatement("stmt0002", "s0002", write(read()))))
    for maximum in (0, -1, 1, 2, 9, 10, 11, 2**64 - 1, 2**64, 2**64 + 1, 10**4298, 10**4299):
        for body_size in (0, 1, 2):
            label = str(maximum) if len(str(maximum)) < 40 else f"digits_{len(str(maximum))}"
            model(f"maximum_{label}_{body_size}",
                  (loop(maximum, [emit(read())] * body_size, read(read())),))
    for outer in (1, 2, 3, 2**64):
        for inner in (1, 2, 3, 2**64):
            model(f"nested_{outer}_{inner}", (loop(outer, (loop(inner, (emit(read()),), read()), emit()), read()),))
    for inner in (8, 9, 10**4299):
        model(f"wide_product_{len(str(inner))}_{inner if inner<100 else 'large'}",
              (loop(10**4299, (loop(inner, (emit(write(read())),), read()),), read()),))
    model("first_invalid_loop", (loop(0, (loop(-1, ()),)), loop(-1, ())))
    model("nested_invalid_loop", (loop(1, (loop(0, (), node_id="stmt0002"),)),))


PUBLIC = DataClassification.PUBLIC
SEMANTICS = WriteSemantics(True, "ATOMIC", IdempotencySemantics(
    True, "TOOL_AND_TENANT", "PROVIDER_DEFINED", "RETURN_ORIGINAL_RESULT", "IDEMPOTENCY_CONFLICT",
), CommitSemantics("EXPLICIT"), True, True)
CATALOG = ToolContractCatalog((
    ToolContract("TEST.VALUE", "1.0.0", "READ", (("amount", INT),), INT, "test:read", PUBLIC),
    ToolContract("TEST.LIST", "1.0.0", "READ", (("seed", INT),), list_type(INT), "test:read", PUBLIC),
    ToolContract("TEST.CREATE", "1.0.0", "WRITE", (("amount", INT),), INT, "test:write", PUBLIC,
                 operation_kind="CREATE", risk_level="R1", write_semantics=SEMANTICS),
))
ROOT = "bounds/skill.ns"
FRAGMENT = "bounds/limits.ns"


def source(label, version, body, *, limits=(100, 100, 100, 100, 60000), include=False, invalid_max=None, controlled=False):
    tools, loops, emits, collection, duration = limits
    limits_text = (f"limits {{ tool_calls {tools}; loop_iterations {loops}; emitted_rows {emits}; "
                   f"collection_size {collection}; duration {duration}ms; }}")
    declarations = (f'// 상한 😀\r\nlanguage NSL "{version}";\r\nskill TEST {{ version "1"; '
                    f'risk {"WRITE" if controlled else "READ_ONLY"};\n'
                    'requires { tool TEST.VALUE version "1.0.0"; tool TEST.LIST version "1.0.0"; '
                    + ('tool TEST.CREATE version "1.0.0"; ' if controlled else '') + '}\n')
    text = (declarations + ('include "limits.ns";' if include else limits_text)
            + '\ninput { i: Int classification PUBLIC; items: List<Int> classification PUBLIC; }\n'
            + 'output { value: Int classification PUBLIC; }\n' + body + '\n}')
    fragment = '// limits 조각 😀\r\n' + limits_text if include else None
    root = SourceFile.from_text(ROOT, text)
    fragments = () if fragment is None else (SourceFile.from_text(FRAGMENT, fragment),)
    captured = []
    analyze = StaticBoundAnalyzer.analyze

    def observe(analyzer, statements):
        if invalid_max is not None:
            statements = tuple(replace(item, max_iterations=invalid_max)
                               if isinstance(item, ForeachStatement) else item for item in statements)
        captured.append(program(statements))
        return analyze(analyzer, statements)

    with patch.object(StaticBoundAnalyzer, "analyze", observe):
        try:
            compiled = NslCompiler(CATALOG, MemoryIncludeResolver(fragments)).compile(root)
        except CompileError as error:
            outcome = ("error", error.code, f"{error.location.line},{error.location.column}",
                       encoded(error.logical_path), encoded(error.snippet), encoded(error.public_message))
        except ValueError as error:
            assert "4300 digits" in str(error), (label, error)
            outcome = ("abort", "ValueError", encoded(str(error)))
        else:
            value = compiled.skill.analysis
            outcome = ("ok", integer_text(value.max_tool_calls), integer_text(value.max_loop_iterations),
                       integer_text(value.max_emit_records), str(value.bounded).lower())
    assert len(captured) == 1, (label, captured)
    print("case", label, version, encoded(text), "~" if fragment is None else encoded(fragment), encoded(captured[0]), sep="\t")
    print(*outcome, sep="\t")


def sources():
    for version in ("0.1", "0.2"):
        source("empty", version, "", limits=(1, 1, 1, 1, 1))
        source("no_calls", version, "let n = 1; let copy = i;", limits=(1, 1, 1, 1, 1))
        source("check_calls", version, 'check C { assert read TEST.VALUE(amount: i) == i; severity WARN; on_fail CONTINUE; message "ok"; }')
        source("call_and_emit", version, 'let n = count(read TEST.LIST(seed: read TEST.VALUE(amount: i))); emit { value: n; }')
        source("nested", version, '''foreach a in read TEST.LIST(seed: i) max 2 {
foreach b in read TEST.LIST(seed: a) max 3 { emit { value: read TEST.VALUE(amount: b); } }
}''', limits=(9, 8, 6, 1, 1))
        source("sequential", version, 'foreach a in items max 2 { emit { value: a; } } foreach b in items max 3 { emit { value: b; } }', limits=(1, 5, 5, 1, 1))
        bodies = (
            ("tool", 'let n = read TEST.VALUE(amount: read TEST.VALUE(amount: i));', 0),
            ("loop", 'foreach a in items max 2 {}', 1),
            ("emit", 'emit { value: i; } emit { value: i; }', 2),
        )
        for name, body, index in bodies:
            for maximum in (1, 2, 3):
                limits = [100, 100, 100, 1, 1]
                limits[index] = maximum
                source(f"{name}_limit_{maximum}", version, body, limits=limits)
        priority_body = 'foreach a in items max 2 { emit { value: read TEST.VALUE(amount: a); } }'
        for limits in ((1, 1, 1, 1, 1), (2, 1, 1, 1, 1), (2, 2, 1, 1, 1)):
            source(f"error_priority_{limits[0]}_{limits[1]}_{limits[2]}", version, priority_body, limits=limits)
        source("included_limits", version, priority_body, limits=(2, 1, 1, 1, 1), include=True)
        source("collection_not_inferred", version, 'foreach a in items max 100 {}', limits=(1, 100, 1, 1, 1))
        for invalid in (0, -1):
            source(f"invalid_ir_max_{invalid}", version, 'foreach a in items max 1 {}', invalid_max=invalid)
        for maximum in (2**64 - 1, 2**64, 2**64 + 1, 10**4298, 10**4299):
            source(f"exact_large_{len(str(maximum))}_{maximum if maximum<2**65 else 'decimal'}", version,
                   f'foreach a in items max {maximum} {{}}', limits=(1, maximum, 1, 1, 1))
        for inner in (8, 9):
            maximum = 10**4299
            source(f"diagnostic_digits_inner_{inner}", version,
                   f'foreach a in items max {maximum} {{ foreach b in items max {inner} {{}} }}',
                   limits=(1, maximum, 1, 1, 1))
        maximum = 10**4299
        source("earlier_error_before_digit_abort", version,
               f'let n = read TEST.VALUE(amount: read TEST.VALUE(amount: i)); foreach a in items max {maximum} {{ foreach b in items max 9 {{}} }}',
               limits=(1, maximum, 1, 1, 1))
    source("controlled_write_counts", "0.2", 'approve A { message "go"; } let n = write TEST.CREATE(amount = read TEST.VALUE(amount: i)) approved_by A; emit { value: n; }', limits=(2, 1, 1, 1, 1), controlled=True)


assert sys.get_int_max_str_digits() == 4300
print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
models() if sys.argv[2] == "models" else sources()
