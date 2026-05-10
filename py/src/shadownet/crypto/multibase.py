from __future__ import annotations

from shadownet.errors import ShadownetError

__all__ = [
    "ED25519_PUB_MULTICODEC",
    "MultibaseDecodeError",
    "decode_multibase_z",
    "encode_multibase_z",
    "strip_multicodec",
    "with_multicodec",
]

# Multicodec varint for Ed25519 public key per https://github.com/multiformats/multicodec.
# 0xed01 = (0x01 << 7) | 0x6d, the unsigned-varint encoding of 0xED.
ED25519_PUB_MULTICODEC = b"\xed\x01"

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {c: i for i, c in enumerate(_BASE58_ALPHABET)}


class MultibaseDecodeError(ShadownetError):
    """A multibase value could not be decoded."""


def encode_multibase_z(data: bytes) -> str:
    """Encode ``data`` using base58btc with the ``z`` multibase prefix."""
    return "z" + _b58encode(data)


def decode_multibase_z(value: str) -> bytes:
    """Decode a ``z``-prefixed base58btc multibase string back to bytes."""
    if not value.startswith("z"):
        raise MultibaseDecodeError("expected base58btc multibase prefix 'z'")
    try:
        return _b58decode(value[1:])
    except ValueError as exc:
        raise MultibaseDecodeError(str(exc)) from exc


def with_multicodec(prefix: bytes, payload: bytes) -> bytes:
    return prefix + payload


def strip_multicodec(prefix: bytes, value: bytes) -> bytes:
    if not value.startswith(prefix):
        raise MultibaseDecodeError(f"missing multicodec prefix {prefix.hex()}")
    return value[len(prefix) :]


def _b58encode(data: bytes) -> str:
    if not data:
        return ""
    n = int.from_bytes(data, "big")
    encoded = ""
    while n > 0:
        n, rem = divmod(n, 58)
        encoded = _BASE58_ALPHABET[rem] + encoded
    leading = len(data) - len(data.lstrip(b"\x00"))
    return _BASE58_ALPHABET[0] * leading + encoded


def _b58decode(value: str) -> bytes:
    if not value:
        return b""
    n = 0
    for ch in value:
        if ch not in _BASE58_INDEX:
            raise ValueError(f"invalid base58 character: {ch!r}")
        n = n * 58 + _BASE58_INDEX[ch]
    full = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    leading = len(value) - len(value.lstrip(_BASE58_ALPHABET[0]))
    return b"\x00" * leading + full
