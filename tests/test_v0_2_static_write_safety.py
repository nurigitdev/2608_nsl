from __future__ import annotations

from dataclasses import replace
from inspect import signature

import pytest

from nsl import CompileError, DiagnosticCode, NslCompiler, SourceFile
from nsl.compiler import WriteSafetyAnalyzer
from nsl.core import (
    DataClassification,
    RiskLevel,
    STRING,
    ToolCapability,
    domain,
)
from nsl.ir import ApprovalStatement, CheckStatement, LetStatement, WriteExpression
from nsl.tools import (
    Atomicity,
    CommitConfirmation,
    CommitSemantics,
    IdempotencySemantics,
    OperationKind,
    ToolContract,
    ToolContractCatalog,
    WriteSemantics,
)


WRITE_RESULT = domain("DraftRecord")
TOOL_ID = "FINANCE.CREATE_DRAFT"


def _idempotency() -> IdempotencySemantics:
    return IdempotencySemantics(
        required=True,
        scope="TOOL_AND_TENANT",
        retention="PROVIDER_DEFINED",
        duplicate_behavior="RETURN_ORIGINAL_RESULT",
        payload_conflict_behavior="IDEMPOTENCY_CONFLICT",
    )


def _write_semantics() -> WriteSemantics:
    return WriteSemantics(
        side_effect=True,
        atomicity=Atomicity.ATOMIC,
        idempotency=_idempotency(),
        commit=CommitSemantics(CommitConfirmation.EXPLICIT),
        approval_required=True,
        preconditions_supported=True,
    )


def _write_contract(*, risk_level: RiskLevel = RiskLevel.R3) -> ToolContract:
    return ToolContract(
        tool_id=TOOL_ID,
        version="1.0.0",
        capability=ToolCapability.WRITE,
        input_types=(("detail", STRING),),
        output_type=WRITE_RESULT,
        required_scope="finance:draft:write",
        output_classification=DataClassification.CONFIDENTIAL,
        operation_kind=OperationKind.CREATE_DRAFT,
        risk_level=risk_level,
        write_semantics=_write_semantics(),
    )


def _read_contract() -> ToolContract:
    return ToolContract(
        tool_id=TOOL_ID,
        version="1.0.0",
        capability=ToolCapability.READ,
        input_types=(("detail", STRING),),
        output_type=WRITE_RESULT,
        required_scope="finance:draft:read",
        output_classification=DataClassification.CONFIDENTIAL,
    )


def _write(
    *,
    target: str = "draft",
    approval_id: str = "CREATE_DRAFT",
    arguments: str = "detail = detail",
) -> str:
    return (
        f"    let {target} = write {TOOL_ID}({arguments}) "
        f"approved_by {approval_id};"
    )


def _approval(approval_id: str = "CREATE_DRAFT") -> str:
    return f'''    approve {approval_id} {{
        message "Create the draft?";
    }}'''


def _source(body: str, *, extra_inputs: str = "") -> str:
    return f'''language NSL "0.2";
skill FINANCE.STATIC_WRITE_SAFETY {{
    version "1.0.0";
    risk WRITE;
    requires {{
        tool {TOOL_ID} version "1.0.0";
    }}
    limits {{
        tool_calls 2;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }}
    input {{
        detail: String;
        approval_flag: Bool;
        count: Int;
        items: List<String>;
{extra_inputs}    }}
    output {{
        result: String;
    }}
{body}
}}
'''


def _compile(body: str, contract: ToolContract | None = None, *, extra_inputs: str = ""):
    source = SourceFile.from_text(
        "compiler/static_write_safety.ns",
        _source(body, extra_inputs=extra_inputs),
    )
    return NslCompiler(ToolContractCatalog((contract or _write_contract(),))).compile(
        source
    )


def _assert_error(
    body: str,
    code: DiagnosticCode,
    contract: ToolContract | None = None,
) -> CompileError:
    with pytest.raises(CompileError) as captured:
        _compile(body, contract)
    assert captured.value.code == code
    assert captured.value.location is not None
    assert captured.value.snippet is not None
    return captured.value


