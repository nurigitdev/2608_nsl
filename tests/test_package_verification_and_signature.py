from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest
from cryptography.exceptions import UnsupportedAlgorithm

from nsl.adapters.ed25519 import Ed25519PackageSigner, Ed25519TrustStore
from nsl.compiler import NslCompiler
from nsl.ir import canonical_json
from nsl.nsp import NspBuildError, NspBuilder, NspManifest, NspSkillManifest
from nsl.nsp_signatures import (
    NSP_SIGNATURE_ALGORITHM,
    NspSignatureError,
    NspSignatureMetadata,
    NspSignatureVerifier,
    NspSigner,
    decode_signature_metadata,
    encode_signature_metadata,
    signature_message,
)
from nsl.nsp_verification import (
    NspVerificationCode,
    NspVerificationError,
    NspVerificationLimits,
    NspVerificationPolicy,
    NspVerifier,
)
from nsl.security import RuntimeEnvironment
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)
NSO = NslCompiler(build_tool_catalog()).compile(SOURCE).nso_bytes
NSP = NspBuilder().build((NSO,))
DEVELOPMENT_UNSIGNED = NspVerificationPolicy(
    RuntimeEnvironment.DEVELOPMENT,
    allow_unsigned_development=True,
)


class DeterministicSigner:
    algorithm = NSP_SIGNATURE_ALGORITHM
    key_id = "publisher.test-key-1"

    def __init__(self, signature: bytes = b"S" * 64) -> None:
        self.signature = signature
        self.messages: list[bytes] = []

    def sign(self, message: bytes) -> bytes:
        self.messages.append(message)
        return self.signature


class RecordingSignatureVerifier:
    def __init__(self, result: object = True) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def verify(self, **request) -> bool:
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result  # type: ignore[return-value]


def _append_member(
    package: bytes,
    name: str,
    data: bytes = b"x",
    *,
    compression: int = ZIP_STORED,
    external_attr: int | None = None,
) -> bytes:
    stream = BytesIO(package)
    with ZipFile(stream, mode="a") as archive:
        member = ZipInfo(name)
        member.compress_type = compression
        if external_attr is not None:
            member.create_system = 3
            member.external_attr = external_attr
        archive.writestr(member, data)
    return stream.getvalue()


def _assert_code(data: bytes, code: NspVerificationCode) -> None:
    with pytest.raises(NspVerificationError) as raised:
        NspVerifier(policy=DEVELOPMENT_UNSIGNED).verify(data)
    assert raised.value.code is code


def _manifest_payload() -> dict:
    with ZipFile(BytesIO(NSP.data)) as archive:
        return json.loads(archive.read("manifest.json"))


def _recompute_package_hash(payload: dict) -> None:
    skills = tuple(NspSkillManifest(**item) for item in payload["skills"])
    manifest = NspManifest(
        format=payload["format"],
        schema_version=payload["schema_version"],
        skills=skills,
        package_sha256="",
    )
    payload["package_sha256"] = manifest.computed_package_hash()


def _package_with(
    manifest: bytes,
    *,
    artifact: bytes | None = NSO,
) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, mode="w", compression=ZIP_STORED) as archive:
        archive.writestr("manifest.json", manifest)
        if artifact is not None:
            archive.writestr("skills/0001.nso", artifact)
    return stream.getvalue()


def _set_first_central_flag(package: bytes, flag: int) -> bytes:
    data = bytearray(package)
    central = data.find(b"PK\x01\x02")
    assert central >= 0
    current = int.from_bytes(data[central + 8 : central + 10], "little")
    data[central + 8 : central + 10] = (current | flag).to_bytes(2, "little")
    return bytes(data)


def test_sec_010_valid_package_is_verified_without_extraction() -> None:
    verified = NspVerifier(policy=DEVELOPMENT_UNSIGNED).verify(NSP.data)

    assert verified.package_hash == NSP.package_hash
    assert len(verified.skills) == 1
    assert verified.skills[0].artifact == NSO
    assert verified.skills[0].skill.skill_id == "FINANCE.PROJECT_BUDGET_CHECK"


