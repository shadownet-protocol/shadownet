from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from shadownet.crypto.jwt import (
    JWTError,
    decode_header,
    decode_unverified_claims,
    sign_jwt,
    verify_jwt,
)

if TYPE_CHECKING:
    from shadownet.crypto.ed25519 import Ed25519KeyPair
    from shadownet.did.resolver import Resolver

# RFC-0006 §Session token. exp - iat ≤ 300s; signed by caller; `aud` = callee DID.

DEFAULT_SESSION_TOKEN_TTL = 300

__all__ = [
    "DEFAULT_SESSION_TOKEN_TTL",
    "SessionToken",
    "mint_session_token",
    "verify_session_token",
]


class SessionToken(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    iss: str = Field(pattern=r"^did:")
    aud: str = Field(pattern=r"^did:")
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    jti: str
    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    purpose: Literal["a2a-session"]


def mint_session_token(
    *,
    holder_key: Ed25519KeyPair,
    holder_did: str,
    audience_did: str,
    kid: str | None = None,
    issued_at: int | None = None,
    ttl_seconds: int = DEFAULT_SESSION_TOKEN_TTL,
) -> str:
    """Mint an A2A session-token JWT per RFC-0006 §Session token.

    The header carries ``kid`` for symmetry with subject-auth and credential
    JWTs (RFC-0006 doesn't mandate it explicitly, but stricter peer SDKs may
    require one). ``kid`` defaults to the bare ``holder_did``; ``did:web``
    callers with multiple keys SHOULD pass an explicit ``<did>#<key-id>``.
    """
    if ttl_seconds > DEFAULT_SESSION_TOKEN_TTL:
        raise ValueError(f"session-token TTL must be ≤ {DEFAULT_SESSION_TOKEN_TTL}s per RFC-0006")
    iat = issued_at if issued_at is not None else int(time.time())
    claims = SessionToken(
        iss=holder_did,
        aud=audience_did,
        iat=iat,
        exp=iat + ttl_seconds,
        jti=f"urn:uuid:{uuid.uuid4()}",
        shadownet_v="0.1",
        purpose="a2a-session",
    )
    return sign_jwt(
        claims.model_dump(by_alias=True),
        holder_key,
        header_extras={"kid": kid or holder_did},
    )


async def verify_session_token(
    token: str,
    *,
    expected_audience: str,
    resolver: Resolver,
    now: int | None = None,
    leeway: int = 0,
) -> SessionToken:
    moment = now if now is not None else int(time.time())
    try:
        claims = decode_unverified_claims(token)
        header = decode_header(token)
    except JWTError as exc:
        from shadownet.a2a.errors import PresentationInvalidError

        raise PresentationInvalidError(f"session token invalid: {exc}") from exc

    from shadownet.a2a.errors import PresentationInvalidError

    try:
        parsed = SessionToken.model_validate(claims)
    except Exception as exc:
        raise PresentationInvalidError(f"session token payload invalid: {exc}") from exc
    if parsed.aud != expected_audience:
        raise PresentationInvalidError("session token aud does not match callee DID")
    if parsed.exp - parsed.iat > DEFAULT_SESSION_TOKEN_TTL + leeway:
        raise PresentationInvalidError("session token TTL exceeds 300s cap")
    holder_doc = await resolver.resolve(parsed.iss)
    key = holder_doc.find_key(header.get("kid"))
    try:
        verify_jwt(
            token,
            key,
            audience=expected_audience,
            issuer=parsed.iss,
            leeway=leeway,
            verify_exp=True,
        )
    except JWTError as exc:
        raise PresentationInvalidError(str(exc)) from exc
    if parsed.exp < moment - leeway:
        raise PresentationInvalidError("session token expired")
    return parsed
