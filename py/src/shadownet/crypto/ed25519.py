from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Self

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from shadownet.errors import ShadownetError

__all__ = ["Ed25519KeyPair", "SignatureError"]


class SignatureError(ShadownetError):
    """A signature failed to verify."""


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


@dataclass(frozen=True, slots=True)
class Ed25519KeyPair:
    """Ed25519 keypair. Holds either a full pair or a public-only verifier."""

    public_bytes: bytes
    _private: Ed25519PrivateKey | None = None

    @classmethod
    def generate(cls) -> Self:
        sk = Ed25519PrivateKey.generate()
        return cls(_public_bytes(sk.public_key()), sk)

    @classmethod
    def from_seed(cls, seed: bytes) -> Self:
        if len(seed) != 32:
            raise SignatureError("Ed25519 seed must be exactly 32 bytes")
        sk = Ed25519PrivateKey.from_private_bytes(seed)
        return cls(_public_bytes(sk.public_key()), sk)

    @classmethod
    def from_public_bytes(cls, public_bytes: bytes) -> Self:
        if len(public_bytes) != 32:
            raise SignatureError("Ed25519 public key must be exactly 32 bytes")
        # Validate by attempting to load.
        Ed25519PublicKey.from_public_bytes(public_bytes)
        return cls(public_bytes, None)

    @classmethod
    def from_jwk(cls, jwk: dict[str, str]) -> Self:
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
            raise SignatureError("JWK is not an Ed25519 OKP key")
        x = jwk.get("x")
        if not isinstance(x, str):
            raise SignatureError("JWK missing 'x'")
        public = _b64u_decode(x)
        if (d := jwk.get("d")) is not None:
            if not isinstance(d, str):
                raise SignatureError("JWK 'd' must be a string")
            return cls.from_seed(_b64u_decode(d))
        return cls.from_public_bytes(public)

    @property
    def has_private(self) -> bool:
        return self._private is not None

    @property
    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(self.public_bytes)

    @property
    def private_key(self) -> Ed25519PrivateKey:
        if self._private is None:
            raise SignatureError("private key not available on this keypair")
        return self._private

    def public_jwk(self) -> dict[str, str]:
        return {"kty": "OKP", "crv": "Ed25519", "x": _b64u(self.public_bytes)}

    def private_jwk(self) -> dict[str, str]:
        if self._private is None:
            raise SignatureError("private key not available on this keypair")
        seed = self._private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        jwk = self.public_jwk()
        jwk["d"] = _b64u(seed)
        return jwk

    def sign(self, message: bytes) -> bytes:
        if self._private is None:
            raise SignatureError("cannot sign with a public-only keypair")
        return self._private.sign(message)

    def verify(self, signature: bytes, message: bytes) -> None:
        try:
            self.public_key.verify(signature, message)
        except InvalidSignature as exc:
            raise SignatureError("signature verification failed") from exc


def _public_bytes(pk: Ed25519PublicKey) -> bytes:
    return pk.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
