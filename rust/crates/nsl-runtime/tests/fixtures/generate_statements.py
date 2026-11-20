"""R6-S06: unmodified 28a949e observations, with approved type exceptions separate.

argv[1]: frozen Python archive; argv[2]: normal|types. In-memory providers only.
The NSO column admits through both codec directions; host-only model mutations
exercise malformed direct-component inputs without weakening NSO admission.
"""
import asyncio
import importlib.util
import sys
from dataclasses import asdict, replace
from itertools import product
from pathlib import Path

spec = importlib.util.spec_from_file_location("expr_fixture", Path(__file__).with_name("generate_expressions.py"))
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)
e.values["record_extra"] = {"a": 7, "extra": 9}
from nsl.core import CHECK_RESULT, Completeness, DataClassification, Presence, RiskLevel, ValueEnvelope
from nsl.ir import (CheckStatement, EmitStatement, ForeachStatement, LetStatement,
                    NsoCodec, OutputField, RequiredTool, SymbolSpec, canonical_json)
from nsl.runtime import RuntimeEngine, _ExecutionServices
from nsl.runtime_models import ExecutionContext, ExecutionRequest
from nsl.security import PrincipalVerification, StaticAuthorizer
from nsl.tools import ToolContract, ToolContractCatalog, ToolExecutionError, ToolResultEnvelope, tool_result_hash


def literal(value="seven", ty="Int", node="literal"):
    return e.parse(f"L,{value},{ty},{node}")


def symbol(name="source", ty="ListInt", node="source-ref"):
    return e.parse(f"S,{name},{ty},{node}")


def read(output="Int"):
    return e.parse(f"L,seven,Int,z;L,one,Int,a;R,t0,z+a,5,{output},read0")


def emit(value=None, name="value", node="emit"):
    return EmitStatement(node, ((name, literal() if value is None else value),))


def loop(collection=None, body=None, maximum=4, iterator="iterator", node="foreach"):
    return ForeachStatement(node, iterator, symbol() if collection is None else collection,
                            maximum, (emit(symbol(iterator, "Int", "item-ref")),) if body is None else body)


def output(ty="Int", name="value", classification="RESTRICTED"):
    return OutputField(name, e.types[ty], DataClassification(classification))


def contract(output_type="Int", inner=False):
    return ToolContract("TEST.INNER" if inner else "TEST.READ", "1.0.0", "READ",
                        () if inner else (("z", e.types["Int"]), ("a", e.types["Int"])),
                        e.types[output_type], "read", DataClassification.INTERNAL)


class Authorizer(StaticAuthorizer):
    def __init__(self):
        super().__init__()
        self.calls = []

    def authorize(self, principal, action, scopes):
        self.calls.append([action, sorted(scopes)])
        return super().authorize(principal, action, scopes)


def mutate(skill, mutation):
    if mutation == "-":
        return skill
    statement = skill.body[0]
    if mutation.startswith("max="):
        statement = replace(statement, max_iterations=int(mutation[4:]))
    elif mutation == "duplicate_output":
        return replace(skill, outputs=skill.outputs + (skill.outputs[0],))
    elif mutation == "duplicate_fields":
        statement = replace(statement, fields=statement.fields + (statement.fields[0],))
    elif mutation == "empty_name":
        statement = replace(statement, fields=(("", statement.fields[0][1]),))
        return replace(skill, body=(statement,), outputs=(replace(skill.outputs[0], name=""),))
    else:
        raise ValueError(mutation)
    return replace(skill, body=(statement,) + skill.body[1:])


def env(v):
    return [e.encode_value(v.value), v.type_info.to_data(), v.presence, v.completeness,
            v.classification, list(v.provenance_refs)]