@pytest.mark.parametrize(
    "path",
    [
        "../escape.nso",
        "/absolute.nso",
        "C:/drive.nso",
        "skills//empty.nso",
        "skills/./dot.nso",
        "skills/../escape.nso",
        "skills/한글.nso",
        "",
    ],
)
def test_sec_010_traversal_and_noncanonical_member_paths_are_rejected(path) -> None:
    _assert_code(
        _append_member(NSP.data, path),
        NspVerificationCode.UNSAFE_MEMBER_PATH,
    )


def test_sec_010_duplicate_directory_symlink_and_compression_are_rejected() -> None:
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = _append_member(NSP.data, "manifest.json")
    _assert_code(duplicate, NspVerificationCode.INVALID_ARCHIVE)
    _assert_code(
        _append_member(NSP.data, "skills/"),
        NspVerificationCode.UNSAFE_MEMBER_PATH,
    )
    _assert_code(
        _append_member(
            NSP.data,
            "skills/link.nso",
            external_attr=0o120777 << 16,
        ),
        NspVerificationCode.UNSAFE_MEMBER_PATH,
    )
    _assert_code(
        _append_member(
            NSP.data,
            "skills/compressed.nso",
            compression=ZIP_DEFLATED,
        ),
        NspVerificationCode.INVALID_ARCHIVE,
    )
    _assert_code(
        _append_member(NSP.data, "metadata.json"),
        NspVerificationCode.CONTENT_MISMATCH,
    )


def test_sec_010_backslash_nul_empty_and_encrypted_paths_are_rejected() -> None:
    normalized = _append_member(NSP.data, "skills/backslash.nso")
    backslash = normalized.replace(
        b"skills/backslash.nso",
        b"skills\\backslash.nso",
    )
    _assert_code(backslash, NspVerificationCode.UNSAFE_MEMBER_PATH)

    for path in ("", "skills/evil\x00.nso"):
        with pytest.raises(NspVerificationError) as raised:
            NspVerifier._validate_member_path(path)
        assert raised.value.code is NspVerificationCode.UNSAFE_MEMBER_PATH

    _assert_code(
        _set_first_central_flag(NSP.data, 0x1),
        NspVerificationCode.INVALID_ARCHIVE,
    )


def test_sec_010_missing_manifest_and_strict_manifest_schema_are_rejected() -> None:
    stream = BytesIO()
    with ZipFile(stream, mode="w") as archive:
        archive.writestr("other.json", b"{}")
    _assert_code(stream.getvalue(), NspVerificationCode.INVALID_ARCHIVE)

    invalid_roots = (b"[]", b"{", b'{"format":NaN}')
    for manifest in invalid_roots:
        _assert_code(
            _package_with(manifest, artifact=None),
            NspVerificationCode.INVALID_MANIFEST,
        )

    mutations = (
        lambda value: value.pop("format"),
        lambda value: value.update(extra=True),
        lambda value: value.update(format="BAD"),
        lambda value: value.update(schema_version="2.0"),
        lambda value: value.update(skills={}),
        lambda value: value.update(skills=[]),
    )
    for mutate in mutations:
        payload = _manifest_payload()
        mutate(payload)
        _assert_code(
            _package_with(canonical_json(payload), artifact=None),
            NspVerificationCode.INVALID_MANIFEST,
        )


def test_sec_010_manifest_field_boundaries_are_rejected() -> None:
    mutations = (
        ("path", "skills/0002.nso"),
        ("skill_id", None),
        ("skill_version", ""),
        ("semantic_sha256", "sha256:BAD"),
        ("artifact_sha256", None),
        ("size_bytes", True),
        ("size_bytes", 0),
    )
    for field, value in mutations:
        payload = _manifest_payload()
        payload["skills"][0][field] = value
        expected = (
            NspVerificationCode.UNSAFE_MEMBER_PATH
            if field == "path" and ".." in str(value)
            else NspVerificationCode.INVALID_MANIFEST
        )
        _assert_code(_package_with(canonical_json(payload)), expected)

    payload = _manifest_payload()
    payload["skills"][0].pop("skill_id")
    _assert_code(
        _package_with(canonical_json(payload)),
        NspVerificationCode.INVALID_MANIFEST,
    )
    payload = _manifest_payload()
    payload["skills"][0]["extra"] = True
    _assert_code(
        _package_with(canonical_json(payload)),
        NspVerificationCode.INVALID_MANIFEST,
    )


