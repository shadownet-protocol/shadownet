from __future__ import annotations

from typing import Any

import jwt as _pyjwt

from shadownet.crypto.ed25519 import Ed25519KeyPair, SignatureError

__all__ = ["JWTError", "sign_jwt", "verify_jwt"]


class JWTError(SignatureError):
    """JWT could not be parsed, validated, or verified."""


_DEFAULT_HEADER = {"alg": "EdDSA", "typ": "JWT"}


def sign_jwt(
    claims: dict[str, Any],
    key: Ed25519KeyPair,
    *,
    header_extras: dict[str, Any] | None = None,
) -> str:
    """Sign ``claims`` as an EdDSA JWS in compact serialization.

    ``header_extras`` overrides or extends the default header (``alg``, ``typ``).
    Use it to set ``kid`` and ``typ=vc+jwt`` for credentials.
    """
    headers = dict(_DEFAULT_HEADER)
    if header_extras:
        headers.update(header_extras)
    return _pyjwt.encode(claims, key.private_key, algorithm="EdDSA", headers=headers)


def verify_jwt(
    token: str,
    key: Ed25519KeyPair,
    *,
    audience: str | list[str] | None = None,
    issuer: str | None = None,
    leeway: int = 0,
    required: list[str] | None = None,
    verify_exp: bool = True,
) -> dict[str, Any]:
    """Verify ``token`` against ``key`` and return its claims.

    Raises :class:`JWTError` on any verification failure (signature, expiry,
    audience mismatch, issuer mismatch, missing required claim).
    """
    options: dict[str, Any] = {"verify_exp": verify_exp, "verify_signature": True}
    if required:
        options["require"] = list(required)
    try:
        return _pyjwt.decode(
            token,
            key.public_key,
            algorithms=["EdDSA"],
            audience=audience,
            issuer=issuer,
            leeway=leeway,
            options=options,  # type: ignore[arg-type]  # PyJWT typed Options TypedDict; runtime accepts dict
        )
    except _pyjwt.PyJWTError as exc:
        raise JWTError(str(exc)) from exc


def decode_header(token: str) -> dict[str, Any]:
    """Return the unverified protected header of a JWS compact-serialized token."""
    try:
        return _pyjwt.get_unverified_header(token)
    except _pyjwt.PyJWTError as exc:
        raise JWTError(str(exc)) from exc


def decode_unverified_claims(token: str) -> dict[str, Any]:
    """Return the unverified claims body. Use only to read claims needed to look up the key."""
    try:
        return _pyjwt.decode(token, options={"verify_signature": False})
    except _pyjwt.PyJWTError as exc:
        raise JWTError(str(exc)) from exc
