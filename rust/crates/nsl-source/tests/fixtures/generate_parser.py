"""Record semantic ASTs, spans and failures from the frozen Python 3.12 Oracle."""

from dataclasses import fields
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import sys

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.core import TypeRef
from nsl.diagnostics import CompileError
from nsl.source import SourceFile, SourceSpan
from nsl.syntax import AstNode, Lexer, ParseMode, Parser


def hex_text(text):
    return text.encode("utf-8").hex() or "-"


def field(text):
    data = text.encode("utf-8")
    return "sha256:" + sha256(data).hexdigest() if len(data) > 256 else hex_text(text)


def span_parts(span):
    return (span.source_id, *(f"{pos.offset},{pos.line},{pos.column}" for pos in (span.start, span.end)))


def observe(value, source_id):
    if isinstance(value, AstNode):
        print("node", type(value).__name__, sep="\t")
        for item in fields(value):
            observe(getattr(value, item.name), source_id)
    elif isinstance(value, SourceSpan):
        actual_id, start, end = span_parts(value)
        assert actual_id == source_id
        print("span", start, end, sep="\t")
    elif isinstance(value, TypeRef):
        depth = 0
        while value.kind == "list":
            depth += 1
            value = value.item
        print("type", depth, value.kind, value.currency or value.name, *value.enum_values, sep="\t")
    elif isinstance(value, tuple):
        print("seq", len(value), sep="\t")
        for item in value:
            observe(item, source_id)
    elif isinstance(value, str):
        print("str", field(value), sep="\t")
    elif isinstance(value, bool):
        print("bool", str(value).lower(), sep="\t")
    elif isinstance(value, int):
        print("int", field(format(Decimal(value), "f")), sep="\t")
    elif isinstance(value, Decimal):
        print("decimal", field(format(value, "f")), sep="\t")
    else:
        assert value is None, value
        print("none")


def emit(label, mode, version, prefix, unit="", count=0, tail="", invalid=None, cap=256):
    text = prefix + unit * count + tail
    source = SourceFile.from_text("parser/fixture.ns", text)
    print("case", label, mode.value, version, hex_text(prefix), hex_text(unit), count,
          hex_text(tail), cap, source.source_id, sep="\t")
    lexer = Lexer()
    try:
        if mode == ParseMode.ROOT_SKILL:
            tokens = lexer.tokenize_root(source)
        else:
            tokens = lexer.tokenize(source, version)
        ast = Parser(tokens, source).parse(mode)
    except CompileError as error:
        line, column = error.location.line, error.location.column
        offset = sum(len(segment) + 1 for segment in text.split("\n")[:line - 1]) + column - 1
        print("diagnostic", error.code, f"{offset},{line},{column}", sep="\t")
    except (ValueError, InvalidOperation) as error:
        if isinstance(error, InvalidOperation):
            kind = "InvalidDecimal"
        elif str(error).startswith("Exceeds the limit"):
            kind = "TooManyIntegerDigits"
        else:
            assert str(error).startswith("invalid literal for int()")
            kind = "NonDecimalDigit"
        token, = (token for token in tokens if token.lexeme == invalid)
        print("numeric", kind, *span_parts(token.span), sep="\t")
    except RecursionError:
        print("recursion")
    else:
        observe(ast, source.source_id)


