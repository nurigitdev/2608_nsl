"""Generate typed Rust test inputs from the frozen Oracle's decoded NSOs."""

import json
from decimal import Decimal
from pathlib import Path
import sys


def string(text):
    return json.dumps(text, ensure_ascii=False)


def owned(text):
    return string(text) + ".into()"


def parse(text):
    return string(str(text)) + ".parse().unwrap()"


def flag(value):
    return str(value).lower()


def vector(values, render):
    return "vec![" + ",".join(map(render, values)) + "]"


def fields(values, render):
    return vector(values, lambda entry: f'({owned(entry[0])},{render(entry[1])})')


def obj(name, /, **data):
    return name + "{" + ",".join(f"{field}:{value}" for field, value in data.items()) + "}"


def typ(data):
    kind = data["kind"]
    if kind in ("primitive", "domain"):
        variant = f'TypeKind::{kind.title()}({owned(data["name"])})'
    elif kind == "money":
        variant = f'TypeKind::Money({parse(data["currency"])})'
    elif kind == "list":
        variant = f'TypeKind::List(Box::new({typ(data["item"])}))'
    elif kind == "enum":
        variant = obj("TypeKind::Enum", name=owned(data["name"]),
                      values=vector(data["values"], owned))
    elif kind == "record":
        variant = obj("TypeKind::Record", name=owned(data["name"]),
                      fields=fields([(f["name"], f["type"]) for f in data["fields"]], typ))
    else:
        raise ValueError(kind)
    return f'ty({variant})'


def literal(data):
    if type(data) is bool:
        return f'LiteralValue::Bool({flag(data)})'
    if type(data) is str:
        return f'LiteralValue::String({owned(data)})'
    if type(data) is int:
        return f'LiteralValue::Int({parse(data)})'
    if data["$type"] == "Decimal":
        return f'LiteralValue::Decimal({parse(data["value"])})'
    if data["$type"] == "Money":
        return f'LiteralValue::Money(Money::new({parse(data["amount"])},{parse(data["currency"])}))'
    raise ValueError(data)


def named(data):
    return obj("NamedExpression", name=owned(data["name"]), value=expr(data["value"]))


def expr(data):
    kind = data["kind"]
    prefix = "ExpressionKind::"
    if kind == "literal":
        node = f'{prefix}Literal({literal(data["value"])})'
    elif kind == "symbol_ref":
        node = obj(prefix + "SymbolRef", symbol_id=owned(data["symbol_id"]))
    elif kind in ("field", "project"):
        node = obj(prefix + ("Field" if kind == "field" else "Projection"),
                   source=f'Box::new({expr(data["source"])})', field=owned(data["field"]))
    elif kind == "call":
        node = obj(prefix + "Call", function=owned(data["function"]),
                   arguments=vector(data["arguments"], expr))
    elif kind == "binary":
        node = obj(prefix + "Binary", operator=owned(data["operator"]),
                   left=f'Box::new({expr(data["left"])})', right=f'Box::new({expr(data["right"])})')
    elif kind == "read":
        node = obj(prefix + "Read", tool_ref=owned(data["tool_ref"]),
                   arguments=vector(data["arguments"], named),
                   result_policy=obj("ResultPolicy", **{k: flag(v) for k, v in data["result_policy"].items()}))
    elif kind == "write":
        write = obj("WriteExpression", tool_ref=owned(data["tool_ref"]),
                    arguments=vector(data["arguments"], named), approval_ref=owned(data["approval_ref"]))
        node = f'{prefix}Write({write})'
    else:
        raise ValueError(kind)
    return obj("Expression", node_id=owned(data["node_id"]), type_info=typ(data["type"]), kind=node)


def stmt(data):
    kind = data["kind"]
    prefix = "StatementKind::"
    if kind == "let":
        node = obj(prefix + "Let", target_symbol_id=owned(data["target_symbol_id"]), value=expr(data["value"]))
    elif kind == "foreach":
        node = obj(prefix + "Foreach", iterator_symbol_id=owned(data["iterator_symbol_id"]),
                   collection=expr(data["collection"]), max_iterations=parse(data["max_iterations"]),
                   body=vector(data["body"], stmt))
    elif kind == "check":
        node = obj(prefix + "Check", condition=expr(data["condition"]), **{
            k: owned(data[k]) for k in ("check_id", "severity", "on_fail", "message", "result_symbol_id")
        })
    elif kind == "emit":
        node = obj(prefix + "Emit", fields=vector(data["fields"], named))
    elif kind == "approval":
        ttl = data["policy"].get("ttl_seconds")
        assert data["policy"]["required"] is True
        approval = obj("ApprovalStatement", approval_id=owned(data["approval_id"]),
                       message=owned(data["message"]), policy=obj("ApprovalPolicy",
                       ttl_seconds="None" if ttl is None else f'Some({parse(ttl)})'))
        node = f'{prefix}Approval({approval})'
    else:
        raise ValueError(kind)
    return obj("Statement", node_id=owned(data["node_id"]), kind=node)