def test_typ_201_write_arguments_are_exactly_type_checked() -> None:
    result = _compile(_approval() + "\n" + _write())
    statement = result.skill.body[1]
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, WriteExpression)
    assert tuple(name for name, _ in statement.value.arguments) == ("detail",)

    error = _assert_error(
        _approval() + "\n" + _write(arguments="detail = approval_flag"),
        DiagnosticCode.SEM_TOOL_ARGUMENT_TYPE,
    )
    assert "FINANCE.CREATE_DRAFT.detail" in error.public_message


@pytest.mark.parametrize(
    "arguments",
    (
        "",
        "detail = detail, detail = detail",
        "detail = detail, extra = detail",
    ),
)
def test_typ_201_write_argument_shape_rejects_zero_duplicate_and_extra_values(
    arguments: str,
) -> None:
    _assert_error(
        _approval() + "\n" + _write(arguments=arguments),
        DiagnosticCode.SEM_TOOL_ARGUMENTS,
    )


def test_typ_202_write_result_type_comes_from_the_tool_contract() -> None:
    result = _compile(_approval() + "\n" + _write())
    statement = result.skill.body[1]
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, WriteExpression)
    assert statement.value.type_info == WRITE_RESULT
    assert result.skill.symbols[-1].type_info == WRITE_RESULT


def test_typ_203_approval_is_a_control_gate_not_bool_truthiness() -> None:
    result = _compile(
        _approval() + "\n" + _write(),
        extra_inputs="        CREATE_DRAFT: Bool;\n",
    )
    approval, statement = result.skill.body
    assert isinstance(approval, ApprovalStatement)
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, WriteExpression)
    assert statement.value.approval_ref == approval.approval_id

    _assert_error(
        _write(approval_id="approval_flag"),
        DiagnosticCode.SEM_UNKNOWN_APPROVAL,
    )


@pytest.mark.parametrize(
    ("assertion", "expected_code"),
    (
        ("approval_flag", None),
        ("count", DiagnosticCode.SEM_CHECK_CONDITION_TYPE),
        ("detail", DiagnosticCode.SEM_CHECK_CONDITION_TYPE),
    ),
)
def test_typ_204_check_requires_exact_bool_in_v0_2(
    assertion: str, expected_code: DiagnosticCode | None
) -> None:
    check = f'''    check READY {{
        assert {assertion};
        severity ERROR;
        on_fail REPORT;
        message "Ready?";
    }}'''
    body = check + "\n" + _approval() + "\n" + _write()
    if expected_code is None:
        result = _compile(body)
        assert isinstance(result.skill.body[0], CheckStatement)
    else:
        _assert_error(body, expected_code)


def test_wrt_002_zero_and_one_write_are_allowed() -> None:
    no_write = _compile("")
    assert no_write.skill.ir_version == "1.1"
    assert no_write.write_safety.write_node_count == 0
    assert no_write.write_safety.requires_verified_package_for_write is False

    one_write = _compile(_approval() + "\n" + _write())
    assert one_write.write_safety.write_node_count == 1
    assert one_write.write_safety.max_write_calls == 1
    assert one_write.write_safety.requires_verified_package_for_write is True


def test_wrt_002_two_writes_are_rejected_without_a_relaxation_option() -> None:
    body = (
        _approval("FIRST")
        + "\n"
        + _approval("SECOND")
        + "\n"
        + _write(target="first", approval_id="FIRST")
        + "\n"
        + _write(target="second", approval_id="SECOND")
    )
    _assert_error(body, DiagnosticCode.SEM_TOO_MANY_WRITES)
    parameters = signature(NslCompiler).parameters
    assert "max_write_nodes" not in parameters
    assert "allow_write_in_foreach" not in parameters


def test_wrt_003_write_inside_foreach_is_rejected() -> None:
    body = _approval() + '''
    foreach item in items max 1 {
''' + _write(arguments="detail = item") + '''
    }'''
    _assert_error(body, DiagnosticCode.SEM_WRITE_IN_FOREACH)


def test_wrt_004_non_atomic_contract_is_rejected_even_if_model_is_tampered() -> None:
    contract = _write_contract()
    assert contract.write_semantics is not None
    object.__setattr__(contract.write_semantics, "atomicity", "BEST_EFFORT")
    _assert_error(
        _approval() + "\n" + _write(),
        DiagnosticCode.SEM_WRITE_ATOMICITY_REQUIRED,
        contract,
    )


