"""Current-Python security regressions; the frozen 28a949e Oracle is unchanged."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from nsl.audit import InMemoryAuditSink
from nsl.core import INT, BusinessOperationId, DataClassification, ExecutionStatus, RiskLevel
from nsl.ir import (
    ApprovalStatement,
    LetStatement,
    NsoCodec,
    ReadExpr,
    RequiredTool,
    ResultPolicy,
    SymbolSpec,
    WriteExpression,
)
from nsl.runtime import RuntimeContractError, RuntimeEngine, _ExecutionServices
from nsl.runtime_models import ExecutionContext, ExecutionRequest
from nsl.security import (
    AuthorizationError,
    ExecutionPrincipal,
    PrincipalVerification,
    StaticAuthorizer,
)
from nsl.tools import (
    Atomicity,
    CommitConfirmation,
    CommitSemantics,
    IdempotencySemantics,
    MockToolExecutor,
    OperationKind,
    ToolContract,
    ToolContractCatalog,
    ToolExecutionError,
    WriteSemantics,
)


ROOT = Path(__file__).resolve().parents[1]
MISMATCHES = (
    ("WRITE", "test:read"),
    ("TRANSACTION", "test:read"),
    ("CUSTOM", "test:read"),
    ("READ", "test:admin"),
    ("WRITE", "test:write"),
)


def _contract(capability="READ", scope="test:read", *, arguments=()):
    write = {}
    if capability == "WRITE":
        write = dict(
            operation_kind=OperationKind.CREATE,
            risk_level=RiskLevel.R2,
            write_semantics=WriteSemantics(
                True,
                Atomicity.ATOMIC,
                IdempotencySemantics(True, "tenant", "P1D", "RETURN_PREVIOUS", "REJECT"),
                CommitSemantics(CommitConfirmation.EXPLICIT),
                True,
                True,
            ),
        )
    return ToolContract(
        "TEST.TOOL", "1.0.0", capability, arguments, INT, scope,
        DataClassification.INTERNAL, **write,
    )


def _skill(contract, version="1-1", *, capability="READ", scope="test:read"):
    base = NsoCodec.decode(
        (ROOT / f"compatibility/v0_2_1/nso/ir-{version}-canonical/expected.nso").read_bytes()
    )
    required = RequiredTool(
        "tool1", contract.tool_id, contract.version, capability, contract.contract_hash,
        scope, DataClassification.INTERNAL,
        RiskLevel.R2 if capability == "WRITE" else (RiskLevel.R0 if version == "1-1" else None),
    )
    read = ReadExpr("read1", "tool1", (), INT, ResultPolicy())
    return replace(
        base, required_tools=(required,), inputs=(), contexts=(), outputs=(),
        symbols=(SymbolSpec("s1", "value", "VARIABLE", INT, DataClassification.INTERNAL),),
        body=(LetStatement("let1", "s1", read),),
    ).with_computed_hash()


def _request(*, granted=True):
    scopes = {"nsl:skill:execute"}
    if granted:
        scopes.add("test:read")
    principal = ExecutionPrincipal(
        "tenant-a", "subject-a", "USER", frozenset(), frozenset(scopes),
        "auth:ref", PrincipalVerification.VERIFIED,
    )
    return ExecutionRequest(
        "exec-read-security", {}, {}, principal,
        business_operation_id=BusinessOperationId("business-read-security"),
    )


class RecordingAuthorizer(StaticAuthorizer):
    def __init__(self):
        super().__init__()
        self.calls = []

    def authorize(self, principal, action, required_scopes):
        self.calls.append((action, required_scopes))
        return super().authorize(principal, action, required_scopes)


def _provider(contract):
    catalog = ToolContractCatalog((contract,))
    return catalog, MockToolExecutor(catalog, {contract.tool_id: lambda arguments: 7})


@pytest.mark.parametrize("version", ("1-0", "1-1"))
@pytest.mark.parametrize(("capability", "scope"), MISMATCHES)
@pytest.mark.parametrize("entry", ("execute", "validate", "validate_controlled"))
def test_security_metadata_mismatch_rejected_before_provider(
    version, capability, scope, entry,
):
    contract = _contract(capability, scope)
    catalog, provider = _provider(contract)
    # The hash is the REAL contract hash; NSO integrity/schema admission succeeds.
    skill = NsoCodec.decode(NsoCodec.encode(_skill(contract, version)))
    authorizer = RecordingAuthorizer()
    engine = RuntimeEngine(catalog, authorizer=authorizer)
    if entry == "execute":
        result = asyncio.run(engine.execute(skill, _request(), provider, InMemoryAuditSink()))
        assert result.status is ExecutionStatus.FAILED
        assert result.error.category == "RUNTIME"
        assert "tool contract metadata mismatch" in result.error.message
        assert result.resources.tool_calls == 0
    else:
        method = engine.validate_execution_request
        if entry == "validate_controlled":
            # A valid controlled plan is needed before checking its declarations.
            skill = _controlled(skill)
            method = engine.validate_controlled_execution_request
        with pytest.raises(RuntimeContractError, match="tool contract metadata mismatch"):
            method(skill, _request())
    assert provider.call_count == 0
    assert len(authorizer.calls) == 1  # Skill authorization only; no Tool authority.


@pytest.mark.parametrize("version", ("1-0", "1-1"))
@pytest.mark.parametrize(("capability", "scope"), MISMATCHES)
def test_security_direct_read_rechecks_contract_before_effects(version, capability, scope):
    contract = _contract(capability, scope)
    catalog, provider = _provider(contract)
    skill = _skill(contract, version)
    ctx = ExecutionContext(skill, _request())
    authorizer = RecordingAuthorizer()
    engine = RuntimeEngine(catalog, authorizer=authorizer)
    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(engine._execute_let(ctx, skill.body[0], provider, _ExecutionServices()))
    assert captured.value.code == "TOOL_CONTRACT_MISMATCH"
    assert provider.call_count == ctx.invocation_counter == ctx.resources.tool_calls == 0
    assert authorizer.calls == []
    assert ctx.frames == [{}]


@pytest.mark.parametrize("capability", ("WRITE", "TRANSACTION", "CUSTOM"))
def test_security_matching_non_read_declaration_cannot_use_read_node(capability):
    contract = _contract(capability)
    catalog, provider = _provider(contract)
    skill = _skill(contract, capability=capability)
    ctx = ExecutionContext(skill, _request())
    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(RuntimeEngine(catalog)._evaluate(
            ctx, skill.body[0].value, provider, _ExecutionServices(),
        ))
    assert captured.value.code == "TOOL_CONTRACT_MISMATCH"
    assert provider.call_count == ctx.resources.tool_calls == ctx.invocation_counter == 0


@pytest.mark.parametrize("version", ("1-0", "1-1"))
def test_security_invalid_outer_read_does_not_evaluate_nested_provider(version):
    outer = _contract("WRITE", "test:write", arguments=(("value", INT),))
    inner = replace(_contract(), tool_id="TEST.INNER")
    skill = _skill(outer, version)
    inner_required = replace(
        skill.required_tools[0], tool_ref="inner", tool_id=inner.tool_id,
        contract_hash=inner.contract_hash,
    )
    nested = ReadExpr("nested", "inner", (), INT, ResultPolicy())
    statement = replace(skill.body[0], value=replace(
        skill.body[0].value, arguments=(("value", nested),),
    ))
    skill = replace(skill, required_tools=(*skill.required_tools, inner_required),
                    body=(statement,)).with_computed_hash()
    skill = NsoCodec.decode(NsoCodec.encode(skill))
    catalog = ToolContractCatalog((outer, inner))
    provider = MockToolExecutor(catalog, {inner.tool_id: lambda arguments: 7})
    ctx = ExecutionContext(skill, _request())
    with pytest.raises(ToolExecutionError) as captured:
        asyncio.run(RuntimeEngine(catalog)._evaluate(
            ctx, skill.body[0].value, provider, _ExecutionServices(),
        ))
    assert captured.value.code == "TOOL_CONTRACT_MISMATCH"
    assert provider.call_count == ctx.resources.tool_calls == ctx.invocation_counter == 0


def _controlled(skill, *, prefix=True):
    base = NsoCodec.decode(
        (ROOT / "compatibility/v0_2_1/nso/ir-1-1-canonical/expected.nso").read_bytes()
    )
    approval = next(node for node in base.body if isinstance(node, ApprovalStatement))
    write = LetStatement(
        "write-let", "s2", WriteExpression("write1", "tool1", (), approval.approval_id, INT),
    )
    return replace(
        skill, ir_version="1.1", language_version="0.2", semantics_profile="NSL-0.2-STRICT",
        symbols=(*skill.symbols, SymbolSpec("s2", "written", "VARIABLE", INT,
                                          DataClassification.INTERNAL)),
        body=((*skill.body,) if prefix else ()) + (approval, write),
    ).with_computed_hash()


def test_security_controlled_prefix_read_cannot_dispatch_even_matching_write_contract():
    contract = _contract("WRITE")
    catalog, provider = _provider(contract)
    # Matching WRITE metadata legitimately passes controlled preflight, but a
    # forged READ node before approval must still never dispatch that contract.
    skill = _controlled(_skill(contract, capability="WRITE"))
    engine = RuntimeEngine(catalog)
    engine.validate_controlled_execution_request(skill, _request())
    outcome = asyncio.run(engine.execute_controlled_prefix(
        skill, _request(), provider, InMemoryAuditSink(),
    ))
    assert outcome.result.status is ExecutionStatus.TOOL_ERROR
    assert outcome.result.error.detail_code == "TOOL_CONTRACT_MISMATCH"
    assert provider.call_count == outcome.result.resources.tool_calls == 0
    assert outcome.checkpoint is None


@pytest.mark.parametrize("entry", ("execute", "validate", "validate_controlled"))
@pytest.mark.parametrize("granted", (False, True))
def test_security_actual_contract_scope_is_authorized(entry, granted):
    contract = _contract("WRITE" if entry == "validate_controlled" else "READ")
    catalog, provider = _provider(contract)
    skill = _skill(contract, capability=contract.capability)
    if entry == "validate_controlled":
        skill = _controlled(skill, prefix=False)
    authorizer = RecordingAuthorizer()
    engine = RuntimeEngine(catalog, authorizer=authorizer)
    request = _request(granted=granted)
    if entry == "execute":
        result = asyncio.run(engine.execute(skill, request, provider, InMemoryAuditSink()))
        assert result.status is (ExecutionStatus.COMPLETED if granted else ExecutionStatus.FAILED)
        if not granted:
            assert result.error.category == "AUTHORIZATION"
        assert provider.call_count == int(granted)
    else:
        method = (engine.validate_execution_request if entry == "validate"
                  else engine.validate_controlled_execution_request)
        if granted:
            method(skill, request)
        else:
            with pytest.raises(AuthorizationError, match=r"missing required scope\(s\)"):
                method(skill, request)
        assert provider.call_count == 0
    assert authorizer.calls == [
        (f"skill:{skill.skill_id}:execute", frozenset({"nsl:skill:execute"})),
        (f"tool:{contract.tool_id}:execute", frozenset({contract.required_scope})),
    ]


@pytest.mark.parametrize("entry", ("execute", "validate", "validate_controlled"))
@pytest.mark.parametrize("changed_field", ("capability", "required_scope"))
def test_security_registry_change_after_preflight_is_revalidated(entry, changed_field):
    original = _contract()
    changed = (_contract("WRITE") if changed_field == "capability"
               else replace(original, required_scope="test:admin"))

    class ChangingRegistry:
        calls = 0

        def resolve(self, tool_id, version):
            self.calls += 1
            return original if self.calls == 1 else changed

    registry = ChangingRegistry()
    _, provider = _provider(original)
    authorizer = RecordingAuthorizer()
    engine = RuntimeEngine(registry, authorizer=authorizer)
    skill = _skill(original)
    if entry == "execute":
        result = asyncio.run(engine.execute(skill, _request(), provider, InMemoryAuditSink()))
        assert result.error.detail_code == "TOOL_CONTRACT_MISMATCH"
        assert result.resources.tool_calls == 0
    else:
        if entry == "validate_controlled":
            skill = _controlled(skill)
        method = (engine.validate_execution_request if entry == "validate"
                  else engine.validate_controlled_execution_request)
        with pytest.raises(RuntimeContractError, match="tool contract metadata mismatch"):
            method(skill, _request())
    assert registry.calls == 2
    assert len(authorizer.calls) == 1
    assert provider.call_count == 0
