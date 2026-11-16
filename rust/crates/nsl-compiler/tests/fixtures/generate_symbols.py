"""Observe SymbolTable calls made by the frozen Compiler and approval analyzer."""

from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.compiler import ApprovalBindingAnalyzer, NslCompiler
from nsl.core import DataClassification, INT
from nsl.diagnostics import CompileError
from nsl.includes import MemoryIncludeResolver
from nsl.source import SourceFile
from nsl.symbols import SymbolNamespace, SymbolTable
from nsl.syntax import Lexer, ParseMode, Parser
from nsl.tools import CommitSemantics, IdempotencySemantics, ToolContract, ToolContractCatalog, WriteSemantics


ROOT = "symbols/skill.ns"
FRAGMENT = "symbols/fields.ns"
LIMITS = "limits { tool_calls 100; loop_iterations 100; emitted_rows 100; collection_size 100; }"
DECLARATIONS = 'context { ctx: Int from "prior" classification PUBLIC; } input { root: Int; items: List<Int>; }'
READ = ToolContract("TOOL.LOOKUP", "1.0.0", "READ", (("amount", INT),), INT,
                    "test:read", DataClassification.INTERNAL)
WRITE = ToolContract(
    "TOOL.CREATE", "1.0.0", "WRITE", (("amount", INT),), INT, "test:write",
    DataClassification.INTERNAL, operation_kind="CREATE", risk_level="R1",
    write_semantics=WriteSemantics(
        True, "ATOMIC", IdempotencySemantics(
            True, "TOOL_AND_TENANT", "PROVIDER_DEFINED", "RETURN_ORIGINAL_RESULT", "IDEMPOTENCY_CONFLICT",
        ), CommitSemantics("EXPLICIT"), True, True,
    ),
)
CATALOG = ToolContractCatalog((READ, WRITE))


def encoded(value):
    if value is None:
        return "~"
    return value.encode("utf-8").hex() or "-"


def location(position):
    return f"{position.offset},{position.line},{position.column}"


def span(value):
    return value.source_id, location(value.start), location(value.end)


def emit(label, version, body, *, declarations=DECLARATIONS, fragment=None, controlled=False, mode="full"):
    metadata = ('risk WRITE; requires { tool TOOL.CREATE version "1.0.0"; }' if controlled else
                'risk READ_ONLY; requires { tool TOOL.LOOKUP version "1.0.0"; }')
    include = 'include "fields.ns";' if fragment is not None else ""
    text = (f'// 한글 😀\r\nlanguage NSL "{version}";\r\n'
            f'skill TEST {{ version "1"; {metadata}\n{LIMITS}\n{include}\n{declarations}\n{body}\n}}')
    root = SourceFile.from_text(ROOT, text)
    fragments = () if fragment is None else (SourceFile.from_text(FRAGMENT, fragment),)
    print("case", mode, version, label, encoded(text), encoded(fragment), sep="\t")
    approvals = []
    scopes = [("scope", "scope0001", "-", "SKILL")]
    bindings = []
    references = []
    declare = SymbolTable.declare
    enter = SymbolTable.enter_scope
    resolve = SymbolTable.resolve

    def record_declaration(table, *args, **kwargs):
        binding = declare(table, *args, **kwargs)
        if binding.namespace is SymbolNamespace.APPROVAL:
            approvals.append(("approval", binding.symbol_id, binding.name, *span(binding.declaration_span)))
        else:
            bindings.append(("binding", binding.symbol_id, binding.name, binding.category,
                             binding.scope_id, *span(binding.declaration_span)))
        return binding

    def record_scope(table, kind):
        parent = table.current_scope_id
        child = enter(table, kind)
        scopes.append(("scope", child, parent, kind))
        return child

    def record_reference(table, name, source_span):
        binding = resolve(table, name, source_span)
        references.append(("reference", binding.symbol_id, binding.name, *span(source_span)))
        return binding

    with (patch.object(SymbolTable, "declare", record_declaration),
          patch.object(SymbolTable, "enter_scope", record_scope),
          patch.object(SymbolTable, "resolve", record_reference)):
        try:
            if mode == "approval":
                ast = Parser(Lexer().tokenize_root(root), root).parse(ParseMode.ROOT_SKILL)
                ApprovalBindingAnalyzer((root,)).analyze(ast)
            else:
                NslCompiler(CATALOG, MemoryIncludeResolver(fragments)).compile(root)
        except CompileError as error:
            assert error.code in ("NSL-E2001", "NSL-E2002"), (label, error)
            coords = f"{error.location.line},{error.location.column}"
            print("error", error.code, coords, encoded(error.logical_path), encoded(error.snippet),
                  encoded(error.public_message), sep="\t")
        else:
            rows = approvals if mode == "approval" else approvals + scopes + bindings + references
            for item in rows:
                print(*item, sep="\t")