async def row(case, version="1-0", mode="block", statements=None, outputs=None,
              bindings="-", tool_output="Int", tool_value="seven", comp="COMPLETE",
              provider_mutation="valid", scopes="read", clock_script="0",
              limits="100,100,100,100,10", depth=0, mutation="-"):
    statements = (loop(),) if statements is None else statements
    outputs = (output(),) if outputs is None else outputs
    c, inner = contract(tool_output), contract(inner=True)
    base = NsoCodec.decode((Path(__file__).resolve().parents[5] /
                            f"compatibility/v0_2_1/nso/ir-{version}-canonical/expected.nso").read_bytes())
    required = tuple(RequiredTool(f"t{i}", tool.tool_id, tool.version, "READ", tool.contract_hash,
                                 "read", DataClassification.INTERNAL, RiskLevel.R0 if version == "1-1" else None)
                     for i, tool in enumerate((c, inner)))
    # Symbols are explicit, but component tests inject bindings without input admission.
    symbols = tuple(SymbolSpec(name, name.replace("-", "_"), "VARIABLE", e.types["Int"], DataClassification.INTERNAL)
                    for name in ("source", "iterator", "inner", "local", "target", "check-result", "unused"))
    skill = replace(base, inputs=(), contexts=(), required_tools=required, symbols=symbols,
                    body=statements, outputs=outputs).with_computed_hash()
    wire = NsoCodec.encode(skill)
    skill = mutate(NsoCodec.decode(wire), mutation)
    collection, loops, emitted, tools, duration = map(int, limits.split(","))
    skill = replace(skill, limits=replace(skill.limits, collection_size=collection, loop_iterations=loops,
                                         emitted_rows=emitted, tool_calls=tools, duration_ms=duration))
    clock = e.Clock(clock_script)
    principal = replace(e.principal, scopes=frozenset() if scopes == "-" else frozenset(scopes.split("+")),
                        verification=PrincipalVerification.VERIFIED)
    ctx = ExecutionContext(skill, ExecutionRequest("exec-a", {}, {}, principal), clock)
    ctx.foreach_depth = depth
    ctx.current_node_id = "caller"
    if bindings != "-":
        for item in bindings.split(";"):
            namespace, target, value, ty, presence, complete, classification, refs = item.split(",")
            v = ValueEnvelope(e.values[value], e.types[ty], Presence(presence), Completeness(complete),
                              DataClassification(classification), () if refs == "-" else tuple(refs.split("+")))
            getattr(ctx, {"v": "bind", "i": "bind_input", "c": "bind_context", "k": "bind_check"}[namespace])(target, v)
    auth = Authorizer()
    requests = []

    class Provider:
        async def execute(self, request):
            requests.append([request.execution_id, request.invocation_id, request.node_id, request.tool_id,
                             request.tool_version, request.contract_hash, {k: env(v) for k, v in request.arguments.items()},
                             request.principal.tenant_id, request.principal.subject_id, sorted(request.principal.scopes),
                             request.authorization_decision_ref])
            if provider_mutation == "provider_error":
                raise RuntimeError("synthetic provider failure")
            if provider_mutation == "provider_timeout":
                raise TimeoutError()
            if provider_mutation == "tool_timeout":
                raise ToolExecutionError("TOOL_TIMEOUT", "synthetic timeout")
            selected = inner if request.tool_id == "TEST.INNER" else c
            value = 7 if request.tool_id == "TEST.INNER" else e.values[tool_value]
            result = ToolResultEnvelope(request.invocation_id, selected.tool_id, selected.version, value,
                                        selected.output_type, Presence.EMPTY if value == [] or value is None else Presence.PRESENT,
                                        Completeness(comp), DataClassification.INTERNAL, tool_result_hash(value), None)
            return replace(result, **{"hash": dict(result_hash="sha256:" + "0" * 64),
                                      "type": dict(type_info=e.types["String"])}.get(provider_mutation, {}))

    engine = RuntimeEngine(ToolContractCatalog((c, inner)), authorizer=auth)
    try:
        if mode == "block":
            await engine._execute_block(ctx, skill.body, Provider(), _ExecutionServices())
        elif mode == "foreach":
            await engine._execute_foreach(ctx, skill.body[0], Provider(), _ExecutionServices())
        else:
            await engine._execute_emit(ctx, skill.body[0], Provider(), _ExecutionServices())
        result = ["ok"]
    except Exception as error:
        result = [type(error).__name__, str(error), getattr(error, "node_id", None),
                  getattr(error, "detail_code", None), getattr(error, "code", None), getattr(error, "decision_ref", None)]
    resolved = []
    for name in ("source", "iterator", "inner", "local", "target", "check-result"):
        try:
            resolved.append(env(ctx.resolve(name)))
        except RuntimeError:
            resolved.append(None)
    usage = ctx.resources
    observed = [result, [[[name, env(value)] for name, value in record.fields.items()] for record in ctx.outputs],
                [asdict(check) for check in ctx.checks], ctx.data_completeness,
                [usage.tool_calls, usage.loop_iterations, usage.emitted_rows, usage.max_collection_size_seen],
                ctx.foreach_depth, len(ctx.frames), ctx.current_node_id, resolved, clock.calls,
                auth.calls, requests]
    print(case, version, mode, wire.hex(), bindings, tool_output, tool_value, comp, provider_mutation,
          scopes, clock_script, limits, depth, mutation, canonical_json(observed).hex(), sep="\t")