def test_wrt_004_missing_write_semantics_is_rejected_before_hashing() -> None:
    contract = _write_contract()
    object.__setattr__(contract, "write_semantics", None)
    _assert_error(
        _approval() + "\n" + _write(),
        DiagnosticCode.SEM_WRITE_ATOMICITY_REQUIRED,
        contract,
    )


def test_write_requires_write_capability_and_supported_operation() -> None:
    _assert_error(
        _approval() + "\n" + _write(),
        DiagnosticCode.SEM_WRITE_CAPABILITY_REQUIRED,
        _read_contract(),
    )

    contract = _write_contract()
    object.__setattr__(contract, "operation_kind", "DELETE")
    _assert_error(
        _approval() + "\n" + _write(),
        DiagnosticCode.SEM_UNSUPPORTED_WRITE_OPERATION,
        contract,
    )


def test_write_requires_idempotency_even_if_contract_is_tampered() -> None:
    contract = _write_contract()
    assert contract.write_semantics is not None
    object.__setattr__(contract.write_semantics.idempotency, "required", False)
    _assert_error(
        _approval() + "\n" + _write(),
        DiagnosticCode.SEM_WRITE_IDEMPOTENCY_REQUIRED,
        contract,
    )


def test_write_requires_structured_idempotency_metadata() -> None:
    contract = _write_contract()
    assert contract.write_semantics is not None
    object.__setattr__(contract.write_semantics, "idempotency", None)
    _assert_error(
        _approval() + "\n" + _write(),
        DiagnosticCode.SEM_WRITE_IDEMPOTENCY_REQUIRED,
        contract,
    )


@pytest.mark.parametrize("invalid_risk", (None, "R3", "R6"))
def test_rsk_002_invalid_risk_metadata_is_rejected_before_hashing(
    invalid_risk: object,
) -> None:
    contract = _write_contract()
    object.__setattr__(contract, "risk_level", invalid_risk)
    _assert_error(
        _approval() + "\n" + _write(),
        DiagnosticCode.SEM_WRITE_RISK_METADATA_REQUIRED,
        contract,
    )


@pytest.mark.parametrize("risk_level", (RiskLevel.R0, RiskLevel.R5))
def test_rsk_002_valid_boundary_risks_are_metadata_not_entitlement_decisions(
    risk_level: RiskLevel,
) -> None:
    contract = _write_contract(risk_level=risk_level)
    result = _compile(_approval() + "\n" + _write(), contract)
    required = result.skill.required_tools[0]
    assert required.risk_level is risk_level
    assert required.contract_hash == contract.contract_hash
    assert result.write_safety.all_write_risk_metadata_present is True
    parameters = signature(WriteSafetyAnalyzer.analyze).parameters
    assert "principal" not in parameters
    assert "tenant" not in parameters
    assert "entitlement" not in parameters


def test_write_safety_analysis_is_complete_and_immutable() -> None:
    result = _compile(_approval() + "\n" + _write())
    assert result.write_safety.approval_node_count == 1
    assert result.write_safety.approval_write_mapping_count == 1
    assert result.write_safety.unused_approval_count == 0
    assert result.write_safety.writes_inside_loop is False
    assert result.write_safety.all_writes_approved_exactly_once is True
    assert result.write_safety.all_write_operations_supported is True
    assert result.write_safety.all_write_tools_atomic is True
    assert result.write_safety.all_write_tools_idempotent is True
    assert result.skill.analysis.max_tool_calls == 1
    with pytest.raises(AttributeError):
        result.write_safety.write_node_count = 2  # type: ignore[misc]


def test_read_expression_cannot_invoke_a_write_contract() -> None:
    body = f"    let draft = read {TOOL_ID}(detail: detail);"
    _assert_error(body, DiagnosticCode.SEM_WRITE_TOOL_FORBIDDEN)


def test_write_tool_must_be_declared_in_requires() -> None:
    body = _approval() + "\n" + _write().replace(TOOL_ID, "FINANCE.UNDECLARED")
    _assert_error(body, DiagnosticCode.SEM_UNDECLARED_TOOL)