def test_sec_010_manifest_count_duplicate_identity_hash_and_canonical_boundaries() -> None:
    payload = _manifest_payload()
    second = dict(payload["skills"][0])
    second["path"] = "skills/0002.nso"
    payload["skills"].append(second)
    _recompute_package_hash(payload)
    _assert_code(
        _package_with(canonical_json(payload), artifact=None),
        NspVerificationCode.INVALID_MANIFEST,
    )

    unique_payload = _manifest_payload()
    second = dict(unique_payload["skills"][0])
    second.update(path="skills/0002.nso", skill_id="FINANCE.SECOND")
    unique_payload["skills"].append(second)
    _recompute_package_hash(unique_payload)
    with pytest.raises(NspVerificationError) as raised:
        NspVerifier(
            policy=DEVELOPMENT_UNSIGNED,
            limits=NspVerificationLimits(max_members=2),
        ).verify(
            _package_with(canonical_json(unique_payload), artifact=None)
        )
    assert raised.value.code is NspVerificationCode.PACKAGE_LIMIT_EXCEEDED

    payload = _manifest_payload()
    payload["package_sha256"] = "sha256:" + "0" * 64
    _assert_code(
        _package_with(canonical_json(payload)),
        NspVerificationCode.CONTENT_MISMATCH,
    )

    payload = _manifest_payload()
    noncanonical = json.dumps(payload, indent=2).encode("utf-8")
    _assert_code(
        _package_with(noncanonical),
        NspVerificationCode.INVALID_MANIFEST,
    )


def test_sec_010_skill_size_hash_schema_encoding_and_identity_are_verified() -> None:
    payload = _manifest_payload()
    payload["skills"][0]["size_bytes"] += 1
    _recompute_package_hash(payload)
    _assert_code(
        _package_with(canonical_json(payload)),
        NspVerificationCode.CONTENT_MISMATCH,
    )

    payload = _manifest_payload()
    payload["skills"][0]["artifact_sha256"] = "sha256:" + "0" * 64
    _recompute_package_hash(payload)
    _assert_code(
        _package_with(canonical_json(payload)),
        NspVerificationCode.CONTENT_MISMATCH,
    )

    invalid_artifact = b"not-an-nso"
    payload = _manifest_payload()
    payload["skills"][0]["size_bytes"] = len(invalid_artifact)
    payload["skills"][0]["artifact_sha256"] = (
        "sha256:" + sha256(invalid_artifact).hexdigest()
    )
    _recompute_package_hash(payload)
    _assert_code(
        _package_with(canonical_json(payload), artifact=invalid_artifact),
        NspVerificationCode.CONTENT_MISMATCH,
    )

    noncanonical_artifact = json.dumps(json.loads(NSO), indent=2).encode("utf-8")
    payload = _manifest_payload()
    payload["skills"][0]["size_bytes"] = len(noncanonical_artifact)
    payload["skills"][0]["artifact_sha256"] = (
        "sha256:" + sha256(noncanonical_artifact).hexdigest()
    )
    _recompute_package_hash(payload)
    _assert_code(
        _package_with(canonical_json(payload), artifact=noncanonical_artifact),
        NspVerificationCode.CONTENT_MISMATCH,
    )

    payload = _manifest_payload()
    payload["skills"][0]["skill_id"] = "FINANCE.FORGED"
    _recompute_package_hash(payload)
    _assert_code(
        _package_with(canonical_json(payload)),
        NspVerificationCode.CONTENT_MISMATCH,
    )


@pytest.mark.parametrize(
    "field",
    [
        "max_package_bytes",
        "max_members",
        "max_member_bytes",
        "max_total_uncompressed_bytes",
    ],
)
@pytest.mark.parametrize("value", [0, -1, True, "1"])
def test_sec_010_verification_limits_require_positive_integers(field, value) -> None:
    values = {
        "max_package_bytes": len(NSP.data),
        "max_members": 2,
        "max_member_bytes": len(NSO),
        "max_total_uncompressed_bytes": len(NSP.data),
    }
    values[field] = value
    with pytest.raises(ValueError, match="positive integers"):
        NspVerificationLimits(**values)  # type: ignore[arg-type]