def main():
    assert sys.get_int_max_str_digits() == 4300 and sys.getrecursionlimit() == 1000
    print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    limits = "limits { tool_calls 1; loop_iterations 2; emitted_rows 3; collection_size 4; }"
    root = ParseMode.ROOT_SKILL
    fragment = ParseMode.INCLUDE_FRAGMENT
    for version in ("0.1", "0.2"):
        header = f'// 주석 😀\r\nlanguage NSL "{version}";\r\n'
        start = header + 'skill APP.TEST { version "1.2.3"; risk READ_ONLY; '
        body = start + 'include "base.ns"; '

        def skill(label, content):
            emit(label, root, version, body + content + "}")

        skill("full", limits + """
            include "nested/a.ns";
            requires { tool APP.LOOKUP version "2"; tool other version "3"; }
            input { rows: List<List<Money<USD>>>; label: String; n: Int;
                    b: Bool; d: Date; dt: DateTime; z: Decimal; y: Year;
                    team: TeamId; employee: EmployeeId; account: AccountCode;
                    project: ProjectCode; period: AccountingPeriod; org: OrganizationId;
                    status: CheckStatus classification PUBLIC; }
            context { employee: EmployeeId from "user..id" classification CONFIDENTIAL; }
            output { amount: Decimal classification RESTRICTED; }
            let total = ((sum(rows.amount) + 1.2500) * 2 - 3 / 4) == 100;
            let ops = 1 < 2 <= 3 > 4 >= 5 != 6;
            let ties = 1 - 2 + 3 * 4 / 5 == 6 != 7;
            let no_args = read APP.LOOKUP();
            let extracted = read APP.LOOKUP(label: "예산😀", count: 2);
            let nested = concat(string(), stringify(read APP.LOOKUP(count: 1)));
            foreach row in rows max ０２ {
                check LOW { assert row.amount > 0; severity WARN; on_fail CONTINUE;
                            message "check\\nvalue"; }
                foreach item in row.items max 1 { emit { amount: item.amount; } }
                emit { amount: row.amount; }
            }
            foreach row in rows max 1 {}
            emit {}
        """)
        skill("literal_values", 'let a = ٠٠١٢; let b = ０.٠١٠; let c = true; '
              'let d = false; let e = "줄😀"; let f = 9223372036854775808; '
              'let g = a "+" 1; "emit" {}')
        skill("repeated_metadata", 'version "2"; risk ALTERNATE; requires { tool one version "1"; } '
              'requires {} input { first: Int; } input { last: String; } ' + limits
              + limits.replace("tool_calls 1", "tool_calls 9"))
        skill("include_supplies_limits", "")
        skill("no_header_inside_code", 'let header = "language";')
        skill("money_currency", "input { x: Money<usd>; }")
        skill("unknown_type", "output { x: Float; }")
        skill("unknown_classification", "input { x: String classification SECRET; }")
        skill("classification_kind", "input { x: String classification true; }")
        skill("context_from_required", 'context { c: Int "x"; }')
        skill("duplicate_limit", "limits { tool_calls 1; tool_calls 2; }")
        skill("missing_limit", "limits { tool_calls 1; }")
        skill("unknown_limit", limits.replace("tool_calls 1;", "tool_calls 1; unknown 2;"))
        skill("nonpositive_limit", limits.replace("tool_calls 1", "tool_calls ０"))
        skill("zero_duration", limits.replace("size 4;", "size 4; duration 0ms;"))
        skill("duration_token_kind", limits.replace("size 4;", "size 4; duration 5;"))
        skill("nonpositive_foreach", "foreach row in rows max 0 {}")
        skill("unterminated_foreach", "foreach row in rows max 1 {")
        skill("unexpected_stmt", "sql SELECT;")
        skill("expected_expr", "let value = ;")
        skill("keyword_in_expression", "let value = risk;")
        skill("missing_group_terminator", "let value = (a + b;")
        skill("call_trailing_comma", "let value = test(1,);")
        skill("call_missing_comma", "let value = test(1 2);")
        skill("required_tool_kind", 'requires { tool APP.42 version "1"; }')
        skill("lexer_error", "let x = @;")
        emit("bad_header", root, version, header.replace(f'"{version}"', '"0.3"'))
        emit("trailing_input", root, version, body + "} trailing")
        emit("missing_metadata", root, version, start + "}")
        emit("no_risk", root, version, header + 'skill A {version "1"; include "base.ns";}')
        emit("empty_version", root, version, body + 'version ""; }')
        emit("fragment_as_root", root, version, 'include "base.ns";')
        emit("root_as_fragment", fragment, version, body + "}")
        emit("fragment_empty", fragment, version, "// only a comment\r\n")
        emit("fragment_full", fragment, version, '// 조각\ninclude "other.ns";\n'
             'requires { tool A version "1"; } requires { tool A.B version "2"; } '
             'context { x: Int from "."; } context { y: Bool from ""; } ' + limits + limits)
        emit("fragment_statement", fragment, version, 'include "base.ns"; emit {}')
        emit("fragment_version_tokens", fragment, version, 'requires { tool write version "1"; }')
        emit("fragment_missing_semi", fragment, version, 'include "base.ns"')
        emit("superscript_int", root, version, body + "let x = ²;}", invalid="²")
        emit("superscript_decimal", root, version, body + "let x = 1.²;}", invalid="1.²")
        emit("superscript_foreach", root, version, body + "foreach row in rows max ² {}}", invalid="²")
        emit("superscript_limit", root, version, body + "limits { tool_calls ²; }}", invalid="²")
        emit("integer_4299", root, version, body + "let x = ", "9", 4299, ";}")
        emit("integer_4300", root, version, body + "let x = ", "９", 4300, ";}")
        emit("integer_4301", root, version, body + "let x = ", "0", 4301, ";}", invalid="0" * 4301)
        emit("integer_4302", root, version, body + "let x = ", "9", 4302, ";}", invalid="9" * 4302)
        emit("integer_nondecimal_precedence", root, version, body + "let x = ", "9", 4301,
             "²;}", invalid="9" * 4301 + "²")
        emit("decimal_4301", root, version, body + "let x = 1.", "9", 4301, ";}")
        emit("duration_result_4305", root, version, body + limits[:-1] + "duration ",
             "9", 4300, "m; }}")
        emit("group_256", root, version, body + "let x = ", "(", 256, "1" + ")" * 256 + ";}")
        emit("group_recursion", root, version, body + "let x = ", "(", 1024, "1" + ")" * 1024 + ";}")
        emit("foreach_recursion", root, version, body, "foreach row in rows max 1 {", 1024, "}" * 1025)
        emit("list_recursion", root, version, body + "input { x: ", "List<", 1024, "Int" + ">" * 1024 + "; }}")
        skill("write_approval", 'approve CHANGE { message "승인 😀"; } '
              'let result = (write TOOL.UPDATE(id = ids.primary, delta = 2) approved_by CHANGE);')
        skill("write_empty", 'approve CHANGE { message "go"; } let result = write UPDATE() approved_by CHANGE;')
        skill("write_missing_approval", "let result = write UPDATE(x = 2);")
        skill("write_missing_approval_id", "let result = write UPDATE() approved_by;")
        skill("write_argument_separator", "let result = write UPDATE(x: 2) approved_by CHANGE;")
        skill("approve_missing_id", 'approve { message "go"; }')
        skill("legacy_names", "input { write: Int; approve: Int; approved_by: Int; } "
              "let write = approve(approved_by);")
        skill("quoted_approve_is_not_keyword", '"approve" CHANGE { message "go"; }')


if __name__ == "__main__":
    main()