def main():
    print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    for version in ("0.1", "0.2"):
        emit("inputs_before_contexts_and_body", version, """
let total = ctx + root;
foreach row in items max 2 {
    let temporary = row + total;
    foreach inner in items max 1 { let subtotal = temporary + inner; }
}
let temporary = root;
""")
        emit("sibling_reuse", version, """
foreach row in items max 2 {
    let copy = row;
    check FLAG { assert copy >= root; severity WARN; on_fail CONTINUE; message "note"; }
    let status = FLAG.status;
}
foreach row in items max 1 {
    let copy = row;
    check FLAG { assert copy >= root; severity WARN; on_fail CONTINUE; message "note"; }
    let status = FLAG.status;
}
""")
        emit("order_expression_paths", version, """
let base = sum(items) + (root * ctx);
let looked_up = read TOOL.LOOKUP(amount: base);
emit { first: looked_up; second: ctx; }
""", declarations=DECLARATIONS + " output { first: Int; second: Int; }")
        emit("output_is_not_a_symbol", version, "let bad = out;",
             declarations=DECLARATIONS + " output { out: Int; }")
        emit("output_can_share_name", version, "emit { root: root; }",
             declarations=DECLARATIONS + " output { root: Int; }")
        emit("let_before_input_section", version, "", declarations="let a = root; " + DECLARATIONS)
        emit("duplicate_input", version, "", declarations="input { x: Int; x: Int; }")
        emit("duplicate_context", version, "", declarations='context { x: Int from "a"; x: Int from "b"; }')
        emit("input_context_collision", version, "", declarations='context { x: Int from "a"; } input { x: Int; }')
        emit("same_scope", version, "let a = root; let a = ctx;")
        emit("outer_scope_shadow", version, "foreach row in items max 1 { let root = row; }")
        emit("iterator_shadow", version, "foreach row in items max 1 { foreach row in items max 1 {} }")
        emit("iterator_shadows_input", version, "foreach root in items max 1 {}")
        emit("check_shadows_value", version,
             'check root { assert true; severity WARN; on_fail CONTINUE; message "x"; }')
        emit("value_shadows_check", version,
             'check FLAG { assert true; severity WARN; on_fail CONTINUE; message "x"; } let FLAG = root;')
        emit("forward", version, "let a = b; let b = root;")
        emit("self_reference", version, "let a = a;")
        emit("condition_before_check", version,
             'check FLAG { assert FLAG.status == FLAG.status; severity WARN; on_fail CONTINUE; message "x"; }')
        emit("collection_before_iterator", version, "foreach list in list max 1 {}")
        emit("escaped_iterator", version, "foreach row in items max 1 {} let escaped = row;")
        emit("escaped_local", version, "foreach row in items max 1 { let x = row; } let escaped = x;")
        emit("escaped_check", version,
             'foreach row in items max 1 { check FLAG { assert true; severity WARN; on_fail CONTINUE; message "x"; } } '
             'let escaped = FLAG.status;')
        emit("collection_error_in_nested_scope", version,
             "foreach row in items max 1 { foreach other in missing max 1 {} }")
        emit("unknown_initializer_precedes_duplicate", version, "let root = unknown;")
        emit("left_operand_precedes_right", version, "let result = left + right;")
        emit("nested_call_argument", version, "let result = sum(missing.field);")
        emit("unicode_snippet", version, "// a\u2028b\nlet a = missing;")
        emit("bare_cr_snippet", version, "// a\rb\nlet a = missing;")
        emit("include_context", version, "let from_fragment = extra;",
             fragment='// 조각😀\r\ncontext { extra: Int from "prior" classification PUBLIC; }')
        emit("include_collision", version, "",
             fragment='// 조각😀\r\ncontext { root: Int from "prior"; }')

    def approval(name, message):
        return f'approve {name} {{ message "{message}"; }}'

    def write(name, approval_id):
        return f"let {name} = write TOOL.CREATE(amount = root) approved_by {approval_id};"

    emit("independent_value_and_approval_ids", "0.2", approval("root", "go") + "\n" + write("result", "root"),
         controlled=True)
    emit("approval_does_not_resolve_as_value", "0.2",
         approval("APPROVAL", "go") + "\n" + write("result", "APPROVAL") + "\nlet copy = APPROVAL;",
         controlled=True)
    emit("approval_duplicate", "0.2", approval("A", "one") + "\n" + approval("A", "two"), controlled=True)
    emit("approval_duplicate_precedes_value_error", "0.2",
         "let bad = unknown;\n" + approval("A", "one") + "\n" + approval("A", "two"), controlled=True)
    emit("approval_id_sequence", "0.2",
         "\n".join((approval("A", "one"), write("first", "A"), approval("B", "two"), write("second", "B"))),
         mode="approval", controlled=True)


if __name__ == "__main__":
    main()
