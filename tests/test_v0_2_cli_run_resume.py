from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path

import pytest

from nsl.cli import CLIExitCode, build_parser, main
from nsl.cli_config import load_tool_catalog
from nsl.cli_controlled import (
    FileControlledStatePort,
    FakeApprovalVerifier,
    create_fake_approval_evidence,
    load_approval_evidence,
    load_approval_request,
)
from nsl.checkpoints import ExecutionCheckpoint
from nsl.approval import ResumeClaimError
from nsl.cli_profile import load_profile_document
from nsl.compiler import NslCompiler
from nsl.ir import NsoCodec


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "voucher_draft_validation"
PROFILE = EXAMPLE / "controlled.profile.json"
EXECUTION_ID = "cli-run-resume-0066"
BUSINESS_ID = "business-cli-0066"


def _invoke(*arguments: str) -> tuple[int, dict, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    payload = json.loads(stdout.getvalue() or stderr.getvalue())
    return code, payload, stderr.getvalue()


def _suspend(tmp_path: Path, *, execution_id: str = EXECUTION_ID):
    checkpoint = tmp_path / "execution.checkpoint.json"
    approval = tmp_path / "approval.request.json"
    audit = tmp_path / "suspend.audit.jsonl"
    result = _invoke(
        "run",
        "--profile",
        str(PROFILE),
        "--execution-id",
        execution_id,
        "--business-operation-id",
        BUSINESS_ID,
        "--checkpoint-out",
        str(checkpoint),
        "--approval-request-out",
        str(approval),
        "--audit-out",
        str(audit),
    )
    return result, checkpoint, approval, audit


def test_cli_0066_preserves_commands_and_compiler_version_mapping(tmp_path) -> None:
    parser = build_parser()
    help_text = parser.format_help()
    for command in (
        "parse",
        "check",
        "compile",
        "inspect",
        "run",
        "resume",
        "replay",
        "test",
    ):
        assert command in help_text

    legacy_catalog = load_tool_catalog(
        ROOT / "examples" / "project_budget_check_cli" / "tools.json"
    )
    legacy = NslCompiler(legacy_catalog).compile(
        (ROOT / "examples" / "project_budget_check.ns").read_text(encoding="utf-8")
    ).skill
    catalog = load_tool_catalog(EXAMPLE / "tools_write.json")
    current = NslCompiler(catalog).compile(
        (EXAMPLE / "voucher_draft_write.ns").read_text(encoding="utf-8")
    ).skill
    assert (legacy.language_version, legacy.ir_version) == ("0.1", "1.0")
    assert (current.language_version, current.ir_version) == ("0.2", "1.1")

    output = tmp_path / "voucher.nso"
    code, payload, error = _invoke(
        "compile", "--profile", str(PROFILE), "-o", str(output)
    )
    assert (code, error) == (CLIExitCode.SUCCESS, "")
    assert payload["status"] == "compiled"
    assert NsoCodec.decode(output.read_bytes()).ir_version == "1.1"


def test_cli_0066_profile_requires_explicit_development_fake_approval(
    tmp_path,
) -> None:
    document = json.loads(PROFILE.read_text(encoding="utf-8"))
    assert load_profile_document(PROFILE).execution.fake_approval is True

    profile = tmp_path / "invalid.profile.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "program": "program.ns",
                "principal": "principal.json",
                "execution": {
                    "environment": "PRODUCTION",
                    "fake_approval": True,
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="requires DEVELOPMENT"):
        load_profile_document(profile)

    for environment in ("TEST", "", 1):
        changed = deepcopy(document)
        changed["execution"]["environment"] = environment
        profile.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError, match="PRODUCTION.*DEVELOPMENT"):
            load_profile_document(profile)


def test_cli_0066_controlled_run_suspends_with_durable_protected_references(
    tmp_path,
) -> None:
    (code, payload, error), checkpoint, approval, audit = _suspend(tmp_path)

    assert (code, error) == (CLIExitCode.SUCCESS, "")
    assert payload["result"]["status"] == "WAITING_FOR_APPROVAL"
    assert payload["result"]["write"] is None
    assert payload["profile_environment"] == "DEVELOPMENT"
    assert payload["fake_approval_enabled"] is True
    assert payload["protected_references"]["checkpoint"] == str(checkpoint)
    assert checkpoint.read_bytes() != b""
    request = load_approval_request(approval)
    assert request.execution_id == EXECUTION_ID
    assert request.business_operation_id == BUSINESS_ID
    assert request.protected_payload_ref.startswith("protected://cli/")
    events = [json.loads(line) for line in audit.read_text().splitlines()]
    assert events[-1]["event_type"] == "CLI_APPROVAL_SUSPENDED"
    assert events[-1]["payload"]["cli_profile_environment"] == "DEVELOPMENT"
    assert not any("amount" in json.dumps(item) for item in events)


