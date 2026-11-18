"""Compile the source corpora through frozen Oracle 28a949e end to end.

Observers only record the real diagnostic factory's token/node span and always
call the original implementation. Model fault-injection fixtures are not
reproduced as source behavior; their source text is compiled without mutation.
"""
from contextlib import ExitStack, redirect_stdout
from hashlib import sha256
from io import StringIO
from pathlib import Path
import inspect
import json
import runpy
import sys
from unittest.mock import patch

ORACLE = "28a949ec96581cbdc52c8133527b23bb5b72ee0a"
FIXTURES = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.cli_config import load_tool_catalog_document
from nsl.compiler import NslCompiler
from nsl.core import INT, DataClassification, list_type
from nsl.diagnostics import CompileError, DiagnosticPhase
from nsl.includes import IncludeOptions, MemoryIncludeResolver
from nsl.semantic_diagnostics import SourceDiagnosticContext
from nsl.source import SourceFile
from nsl.syntax import Parser
from nsl.tools import (CommitSemantics, IdempotencySemantics, ToolContract,
                       ToolContractCatalog, WriteSemantics)
import nsl.includes as includes

saved_args = sys.argv[:]
sys.argv = [str(FIXTURES / "generate_types.py"), saved_args[1], "skills"]
with redirect_stdout(StringIO()):
    baseline = runpy.run_path(str(FIXTURES / "generate_types.py"))
sys.argv = saved_args


def tool(name, capability="READ", operation="CREATE", risk="R1", *, scope="test:read",
         classification=DataClassification.PUBLIC, output=INT, argument="amount"):
    options = {}
    if capability == "WRITE":
        options = dict(operation_kind=operation, risk_level=risk, write_semantics=WriteSemantics(
            True, "ATOMIC", IdempotencySemantics(True, "TOOL_AND_TENANT", "PROVIDER_DEFINED",
                "RETURN_ORIGINAL_RESULT", "IDEMPOTENCY_CONFLICT"), CommitSemantics("EXPLICIT"), True, True))
    return ToolContract(name, "1.0.0", capability, ((argument, INT),), output, scope,
                        classification, **options)


def catalog(profile):
    if profile.startswith("legacy-"):
        version = "1-0" if profile == "legacy-read" else "1-1"
        directory = FIXTURES.parents[4] / "compatibility/v0_2_1/nso" / f"ir-{version}-canonical"
        return load_tool_catalog_document(json.loads(next(directory.glob("tools*.json")).read_text()))
    if profile == "types": return baseline["CATALOG"]
    if profile == "empty": return ToolContractCatalog(())
    if profile == "symbols":
        return ToolContractCatalog((tool("TOOL.LOOKUP", classification=DataClassification.INTERNAL),
            tool("TOOL.CREATE", "WRITE", scope="test:write", classification=DataClassification.INTERNAL)))
    if profile == "bounds":
        return ToolContractCatalog((tool("TEST.VALUE"), tool("TEST.LIST", output=list_type(INT), argument="seed"),
                                   tool("TEST.CREATE", "WRITE", scope="test:write")))
    _, capability, operation, risk = profile.split(":")
    return ToolContractCatalog((tool("TEST.WRITE", capability, operation, risk, scope="test:write"),))


def encoded(value): return "~" if value is None else value.encode().hex() or "-"
def decoded(value): return "" if value == "-" else bytes.fromhex(value).decode()
def position(value): return f"{value.offset},{value.line},{value.column}"
def sources_field(sources):
    return ";".join(f"{encoded(path)}:{encoded(text)}" for path, text in sources) or "~"