def test_sec_010_package_member_and_total_size_boundaries_are_enforced() -> None:
    exact = NspVerificationLimits(
        max_package_bytes=len(NSP.data),
        max_members=2,
        max_member_bytes=max(
            info.file_size
            for info in ZipFile(BytesIO(NSP.data)).infolist()
        ),
        max_total_uncompressed_bytes=sum(
            info.file_size
            for info in ZipFile(BytesIO(NSP.data)).infolist()
        ),
    )
    assert (
        NspVerifier(policy=DEVELOPMENT_UNSIGNED, limits=exact)
        .verify(NSP.data)
        .package_hash
        == NSP.package_hash
    )

    cases = (
        NspVerificationLimits(max_package_bytes=len(NSP.data) - 1),
        NspVerificationLimits(max_members=1),
        NspVerificationLimits(max_member_bytes=len(NSO) - 1),
        NspVerificationLimits(
            max_total_uncompressed_bytes=sum(
                info.file_size
                for info in ZipFile(BytesIO(NSP.data)).infolist()
            )
            - 1
        ),
    )
    for limits in cases:
        with pytest.raises(NspVerificationError) as raised:
            NspVerifier(policy=DEVELOPMENT_UNSIGNED, limits=limits).verify(NSP.data)
        assert raised.value.code is NspVerificationCode.PACKAGE_LIMIT_EXCEEDED


@pytest.mark.parametrize("data", [None, bytearray(), b"", b"not-a-zip"])
def test_sec_010_non_byte_or_malformed_archives_are_rejected(data) -> None:
    _assert_code(data, NspVerificationCode.INVALID_ARCHIVE)  # type: ignore[arg-type]


def test_tst_012_package_tamper_matrix_is_fail_closed() -> None:
    manifest = canonical_json(_manifest_payload())
    artifact_tamper = bytearray(NSO)
    artifact_tamper[-1] ^= 0x01

    hash_payload = _manifest_payload()
    hash_payload["package_sha256"] = "sha256:" + "f" * 64

    cases = (
        NSP.data[:-22],
        _package_with(manifest, artifact=None),
        _append_member(NSP.data, "metadata.json", b"forged"),
        _package_with(manifest, artifact=bytes(artifact_tamper)),
        _package_with(canonical_json(hash_payload)),
    )
    for package in cases:
        with pytest.raises(NspVerificationError) as raised:
            NspVerifier(policy=DEVELOPMENT_UNSIGNED).verify(package)
        assert raised.value.code in {
            NspVerificationCode.INVALID_ARCHIVE,
            NspVerificationCode.CONTENT_MISMATCH,
        }


def test_pkg_006_signature_metadata_is_canonical_and_binds_package_hash() -> None:
    signer = DeterministicSigner()
    signed = NspBuilder().build((NSO,), signer=signer)

    assert isinstance(signer, NspSigner)
    assert signed.signature is not None
    assert signed.package_hash == NSP.package_hash
    assert signed.signature.package_sha256 == signed.package_hash
    assert signed.signature.signature_bytes() == b"S" * 64
    assert signer.messages == [
        signature_message(
            signed.package_hash,
            signed.signature.algorithm,
            signed.signature.key_id,
        )
    ]

    with ZipFile(BytesIO(signed.data)) as archive:
        assert archive.namelist()[-1] == "signature.json"
        encoded = archive.read("signature.json")
    assert encoded == encode_signature_metadata(signed.signature)
    assert decode_signature_metadata(encoded) == signed.signature

    verifier = RecordingSignatureVerifier()
    assert isinstance(verifier, NspSignatureVerifier)
    verified = NspVerifier(
        policy=DEVELOPMENT_UNSIGNED,
        signature_verifier=verifier,
    ).verify(signed.data)
    assert verified.signature == signed.signature
    assert verifier.calls == [
        {
            "algorithm": signed.signature.algorithm,
            "key_id": signed.signature.key_id,
            "message": signature_message(
                signed.package_hash,
                signed.signature.algorithm,
                signed.signature.key_id,
            ),
            "signature": b"S" * 64,
        }
    ]


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("algorithm", "RSA"),
        ("key_id", ""),
        ("key_id", "bad key"),
        ("key_id", "한글"),
        ("key_id", "k" * 129),
    ],
)
def test_pkg_006_builder_rejects_invalid_signature_identity(attribute, value) -> None:
    signer = DeterministicSigner()
    setattr(signer, attribute, value)
    with pytest.raises(NspBuildError, match="invalid NSP signer or signature"):
        NspBuilder().build((NSO,), signer=signer)


