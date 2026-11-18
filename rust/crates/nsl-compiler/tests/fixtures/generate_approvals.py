"""Observe approval binding analysis and diagnostics from frozen Oracle 28a949e."""

from dataclasses import replace
from itertools import product
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.compiler import ApprovalBindingAnalyzer, NslCompiler
from nsl.diagnostics import CompileError, DiagnosticPhase
from nsl.semantic_diagnostics import SourceDiagnosticContext
from nsl.source import SourceFile
from nsl.syntax import Lexer, Parser, ParseMode
from nsl.tools import ToolContractCatalog


def encoded(value):
    return "~" if value is None else value.encode().hex() or "-"


def location(point):
    return f"{point.offset},{point.line},{point.column}"


def span(value):
    return (value.source_id, location(value.start), location(value.end))


def approval(name):
    return f'approve {name} {{ message "승인 😀"; }}'


def write(name, value="root"):
    return f"write TEST.CREATE(amount = {value}) approved_by {name}"


def let(name, expression):
    return f"let {name} = {expression};"


def emit(label, body, *, version="0.2", mode="normal", context=True, path="approvals/skill.ns"):
    text = (f'// 승인 😀\r\nlanguage NSL "{version}";\r\nskill TEST {{ version "1"; '
            f'risk {"WRITE" if version == "0.2" else "READ_ONLY"};\n'
            'requires {}\nlimits { tool_calls 10000; loop_iterations 10000; emitted_rows 10000; collection_size 10000; }\n'
            'context { ctx: Int from "prior"; } input { root: Int; items: List<Int>; }\n'
            f'{body}\n}}')
    source = SourceFile.from_text(path, text)
    ast = Parser(Lexer().tokenize_root(source), source).parse(ParseMode.ROOT_SKILL)
    if mode == "force_v01":
        ast = replace(ast, language_version="0.1")
    error_span = []
    error_method = SourceDiagnosticContext.error

    def observe(diagnostics, code, message, source_span, phase=DiagnosticPhase.SEMANTIC):
        error_span.append(source_span)
        return error_method(diagnostics, code, message, source_span, phase)

    print("case", mode, version, label, encoded(path), encoded(text), "present" if context else "missing", sep="\t")
    with patch.object(SourceDiagnosticContext, "error", observe):
        try:
            result = ApprovalBindingAnalyzer((source,) if context else ()).analyze(ast)
        except CompileError as error:
            assert error.code in ("NSL-E2002", "NSL-E5301", "NSL-E5302", "NSL-E5303", "NSL-E5304", "NSL-E5305"), (label, error)
            position = "~" if error.location is None else f"{error.location.line},{error.location.column}"
            assert error.diagnostic.phase == DiagnosticPhase.SEMANTIC
            print("error", error.code, position, encoded(error.logical_path), encoded(error.snippet),
                  encoded(error.public_message), *span(error_span[-1]), sep="\t")
            # The real compiler runs this analysis before contracts, values and
            # types. Its rejected sources must report this same first error.
            if mode == "normal":
                try:
                    NslCompiler(ToolContractCatalog(())).compile(source)
                except CompileError as integrated:
                    assert integrated.code == error.code and integrated.public_message == error.public_message, (label, integrated, error)
                else:
                    raise AssertionError(label)
        else:
            for item in result.approvals:
                print("approval", item.symbol_id, item.approval_id, item.declaration_order, *span(item.declaration_span), sep="\t")
            for binding in result.bindings:
                print("binding", binding.approval.symbol_id, binding.write_order, int(binding.inside_foreach),
                      *span(binding.write.span), *span(binding.write.approval_span), sep="\t")


