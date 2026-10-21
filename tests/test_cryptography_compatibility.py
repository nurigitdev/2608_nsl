from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import tomllib

import pytest

from nsl.adapters.ed25519 import Ed25519PackageSigner, Ed25519TrustStore
from nsl.nsp_signatures import NSP_SIGNATURE_ALGORITHM


ROOT = Path(__file__).resolve().parents[1]
NEX_PLATFORM_CRYPTOGRAPHY_VERSION = "46.0.7"
APPROVED_RUNTIME_DEPENDENCY = "cryptography>=46.0.7,<47.0.0"


def test_slice_0071_dependency_contract_matches_nex_platform_baseline() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    development_requirements = (ROOT / "requirements-dev.txt").read_text(
        encoding="utf-8"
    ).splitlines()

    assert project["project"]["dependencies"] == [APPROVED_RUNTIME_DEPENDENCY]
    assert f"cryptography=={NEX_PLATFORM_CRYPTOGRAPHY_VERSION}" in development_requirements
    assert version("cryptography") == NEX_PLATFORM_CRYPTOGRAPHY_VERSION


def test_slice_0071_ed25519_round_trip_on_nex_platform_baseline() -> None:
    signer = Ed25519PackageSigner("nex-platform-key", bytes(range(32)))
    trust_store = Ed25519TrustStore(
        {"nex-platform-key": signer.public_key_bytes}
    )
    message = b"NSL v0.2 cryptography 46.0.7 compatibility"
    signature = signer.sign(message)

    assert len(signature) == 64
    assert trust_store.verify(
        algorithm=NSP_SIGNATURE_ALGORITHM,
        key_id="nex-platform-key",
        message=message,
        signature=signature,
    )
    assert not trust_store.verify(
        algorithm=NSP_SIGNATURE_ALGORITHM,
        key_id="nex-platform-key",
        message=message + b"-tampered",
        signature=signature,
    )


@pytest.mark.parametrize("signature_size", [0, 1, 63, 65, 1024])
def test_slice_0071_invalid_signature_size_boundaries_fail_closed(
    signature_size: int,
) -> None:
    signer = Ed25519PackageSigner("nex-platform-key", bytes(range(32)))
    trust_store = Ed25519TrustStore(
        {"nex-platform-key": signer.public_key_bytes}
    )

    assert not trust_store.verify(
        algorithm=NSP_SIGNATURE_ALGORITHM,
        key_id="nex-platform-key",
        message=b"message",
        signature=b"x" * signature_size,
    )