@pytest.mark.parametrize("signature", [b"", b"x" * 63, b"x" * 65, None])
def test_pkg_006_builder_rejects_invalid_signature_bytes(signature) -> None:
    signer = DeterministicSigner(signature)  # type: ignore[arg-type]
    with pytest.raises(NspBuildError, match="invalid NSP signer or signature"):
        NspBuilder().build((NSO,), signer=signer)


def test_pkg_006_signer_failure_is_normalized() -> None:
    class FailingSigner(DeterministicSigner):
        def sign(self, message: bytes) -> bytes:
            raise RuntimeError("private detail")

    with pytest.raises(NspBuildError, match="invalid NSP signer or signature") as raised:
        NspBuilder().build((NSO,), signer=FailingSigner())
    assert "private detail" not in str(raised.value)

    with pytest.raises(NspBuildError, match="does not implement"):
        NspBuilder().build((NSO,), signer=object())  # type: ignore[arg-type]


def test_pkg_006_signature_metadata_schema_boundaries_are_rejected() -> None:
    signed = NspBuilder().build((NSO,), signer=DeterministicSigner())
    assert signed.signature is not None
    valid = signed.signature.to_data()
    mutations = (
        lambda value: value.pop("format"),
        lambda value: value.update(extra=True),
        lambda value: value.update(format="BAD"),
        lambda value: value.update(schema_version="2.0"),
        lambda value: value.update(algorithm=None),
        lambda value: value.update(key_id=[]),
        lambda value: value.update(package_sha256="bad"),
        lambda value: value.update(signature="bad"),
    )
    for mutate in mutations:
        payload = dict(valid)
        mutate(payload)
        with pytest.raises(NspSignatureError):
            NspSignatureMetadata.from_data(payload)

    with pytest.raises(NspSignatureError):
        NspSignatureMetadata.from_data([])
    with pytest.raises(NspSignatureError, match="canonical JSON"):
        decode_signature_metadata(json.dumps(valid, indent=2).encode("utf-8"))

    for encoded in ("!", "", "AAAA"):
        forged = NspSignatureMetadata(
            format=signed.signature.format,
            schema_version=signed.signature.schema_version,
            algorithm=signed.signature.algorithm,
            key_id=signed.signature.key_id,
            package_sha256=signed.signature.package_sha256,
            signature_b64=encoded,
        )
        with pytest.raises(NspSignatureError):
            forged.signature_bytes()


def test_pkg_006_signature_package_hash_and_schema_tamper_are_rejected() -> None:
    signed = NspBuilder().build((NSO,), signer=DeterministicSigner())
    with ZipFile(BytesIO(signed.data)) as archive:
        signature = json.loads(archive.read("signature.json"))
    signature["package_sha256"] = "sha256:" + "0" * 64
    mismatch = _append_member(
        NSP.data,
        "signature.json",
        canonical_json(signature),
    )
    _assert_code(mismatch, NspVerificationCode.CONTENT_MISMATCH)

    malformed = _append_member(NSP.data, "signature.json", b"{}")
    _assert_code(malformed, NspVerificationCode.INVALID_SIGNATURE_METADATA)


def test_pkg_007_development_unsigned_package_requires_explicit_opt_in() -> None:
    deny_unsigned = NspVerificationPolicy(RuntimeEnvironment.DEVELOPMENT)

    assert DEVELOPMENT_UNSIGNED.allows_unsigned()
    assert not deny_unsigned.allows_unsigned()
    assert (
        NspVerifier(policy=DEVELOPMENT_UNSIGNED).verify(NSP.data).package_hash
        == NSP.package_hash
    )
    with pytest.raises(NspVerificationError) as raised:
        NspVerifier(policy=deny_unsigned).verify(NSP.data)
    assert raised.value.code is NspVerificationCode.SIGNATURE_REQUIRED

    signed = NspBuilder().build((NSO,), signer=DeterministicSigner())
    assert (
        NspVerifier(
            policy=deny_unsigned,
            signature_verifier=RecordingSignatureVerifier(),
        )
        .verify(signed.data)
        .signature
        is not None
    )


