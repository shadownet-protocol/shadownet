"""PKCE (RFC 7636) primitives used by both the AS and the client.

RFC-0009 mandates `S256` and forbids `plain`. The helpers here generate
verifier+challenge pairs and verify a challenge against a verifier on
the server side.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

__all__ = [
    "VERIFIER_MAX_LENGTH",
    "VERIFIER_MIN_LENGTH",
    "generate_code_verifier",
    "s256_challenge",
    "verify_s256",
]

# RFC 7636 § 4.1 — the verifier MUST be 43..128 characters from the
# unreserved-URI character set.
VERIFIER_MIN_LENGTH = 43
VERIFIER_MAX_LENGTH = 128
_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def generate_code_verifier(length: int = 64) -> str:
    """Generate a random PKCE verifier of ``length`` characters.

    The default of 64 lands comfortably inside the 43..128 window. The
    output uses URL-safe base64 then trims to length, keeping every
    character inside the unreserved set per RFC 7636 § 4.1.
    """
    if not VERIFIER_MIN_LENGTH <= length <= VERIFIER_MAX_LENGTH:
        raise ValueError(
            f"PKCE verifier length {length} outside RFC 7636 window "
            f"[{VERIFIER_MIN_LENGTH}, {VERIFIER_MAX_LENGTH}]"
        )
    # Each base64 char encodes ~6 bits; over-allocate then trim.
    raw = secrets.token_urlsafe(length)
    return raw[:length]


def s256_challenge(verifier: str) -> str:
    """Return the RFC 7636 S256 challenge for ``verifier``."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_s256(*, verifier: str, challenge: str) -> bool:
    """Constant-time verification of an S256 PKCE challenge.

    Returns ``True`` iff ``S256(verifier) == challenge``. Constant-time
    comparison via :func:`hmac.compare_digest` prevents timing side
    channels on the AS code-redemption path.
    """
    if not verifier or not challenge:
        return False
    if not all(c in _UNRESERVED for c in verifier):
        return False
    if not VERIFIER_MIN_LENGTH <= len(verifier) <= VERIFIER_MAX_LENGTH:
        return False
    return hmac.compare_digest(s256_challenge(verifier), challenge)