def bind(value="ints", ty="ListInt", comp="COMPLETE", classification="INTERNAL", target="source", namespace="v"):
    return e.bind(value, ty, comp, classification, "prov:z+prov:a+prov:z", target, namespace)


async def normal():
    await row("empty-block", statements=(), outputs=())
    await row("empty-body", statements=(loop(body=()),), bindings=bind())
    for version, value, complete, classification in product(("1-0", "1-1"), ("list0", "int1", "ints"), Completeness, DataClassification):
        body = (LetStatement("let", "local", symbol("iterator", "Int", "item")),
                CheckStatement("check", "rule", e.parse("S,local,Int,left;L,zero,Int,right;B,GE,Bool,condition"),
                               "ERROR", "CONTINUE", "검증😀", "check-result"),
                emit(symbol("local", "Int", "emit-ref")))
        await row("loop-metadata", version, statements=(loop(body=body),),
                  bindings=bind(value, comp=complete.value, classification=classification.value))
    for mode, limit_name, limit in product(("block", "foreach"), ("collection", "loop", "emit", "max"), (-1, 0, 1, 2, 3, 4)):
        limits = [100, 100, 100, 100, 10]
        if limit_name != "max":
            limits[{"collection": 0, "loop": 1, "emit": 2}[limit_name]] = limit
        await row("loop-" + limit_name, mode=mode, bindings=bind(), limits=",".join(map(str, limits)),
                  mutation=f"max={limit}" if limit_name == "max" else "-")
    for maximum in (-1, 0, 1):
        await row("empty-max", bindings=bind("list0"), mutation=f"max={maximum}")
    for initial, nested in product((0, 14, 15, 16, 17), (1, 2, 3)):
        body = (emit(),)
        for index in reversed(range(nested)):
            body = (loop(symbol("source", "ListInt", f"source-{index}"), body, iterator=f"level-{index}", node=f"loop-{index}"),)
        await row("loop-depth", statements=body, bindings=bind("int1"), depth=initial)
    for namespace in ("v", "i", "c", "k"):
        await row("iterator-collision", bindings=bind("int1") + ";" + bind("seven", "Int", target="iterator", namespace=namespace))
    await row("siblings", statements=(loop(), replace(loop(), node_id="sibling")), bindings=bind("int1"))
    await row("iterator-escape", statements=(loop(), LetStatement("after", "target", symbol("iterator", "Int"))), bindings=bind("int1"))
    await row("let-collision", statements=(loop(body=(LetStatement("let", "iterator", literal()),)),), bindings=bind("int1"))
    check = CheckStatement("check", "rule", literal("true", "Bool"), "ERROR", "CONTINUE", "check", "check-result")
    await row("check-collision", statements=(loop(body=(check,)),), bindings=bind("int1") + ";" + bind("seven", "Int", target="check-result"))
    await row("check-predicate", statements=(loop(body=(replace(check, condition=literal()),)),), bindings=bind("int1"))
    for mode in ("block", "foreach"):
        await row("collection-read", mode=mode, statements=(loop(read("ListInt")),), tool_output="ListInt", tool_value="ints", comp="PARTIAL")
    for mutation in ("valid", "provider_error", "provider_timeout", "tool_timeout", "hash", "type"):
        await row("body-read", statements=(loop(body=(LetStatement("read-let", "local", read()), emit(symbol("local", "Int")))),),
                  bindings=bind("int1"), provider_mutation=mutation)
    for limit in (0, 1, 2):
        await row("body-read-limit", statements=(loop(body=(LetStatement("read-let", "local", read()), emit())),),
                  bindings=bind(), limits=f"100,100,100,{limit},10")
    await row("body-read-denied", statements=(loop(body=(emit(read()),)),), bindings=bind("int1"), scopes="-")
    for position in range(1, 15):
        script = ",".join(["0"] * position + ["10000000"])
        await row("loop-clock", bindings=bind(), clock_script=script)
    for version, actual, allowed, complete in product(("1-0", "1-1"), DataClassification, DataClassification, Completeness):
        await row("emit-classification", version, mode="emit", statements=(emit(symbol("source", "Int")),),
                  outputs=(output(classification=allowed.value),), bindings=bind("seven", "Int", complete.value, actual.value))
    for kind in ("missing", "extra", "duplicate_fields", "duplicate_output", "empty_name", "type"):
        fields = (("value", literal()),)
        if kind == "missing": fields = ()
        if kind == "extra": fields += (("extra", literal()),)
        if kind == "type": fields = (("value", literal("true", "Bool")),)
        await row("emit-schema", mode="emit", statements=(EmitStatement("emit", fields),),
                  mutation=kind if kind in ("duplicate_fields", "duplicate_output", "empty_name") else "-")
    await row("emit-wrong-name", mode="emit", statements=(emit(name="other"),))
    await row("emit-balanced-duplicate", mode="emit", statements=(EmitStatement("emit", (("value", literal()), ("value", literal()))),),
              outputs=(output(), output(name="other")))
    for limit in (-1, 0, 1, 2):
        await row("emit-limit-priority", mode="emit", statements=(EmitStatement("emit", ()),), limits=f"100,100,{limit},100,10")
    await row("empty-output", mode="emit", statements=(EmitStatement("emit", ()),), outputs=())
    await row("field-order", mode="emit", statements=(EmitStatement("emit", (("a", literal("a", "String")), ("z", literal()))),),
              outputs=(output(name="z"), output("String", "a")))
    for complete in Completeness:
        await row("emit-read", mode="emit", statements=(emit(read()),), comp=complete.value)
    for value, ty in (("null", "Int"), ("true", "Int"), ("one", "Bool"), ("a", "Enum")):
        await row("scalar-type", mode="emit", statements=(emit(symbol("source", ty)),), outputs=(output(ty),), bindings=bind(value, ty))
    for ty, value in (("Int", "seven"), ("String", "unicode"), ("Domain", "a"), ("Enum", "a"), ("USD", "usd"),
                      ("Date", "date0"), ("DateTime", "utc"), ("Decimal", "dp"), ("ListInt", "list0"),
                      ("Record", "record"), ("Nested", "record_list")):
        await row("emit-types", mode="emit", statements=(emit(symbol("source", ty)),), outputs=(output(ty),), bindings=bind(value, ty))
    for script in ("0,10000000", "0,0,10000000", "0,0,0,10000000"):
        await row("emit-clock", mode="emit", statements=(emit(),), clock_script=script)