@pytest.mark.parametrize(
    ("environment", "allow_unsigned", "message"),
    [
        ("DEVELOPMENT", False, "environment"),
        (RuntimeEnvironment.DEVELOPMENT, 1, "must be boolean"),
        (RuntimeEnvironment.PRODUCTION, True, "only be allowed"),
    ],
)
def test_pkg_007_unsigned_policy_rejects_invalid_or_unsafe_configuration(
    environment, allow_unsigned, message
) -> None:
    with pytest.raises(ValueError, match=message):
        NspVerificationPolicy(
            environment,  # type: ignore[arg-type]
            allow_unsigned_development=allow_unsigned,  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="policy is required"):
        NspVerifier(policy=None)  # type: ignore[arg-type]


def test_pkg_008_production_requires_a_verified_signature() -> None:
    production = NspVerificationPolicy(RuntimeEnvironment.PRODUCTION)
    signed = NspBuilder().build((NSO,), signer=DeterministicSigner())

    with pytest.raises(NspVerificationError) as raised:
        NspVerifier(policy=production).verify(NSP.data)
    assert raised.value.code is NspVerificationCode.SIGNATURE_REQUIRED

    with pytest.raises(NspVerificationError) as raised:
        NspVerifier(policy=production).verify(signed.data)
    assert raised.value.code is NspVerificationCode.SIGNATURE_VERIFIER_REQUIRED

    verified = NspVerifier(
        policy=production,
        signature_verifier=RecordingSignatureVerifier(),
    ).verify(signed.data)
    assert verified.signature is not None


@pytest.mark.parametrize("result", [False, None, 1, RuntimeError("key failure")])
def test_pkg_008_production_rejects_untrusted_or_failed_signature_verifier(
    result,
) -> None:
    production = NspVerificationPolicy(RuntimeEnvironment.PRODUCTION)
    signed = NspBuilder().build((NSO,), signer=DeterministicSigner())

    with pytest.raises(NspVerificationError) as raised:
        NspVerifier(
            policy=production,
            signature_verifier=RecordingSignatureVerifier(result),
        ).verify(signed.data)
    assert raised.value.code is NspVerificationCode.INVALID_SIGNATURE


def test_pkg_008_signature_verifier_must_implement_the_port() -> None:
    with pytest.raises(ValueError, match="does not implement"):
        NspVerifier(
            policy=DEVELOPMENT_UNSIGNED,
            signature_verifier=object(),  # type: ignore[arg-type]
        )


def test_sec_011_production_verifies_real_ed25519_signature() -> None:
    signer = Ed25519PackageSigner("publisher.production-1", bytes(range(1, 33)))
    trust_store = Ed25519TrustStore({signer.key_id: signer.public_key_bytes})
    signed = NspBuilder().build((NSO,), signer=signer)

    assert isinstance(trust_store, NspSignatureVerifier)
    verified = NspVerifier(
        policy=NspVerificationPolicy(RuntimeEnvironment.PRODUCTION),
        signature_verifier=trust_store,
    ).verify(signed.data)

    assert verified.package_hash == signed.package_hash
    assert verified.signature is not None
    assert verified.signature.key_id == signer.key_id