def spec(name, data, **extra):
    return obj(name, name=owned(data["name"]), type_info=typ(data["type"]),
               classification=parse(data["classification"]), **extra)


def tool(data):
    if "risk_level" in data:
        kind = obj("RequiredToolKind::V1_1", capability=parse(data["capability"]),
                   risk_level=parse(data["risk_level"]))
    else:
        assert data["capability"] == "READ"
        kind = "RequiredToolKind::ReadV1_0"
    return obj("RequiredTool", kind=kind, contract_hash=parse(data["contract_hash"]),
               output_classification=parse(data["output_classification"]), **{
                   k: owned(data[k]) for k in ("tool_ref", "tool_id", "version", "required_scope")
               })


def source(data):
    return obj("BuildSource", logical_path=owned(data["logical_path"]), content_hash=parse(data["sha256"]),
               size_bytes=parse(data["size_bytes"]), is_root=flag(data["is_root"]))


def skill(data):
    return obj(
        "SkillObject", ir_version=parse(data["ir_version"]), language_version=owned(data["language"]["version"]),
        skill_id=owned(data["skill"]["id"]), skill_version=owned(data["skill"]["version"]),
        risk=owned(data["skill"]["risk"]), semantics_profile=owned(data["semantics_profile"]),
        semantic_hash=parse(data["hashes"]["semantic_sha256"]),
        features="[" + ",".join(map(owned, data["features"])) + "].into()",
        symbols=vector(data["symbols"], lambda d: spec("SymbolSpec", d, symbol_id=owned(d["symbol_id"]),
                       category=owned(d["category"]))),
        required_tools=vector(data["requires"], tool), limits=obj("ResourceLimits", **{
            k: parse(v) for k, v in data["limits"].items()
        }), inputs=vector(data["inputs"], lambda d: spec("InputSpec", d, symbol_id=owned(d["symbol_id"]),
                         required=flag(d["required"]))),
        contexts=vector(data["contexts"], lambda d: spec("ContextSpec", d, symbol_id=owned(d["symbol_id"]),
                           path=vector(d["path"], owned))),
        outputs=vector(data["output"], lambda d: spec("OutputField", d)), body=vector(data["body"], stmt),
        analysis=obj("StaticAnalysis", **{k: flag(v) if k == "bounded" else parse(v)
                                        for k, v in data["analysis"].items()}),
        build=obj("BuildMetadata", root_source=owned(data["build"]["root_source"]),
                  source_bundle_sha256=parse(data["build"]["source_bundle_sha256"]),
                  sources=vector(data["build"]["sources"], source)),
    )


def main(oracle_root, corpus_root):
    sys.path.insert(0, str(oracle_root.resolve()))
    from nsl.core import BOOL, DECIMAL, INT, STRING, Money, money_type
    from nsl.ir import (
        ApprovalPolicy, ApprovalStatement, LiteralExpr, NsoCodec, ResultPolicy,
        _expr_to_data, _statement_to_data, canonical_json, skill_to_data,
    )
    from nsl.ir_schema import _validate_expression, _validate_statement

    print("// Generated from the frozen Oracle's decoded IR by fixtures/generate.py.")
    for version in ("1-0", "1-1"):
        content = (corpus_root / f"ir-{version}-canonical" / "expected.nso").read_bytes()
        model = NsoCodec.decode(content)
        assert NsoCodec.encode(model) == content
        print(f"fn ir_{version.replace('-', '_')}() -> SkillObject {{")
        print(skill(skill_to_data(model)))
        print("}")

    print("fn literal_cases() -> [(Expression, &'static str); 5] { [")
    for index, (value, value_type) in enumerate((
        (False, BOOL), (2**128 + 1, INT), ("예산", STRING),
        (Decimal("-0.00"), DECIMAL), (Money(Decimal("0.10"), "KRW"), money_type("KRW")),
    )):
        node = _expr_to_data(LiteralExpr(f"literal-{index}", value, value_type))
        _validate_expression(node, "$.literal", "1.0")
        print(f'({expr(node)},{string(canonical_json(node).decode())}),')
    print("] }")

    print("fn approval_cases() -> [(Statement, &'static str); 3] { [")
    for ttl in (None, 1, 2**64):
        node = _statement_to_data(ApprovalStatement("approval-1", "DEMO", "", ApprovalPolicy(ttl_seconds=ttl)))
        _validate_statement(node, "$.approval", "1.1")
        print(f'({stmt(node)},{string(canonical_json(node).decode())}),')
    print("] }")

    policy = ResultPolicy()
    default = obj("ResultPolicy", required=flag(policy.required),
                  accept_partial=flag(policy.accept_partial), empty_is_valid=flag(policy.empty_is_valid))
    print(f"fn oracle_default_result_policy() -> ResultPolicy {{ {default} }}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