def test_cli_0066_explicit_fake_resume_is_durable_single_consume_and_no_write(
    tmp_path,
) -> None:
    (_, _, _), checkpoint, approval, _ = _suspend(tmp_path)
    evidence = tmp_path / "approval.evidence.json"
    audit = tmp_path / "resume.audit.jsonl"
    result = _invoke(
        "resume",
        "--profile",
        str(PROFILE),
        "--execution-id",
        EXECUTION_ID,
        "--business-operation-id",
        BUSINESS_ID,
        "--checkpoint",
        str(checkpoint),
        "--approval-request",
        str(approval),
        "--fake-approval",
        "--approval-evidence-out",
        str(evidence),
        "--audit-out",
        str(audit),
    )
    code, payload, error = result
    assert (code, error) == (CLIExitCode.SUCCESS, "")
    assert payload["status"] == "ready_for_write"
    assert payload["write_execution"] == "not_started"
    assert payload["approval"] == {
        "status": "APPROVED",
        "validation": "VALID",
        "reason_code": None,
        "claim_consumed": True,
    }
    restored = load_approval_evidence(evidence)
    assert FakeApprovalVerifier().verify(restored) is True
    assert checkpoint.with_name(f"{checkpoint.name}.consumed").is_file()
    event = json.loads(audit.read_text().splitlines()[-1])
    assert event["payload"]["fake_approval_enabled"] is True

    duplicate = _invoke(
        "resume",
        "--profile",
        str(PROFILE),
        "--execution-id",
        EXECUTION_ID,
        "--business-operation-id",
        BUSINESS_ID,
        "--checkpoint",
        str(checkpoint),
        "--approval-request",
        str(approval),
        "--approval-evidence",
        str(evidence),
    )
    assert duplicate[0] == CLIExitCode.EXECUTION_ERROR
    assert duplicate[1]["error"]["message"] == "checkpoint already consumed"


def test_cli_0066_production_never_auto_approves(tmp_path) -> None:
    (_, _, _), checkpoint, approval, _ = _suspend(tmp_path)
    request = load_approval_request(approval)
    evidence = create_fake_approval_evidence(request, approver_id="test-approver")
    evidence_path = tmp_path / "external.evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "approval_evidence": evidence.to_data(),
            }
        ),
        encoding="utf-8",
    )
    common = (
        "resume",
        str(EXAMPLE / "voucher_draft_write.ns"),
        "--principal",
        str(EXAMPLE / "principal_write.json"),
        "--tool-contracts",
        str(EXAMPLE / "tools_write.json"),
        "--input",
        str(EXAMPLE / "input.json"),
        "--context",
        str(EXAMPLE / "context.json"),
        "--execution-id",
        EXECUTION_ID,
        "--business-operation-id",
        BUSINESS_ID,
        "--checkpoint",
        str(checkpoint),
        "--approval-request",
        str(approval),
    )
    fake = _invoke(*common, "--fake-approval")
    assert fake[0] == CLIExitCode.EXECUTION_ERROR
    assert "explicit DEVELOPMENT profile" in fake[1]["error"]["message"]

    external = _invoke(*common, "--approval-evidence", str(evidence_path))
    assert external[0] == CLIExitCode.SUCCESS
    assert external[1]["status"] == "blocked"
    assert external[1]["approval"]["validation"] == "INVALID"
    assert not checkpoint.with_name(f"{checkpoint.name}.consumed").exists()


def test_cli_0066_rejects_tampering_binding_errors_and_incomplete_options(
    tmp_path,
) -> None:
    dry = _invoke("run", "--profile", str(PROFILE), "--dry-run")
    assert dry[0] == CLIExitCode.SUCCESS
    assert dry[1]["status"] == "ready"

    incomplete = _invoke(
        "run",
        "--profile",
        str(PROFILE),
        "--business-operation-id",
        BUSINESS_ID,
    )
    assert incomplete[0] == CLIExitCode.USAGE_ERROR

    (_, _, _), checkpoint, approval, _ = _suspend(tmp_path)
    tampered = bytearray(checkpoint.read_bytes())
    tampered[-3] = ord("0") if tampered[-3] != ord("0") else ord("1")
    checkpoint.write_bytes(tampered)
    evidence = tmp_path / "approval.evidence.json"
    create = _invoke(
        "resume",
        "--profile",
        str(PROFILE),
        "--execution-id",
        EXECUTION_ID,
        "--business-operation-id",
        BUSINESS_ID,
        "--checkpoint",
        str(checkpoint),
        "--approval-request",
        str(approval),
        "--fake-approval",
        "--approval-evidence-out",
        str(evidence),
    )
    assert create[0] == CLIExitCode.EXECUTION_ERROR
    assert "traceback" not in json.dumps(create[1]).lower()

    binding = _invoke(
        "resume",
        "--profile",
        str(PROFILE),
        "--execution-id",
        "different-execution",
        "--business-operation-id",
        BUSINESS_ID,
        "--checkpoint",
        str(checkpoint),
        "--approval-request",
        str(approval),
        "--approval-evidence",
        str(evidence),
    )
    assert binding[0] in {CLIExitCode.IO_ERROR, CLIExitCode.VALIDATION_ERROR}