def test_sec_011_unknown_wrong_and_tampered_ed25519_signatures_are_rejected() -> None:
    trusted = Ed25519PackageSigner("publisher.production-1", bytes(range(1, 33)))
    rogue = Ed25519PackageSigner("publisher.production-1", bytes(range(33, 65)))
    production = NspVerificationPolicy(RuntimeEnvironment.PRODUCTION)
    trusted_store = Ed25519TrustStore(
        {
            trusted.key_id: trusted.public_key_bytes,
            "publisher.alias": trusted.public_key_bytes,
        }
    )

    unknown = NspBuilder().build(
        (NSO,),
        signer=Ed25519PackageSigner("publisher.unknown", bytes(range(1, 33))),
    )
    wrong_key = NspBuilder().build((NSO,), signer=rogue)
    valid = NspBuilder().build((NSO,), signer=trusted)
    with ZipFile(BytesIO(valid.data)) as archive:
        metadata = json.loads(archive.read("signature.json"))
    first = metadata["signature"][0]
    metadata["signature"] = ("A" if first != "A" else "B") + metadata[
        "signature"
    ][1:]
    tampered = _append_member(
        NSP.data,
        "signature.json",
        canonical_json(metadata),
    )

    with ZipFile(BytesIO(valid.data)) as archive:
        substituted_metadata = json.loads(archive.read("signature.json"))
    substituted_metadata["key_id"] = "publisher.alias"
    identity_substitution = _append_member(
        NSP.data,
        "signature.json",
        canonical_json(substituted_metadata),
    )

    for package in (
        unknown.data,
        wrong_key.data,
        tampered,
        identity_substitution,
    ):
        with pytest.raises(NspVerificationError) as raised:
            NspVerifier(
                policy=production,
                signature_verifier=trusted_store,
            ).verify(package)
        assert raised.value.code is NspVerificationCode.INVALID_SIGNATURE


def test_sec_011_ed25519_adapter_boundary_values() -> None:
    private_key = bytes(range(1, 33))
    signer = Ed25519PackageSigner("key-1", private_key)
    message = b"message"
    signature = signer.sign(message)
    trust_store = Ed25519TrustStore({"key-1": signer.public_key_bytes})

    assert len(signer.public_key_bytes) == 32
    assert len(signature) == 64
    assert trust_store.verify(
        algorithm=NSP_SIGNATURE_ALGORITHM,
        key_id="key-1",
        message=message,
        signature=signature,
    )
    assert not trust_store.verify(
        algorithm="RSA",
        key_id="key-1",
        message=message,
        signature=signature,
    )
    assert not trust_store.verify(
        algorithm=NSP_SIGNATURE_ALGORITHM,
        key_id="missing",
        message=message,
        signature=signature,
    )
    assert not trust_store.verify(
        algorithm=NSP_SIGNATURE_ALGORITHM,
        key_id="key-1",
        message="message",  # type: ignore[arg-type]
        signature=signature,
    )
    assert not trust_store.verify(
        algorithm=NSP_SIGNATURE_ALGORITHM,
        key_id="key-1",
        message=message,
        signature=b"short",
    )
    assert not trust_store.verify(
        algorithm=NSP_SIGNATURE_ALGORITHM,
        key_id="key-1",
        message=message,
        signature=b"X" * 64,
    )
    with pytest.raises(ValueError, match="message must be bytes"):
        signer.sign("message")  # type: ignore[arg-type]


@pytest.mark.parametrize("key_id", ["", None])
def test_sec_011_ed25519_signer_rejects_invalid_key_id(key_id) -> None:
    with pytest.raises(ValueError, match="key_id"):
        Ed25519PackageSigner(key_id, bytes(range(1, 33)))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "private_key",
    [b"", b"x" * 31, b"x" * 33, bytearray(b"x" * 32), None],
)
def test_sec_011_ed25519_signer_rejects_invalid_private_key(private_key) -> None:
    with pytest.raises(ValueError, match="32 raw bytes"):
        Ed25519PackageSigner("key-1", private_key)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "public_keys",
    [[], {"": b"x" * 32}, {"key-1": b""}, {"key-1": b"x" * 31}, {"key-1": None}],
)
def test_sec_011_trust_store_rejects_invalid_key_mapping(public_keys) -> None:
    with pytest.raises(ValueError):
        Ed25519TrustStore(public_keys)  # type: ignore[arg-type]


def test_sec_011_ed25519_unsupported_platform_errors_are_normalized() -> None:
    with patch(
        "nsl.adapters.ed25519.Ed25519PrivateKey.from_private_bytes",
        side_effect=UnsupportedAlgorithm("unsupported"),
    ):
        with pytest.raises(ValueError, match="invalid Ed25519 private key"):
            Ed25519PackageSigner("key-1", bytes(range(1, 33)))

    with patch(
        "nsl.adapters.ed25519.Ed25519PublicKey.from_public_bytes",
        side_effect=UnsupportedAlgorithm("unsupported"),
    ):
        with pytest.raises(ValueError, match="invalid Ed25519 public key"):
            Ed25519TrustStore({"key-1": b"x" * 32})