async def types():
    for version, value, ty in product(("1-0", "1-1"), ("a", "unicode", "record", "ints", "bools", "records_bad", "nested_bool", "money_mixed"), ("ListInt",)):
        if value == "ints": continue
        await row("foreach-type", version, statements=(loop(body=(LetStatement("body-read", "local", read()), emit())),), bindings=bind(value, ty))
    for version, value, ty in product(("1-0", "1-1"), ("ints", "list0"), ("Int", "String")):
        await row("foreach-declaration", version, statements=(loop(symbol("source", ty), (emit(),)),), bindings=bind(value, ty))
    for version, ty, value in product(("1-0", "1-1"), ("ListInt", "Record", "Nested"), ("one", "a", "null", "bools", "record_other", "record_null", "record_list")):
        # Only observations Python accepts as List/Record: all these declarations
        # are currently unchecked. Some values also conform; keep exceptions strict.
        if (ty, value) == ("Nested", "record_list"): continue
        await row("emit-recursive-type", version, mode="emit", statements=(emit(symbol("source", ty)),),
                  outputs=(output(ty),), bindings=bind(value, ty))
    for version in ("1-0", "1-1"):
        await row("foreach-nested-type", version, statements=(loop(symbol("source", "ListListInt"), (emit(),)),), bindings=bind("nested_bool", "ListListInt"))
        await row("foreach-record-type", version, statements=(loop(symbol("source", "ListRecord"), (emit(),)),), bindings=bind("records_bad", "ListRecord"))
        await row("emit-record-extra", version, mode="emit", statements=(emit(symbol("source", "Record")),), outputs=(output("Record"),), bindings=bind("record_extra", "Record"))


if __name__ == "__main__":
    print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    asyncio.run(normal() if sys.argv[2] == "normal" else types())
