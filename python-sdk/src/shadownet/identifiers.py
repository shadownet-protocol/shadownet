"""Identifier parsers and validators — RFC 0001 §3, §5.1.

v0.2 admits two equally-valid forms wherever a Shadow or organization can be
named on the wire:

  * **Shadowname** (``local@provider``) — the human-readable alias bound to a
    public key by a provider's signed AgentCard.
  * **Bare multibase Ed25519 public key** (``z6Mk...``) — the cryptographic
    identity directly, used by direct-mode Shadows and keyed Hubs.

This module exposes both forms plus a discriminator (:func:`is_shadowname`,
:func:`is_public_key_identifier`) so callers can branch resolution paths.
"""

from __future__ import annotations

import re
from typing import Annotated, Final

from pydantic import AfterValidator, BeforeValidator

from shadownet.crypto.multibase import (
    ED25519_PUB_MULTICODEC,
    MultibaseDecodeError,
    decode_multibase_z,
    encode_multibase_z,
    strip_multicodec,
    with_multicodec,
)
from shadownet.errors import ShadownetError

__all__ = [
    "Domain",
    "DomainOrPublicKey",
    "Identifier",
    "InvalidIdentifierError",
    "IssuerIdentifier",
    "MultibasePublicKey",
    "Shadowname",
    "canonicalize_identifier",
    "canonicalize_subject_identifier",
    "encode_public_key",
    "is_public_key_identifier",
    "is_shadowname",
    "is_subdomain_of",
    "parse_public_key",
    "parse_shadowname",
    "split_shadowname",
]


class InvalidIdentifierError(ShadownetError):
    """An identifier string did not match the grammar in RFC 0001 §3 or §5.1."""


# §5.1.
_SHADOWNAME_LOCAL_RE: Final = re.compile(r"^[A-Za-z0-9_.-]{1,63}$")

# RFC 1035 §2.3.1 / RFC 5891 IDNA2008. Total length and per-label rules.
_DOMAIN_LABEL_RE: Final = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_DOMAIN_MAX_LEN: Final = 253


def _validate_domain(value: str) -> str:
    s = value.strip().rstrip(".").lower()
    if not s or len(s) > _DOMAIN_MAX_LEN:
        raise InvalidIdentifierError(f"domain length out of range: {value!r}")
    for label in s.split("."):
        if not _DOMAIN_LABEL_RE.match(label):
            raise InvalidIdentifierError(f"invalid domain label {label!r} in {value!r}")
    return s


def _validate_shadowname(value: str) -> str:
    if "@" not in value:
        raise InvalidIdentifierError(f"shadowname missing '@': {value!r}")
    local, _, provider = value.partition("@")
    if not _SHADOWNAME_LOCAL_RE.match(local):
        raise InvalidIdentifierError(f"invalid shadowname local part: {value!r}")
    return f"{local.lower()}@{_validate_domain(provider)}"


def _validate_multibase_pk(value: str) -> str:
    if not value.startswith("z6Mk"):
        raise InvalidIdentifierError(f"public key must start with 'z6Mk': {value!r}")
    try:
        decoded = decode_multibase_z(value)
        raw = strip_multicodec(ED25519_PUB_MULTICODEC, decoded)
    except MultibaseDecodeError as exc:
        raise InvalidIdentifierError(str(exc)) from exc
    if len(raw) != 32:
        raise InvalidIdentifierError(f"Ed25519 public key must be 32 bytes, got {len(raw)}")
    return value


Shadowname = Annotated[str, AfterValidator(_validate_shadowname)]
Domain = Annotated[str, BeforeValidator(_validate_domain)]
MultibasePublicKey = Annotated[str, AfterValidator(_validate_multibase_pk)]


def parse_shadowname(value: str) -> str:
    return _validate_shadowname(value)


def split_shadowname(value: str) -> tuple[str, str]:
    canonical = _validate_shadowname(value)
    local, _, provider = canonical.partition("@")
    return local, provider


def encode_public_key(public_bytes: bytes) -> str:
    if len(public_bytes) != 32:
        raise InvalidIdentifierError(
            f"Ed25519 public key must be 32 bytes, got {len(public_bytes)}"
        )
    return encode_multibase_z(with_multicodec(ED25519_PUB_MULTICODEC, public_bytes))


def parse_public_key(value: str) -> bytes:
    _validate_multibase_pk(value)
    return strip_multicodec(ED25519_PUB_MULTICODEC, decode_multibase_z(value))


# §6.6: an issuer domain may issue for an org if it equals the org or is a
# sub-domain. (The third path — DNS delegation — is checked by the DNS layer,
# not here.)
def is_subdomain_of(candidate: str, parent: str) -> bool:
    c = _validate_domain(candidate)
    p = _validate_domain(parent)
    return c == p or c.endswith("." + p)


def is_shadowname(value: str) -> bool:
    return "@" in value


def is_public_key_identifier(value: str) -> bool:
    return value.startswith("z6Mk") and "@" not in value


def canonicalize_identifier(value: str) -> str:
    """Normalize an identifier that may be a Shadowname or a bare public key.

    Used for envelope ``from`` / ``to``, JWS ``kid``, and credential ``sub`` —
    contexts where either form is admissible per RFC 0001 §3, §6.1, §8.3.
    """
    if is_shadowname(value):
        return _validate_shadowname(value)
    if is_public_key_identifier(value):
        return _validate_multibase_pk(value)
    raise InvalidIdentifierError(
        f"identifier must be a Shadowname or a multibase Ed25519 public key: {value!r}"
    )


def canonicalize_subject_identifier(value: str) -> str:
    """Alias for :func:`canonicalize_identifier` used at credential ``sub``,
    CSR ``iss``, and envelope ``from``/``to`` validation sites."""
    return canonicalize_identifier(value)


def canonicalize_issuer_or_org_identifier(value: str) -> str:
    """Normalize an identifier that may be a domain or a bare public key.

    Used for credential ``iss``, ``org``, and CSR ``aud`` — contexts where
    keyed organizations / Hubs (RFC 0001 §6.6 rule 1) are valid alongside
    domain issuers.
    """
    if is_public_key_identifier(value):
        return _validate_multibase_pk(value)
    return _validate_domain(value)


Identifier = Annotated[str, AfterValidator(canonicalize_identifier)]
IssuerIdentifier = Annotated[str, AfterValidator(canonicalize_issuer_or_org_identifier)]
DomainOrPublicKey = IssuerIdentifier
