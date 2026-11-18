"""Observe strict WRITE safety, injected-policy failures and contract admission."""

from dataclasses import fields
from itertools import product
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.compiler import ApprovalBindingAnalyzer, WriteSafetyAnalyzer
from nsl.core import INT, DataClassification, RiskLevel, ToolCapability
from nsl.diagnostics import CompileError, DiagnosticPhase
from nsl.semantic_diagnostics import SourceDiagnosticContext
from nsl.source import SourceFile
from nsl.syntax import Lexer, Parser, ParseMode
from nsl.tools import Atomicity, CommitSemantics, IdempotencySemantics, OperationKind, ToolContract, WriteSemantics


def encoded(value):
    return "~" if value is None else value.encode().hex() or "-"


def span(value):
    def position(point):
        return f"{point.offset},{point.line},{point.column}"
    return value.source_id, position(value.start), position(value.end)


def semantics(*, atomicity="ATOMIC", required=True, side_effect=True, approval=True, preconditions=True, commit=True):
    return WriteSemantics(side_effect, atomicity, IdempotencySemantics(
        required, "TOOL_AND_TENANT", "PROVIDER_DEFINED", "RETURN_ORIGINAL_RESULT", "IDEMPOTENCY_CONFLICT",
    ), CommitSemantics("EXPLICIT") if commit else None, approval, preconditions)


def contract(capability="WRITE", operation="CREATE_DRAFT", risk="R3", **kwargs):
    return ToolContract("TEST.WRITE", "1.0.0", capability, (("amount", INT),), INT, "test:write",
                        DataClassification.PUBLIC, operation_kind=operation if capability == "WRITE" else None,
                        risk_level=risk if capability == "WRITE" else None,
                        write_semantics=semantics(**kwargs) if capability == "WRITE" else None)


def approval(name="A"):
    return f'approve {name} {{ message "승인 😀"; }}\n'


def write(name="A", value="root", tool="TEST.WRITE"):
    return f"write {tool}(amount = {value}) approved_by {name}"


def text(body, version="0.2"):
    return (f'// WRITE 😀\r\nlanguage NSL "{version}";\r\nskill TEST {{ version "1"; '
            f'risk {"WRITE" if version == "0.2" else "READ_ONLY"};\n'
            'requires { tool TEST.WRITE version "1.0.0"; }\n'
            'limits { tool_calls 100; loop_iterations 100; emitted_rows 100; collection_size 100; }\n'
            'input { root: Int; items: List<Int>; }\n' + body + '\n}')


def ast(source):
    return Parser(Lexer().tokenize_root(source), source).parse(ParseMode.ROOT_SKILL)


def policies():
    source = SourceFile.from_text("write-safety/policy.ns", text(approval() + f"let x = {write()};"))
    approvals = ApprovalBindingAnalyzer((source,)).analyze(ast(source))
    cases = []
    for bits in product((False, True), repeat=5):
        cases.append(("priority_" + "".join(str(int(bit)) for bit in bits),
                      "READ" if bits[0] else "WRITE", "NONE" if bits[1] else "CREATE_DRAFT",
                      "NONE" if bits[2] else "ATOMIC", "false" if bits[3] else "true", "NONE" if bits[4] else "R3"))
    for operation in ("CREATE", "CREATE_DRAFT", "UPDATE", "DELETE", "UNKNOWN", "RAW_CREATE"):
        cases.append(("operation_" + operation, "WRITE", operation, "ATOMIC", "true", "R3"))
    for risk in ("R0", "R1", "R2", "R3", "R4", "R5", "R6", "RAW_R3"):
        cases.append(("risk_" + risk, "WRITE", "CREATE_DRAFT", "ATOMIC", "true", risk))
    for capability in ("TRANSACTION", "CUSTOM"):
        cases.append(("capability_" + capability, capability, "CREATE_DRAFT", "ATOMIC", "true", "R3"))
    for atomicity in ("BEST_EFFORT", "RAW_ATOMIC"):
        cases.append(("atomicity_" + atomicity, "WRITE", "CREATE_DRAFT", atomicity, "true", "R3"))
    for idem in ("NONE",):
        cases.append(("idempotency_" + idem, "WRITE", "CREATE_DRAFT", "ATOMIC", idem, "R3"))
    cases.append(("missing_semantics", "WRITE", "CREATE_DRAFT", "NONE", "NONE", "R3"))
    cases.append(("missing_operation", "WRITE", "NONE", "ATOMIC", "true", "R3"))
    for label, cap, operation, atomicity, idem, risk in cases:
        tool = contract()
        object.__setattr__(tool, "capability", cap)
        object.__setattr__(tool, "operation_kind", OperationKind(operation) if operation in OperationKind._value2member_map_ else "CREATE" if operation == "RAW_CREATE" else None if operation == "NONE" else operation)
        object.__setattr__(tool.write_semantics, "atomicity", Atomicity.ATOMIC if atomicity == "ATOMIC" else "ATOMIC" if atomicity == "RAW_ATOMIC" else None if atomicity == "NONE" else atomicity)
        if idem == "NONE":
            object.__setattr__(tool.write_semantics, "idempotency", None)
        else:
            object.__setattr__(tool.write_semantics.idempotency, "required", idem == "true")
        object.__setattr__(tool, "risk_level", RiskLevel(risk) if risk in RiskLevel._value2member_map_ else "R3" if risk == "RAW_R3" else None if risk == "NONE" else risk)
        if label == "missing_semantics":
            object.__setattr__(tool, "write_semantics", None)
        try:
            WriteSafetyAnalyzer((source,)).analyze(approvals, {"TEST.WRITE": tool})
        except CompileError as error:
            outcome = (error.code, encoded(error.public_message))
        else:
            outcome = ("ok", "-")
        print(label, cap, operation, atomicity, idem, risk, *outcome, sep="\t")


