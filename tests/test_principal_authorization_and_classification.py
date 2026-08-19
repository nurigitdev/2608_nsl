from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from nsl.audit import InMemoryAuditSink
from nsl.compiler import NslCompiler
from nsl.core import DataClassification, ExecutionStatus
from nsl.diagnostics import Diagnostic, DiagnosticCode, DiagnosticPhase, CompileError
from nsl.data_protection import (
    REDACTED,
    CredentialMaterialError,
    collect_sensitive_text,
    ensure_no_credential_material,
    find_credential_path,
    is_credential_key,
    redact_data,
    redact_text,
)
from nsl.runtime import ExecutionRequest, RuntimeEngine
from nsl.ir import NsoCodec
from nsl.security import RuntimeEnvironment
from nsl.vertical_slice import (
    build_mock_executor,
    build_principal,
    build_tool_catalog,
)
from nsl.tools import ToolExecutionError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def execute_with_principal(principal: object, *, development: bool = False):
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    executor = build_mock_executor(catalog)
    audit = InMemoryAuditSink()
    request = ExecutionRequest(
        execution_id="exec-sec-012",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=principal,
    )
    engine = RuntimeEngine(
        catalog,
        environment=(
            RuntimeEnvironment.DEVELOPMENT
            if development
            else RuntimeEnvironment.PRODUCTION
        ),
    )
    result = asyncio.run(engine.execute(skill, request, executor, audit))
    return result, executor, audit


@pytest.mark.parametrize(
    ("key", "expected"),
    (
        ("password", True),
        ("access_token", True),
        ("API-Key", True),
        ("auth_context_ref", False),
        ("project_token_count", False),
    ),
)
def test_data_protection_credential_key_boundaries(
    key: str, expected: bool
) -> None:
    assert is_credential_key(key) is expected


@pytest.mark.parametrize(
    "value",
    (
        {"password": "open-sesame"},
        {"nested": [{"access_token": "token-value"}]},
        "Authorization: Bearer header.payload.signature",
        "-----BEGIN PRIVATE KEY-----\nmaterial\n-----END PRIVATE KEY-----",
        "eyJheader.payload.signature",
    ),
)
def test_data_protection_finds_and_rejects_credential_material(value: object) -> None:
    assert find_credential_path(value) is not None
    with pytest.raises(CredentialMaterialError, match="credential material is forbidden"):
        ensure_no_credential_material(value, "test surface")


def test_data_protection_redacts_structured_and_text_values() -> None:
    payload = {
        "password": "open-sesame",
        "message": "Authorization: Bearer abc.def.ghi",
        "nested": ["api_key=key-123", ("ordinary", 1)],
    }

    redacted = redact_data(payload)

    assert redacted == {
        "password": REDACTED,
        "message": f"Authorization: {REDACTED}",
        "nested": [f"api_key={REDACTED}", ("ordinary", 1)],
    }
    assert redact_text(
        "provider failed for PROJECT-SECRET",
        ("PROJECT-SECRET",),
    ) == f"provider failed for {REDACTED}"
    assert redact_text("ordinary", ("",)) == "ordinary"
    assert redact_data({"password=raw-key": "value"}) == {
        f"password={REDACTED}": "value"
    }


def test_data_protection_collects_unique_nested_sensitive_text() -> None:
    assert collect_sensitive_text(
        {"items": ["PARENT-001", {"code": "PARENT-001"}], "next": "PARENT-002"}
    ) == ("PARENT-001", "PARENT-002")
    assert collect_sensitive_text(1) == ()
    assert collect_sensitive_text(
        [Decimal("1234.50"), date(2026, 8, 20), 1234, 123.5, True]
    ) == ("1234.50", "2026-08-20", "1234", "123.5")


def test_data_protection_clean_and_empty_credential_boundaries() -> None:
    clean = {"token": "", "password": None, "items": [1, {"value": "ordinary"}]}

    assert find_credential_path(clean) is None
    assert find_credential_path((1, "ordinary")) is None
    assert find_credential_path(1) is None
    assert ensure_no_credential_material(clean, "test surface") is None
    assert find_credential_path({"password=raw-key": "value"}) == f"$.{REDACTED}"


@pytest.mark.parametrize(
    "principal",
    (
        None,
        build_principal(verified=False),
        replace(build_principal(), tenant_id=""),
    ),
)
def test_sec_012_production_rejects_missing_unverified_or_malformed_principal(
    principal: object,
) -> None:
    result, executor, audit = execute_with_principal(principal)

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code == "NSL-E5201"
    assert result.completeness.value == "UNKNOWN"
    assert executor.call_count == 0
    assert [event.event_type for event in audit.events] == ["EXECUTION_REJECTED"]


