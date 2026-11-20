"""R6-S05 CHECK observations from an explicitly selected frozen Python tree.

Run with Python 3.12 -I -B, argv[1]=Oracle archive, argv[2]=rules|runtime.
Only injected in-memory providers are used. Expected values are never patched.
"""
import asyncio
import importlib.util
import sys
from dataclasses import asdict, replace
from itertools import product
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "expr_fixture", Path(__file__).with_name("generate_expressions.py")
)
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)
from nsl.core import Completeness, DataClassification, Presence, RiskLevel, ValueEnvelope
from nsl.ir import CheckStatement, NsoCodec, RequiredTool, canonical_json
from nsl.runtime import RuntimeEngine, _ExecutionServices
from nsl.runtime_models import ExecutionContext, ExecutionRequest
from nsl.security import PrincipalVerification, StaticAuthorizer
from nsl.tools import ToolContract, ToolContractCatalog, ToolExecutionError, ToolResultEnvelope, tool_result_hash
from nsl.validation import StrictRuleEvaluator


def statement(program, severity="ERROR", on_fail="CONTINUE", check_id="rule-가😀", message="검증\n실패😀"):
    return CheckStatement("check-node", check_id, e.parse(program), severity, on_fail, message, "check-result")


def envelope(value, type_name, presence, completeness, classification, refs):
    return ValueEnvelope(e.values[value], e.types[type_name], Presence(presence),
                         Completeness(completeness), DataClassification(classification),
                         () if refs == "-" else tuple(refs.split("+")))


def rules():
    for value, comp, presence, classification, refs in product(
        ("false", "true"), Completeness, Presence, DataClassification,
        ("-", "prov:one", "prov:z+prov:a+prov:z"),
    ):
        rule_row(value, "Bool", presence.value, comp.value, classification.value, refs)
    for value, type_name in product(
        ("zero", "one", "a", "null", "dp", "list0", "record_empty", "true"),
        ("Bool", "Int", "String", "Domain", "Enum", "ListBool"),
    ):
        if (value, type_name) == ("true", "Bool"):
            continue
        for comp in Completeness:
            rule_row(value, type_name, "EMPTY", comp.value, "RESTRICTED", "prov:bad")
    for severity, on_fail, check_id, message in (
        ("WARN", "STOP", "", ""),
        ("INFO", "REPORT", "check-2", "Authorization: Bearer synthetic.test.fixture"),
        ("CUSTOM", "CUSTOM", "한글😀", "line\n\tquoted\""),
    ):
        rule_row("false", "Bool", "PRESENT", "COMPLETE", "INTERNAL", "-",
                 severity, on_fail, check_id, message)


def rule_row(value, type_name, presence, comp, classification, refs,
             severity="ERROR", on_fail="CONTINUE", check_id="rule-가😀", message="검증\n실패😀"):
    fields = (value, type_name, presence, comp, classification, refs,
              severity.encode().hex(), on_fail.encode().hex(), check_id.encode().hex(), message.encode().hex())
    predicate = envelope(*fields[:6])
    rule = statement("L,true,Bool,condition", severity, on_fail, check_id, message)
    try:
        result = ["ok", asdict(StrictRuleEvaluator().evaluate(rule, predicate))]
    except Exception as error:
        result = [type(error).__name__, str(error)]
    print(*fields, canonical_json(result).hex(), sep="\t")


class Authorizer(StaticAuthorizer):
    def __init__(self):
        super().__init__()
        self.calls = []

    def authorize(self, principal, action, scopes):
        self.calls.append([action, sorted(scopes)])
        return super().authorize(principal, action, scopes)


def normal(output="Bool", flags=5):
    return f"L,seven,Int,z;L,one,Int,a;R,t0,z+a,{flags},{output},read0"