def emit(label, body, *, version="0.2", capability="WRITE", operation="CREATE_DRAFT", risk="R3", context=True, missing=False):
    source = SourceFile.from_text("write-safety/skill.ns", text(body, version))
    parsed = ast(source)
    captures = []
    error_method = SourceDiagnosticContext.error

    def observe(diagnostics, code, message, source_span, phase=DiagnosticPhase.SEMANTIC):
        captures.append(source_span)
        return error_method(diagnostics, code, message, source_span, phase)

    print("case", label, version, capability, operation, risk, "present" if context else "missing", "absent" if missing else "declared", encoded(source.text), sep="\t")
    sources = (source,) if context else ()
    with patch.object(SourceDiagnosticContext, "error", observe):
        stage = "approval"
        try:
            approved = ApprovalBindingAnalyzer(sources).analyze(parsed)
            stage = "safety"
            result = WriteSafetyAnalyzer(sources).analyze(approved, {} if missing else {"TEST.WRITE": contract(capability, operation, risk)})
        except CompileError as error:
            location = "~" if error.location is None else f"{error.location.line},{error.location.column}"
            print("error", stage, error.code, location, encoded(error.logical_path), encoded(error.snippet), encoded(error.public_message), *span(captures[-1]), sep="\t")
        else:
            print("ok", *(str(getattr(result, field.name)).lower() for field in fields(result)), sep="\t")


def sources():
    emit("v01_empty", "", version="0.1", capability="READ")
    emit("v02_empty", "")
    for operation, risk in product(("CREATE", "CREATE_DRAFT", "UPDATE"), ("R0", "R1", "R2", "R3", "R4", "R5")):
        emit(f"valid_{operation}_{risk}", approval() + f"let x = {write()};", operation=operation, risk=risk)
    for count in (0, 1, 2, 3):
        body = "".join(approval(f"A{i}") + f"let x{i} = {write(f'A{i}')};\n" for i in range(count))
        emit(f"write_count_{count}", body)
    for cap in ("READ", "TRANSACTION", "CUSTOM"):
        emit("capability_" + cap, approval() + f"let x = {write()};", capability=cap)
    emit("undeclared_tool", approval() + f"let x = {write()};", missing=True)
    emit("dynamic_value_is_not_a_tool_id", 'let selected = "TEST.WRITE"; ' + approval() + f'let x = {write(tool="selected")};')
    emit("write_in_body", approval() + f'foreach row in items max 1 {{ let x = {write()}; }}')
    emit("write_in_nested_collection", approval() + f'foreach row in items max 1 {{ foreach inner in {write()} max 1 {{}} }}')
    emit("write_in_root_collection", approval() + f'foreach row in {write()} max 1 {{}}')
    emit("write_after_loop", 'foreach row in items max 1 {} ' + approval() + f'let x = {write()};')
    emit("two_before_loop_error", approval("A") + approval("B") + f'foreach row in items max 1 {{ let a = {write("A")}; let b = {write("B")}; }}')
    emit("count_before_capability", approval("A") + approval("B") + f'let a = {write("A")}; let b = {write("B")};', capability="READ")
    emit("loop_before_capability", approval() + f'foreach row in items max 1 {{ let x = {write()}; }}', capability="READ")
    emit("loop_before_missing_tool", approval() + f'foreach row in items max 1 {{ let x = {write()}; }}', missing=True)
    emit("nested_write_preorder", approval("A") + approval("B") + f'let x = {write("A", write("B"))};')
    emit("binary_write_order", approval("A") + approval("B") + f'let x = {write("A")} + {write("B")};')
    emit("check_write", approval() + f'check C {{ assert {write()} == root; severity WARN; on_fail CONTINUE; message "ok"; }}')
    emit("builtin_write", approval() + f'let x = count({write()});')
    emit("read_argument_write", approval() + f'let x = read TEST.READ(value: {write()});')
    emit("emit_write", approval() + f'emit {{ value: {write()}; }}')
    emit("approval_before_safety", f'let x = {write("UNKNOWN")};', capability="READ")
    emit("unused_approval", approval())
    emit("reused_approval_before_count", approval() + f'let x = {write()}; let y = {write()};')
    emit("late_approval", f'let x = {write()};' + approval())
    emit("missing_context", approval() + f'foreach row in items max 1 {{ let x = {write()}; }}', context=False)
    emit("unicode_snippet", '// a\u2028b\n' + approval() + f'foreach row in items max 1 {{ let x = {write()}; }}')
    emit("bare_cr_snippet", '// a\rb\n' + approval() + f'foreach row in items max 1 {{ let x = {write()}; }}')


def admissions():
    cases = [("operation", value) for value in ("CREATE", "CREATE_DRAFT", "UPDATE", "DELETE", "UNKNOWN", "NONE")]
    cases += [("risk", value) for value in ("R0", "R5", "R6", "NONE")]
    cases += [("atomicity", value) for value in ("ATOMIC", "BEST_EFFORT")]
    cases += [(name, "false") for name in ("required", "side_effect", "approval", "preconditions", "commit")]
    for name, value in cases:
        try:
            if name == "operation": contract(operation=None if value == "NONE" else value)
            elif name == "risk": contract(risk=None if value == "NONE" else value)
            else: contract(**{name: value if name == "atomicity" else False})
        except ValueError as error:
            print(name, value, "ValueError", encoded(str(error)), sep="\t")
        else:
            print(name, value, "ok", "-", sep="\t")


print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
{"policies": policies, "sources": sources, "admissions": admissions}[sys.argv[2]]()
