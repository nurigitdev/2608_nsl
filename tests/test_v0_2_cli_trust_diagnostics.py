from __future__ import annotations

from base64 import b64encode
from io import StringIO
import json
from pathlib import Path

import pytest

from nsl.adapters.ed25519 import Ed25519PackageSigner
from nsl.cli import CLIExitCode, main
from nsl.cli_trust import MAX_TRUST_STORE_BYTES, load_ed25519_trust_store
from nsl.compiler import NslCompiler
from nsl.nsp import NspBuilder
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "voucher_draft_validation"


def _invoke(*arguments: str) -> tuple[int, dict, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, stdout=stdout, stderr=stderr)
    document = json.loads(stdout.getvalue() or stderr.getvalue())
    return code, document, stdout.getvalue(), stderr.getvalue()


def _package(tmp_path: Path, *, signed: bool = True):
    source = (ROOT / "examples" / "project_budget_check.ns").read_text(
        encoding="utf-8"
    )
    artifact = NslCompiler(build_tool_catalog()).compile(source).nso_bytes
    signer = Ed25519PackageSigner("publisher.cli-0067", bytes(range(1, 33)))
    package = NspBuilder().build((artifact,), signer=signer if signed else None)
    package_path = tmp_path / ("signed.nsp" if signed else "unsigned.nsp")
    package_path.write_bytes(package.data)
    trust_store = tmp_path / "trust-store.json"
    trust_store.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "keys": [
                    {
                        "key_id": signer.key_id,
                        "public_key_b64": b64encode(
                            signer.public_key_bytes
                        ).decode("ascii"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return package, package_path, trust_store, signer


def _trust_arguments(package: Path, trust_store: Path) -> tuple[str, ...]:
    return (
        str(package),
        "--trust-store",
        str(trust_store),
        "--publisher-id",
        "publisher.finance",
        "--trust-policy-id",
        "trust-policy-production",
    )


def test_cli_0067_verify_and_inspect_use_signed_nsp_trust_identity(tmp_path) -> None:
    package, package_path, trust_store, signer = _package(tmp_path)

    for command in ("verify", "inspect-signature"):
        code, payload, stdout, error = _invoke(
            command, *_trust_arguments(package_path, trust_store)
        )
        assert (code, error) == (CLIExitCode.SUCCESS, "")
        assert stdout.startswith("{\n  ")
        assert payload["command"] == command
        assert payload["status"] == "verified"
        assert payload["package"]["package_hash"] == package.package_hash
        assert payload["package"]["package_id"].startswith("nsp:sha256:")
        assert payload["signature"] == {
            "present": True,
            "status": "VERIFIED",
            "algorithm": "Ed25519",
            "signing_key_id": signer.key_id,
            "package_hash": package.package_hash,
        }
        assert payload["trust"]["publisher_id"] == "publisher.finance"
        assert payload["trust"]["trust_policy_id"] == "trust-policy-production"
        assert payload["artifacts"][0]["semantic_hash"].startswith("sha256:")
        assert "signature_b64" not in json.dumps(payload)


def test_cli_0067_unsigned_development_and_production_policies_are_explicit(
    tmp_path,
) -> None:
    _, unsigned, _, _ = _package(tmp_path, signed=False)
    common = (
        str(unsigned),
        "--publisher-id",
        "publisher.dev",
        "--trust-policy-id",
        "trust-policy-dev",
    )
    development = _invoke(
        "verify",
        *common,
        "--environment",
        "DEVELOPMENT",
        "--allow-unsigned-development",
    )
    assert development[0] == CLIExitCode.SUCCESS
    assert development[1]["status"] == "valid_unsigned_development"
    assert development[1]["signature"]["status"] == "UNSIGNED"
    assert development[1]["trust"]["status"] == "UNVERIFIED"

    production = _invoke("verify", *common)
    assert production[0] == CLIExitCode.VALIDATION_ERROR
    assert production[1]["error"]["code"] == "NSP_SIGNATURE_REQUIRED"

    signed, signed_path, _, _ = _package(tmp_path, signed=True)
    missing_verifier = _invoke(
        "verify",
        str(signed_path),
        "--publisher-id",
        "publisher.finance",
        "--trust-policy-id",
        "trust-policy-production",
    )
    assert signed.signature is not None
    assert missing_verifier[0] == CLIExitCode.VALIDATION_ERROR
    assert (
        missing_verifier[1]["error"]["code"]
        == "NSP_SIGNATURE_VERIFIER_REQUIRED"
    )


def test_cli_0067_trust_store_boundaries_and_bad_signature_are_safe(tmp_path) -> None:
    _, package_path, trust_store, _ = _package(tmp_path)
    valid = json.loads(trust_store.read_text(encoding="utf-8"))
    malformed = tmp_path / "malformed-trust.json"
    cases = (
        [],
        {"schema_version": "2.0", "keys": []},
        {"schema_version": "1.0"},
        {"schema_version": "1.0", "keys": "bad"},
        {
            "schema_version": "1.0",
            "keys": [valid["keys"][0], valid["keys"][0]],
        },
        {
            "schema_version": "1.0",
            "keys": [{"key_id": "key", "public_key_b64": "%%%"}],
        },
        {
            "schema_version": "1.0",
            "keys": [
                {
                    "key_id": "key",
                    "public_key_b64": b64encode(b"short").decode("ascii"),
                }
            ],
        },
    )
    for document in cases:
        malformed.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(ValueError):
            load_ed25519_trust_store(malformed)

    malformed.write_bytes(b" " * (MAX_TRUST_STORE_BYTES + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        load_ed25519_trust_store(malformed)

    wrong_signer = Ed25519PackageSigner("publisher.cli-0067", bytes(range(2, 34)))
    trust_store.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "keys": [
                    {
                        "key_id": wrong_signer.key_id,
                        "public_key_b64": b64encode(
                            wrong_signer.public_key_bytes
                        ).decode("ascii"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    code, payload, _, stderr = _invoke(
        "verify", *_trust_arguments(package_path, trust_store)
    )
    assert code == CLIExitCode.VALIDATION_ERROR
    assert payload["error"]["code"] == "NSP_INVALID_SIGNATURE"
    assert "traceback" not in stderr.lower()
    assert "private" not in stderr.lower()


def test_cli_0067_missing_malformed_and_tampered_packages_use_stable_errors(
    tmp_path,
) -> None:
    _, package_path, trust_store, _ = _package(tmp_path)
    missing = _invoke(
        "verify", *_trust_arguments(tmp_path / "missing.nsp", trust_store)
    )
    assert missing[0] == CLIExitCode.IO_ERROR
    assert missing[1]["error"]["code"] == "CLI_IO_ERROR"

    malformed = tmp_path / "malformed.nsp"
    malformed.write_bytes(b"not-a-zip")
    invalid = _invoke("verify", *_trust_arguments(malformed, trust_store))
    assert invalid[0] == CLIExitCode.VALIDATION_ERROR
    assert invalid[1]["error"]["code"] == "NSP_INVALID_ARCHIVE"

    tampered = bytearray(package_path.read_bytes())
    tampered[len(tampered) // 2] ^= 1
    package_path.write_bytes(tampered)
    changed = _invoke("inspect-signature", *_trust_arguments(package_path, trust_store))
    assert changed[0] == CLIExitCode.VALIDATION_ERROR
    assert changed[1]["error"]["code"].startswith("NSP_")


def test_cli_0067_production_controlled_mock_is_rejected_before_evidence(
    tmp_path,
) -> None:
    checkpoint = tmp_path / "production.checkpoint.json"
    approval = tmp_path / "production.approval.json"
    result = _invoke(
        "run",
        str(EXAMPLE / "voucher_draft_write.ns"),
        "--principal",
        str(EXAMPLE / "principal_write.json"),
        "--tool-contracts",
        str(EXAMPLE / "tools_write.json"),
        "--input",
        str(EXAMPLE / "input.json"),
        "--context",
        str(EXAMPLE / "context.json"),
        "--fixture",
        str(EXAMPLE / "fixtures" / "success.json"),
        "--execution-id",
        "production-mock-0067",
        "--business-operation-id",
        "business-production-mock-0067",
        "--checkpoint-out",
        str(checkpoint),
        "--approval-request-out",
        str(approval),
    )
    assert result[0] == CLIExitCode.EXECUTION_ERROR
    assert "explicit DEVELOPMENT profile" in result[1]["error"]["message"]
    assert checkpoint.exists() is False
    assert approval.exists() is False