def emit(label, path, text, sources=(), *, resolve=True, profile="types", depth=16, files=100, size=10485760):
    root = SourceFile.from_text(path, text)
    source_files = (root,) + tuple(SourceFile.from_text(path, text) for path, text in sources)
    resolver = MemoryIncludeResolver(source_files[1:]) if resolve else None
    captured = []
    factory = SourceDiagnosticContext.error
    parser_error = Parser._error
    include_factory = includes.compile_error

    def observe(context, code, message, span, phase=DiagnosticPhase.SEMANTIC):
        captured.append(span)
        return factory(context, code, message, span, phase)

    def observe_parser(parser, code, message, token):
        captured.append(token.span)
        return parser_error(parser, code, message, token)

    def observe_include(*args, **kwargs):
        # The frozen include/composition factories have the declaration/node
        # span in their locals, before the public point-only Diagnostic is made.
        caller = inspect.currentframe().f_back
        try:
            span = caller.f_locals.get("span")
            declaration = caller.f_locals.get("declaration")
            captured.append(span or getattr(declaration, "span", None))
        finally:
            del caller
        return include_factory(*args, **kwargs)

    print("case", label, encoded(path), encoded(text), sources_field(sources), int(resolve),
          profile, depth, files, size, sep="\t")
    with ExitStack() as stack:
        stack.enter_context(patch.object(SourceDiagnosticContext, "error", observe))
        stack.enter_context(patch.object(Parser, "_error", observe_parser))
        stack.enter_context(patch.object(includes, "compile_error", observe_include))
        try:
            result = NslCompiler(catalog(profile), resolver, IncludeOptions(".", depth, files, size)).compile(root)
        except CompileError as error:
            mode = "span" if captured and captured[-1] is not None else "none"
            if mode == "span":
                span = captured[-1]
                coords = (span.source_id, position(span.start), position(span.end))
            elif error.location is not None:
                # Lexer diagnostics carry only a point in this Oracle. Record
                # that fact explicitly rather than inventing an end token.
                mode = "point"
                source = next(s for s in source_files if s.logical_path == error.logical_path)
                line, column = error.location.line, error.location.column
                offset = sum(len(part) + 1 for part in source.text.split("\n")[:line-1]) + column - 1
                point = f"{offset},{line},{column}"
                coords = (source.source_id, point, point)
            else:
                coords = ("~", "~", "~")
            print("error", error.code, error.diagnostic.phase.value, mode, *coords,
                  encoded(error.logical_path), encoded(error.snippet), encoded(error.public_message), sep="\t")
        except (ValueError, TypeError) as error:
            # Raw admission/conversion exceptions remain codeless typed aborts.
            category = "codec" if type(error).__name__ in ("NsoSchemaError", "CredentialMaterialError") else "numeric"
            print("abort", category, encoded(type(error).__name__), sep="\t")
        else:
            print("ok", result.nso_bytes.hex(), result.semantic_hash,
                  result.source_bundle_hash, sha256(result.nso_bytes).hexdigest(), sep="\t")


def headers(name):
    for row in (FIXTURES / name).read_text().splitlines():
        if row.startswith("case\t"): yield row.split("\t")


print(f"# Full compiler gate; Python Oracle {ORACLE}")
for c in headers("compiler-oracle.tsv"):
    emit(f"lowering:{c[1]}:{c[2]}", "compiler/root.ns", decoded(c[3]),
         () if c[4] == "~" else (("compiler/part.ns", decoded(c[4])),), resolve=c[5] == "resolve")
for c in headers("type-skills-oracle.tsv"):
    emit(f"types:{c[1]}:{c[2]}", "types/skill.ns", decoded(c[3]))
for c in headers("symbol-oracle.tsv"):
    emit(f"symbols:{c[3]}:{c[2]}:{c[1]}", "symbols/skill.ns", decoded(c[4]),
         () if c[5] == "~" else (("symbols/fields.ns", decoded(c[5])),), profile="symbols")
for c in headers("bounds-source-oracle.tsv"):
    emit(f"bounds:{c[1]}:{c[2]}", "bounds/skill.ns", decoded(c[3]),
         () if c[4] == "~" else (("bounds/limits.ns", decoded(c[4])),), profile="bounds")
for c in headers("approval-oracle.tsv"):
    emit(f"approval:{c[3]}:{c[2]}:{c[1]}:{c[6]}", decoded(c[4]), decoded(c[5]), profile="empty")