def test_sec_012_production_rejects_credential_in_principal_reference() -> None:
    principal = replace(
        build_principal(), auth_context_ref="Authorization: Bearer raw-principal-token"
    )

    result, executor, audit = execute_with_principal(principal)

    exposed = result.to_json() + repr(audit.events)
    assert result.status is ExecutionStatus.FAILED
    assert executor.call_count == 0
    assert "raw-principal-token" not in exposed


def test_sec_012_production_accepts_verified_principal() -> None:
    result, executor, audit = execute_with_principal(build_principal())

    assert result.status is ExecutionStatus.COMPLETED
    assert executor.call_count == 2
    started = next(event for event in audit.events if event.event_type == "EXECUTION_STARTED")
    assert started.payload["tenant_id"] == "tenant-nex"
    assert started.payload["subject_id"] == "user-finance-001"
    assert started.payload["authorization_decision_ref"].startswith("authz-")


def test_sec_014_missing_skill_scope_is_rejected_before_context_and_provider() -> None:
    principal = build_principal()
    principal = replace(
        principal,
        scopes=principal.scopes - {"nsl:skill:execute"},
    )

    with patch(
        "nsl.runtime.ExecutionContext",
        side_effect=AssertionError("context created before skill authorization"),
    ):
        result, executor, audit = execute_with_principal(principal)

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.code == "NSL-E5201"
    assert executor.call_count == 0
    assert [event.event_type for event in audit.events] == ["EXECUTION_REJECTED"]


def test_sec_012_development_requires_structure_but_not_verification() -> None:
    result, executor, _ = execute_with_principal(
        build_principal(verified=False), development=True
    )

    assert result.status is ExecutionStatus.COMPLETED
    assert executor.call_count == 2

    with pytest.raises(ValueError, match="RuntimeEnvironment"):
        RuntimeEngine(build_tool_catalog(), environment="PRODUCTION")


@pytest.mark.parametrize(
    ("field", "value", "surface"),
    (
        ("inputs", {"access_token": "raw-token"}, "ExecutionRequest.inputs"),
        (
            "runtime_context",
            {"session": {"authorization": "Bearer raw-token"}},
            "ExecutionRequest.runtime_context",
        ),
        ("inputs", {"note": "Bearer raw-token"}, "ExecutionRequest.inputs"),
    ),
)
def test_sec_016_execution_request_rejects_raw_credentials(
    field: str, value: dict, surface: str
) -> None:
    arguments = {
        "execution_id": "exec-sec-016",
        "inputs": {"year": 2026},
        "runtime_context": {"user": {"team_id": "TEAM-FINANCE"}},
        "principal": build_principal(),
    }
    arguments[field] = value

    with pytest.raises(CredentialMaterialError, match=surface):
        ExecutionRequest(**arguments)


@pytest.mark.parametrize("field", ("inputs", "runtime_context"))
def test_sec_016_execution_request_requires_structured_mappings(field: str) -> None:
    arguments = {
        "execution_id": "exec-sec-016-shape",
        "inputs": {"year": 2026},
        "runtime_context": {},
        "principal": build_principal(),
    }
    arguments[field] = []

    with pytest.raises(ValueError, match=f"{field} must be a mapping"):
        ExecutionRequest(**arguments)


def test_sec_016_execution_request_rejects_credential_in_execution_id() -> None:
    with pytest.raises(CredentialMaterialError, match="ExecutionRequest.execution_id"):
        ExecutionRequest(
            execution_id="password=raw-execution-secret",
            inputs={},
            runtime_context={},
            principal=build_principal(),
        )


def test_sec_016_nso_encode_and_decode_reject_raw_credentials() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    credential_skill = replace(
        skill,
        body=(
            skill.body[0],
            replace(
                skill.body[1],
                body=(
                    *skill.body[1].body[:-2],
                    replace(
                        skill.body[1].body[-2],
                        message="Authorization: Bearer raw-token",
                    ),
                    skill.body[1].body[-1],
                ),
            ),
        ),
    ).with_computed_hash()

    with pytest.raises(CredentialMaterialError, match=r"forbidden in \.nso"):
        NsoCodec.encode(credential_skill)

    payload = json.loads(NsoCodec.encode(skill))
    payload["body"][1]["body"][-2]["message"] = "password=raw-password"
    with pytest.raises(CredentialMaterialError, match=r"forbidden in \.nso"):
        NsoCodec.decode(json.dumps(payload).encode("utf-8"))