def main():
    print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    for version in ("0.1", "0.2"):
        emit("no_approval_nodes", 'let n = root + ctx; foreach row in items max 2 { check C { assert true; severity WARN; on_fail CONTINUE; message "ok"; } } emit { value: root; }', version=version)
    operations = (approval("A"), approval("B"), let("x", write("A")), let("x", write("B")))
    for count in range(5):
        for choices in product(range(4), repeat=count):
            emit("event_sequence_" + ("".join(map(str, choices)) or "empty"), "\n".join(operations[index] for index in choices))
    cases = {
        "namespace_independent": 'let A = root; ' + approval("A") + let("x", write("A")),
        "value_does_not_declare_approval": 'let A = root; ' + let("x", write("A")),
        "unknown_value_before_unknown_approval": 'let bad = missing; ' + let("x", write("UNKNOWN")),
        "unknown_value_before_unused": 'let bad = missing; ' + approval("UNUSED"),
        "condition_namespace": 'check A { assert true; severity WARN; on_fail CONTINUE; message "ok"; } ' + approval("A") + let("x", write("A")),
        "context_namespace": approval("ctx") + let("x", write("ctx")),
        "case_sensitive_names": approval("A") + let("x", write("a")),
        "iterator_namespace": approval("row") + 'foreach row in items max 1 { ' + let("x", write("row")) + ' }',
        "unknown_before_duplicate": let("x", write("MISSING")) + approval("A") + approval("A"),
        "unknown_before_scope": let("x", write("MISSING")) + 'foreach row in items max 1 { ' + approval("A") + ' }',
        "scope_before_duplicate": 'foreach row in items max 1 { ' + approval("A") + ' }' + approval("B") + approval("B"),
        "duplicate_before_scope": approval("A") + approval("A") + 'foreach row in items max 1 { ' + approval("B") + ' }',
        "scope_before_same_name_duplicate": approval("A") + 'foreach row in items max 1 { ' + approval("A") + ' }',
        "nested_approval": 'foreach a in items max 1 { foreach b in items max 1 { ' + approval("A") + ' } }',
        "sibling_approval": 'foreach a in items max 1 { ' + approval("A") + ' } foreach b in items max 1 { ' + approval("A") + ' }',
        "unused_follows_declaration_order": approval("Z") + approval("A"),
        "unknown_before_unused": approval("UNUSED") + let("x", write("MISSING")),
        "late_before_reused": approval("A") + let("x", write("A")) + let("y", write("B")) + let("z", write("A")) + approval("B"),
        "nested_write_parent_before_argument": approval("A") + approval("B") + let("x", write("A", write("B"))),
        "nested_write_reused": approval("A") + let("x", write("A", write("A"))),
        "nested_unknown_parent_first": let("x", write("PARENT", write("CHILD"))),
        "read_argument_writes": approval("A") + approval("B") + let("x", f'read TEST.READ(first: {write("A")}, second: {write("B")})'),
        "function_argument_writes": approval("A") + approval("B") + let("x", f'count({write("A")}, {write("B")})'),
        "binary_writes": approval("A") + approval("B") + let("x", write("A") + ' + ' + write("B")),
        "check_condition_write": approval("A") + f'check C {{ assert {write("A")} == root; severity WARN; on_fail CONTINUE; message "ok"; }}',
        "emit_writes": approval("A") + approval("B") + f'emit {{ first: {write("A")}; second: {write("B")}; }}',
        "foreach_collection_outside_body": approval("A") + f'foreach row in {write("A")} max 1 {{ let n = row; }}',
        "nested_foreach_collection_inside_parent": approval("A") + f'foreach a in items max 1 {{ foreach b in {write("A")} max 1 {{}} }}',
        "write_in_foreach_body": approval("A") + 'foreach row in items max 1 { ' + let("x", write("A")) + ' }',
        "after_foreach_is_skill_scope": 'foreach row in items max 1 {} ' + approval("A") + let("x", write("A")),
        "collection_precedes_body_write": approval("A") + approval("B") + f'foreach row in {write("A")} max 1 {{ {let("x", write("B"))} }}',
        "unicode_snippet": '// a\u2028b\n' + let("x", write("MISSING")),
        "bare_cr_snippet": '// a\rb\n' + let("x", write("MISSING")),
        "metadata_mentions_no_nodes": 'let text = "approve A approved_by A write TEST.CREATE";',
        "non_events_do_not_change_order": 'let n = 1; ' + approval("A") + 'let m = root; ' + let("x", write("A")),
    }
    for label, body in cases.items():
        emit(label, body)
    for count in (0, 1, 2, 128):
        body = "\n".join(approval(f"A{i}") + let(f"value{i}", write(f"A{i}")) for i in range(count))
        emit(f"sequential_pairs_{count}", body)
    emit("missing_context", let("x", write("MISSING")), context=False)
    emit("logical_path", approval("UNUSED"), path="tenant-a/승인.ns")
    emit("v01_model_bypasses_approval_nodes", approval("A") + approval("A") + 'foreach row in items max 1 { ' + approval("NESTED") + ' }' + let("x", write("UNKNOWN")), mode="force_v01")


main()
