from __future__ import annotations

from typing import Mapping

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ..nsp_signatures import NSP_SIGNATURE_ALGORITHM


class Ed25519PackageSigner:
    __slots__ = ("algorithm", "key_id", "_private_key")

    def __init__(self, key_id: str, private_key: bytes) -> None:
        if not isinstance(key_id, str) or not key_id:
            raise ValueError("Ed25519 signer key_id must be non-empty")
        if type(private_key) is not bytes or len(private_key) != 32:
            raise ValueError("Ed25519 private key must be 32 raw bytes")
        try:
            key = Ed25519PrivateKey.from_private_bytes(private_key)
        except (ValueError, UnsupportedAlgorithm) as error:
            raise ValueError("invalid Ed25519 private key") from error
        self.algorithm = NSP_SIGNATURE_ALGORITHM
        self.key_id = key_id
        self._private_key = key

    @property
    def public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def sign(self, message: bytes) -> bytes:
        if type(message) is not bytes:
            raise ValueError("Ed25519 signing message must be bytes")
        return self._private_key.sign(message)


class Ed25519TrustStore:
    __slots__ = ("_keys",)

    def __init__(self, public_keys: Mapping[str, bytes]) -> None:
        if not isinstance(public_keys, Mapping):
            raise ValueError("Ed25519 trust store requires a key mapping")
        keys: dict[str, Ed25519PublicKey] = {}
        for key_id, public_key in public_keys.items():
            if not isinstance(key_id, str) or not key_id:
                raise ValueError("Ed25519 trust key_id must be non-empty")
            if type(public_key) is not bytes or len(public_key) != 32:
                raise ValueError("Ed25519 public key must be 32 raw bytes")
            try:
                keys[key_id] = Ed25519PublicKey.from_public_bytes(public_key)
            except (ValueError, UnsupportedAlgorithm) as error:
                raise ValueError("invalid Ed25519 public key") from error
        self._keys = keys

    def verify(
        self,
        *,
        algorithm: str,
        key_id: str,
        message: bytes,
        signature: bytes,
    ) -> bool:
        if algorithm != NSP_SIGNATURE_ALGORITHM:
            return False
        key = self._keys.get(key_id)
        if key is None:
            return False
        if type(message) is not bytes or type(signature) is not bytes:
            return False
        if len(signature) != 64:
            return False
        try:
            key.verify(signature, message)
        except InvalidSignature:
            return False
        return True