def test_sec_016_audit_never_stores_raw_credentials() -> None:
    from nsl.audit import AuditRecorder
    from nsl.security import DataHandlingPolicy

    audit = InMemoryAuditSink()
    AuditRecorder(audit, DataHandlingPolicy()).emit(
        "CREDENTIAL_TEST",
        {
            "access_token": "raw-token",
            "provider_message": "Authorization: Bearer raw-token",
        },
    )

    rendered = repr(audit.events)
    assert "raw-token" not in rendered
    assert audit.events[0].payload == {
        "access_token": REDACTED,
        "provider_message": f"Authorization: {REDACTED}",
    }


def test_sec_016_runtime_storage_models_declare_no_credential_fields() -> None:
    from dataclasses import fields

    from nsl.runtime_models import ExecutionContext
    from nsl.tools import ToolCallRequest

    forbidden = ("credential", "password", "secret", "token")
    for model in (ExecutionContext, ToolCallRequest):
        names = {field.name.lower() for field in fields(model)}
        assert not any(marker in name for name in names for marker in forbidden)


def test_sec_021_diagnostic_redacts_message_snippet_and_path() -> None:
    diagnostic = Diagnostic(
        DiagnosticCode.PAR_EXPECTED_TOKEN,
        DiagnosticPhase.PARSER,
        "Authorization: Bearer raw-message",
        snippet="password=raw-snippet",
        logical_path="skills/token=raw-path.ns",
    )

    rendered = repr(diagnostic) + str(CompileError(diagnostic))
    assert "raw-message" not in rendered
    assert "raw-snippet" not in rendered
    assert "raw-path" not in rendered
    assert rendered.count(REDACTED) >= 3


def test_sec_021_lexer_diagnostic_never_exposes_source_credential() -> None:
    source = SOURCE + "\n@ password=raw-source-secret\n"

    with pytest.raises(CompileError) as captured:
        NslCompiler(build_tool_catalog()).compile(source)

    error = captured.value
    assert error.snippet is not None
    assert "raw-source-secret" not in str(error)
    assert REDACTED in error.snippet


def test_sec_021_tool_failure_redacts_credentials_and_classified_values() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    delegate = build_mock_executor(catalog)

    class SensitiveFailureExecutor:
        async def execute(self, request):
            if request.tool_id == "PROJECT.LIST_CHILD_PROJECTS":
                raise ToolExecutionError(
                    "password=raw-code",
                    "provider failed for PARENT-001; Authorization: Bearer raw-token",
                )
            return await delegate.execute(request)

    audit = InMemoryAuditSink()
    request = ExecutionRequest(
        execution_id="exec-sec-021-tool",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )
    result = asyncio.run(
        RuntimeEngine(catalog).execute(skill, request, SensitiveFailureExecutor(), audit)
    )

    exposed = result.to_json() + repr(audit.events)
    for secret in ("PARENT-001", "raw-code", "raw-token"):
        assert secret not in exposed
    assert result.error is not None
    assert result.error.message == (
        f"provider failed for {REDACTED}; Authorization: {REDACTED}"
    )
    assert result.error.detail_code == f"password={REDACTED}"


def test_sec_021_debug_trace_redacts_credentials_and_classified_values() -> None:
    catalog = build_tool_catalog()
    skill = NslCompiler(catalog).compile(SOURCE).skill
    delegate = build_mock_executor(catalog)

    class UnexpectedSensitiveFailureExecutor:
        async def execute(self, request):
            if request.tool_id == "PROJECT.LIST_CHILD_PROJECTS":
                raise RuntimeError(
                    "PARENT-001 failed with access_token=raw-debug-token"
                )
            return await delegate.execute(request)

    traces: list[str] = []
    request = ExecutionRequest(
        execution_id="exec-sec-021-debug",
        inputs={"year": 2026},
        runtime_context={"user": {"team_id": "TEAM-FINANCE"}},
        principal=build_principal(),
    )
    result = asyncio.run(
        RuntimeEngine(
            catalog,
            debug_mode=True,
            debug_trace_sink=traces.append,
        ).execute(
            skill,
            request,
            UnexpectedSensitiveFailureExecutor(),
            InMemoryAuditSink(),
        )
    )

    assert result.error is not None
    assert result.error.message == "An unexpected runtime error occurred."
    assert len(traces) == 1
    assert "PARENT-001" not in traces[0]
    assert "raw-debug-token" not in traces[0]
    assert REDACTED in traces[0]


def test_sec_017_data_classification_is_a_closed_four_level_lattice() -> None:
    assert tuple(item.value for item in DataClassification) == (
        "PUBLIC",
        "INTERNAL",
        "CONFIDENTIAL",
        "RESTRICTED",
    )
