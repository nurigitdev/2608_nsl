from __future__ import annotations

from base64 import b64decode
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapters.ed25519 import Ed25519TrustStore
from .data_protection import ensure_no_credential_material
from .ir_schema import NsoSchemaError, load_nso_json
from .nsp_verification import NspVerificationPolicy, NspVerifier
from .security import RuntimeEnvironment
from .trust import package_id_for


TRUST_STORE_SCHEMA_VERSION = "1.0"
MAX_TRUST_STORE_BYTES = 1024 * 1024


def load_ed25519_trust_store(path: Path | None) -> Ed25519TrustStore | None:
    if path is None:
        return None
    data = path.read_bytes()
    if len(data) > MAX_TRUST_STORE_BYTES:
        raise NsoSchemaError("$", "CLI trust store exceeds the 1 MiB limit")
    document = load_nso_json(data)
    ensure_no_credential_material(document, "CLI public-key trust store")
    root = _object(document, "$", {"schema_version", "keys"})
    if root["schema_version"] != TRUST_STORE_SCHEMA_VERSION:
        raise NsoSchemaError("$.schema_version", "expected '1.0'")
    keys: dict[str, bytes] = {}
    for index, value in enumerate(_array(root["keys"], "$.keys")):
        path_prefix = f"$.keys[{index}]"
        item = _object(value, path_prefix, {"key_id", "public_key_b64"})
        key_id = _string(item["key_id"], f"{path_prefix}.key_id")
        if key_id in keys:
            raise NsoSchemaError(path_prefix, "duplicate key_id")
        encoded = _string(
            item["public_key_b64"], f"{path_prefix}.public_key_b64"
        )
        try:
            public_key = b64decode(encoded, validate=True)
        except ValueError as error:
            raise NsoSchemaError(
                f"{path_prefix}.public_key_b64", "invalid base64"
            ) from error
        if len(public_key) != 32:
            raise NsoSchemaError(
                f"{path_prefix}.public_key_b64",
                "Ed25519 public key must be 32 bytes",
            )
        keys[key_id] = public_key
    return Ed25519TrustStore(keys)


def verify_nsp_for_cli(
    package_path: Path,
    *,
    environment: RuntimeEnvironment,
    allow_unsigned_development: bool,
    trust_store_path: Path | None,
    publisher_id: str,
    trust_policy_id: str,
) -> dict[str, Any]:
    policy = NspVerificationPolicy(
        environment,
        allow_unsigned_development=allow_unsigned_development,
    )
    trust_store = load_ed25519_trust_store(trust_store_path)
    package = NspVerifier(
        policy=policy,
        signature_verifier=trust_store,
    ).verify(package_path.read_bytes())
    signature = package.signature
    trust = (
        None
        if signature is None
        else package.trust_evidence(
            publisher_id=publisher_id,
            trust_policy_id=trust_policy_id,
            verified_at=datetime.now(UTC),
        )
    )
    return {
        "status": "verified" if trust is not None else "valid_unsigned_development",
        "package": {
            "package_id": package_id_for(package.package_hash),
            "package_hash": package.package_hash,
            "format": package.manifest.format,
            "schema_version": package.manifest.schema_version,
            "skill_count": len(package.skills),
        },
        "signature": {
            "present": signature is not None,
            "status": "VERIFIED" if signature is not None else "UNSIGNED",
            "algorithm": None if signature is None else signature.algorithm,
            "signing_key_id": None if signature is None else signature.key_id,
            "package_hash": (
                None if signature is None else signature.package_sha256
            ),
        },
        "trust": {
            "status": "UNVERIFIED" if trust is None else trust.trust_status.value,
            "publisher_id": publisher_id,
            "trust_policy_id": trust_policy_id,
            "signing_key_id": (
                None if trust is None else trust.signing_key_id
            ),
        },
        "policy": {
            "environment": environment.value,
            "allow_unsigned_development": allow_unsigned_development,
        },
        "artifacts": [
            {
                "path": item.manifest.path,
                "skill_id": item.skill.skill_id,
                "skill_version": item.skill.skill_version,
                "artifact_hash": item.manifest.artifact_sha256,
                "semantic_hash": item.skill.semantic_hash,
                "ir_version": item.skill.ir_version,
                "language_version": item.skill.language_version,
            }
            for item in package.skills
        ],
    }


def _object(value: Any, path: str, required: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise NsoSchemaError(path, "expected object")
    missing = required - set(value)
    if missing:
        raise NsoSchemaError(path, f"missing fields: {sorted(missing)}")
    unexpected = set(value) - required
    if unexpected:
        raise NsoSchemaError(path, f"unexpected fields: {sorted(unexpected)}")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if type(value) is not list:
        raise NsoSchemaError(path, "expected array")
    return value


def _string(value: Any, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise NsoSchemaError(path, "expected non-empty string")
    return value