async def runtime_row(version="1-0", program="L,true,Bool,condition", output="Bool", value="true",
                      comp="COMPLETE", mutation="valid", scopes="read", clock_script="0",
                      collection=100, tool_limit=10, seed="0", bindings="-",
                      severity="ERROR", on_fail="CONTINUE"):
    fields = (version, program, output, value, comp, mutation, scopes, clock_script,
              collection, tool_limit, seed, "valid", bindings, severity, on_fail)
    base = NsoCodec.decode((Path(__file__).resolve().parents[5] /
                            f"compatibility/v0_2_1/nso/ir-{version}-canonical/expected.nso").read_bytes())
    contracts = tuple(ToolContract(tool, "1.0.0", "READ", args, e.types[ty], "read", DataClassification.INTERNAL)
                      for tool, args, ty in (("TEST.READ", (("z", e.types["Int"]), ("a", e.types["Int"])), output),
                                             ("TEST.INNER", (), "Int")))
    required = tuple(RequiredTool(f"t{i}", c.tool_id, c.version, "READ", c.contract_hash,
                                  "read", DataClassification.INTERNAL, RiskLevel.R0 if version == "1-1" else None)
                     for i, c in enumerate(contracts))
    skill = replace(base, required_tools=required,
                    limits=replace(base.limits, duration_ms=10, collection_size=collection, tool_calls=tool_limit))
    principal = replace(e.principal, scopes=frozenset() if scopes == "-" else frozenset(scopes.split("+")),
                        verification=PrincipalVerification.VERIFIED)
    clock = e.Clock(clock_script)
    ctx = ExecutionContext(skill, ExecutionRequest("exec-a", {}, {}, principal), clock)
    ctx.invocation_counter = int(seed)
    if bindings != "-":
        for item in bindings.split(";"):
            namespace, target, *data = item.split(",")
            getattr(ctx, {"i": "bind_input", "c": "bind_context", "v": "bind", "k": "bind_check"}[namespace])(
                target, envelope(*data))
    auth = Authorizer()
    requests = []

    class Provider:
        async def execute(self, request):
            requests.append([request.execution_id, request.invocation_id, request.node_id, request.tool_id,
                             request.tool_version, request.contract_hash,
                             {k: env(v) for k, v in request.arguments.items()}, request.principal.tenant_id,
                             request.principal.subject_id, sorted(request.principal.scopes), request.authorization_decision_ref])
            if mutation == "provider_error":
                raise RuntimeError("synthetic provider failure")
            if mutation == "provider_timeout":
                raise TimeoutError()
            if mutation == "tool_timeout":
                raise ToolExecutionError("TOOL_TIMEOUT", "synthetic timeout")
            c = contracts[int(request.tool_id == "TEST.INNER")]
            v = 7 if request.tool_id == "TEST.INNER" else e.values[value]
            result = ToolResultEnvelope(request.invocation_id, c.tool_id, c.version, v, c.output_type,
                                        Presence.EMPTY if v == [] or v is None else Presence.PRESENT,
                                        Completeness(comp), DataClassification.INTERNAL, tool_result_hash(v), None)
            return replace(result, **{
                "type": dict(type_info=e.types["String"]),
                "hash": dict(result_hash="sha256:" + "0" * 64),
                "force_empty": dict(presence=Presence.EMPTY),
            }.get(mutation, {}))

    rule = statement(program, severity, on_fail)
    try:
        await RuntimeEngine(ToolContractCatalog(contracts), authorizer=auth)._execute_check(
            ctx, rule, Provider(), _ExecutionServices())
        result = ["ok"]
    except Exception as error:
        result = [type(error).__name__, str(error), getattr(error, "node_id", None),
                  getattr(error, "detail_code", None), getattr(error, "code", None), getattr(error, "decision_ref", None)]
    try:
        bound = env(ctx.resolve("check-result"))
    except RuntimeError:
        bound = None
    meter = ctx.resources
    observed = [result, [asdict(c) for c in ctx.checks], bound, ctx.data_completeness,
                [meter.tool_calls, meter.loop_iterations, meter.emitted_rows, meter.max_collection_size_seen],
                ctx.invocation_counter, clock.calls, auth.calls, requests]
    print(*fields, canonical_json(observed).hex(), sep="\t")


def env(value):
    return [e.encode_value(value.value), value.type_info.to_data(), value.presence,
            value.completeness, value.classification, list(value.provenance_refs)]


async def runtime():
    for version, truth, comp, presence in product(("1-0", "1-1"), ("false", "true"), Completeness, Presence):
        await runtime_row(version, "S,predicate,Bool,condition", bindings=e.bind(
            truth, "Bool", comp.value, "RESTRICTED", "prov:z+prov:a+prov:z", "predicate", "v", presence.value))
    for version, truth, comp, flags in product(("1-0", "1-1"), ("false", "true"), Completeness, (5, 7)):
        await runtime_row(version, normal(flags=flags), value=truth, comp=comp.value)
    for version, comp, value in product(("1-0", "1-1"), Completeness, ("list0", "ints")):
        program = normal("ListInt") + ";C,sum,1,Int,sum;L,zero,Int,zero;B,GE,Bool,condition"
        await runtime_row(version, program, "ListInt", value, comp.value)
    for version, value in product(("1-0", "1-1"), ("zero", "one", "a", "null", "true")):
        for type_name in ("Bool", "Int"):
            await runtime_row(version, "S,predicate," + type_name + ",condition",
                              bindings=e.bind(value, type_name, target="predicate"))
    for namespace in ("i", "c", "v", "k"):
        await runtime_row(bindings=e.bind("seven", target="check-result", namespace=namespace))
    for mutation in ("type", "hash", "force_empty", "provider_error", "provider_timeout", "tool_timeout"):
        await runtime_row(program=normal(), mutation=mutation)
    for value, type_name in (("true", "Bool"), ("seven", "Int")):
        await runtime_row(program=normal(type_name), output=type_name, value=value, scopes="-")
    for limit in (0, 1, 2):
        await runtime_row(program=normal(), tool_limit=limit)
    for limit in (0, 2, 3, 4):
        await runtime_row(program=normal("ListInt") + ";C,count,1,Int,count;L,zero,Int,z;B,GE,Bool,condition",
                          output="ListInt", value="ints", collection=limit, comp="PARTIAL")
    for script in ("0,10000000", "0,0,10000000", "0,0,0,10000000", "0,0,0,0,10000000",
                   "0,0,0,0,0,10000000", "0,0,0,0,0,0,10000000", "0,0,0,0,0,0,0,10000000"):
        await runtime_row(program=normal(), clock_script=script, comp="UNKNOWN")
    for severity, on_fail in product(("INFO", "WARN", "ERROR", "CUSTOM"), ("CONTINUE", "STOP")):
        await runtime_row(program="L,false,Bool,condition", severity=severity, on_fail=on_fail)


if __name__ == "__main__":
    print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    if sys.argv[2] == "rules":
        rules()
    elif sys.argv[2] == "runtime":
        asyncio.run(runtime())
    else:
        raise ValueError("mode must be rules or runtime")
