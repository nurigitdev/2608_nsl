from __future__ import annotations

from hashlib import sha256
from inspect import signature
import json
from pathlib import Path

import pytest

from nsl import NslCompiler, SourceFile
from nsl.core import DataClassification, RiskLevel, STRING, ToolCapability, domain
from nsl.ir import ApprovalStatement, LetStatement, NsoCodec, WriteExpression
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
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
V0_1_SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)
V0_1_SEMANTIC_HASH = (
    "sha256:ba75f7b96d08f0a8bff6ff1917b127c36cf2a0c96f1d29b7dc883674a4678b56"
)
V0_1_NSO_SHA256 = "35c8201ff8e55f22cd55f3fa677182a800b5452099a79cfe9e450792bb33337c"
WRITE_RESULT = domain("DraftRecord")


def _empty_source(language_version: str) -> SourceFile:
    return SourceFile.from_text(
        "compiler/version_mapping.ns",
        f'''language NSL "{language_version}";
skill TEST.VERSION_MAPPING {{
    version "1.0.0";
    risk READ_ONLY;
    limits {{
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }}
    output {{
        result: String;
    }}
}}
''',
    )


def _write_contract() -> ToolContract:
    return ToolContract(
        tool_id="FINANCE.CREATE_DRAFT",
        version="1.0.0",
        capability=ToolCapability.WRITE,
        input_types=(("detail", STRING),),
        output_type=WRITE_RESULT,
        required_scope="finance:draft:write",
        output_classification=DataClassification.CONFIDENTIAL,
        operation_kind=OperationKind.CREATE,
        risk_level=RiskLevel.R3,
        write_semantics=WriteSemantics(
            side_effect=True,
            atomicity=Atomicity.ATOMIC,
            idempotency=IdempotencySemantics(
                required=True,
                scope="TOOL_AND_TENANT",
                retention="PROVIDER_DEFINED",
                duplicate_behavior="RETURN_ORIGINAL_RESULT",
                payload_conflict_behavior="IDEMPOTENCY_CONFLICT",
            ),
            commit=CommitSemantics(CommitConfirmation.EXPLICIT),
            approval_required=True,
            preconditions_supported=True,
        ),
    )


def _write_source() -> SourceFile:
    return SourceFile.from_text(
        "compiler/write_lowering.ns",
        '''language NSL "0.2";
skill FINANCE.WRITE_LOWERING {
    version "1.0.0";
    risk WRITE;
    requires {
        tool FINANCE.CREATE_DRAFT version "1.0.0";
    }
    limits {
        tool_calls 1;
        loop_iterations 1;
        emitted_rows 1;
        collection_size 1;
    }
    input {
        detail: String;
    }
    output {
        result: String;
    }
    approve CREATE_DRAFT {
        message "Create draft?";
    }
    let draft = write FINANCE.CREATE_DRAFT(
        detail = detail
    ) approved_by CREATE_DRAFT;
}
''',
    )


def test_ir_206_language_0_1_compiler_output_matches_the_frozen_golden() -> None:
    compiler = NslCompiler(build_tool_catalog())
    first = compiler.compile(V0_1_SOURCE)
    second = compiler.compile(V0_1_SOURCE)

    assert first.skill.language_version == "0.1"
    assert first.skill.ir_version == "1.0"
    assert first.skill.semantics_profile == "NSL-0.1-STRICT"
    assert first.semantic_hash == V0_1_SEMANTIC_HASH
    assert sha256(first.nso_bytes).hexdigest() == V0_1_NSO_SHA256
    assert first.nso_bytes == second.nso_bytes
    assert first.semantic_hash == second.semantic_hash


def test_ir_206_language_0_2_without_write_still_lowers_to_ir_1_1() -> None:
    result = NslCompiler(ToolContractCatalog(())).compile(_empty_source("0.2"))
    payload = json.loads(result.nso_bytes)

    assert result.skill.language_version == "0.2"
    assert result.skill.ir_version == "1.1"
    assert result.skill.semantics_profile == "NSL-0.2-STRICT"
    assert {"APPROVAL", "WRITE"}.issubset(result.skill.features)
    assert payload["language"]["version"] == "0.2"
    assert payload["ir_version"] == "1.1"
    assert NsoCodec.decode(result.nso_bytes) == result.skill


def test_ir_206_approval_and_write_lower_deterministically_to_ir_1_1() -> None:
    compiler = NslCompiler(ToolContractCatalog((_write_contract(),)))
    first = compiler.compile(_write_source())
    second = compiler.compile(_write_source())
    approval, statement = first.skill.body

    assert isinstance(approval, ApprovalStatement)
    assert approval.node_id == "approval0001"
    assert isinstance(statement, LetStatement)
    assert isinstance(statement.value, WriteExpression)
    assert statement.value.node_id == "write0001"
    assert statement.value.approval_ref == approval.approval_id
    assert statement.value.type_info == WRITE_RESULT
    assert first.skill.required_tools[0].risk_level is RiskLevel.R3
    assert first.skill.analysis.max_tool_calls == 1
    assert first.nso_bytes == second.nso_bytes
    assert first.semantic_hash == second.semantic_hash


def test_ir_207_source_language_is_authoritative_without_compiler_override() -> None:
    constructor_parameters = signature(NslCompiler).parameters
    compile_parameters = signature(NslCompiler.compile).parameters
    assert "language_version" not in constructor_parameters
    assert "source_language_version" not in constructor_parameters
    assert "language_version" not in compile_parameters
    assert "source_language_version" not in compile_parameters

    compiler = NslCompiler(ToolContractCatalog(()))
    v0_1 = compiler.compile(_empty_source("0.1"))
    v0_2 = compiler.compile(_empty_source("0.2"))
    assert (v0_1.skill.language_version, v0_1.skill.ir_version) == ("0.1", "1.0")
    assert (v0_2.skill.language_version, v0_2.skill.ir_version) == ("0.2", "1.1")
    assert v0_1.semantic_hash != v0_2.semantic_hash

    with pytest.raises(TypeError, match="language_version"):
        NslCompiler(  # type: ignore[call-arg]
            ToolContractCatalog(()), language_version="0.1"
        )