def test_cli_0066_prefix_failure_and_failed_check_do_not_publish_approval(
    tmp_path,
) -> None:
    for fixture, expected_status in (
        ("missing_account.json", "TOOL_ERROR"),
        ("invalid_account.json", "COMPLETED"),
    ):
        checkpoint = tmp_path / f"{fixture}.checkpoint.json"
        approval = tmp_path / f"{fixture}.approval.json"
        code, payload, error = _invoke(
            "run",
            "--profile",
            str(PROFILE),
            "--fixture",
            str(EXAMPLE / "fixtures" / fixture),
            "--execution-id",
            f"failure-{fixture}",
            "--business-operation-id",
            f"business-{fixture}",
            "--checkpoint-out",
            str(checkpoint),
            "--approval-request-out",
            str(approval),
        )
        assert (code, error) == (CLIExitCode.SUCCESS, "")
        assert payload["result"]["status"] == expected_status
        assert "protected_references" not in payload
        assert approval.exists() is False


def test_cli_0066_file_claims_fail_closed_for_identity_owner_and_collision(
    tmp_path,
) -> None:
    (_, _, _), checkpoint_path, approval_path, _ = _suspend(tmp_path)
    checkpoint = ExecutionCheckpoint.from_bytes(checkpoint_path.read_bytes())
    state = FileControlledStatePort(checkpoint_path, approval_path)

    with pytest.raises(ResumeClaimError, match="identity mismatch"):
        asyncio.run(
            state.claim("wrong-execution", checkpoint.checkpoint_version, "claim-a")
        )
    asyncio.run(
        state.claim(checkpoint.execution_id, checkpoint.checkpoint_version, "claim-a")
    )
    with pytest.raises(ResumeClaimError, match="live claim"):
        asyncio.run(
            state.claim(
                checkpoint.execution_id, checkpoint.checkpoint_version, "claim-b"
            )
        )
    with pytest.raises(ResumeClaimError, match="ownership mismatch"):
        asyncio.run(
            state.release(
                checkpoint.execution_id, checkpoint.checkpoint_version, "wrong-claim"
            )
        )
    asyncio.run(
        state.release(
            checkpoint.execution_id, checkpoint.checkpoint_version, "claim-a"
        )
    )
    with pytest.raises(ResumeClaimError, match="not active"):
        asyncio.run(
            state.release(
                checkpoint.execution_id, checkpoint.checkpoint_version, "claim-a"
            )
        )

    asyncio.run(
        state.claim(checkpoint.execution_id, checkpoint.checkpoint_version, "claim-c")
    )
    state.consumed_path.write_text("occupied", encoding="utf-8")
    with pytest.raises(ResumeClaimError, match="already consumed"):
        asyncio.run(
            state.consume(
                checkpoint.execution_id, checkpoint.checkpoint_version, "claim-c"
            )
        )


def test_cli_0066_wire_documents_are_strict_and_idempotent(tmp_path) -> None:
    (_, _, _), checkpoint, approval, _ = _suspend(tmp_path)
    state = FileControlledStatePort(checkpoint, approval)
    request = load_approval_request(approval)
    asyncio.run(state.publish(request))
    with pytest.raises(ValueError, match="durable evidence collision"):
        asyncio.run(state.publish(replace(request, message="changed")))

    original = json.loads(approval.read_text(encoding="utf-8"))
    malformed = tmp_path / "malformed.json"
    cases = (
        [],
        {"schema_version": "2.0", "approval_request": original["approval_request"]},
        {"schema_version": "1.0"},
        {**original, "unexpected": True},
    )
    for document in cases:
        malformed.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(ValueError):
            load_approval_request(malformed)

    for bad_time in (1, "not-a-time", "2026-10-21T00:00:00"):
        changed = deepcopy(original)
        changed["approval_request"]["expires_at"] = bad_time
        malformed.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError):
            load_approval_request(malformed)

    changed = deepcopy(original)
    changed["approval_request"]["protected_payload_ref"] = None
    malformed.write_text(json.dumps(changed), encoding="utf-8")
    assert load_approval_request(malformed).protected_payload_ref is None

    evidence = create_fake_approval_evidence(request, approver_id="test-approver")
    evidence_document = {
        "schema_version": "1.0",
        "approval_evidence": {**evidence.to_data(), "decision": "UNKNOWN"},
    }
    malformed.write_text(json.dumps(evidence_document), encoding="utf-8")
    with pytest.raises(ValueError, match="ApprovalDecision"):
        load_approval_evidence(malformed)
