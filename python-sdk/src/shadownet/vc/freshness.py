from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from shadownet.crypto.jwt import (
    JWTError,
    decode_header,
    decode_unverified_claims,
    sign_jwt,
    verify_jwt,
)
from shadownet.vc.errors import FreshnessExpired

if TYPE_CHECKING:
    from shadownet.crypto.ed25519 import Ed25519KeyPair
    from shadownet.did.resolver import Resolver
    from shadownet.vc.credential import SubjectCredential

# RFC-0003 §Lifetimes and freshness — short-lived (≤24h) JWT signed by the SCA
# attesting that a credential jti is not revoked at iat.

DEFAULT_FRESHNESS_WINDOW_SECONDS = 24 * 3600
DEFAULT_FRESHNESS_LIFETIME_SECONDS = 24 * 3600

__all__ = [
    "DEFAULT_FRESHNESS_LIFETIME_SECONDS",
    "DEFAULT_FRESHNESS_WINDOW_SECONDS",
    "FreshnessProof",
    "mint_freshness_proof",
    "verify_freshness",
]


class FreshnessProof(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    iss: str = Field(pattern=r"^did:")
    sub: str
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    shadownet_freshness: Literal["v1"] = Field(alias="shadownet:freshness")

    def to_claims(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude_none=True)


def mint_freshness_proof(
    *,
    issuer_key: Ed25519KeyPair,
    issuer_did: str,
    issuer_kid: str,
    credential_jti: str,
    issued_at: int | None = None,
    lifetime_seconds: int = DEFAULT_FRESHNESS_LIFETIME_SECONDS,
) -> str:
    iat = issued_at if issued_at is not None else int(time.time())
    proof = FreshnessProof.model_validate(
        {
            "iss": issuer_did,
            "sub": credential_jti,
            "iat": iat,
            "exp": iat + lifetime_seconds,
            "shadownet:freshness": "v1",
        }
    )
    return sign_jwt(
        proof.to_claims(),
        issuer_key,
        header_extras={"kid": issuer_kid},
    )


async def verify_freshness(
    token: str,
    credential: SubjectCredential,
    *,
    resolver: Resolver,
    now: int,
    window_seconds: int = DEFAULT_FRESHNESS_WINDOW_SECONDS,
) -> FreshnessProof:
    """Verify a freshness proof against ``credential`` and the verifier's window.

    RFC-0003 §Lifetimes: ``iat`` MUST be within ``window_seconds`` of ``now``;
    issuer MUST equal the credential's issuer; ``sub`` MUST equal the credential's ``jti``.
    """
    try:
        claims = decode_unverified_claims(token)
        header = decode_header(token)
    except JWTError as exc:
        raise FreshnessExpired(f"freshness proof is not a valid JWT: {exc}") from exc
    try:
        proof = FreshnessProof.model_validate(claims)
    except Exception as exc:
        raise FreshnessExpired(f"freshness proof payload invalid: {exc}") from exc
    if proof.iss != credential.iss:
        raise FreshnessExpired("freshness proof issuer does not match credential issuer")
    if proof.sub != credential.jti:
        raise FreshnessExpired("freshness proof sub does not match credential jti")
    issuer_doc = await resolver.resolve(proof.iss)
    key = issuer_doc.find_key(header.get("kid"))
    try:
        verify_jwt(token, key, issuer=proof.iss, verify_exp=True)
    except JWTError as exc:
        raise FreshnessExpired(str(exc)) from exc
    if now - proof.iat > window_seconds:
        raise FreshnessExpired(
            f"freshness proof iat is older than window ({now - proof.iat}s > {window_seconds}s)"
        )
    if proof.exp < now:
        raise FreshnessExpired("freshness proof expired")
    return proof


def freshness_required(
    credential: SubjectCredential,
    *,
    now: int,
    window_seconds: int = DEFAULT_FRESHNESS_WINDOW_SECONDS,
) -> bool:
    """Return True if RFC-0003 mandates a freshness proof for ``credential`` at ``now``."""
    return now - credential.iat > window_seconds


__all__.append("freshness_required")
