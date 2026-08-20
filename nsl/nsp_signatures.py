from __future__ import annotations

from base64 import b64decode, urlsafe_b64encode
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .ir import canonical_json
from .json_boundary import load_strict_json


NSP_SIGNATURE_FORMAT = "NSP-SIGNATURE"
NSP_SIGNATURE_SCHEMA_VERSION = "1.0"
NSP_SIGNATURE_ALGORITHM = "Ed25519"
_SIGNATURE_DOMAIN = b"NSP-SIGNATURE-V1\x00"
_KEY_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-"
)
_BASE64URL_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


class NspSignatureError(ValueError):
    pass


@runtime_checkable
class NspSigner(Protocol):
    algorithm: str
    key_id: str

    def sign(self, message: bytes) -> bytes:
        ...


@runtime_checkable
class NspSignatureVerifier(Protocol):
    def verify(
        self,
        *,
        algorithm: str,
        key_id: str,
        message: bytes,
        signature: bytes,
    ) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class NspSignatureMetadata:
    format: str
    schema_version: str
    algorithm: str
    key_id: str
    package_sha256: str
    signature_b64: str

    @classmethod
    def create(
        cls,
        package_sha256: str,
        signer: NspSigner,
    ) -> NspSignatureMetadata:
        package_hash = _sha256(package_sha256, "$.package_sha256")
        algorithm = _algorithm(signer.algorithm, "$.algorithm")
        key_id = _key_id(signer.key_id, "$.key_id")
        try:
            signature = signer.sign(
                signature_message(package_hash, algorithm, key_id)
            )
        except Exception as error:
            raise NspSignatureError("NSP signer failed") from error
        if type(signature) is not bytes or len(signature) != 64:
            raise NspSignatureError("NSP Ed25519 signature must be 64 bytes")
        return cls(
            format=NSP_SIGNATURE_FORMAT,
            schema_version=NSP_SIGNATURE_SCHEMA_VERSION,
            algorithm=algorithm,
            key_id=key_id,
            package_sha256=package_hash,
            signature_b64=urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        )

    @classmethod
    def from_data(cls, value: Any) -> NspSignatureMetadata:
        if type(value) is not dict:
            raise _error("$", "expected object")
        fields = {
            "format",
            "schema_version",
            "algorithm",
            "key_id",
            "package_sha256",
            "signature",
        }
        actual = set(value)
        missing = fields - actual
        if missing:
            raise _error("$", f"missing fields: {sorted(missing)}")
        unexpected = actual - fields
        if unexpected:
            raise _error("$", f"unexpected fields: {sorted(unexpected)}")
        if value["format"] != NSP_SIGNATURE_FORMAT:
            raise _error("$.format", f"expected {NSP_SIGNATURE_FORMAT!r}")
        if value["schema_version"] != NSP_SIGNATURE_SCHEMA_VERSION:
            raise _error(
                "$.schema_version",
                f"expected {NSP_SIGNATURE_SCHEMA_VERSION!r}",
            )
        metadata = cls(
            format=NSP_SIGNATURE_FORMAT,
            schema_version=NSP_SIGNATURE_SCHEMA_VERSION,
            algorithm=_algorithm(value["algorithm"], "$.algorithm"),
            key_id=_key_id(value["key_id"], "$.key_id"),
            package_sha256=_sha256(value["package_sha256"], "$.package_sha256"),
            signature_b64=_signature_b64(value["signature"], "$.signature"),
        )
        metadata.signature_bytes()
        return metadata

    def to_data(self) -> dict[str, str]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "package_sha256": self.package_sha256,
            "signature": self.signature_b64,
        }

    def signature_bytes(self) -> bytes:
        try:
            signature = b64decode(
                self.signature_b64 + "==",
                altchars=b"-_",
                validate=True,
            )
        except ValueError as error:
            raise _error("$.signature", "invalid base64url encoding") from error
        if len(signature) != 64:
            raise _error("$.signature", "Ed25519 signature must be 64 bytes")
        return signature


def signature_message(
    package_sha256: str,
    algorithm: str,
    key_id: str,
) -> bytes:
    package_hash = _sha256(package_sha256, "$.package_sha256")
    protected = {
        "algorithm": _algorithm(algorithm, "$.algorithm"),
        "key_id": _key_id(key_id, "$.key_id"),
        "package_sha256": package_hash,
    }
    return _SIGNATURE_DOMAIN + canonical_json(protected)


def encode_signature_metadata(metadata: NspSignatureMetadata) -> bytes:
    return canonical_json(metadata.to_data())


def decode_signature_metadata(data: bytes) -> NspSignatureMetadata:
    raw = load_strict_json(
        data,
        document_name="NSP signature metadata",
        error_factory=_error,
    )
    metadata = NspSignatureMetadata.from_data(raw)
    if data != encode_signature_metadata(metadata):
        raise _error("$", "signature metadata must use canonical JSON")
    return metadata


def _error(path: str, reason: str) -> NspSignatureError:
    return NspSignatureError(f"invalid NSP signature metadata at {path}: {reason}")


def _string(value: Any, path: str) -> str:
    if type(value) is not str:
        raise _error(path, "expected string")
    if not value:
        raise _error(path, "must not be empty")
    return value


def _algorithm(value: Any, path: str) -> str:
    algorithm = _string(value, path)
    if algorithm != NSP_SIGNATURE_ALGORITHM:
        raise _error(path, f"expected {NSP_SIGNATURE_ALGORITHM!r}")
    return algorithm


def _key_id(value: Any, path: str) -> str:
    key_id = _string(value, path)
    if (
        len(key_id) > 128
        or not key_id.isascii()
        or any(character not in _KEY_ID_CHARACTERS for character in key_id)
    ):
        raise _error(path, "expected a safe ASCII key identifier")
    return key_id


def _sha256(value: Any, path: str) -> str:
    digest = _string(value, path)
    if (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise _error(path, "expected lowercase sha256 digest")
    return digest


def _signature_b64(value: Any, path: str) -> str:
    encoded = _string(value, path)
    if len(encoded) != 86 or any(
        character not in _BASE64URL_CHARACTERS for character in encoded
    ):
        raise _error(path, "expected unpadded Ed25519 base64url signature")
    return encoded
