"""Proof-of-possession over a server nonce, binding an HTTP caller to its Shadow key."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from shadownet.crypto.jwt import JWTError, decode_header, sign_jwt, verify_jwt

from shadownet_x402.errors import PoPError

if TYPE_CHECKING:
    from collections.abc import Callable

    from shadownet.crypto.ed25519 import Ed25519KeyPair

POP_TYP: Final = "shadownet-x402-pop+jwt"
DEFAULT_POP_LIFETIME_SECONDS: Final = 120


def mint_pop(
    key: Ed25519KeyPair,
    *,
    sub: str,
    audience: str,
    nonce: str,
    now: int | None = None,
    lifetime: int = DEFAULT_POP_LIFETIME_SECONDS,
) -> str:
    """Sign a proof-of-possession asserting control of ``sub`` over ``nonce``."""
    issued = int(time.time()) if now is None else now
    claims = {
        "sub": sub,
        "aud": audience,
        "nonce": nonce,
        "iat": issued,
        "exp": issued + lifetime,
    }
    return sign_jwt(claims, key, header_extras={"typ": POP_TYP, "kid": sub})


def verify_pop(
    token: str,
    *,
    expected_sub: str,
    expected_audience: str,
    expected_nonce: str,
    resolve_subject_key: Callable[[str], Ed25519KeyPair],
    leeway: int = 60,
) -> None:
    """Verify ``token`` is a valid proof-of-possession for ``expected_sub``; raise PoPError otherwise."""
    try:
        header = decode_header(token)
    except JWTError as exc:
        raise PoPError(f"invalid proof-of-possession header: {exc}") from exc
    if header.get("typ") != POP_TYP:
        raise PoPError(f"typ must be {POP_TYP!r}, got {header.get('typ')!r}")
    key = resolve_subject_key(expected_sub)
    try:
        claims = verify_jwt(
            token,
            key,
            audience=expected_audience,
            leeway=leeway,
            required=["sub", "aud", "nonce", "iat", "exp"],
        )
    except JWTError as exc:
        raise PoPError(f"proof-of-possession did not verify: {exc}") from exc
    if claims.get("sub") != expected_sub:
        raise PoPError("proof-of-possession sub does not match the credential subject")
    if claims.get("nonce") != expected_nonce:
        raise PoPError("proof-of-possession nonce does not match the challenge")