for c in headers("write-safety-source-oracle.tsv"):
    emit(f"write:{c[1]}", "write-safety/skill.ns", decoded(c[8]),
         profile="empty" if c[7] == "absent" else f"write:{c[3]}:{c[4]}:{c[5]}")

LIMITS = 'limits {tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1;}'
def source(version, body, limits=LIMITS):
    return f'language NSL "{version}"; skill TEST {{version "1"; risk READ_ONLY; {limits}\n{body}\n}}'

for version in ("0.1", "0.2"):
    for name, material in (("auth_scheme", "Bearer synthetic-fixture"),
                          ("assignment", "api_key=synthetic-fixture"),
                          ("jwt", "eyJhbGciOiJub25lIn0.eyJzdWIiOiJmaXh0dXJlIn0.signature")):
        emit(f"protection:{name}:{version}", "compiler/root.ns",
             source(version, f'let text = "{material}"; let result = missing;'))
    emit(f"protection:path:{version}", "compiler/Bearer synthetic.ns", source(version,"let x = missing;"))
    emit(f"protection:message:{version}", "compiler/root.ns",
         source(version,'requires {tool MISSING version "Bearer synthetic-fixture";}'))
    emit(f"protection:non_idempotent:{version}", "compiler/root.ns",
         source(version,'requires {tool MISSING version "api_key=synthetic-fixture";}'))
    for label,body in (("empty",""), ("unknown_type", "input {x: Invalid;}"),
        ("bad_expression", "let x = ;"), ("nonpositive", "foreach x in xs max 0 {}"),
        ("expected_kind", "let 1 = 1;"), ("unknown_classification", "input {x: Int classification UNKNOWN;}"),
        ("unexpected_statement", "bad;"), ("missing_semicolon", "let x = 1")):
        emit(f"parser:{label}:{version}", "compiler/root.ns", source(version,body),profile="empty")
    for label,text in (("missing_header",'skill TEST {}'), ("unsupported",'language NSL "0.3"; skill TEST {}'),
        ("malformed",'language INVALID "0.2";'), ("unexpected_character", source(version,"@")),
        ("unterminated_string", source(version,'let text = "oops')), ("unterminated_escape", source(version,'let text = "oops')+'\\')):
        emit(f"lexer:{label}:{version}", "compiler/root.ns",text,profile="empty")
    for label,body,fragment in (("missing",'include "missing.ns";',None),
        ("cycle",'include "root.ns";',None),
        ("bad_fragment",'include "part.ns";', 'context {x: Invalid from "prior";}'),
        ("duplicate_limits",'include "part.ns";',LIMITS),
        ("duplicate_context",'context {x: Int from "root";} include "part.ns";', 'context {x: Int from "part";}')):
        text=source(version,body)
        fragments=(("compiler/root.ns",text),) if label=="cycle" else () if fragment is None else (("compiler/part.ns",fragment),)
        emit(f"include:{label}:{version}","compiler/root.ns",text,fragments,profile="empty")
    text=source(version,'include "part.ns";')
    for kind,options in (("depth",dict(depth=0)),("files",dict(files=0)),("root_bytes",dict(size=1))):
        emit(f"include:{kind}:{version}","compiler/root.ns",text,(("compiler/part.ns",""),),profile="empty",**options)

# Recompile the two original canonical corpus sources, including the complete
# governed UPDATE contract with post-WRITE verification metadata.
for version, profile, filename in (("1-0", "legacy-read", "project_budget_check.ns"),
                                   ("1-1", "legacy-write", "project_project_deactivate.ns")):
    directory = FIXTURES.parents[4] / "compatibility/v0_2_1/nso" / f"ir-{version}-canonical"
    text = (directory / filename).read_text()
    compiled = NslCompiler(catalog(profile)).compile(SourceFile.from_text("memory/root.ns", text))
    assert compiled.nso_bytes == (directory / "expected.nso").read_bytes()
    emit(f"canonical:{version}", "memory/root.ns", text, profile=profile)
