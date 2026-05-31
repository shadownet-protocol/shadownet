"""Shadow addressing URI parser — RFC 0001 §3.2.

Parses ``shadow://`` URIs that name a Shadow (NOT the onboarding URIs in
``shadownet.onboarding`` — those use ``shadow://connect?...``). Two forms:

  * **Shadowname.** ``shadow://alice@sh4dow.org`` (or the friendly form
    ``alice@sh4dow.org`` without scheme).
  * **Direct.** ``shadow://key:z6Mk...@host[:port][#sha256:<pin>]`` —
    embeds the Shadow's public key and an HTTPS endpoint, optionally with
    a TLS certificate fingerprint pin.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import ParseResult, urlparse

from shadownet.errors import ShadownetError
from shadownet.identifiers import (
    InvalidIdentifierError,
    canonicalize_identifier,
    is_public_key_identifier,
    parse_shadowname,
)

__all__ = [
    "DEFAULT_DIRECT_PORT",
    "TLS_PIN_PREFIX",
    "DirectAddress",
    "ShadowAddress",
    "ShadowAddressError",
    "ShadownameAddress",
    "parse_shadow_address",
    "parse_tls_pin",
]


SHADOW_SCHEME: Final = "shadow"
TLS_PIN_PREFIX: Final = "sha256:"
DEFAULT_DIRECT_PORT: Final = 443
_USERINFO_KEY_TAG: Final = "key"
_FINGERPRINT_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]+$")


class ShadowAddressError(ShadownetError):
    """A ``shadow://`` Shadow-addressing URI is malformed."""


@dataclass(frozen=True, slots=True)
class ShadownameAddress:
    """Shadowname-mode address: resolves via DNS + provider AgentCard (§5.2)."""

    shadowname: str


@dataclass(frozen=True, slots=True)
class DirectAddress:
    """Direct-mode address: the public key IS the identity (§5.3).

    ``host`` may be a DNS hostname, an IPv4 literal, or an IPv6 literal
    (already stripped of its bracket-enclosure). ``port`` defaults to 443
    when omitted. ``tls_pin_sha256`` is the base64url fingerprint from the
    URI's ``#sha256:`` fragment when present.
    """

    public_key: str
    host: str
    port: int
    tls_pin_sha256: str | None = None

    @property
    def endpoint(self) -> str:
        host_part = f"[{self.host}]" if ":" in self.host else self.host
        return f"https://{host_part}:{self.port}"


ShadowAddress = ShadownameAddress | DirectAddress


def parse_shadow_address(value: str) -> ShadowAddress:
    """Parse a Shadowname (with or without ``shadow://``) or a direct URI."""
    stripped = value.strip()
    if not stripped:
        raise ShadowAddressError("empty input")

    if "://" not in stripped:
        return ShadownameAddress(shadowname=_canonical_shadowname(stripped))

    parts = urlparse(stripped)
    if parts.scheme.lower() != SHADOW_SCHEME:
        raise ShadowAddressError(f"scheme must be shadow://, got {parts.scheme!r}://")
    if not parts.netloc:
        raise ShadowAddressError("shadow:// URI requires an authority")
    if parts.path not in ("", "/"):
        raise ShadowAddressError(f"shadow:// URI path must be empty or '/', got {parts.path!r}")
    if parts.query:
        raise ShadowAddressError("shadow:// addressing URIs MUST NOT carry a query")

    if parts.username is None:
        raise ShadowAddressError("shadow:// URI is missing userinfo before '@'")

    if parts.username.lower() == _USERINFO_KEY_TAG:
        return _parse_direct(parts)
    if parts.password is not None:
        raise ShadowAddressError(
            f"unrecognized userinfo type tag {parts.username!r} (only 'key:' is defined)"
        )
    return _parse_shadowname_uri(parts)


def parse_tls_pin(fragment: str) -> str:
    """Validate and return the base64url fingerprint from a TLS pin fragment.

    Accepts the ``sha256:`` prefix or a bare fingerprint. Raises
    :class:`ShadowAddressError` for any other format.
    """
    if fragment.lower().startswith(TLS_PIN_PREFIX):
        fingerprint = fragment[len(TLS_PIN_PREFIX) :]
    else:
        fingerprint = fragment
    if not _FINGERPRINT_PATTERN.match(fingerprint):
        raise ShadowAddressError(f"TLS pin must be base64url, got {fragment!r}")
    try:
        padded = fingerprint + "=" * (-len(fingerprint) % 4)
        digest = base64.urlsafe_b64decode(padded)
    except ValueError as exc:
        raise ShadowAddressError(f"TLS pin not decodable: {exc}") from exc
    if len(digest) != 32:
        raise ShadowAddressError(f"TLS pin must decode to 32 bytes (SHA-256), got {len(digest)}")
    return fingerprint


def _parse_direct(parts: ParseResult) -> DirectAddress:
    # ``key:z6Mk...@host:port`` lands in urlparse as username="key" password="z6Mk..."
    password = parts.password
    if password is None:
        raise ShadowAddressError("direct URI must have 'key:<pubkey>' in userinfo")
    try:
        public_key = canonicalize_identifier(password)
    except InvalidIdentifierError as exc:
        raise ShadowAddressError(f"invalid pubkey in direct URI: {exc}") from exc
    if not is_public_key_identifier(public_key):
        raise ShadowAddressError(f"direct URI userinfo pubkey {public_key!r} is not a key")

    host = parts.hostname
    if not host:
        raise ShadowAddressError("direct URI must have a host")
    port = parts.port if parts.port is not None else DEFAULT_DIRECT_PORT
    pin = parse_tls_pin(parts.fragment) if parts.fragment else None
    return DirectAddress(public_key=public_key, host=host, port=port, tls_pin_sha256=pin)


def _parse_shadowname_uri(parts: ParseResult) -> ShadownameAddress:
    username = parts.username
    host = parts.hostname
    if not username or not host:
        raise ShadowAddressError("Shadowname URI requires 'local@provider'")
    if parts.port is not None:
        raise ShadowAddressError(
            "Shadowname URI MUST NOT carry a port (the provider is the resolver)"
        )
    if parts.fragment:
        raise ShadowAddressError("Shadowname URI MUST NOT carry a TLS pin fragment")
    return ShadownameAddress(shadowname=_canonical_shadowname(f"{username}@{host}"))


def _canonical_shadowname(value: str) -> str:
    try:
        return parse_shadowname(value)
    except InvalidIdentifierError as exc:
        raise ShadowAddressError(str(exc)) from exc
