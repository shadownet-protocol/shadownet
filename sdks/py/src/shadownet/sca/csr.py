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
from shadownet.sca.errors import CSRInvalid

if TYPE_CHECKING:
    from shadownet.crypto.ed25519 import Ed25519KeyPair
    from shadownet.did.resolver import Resolver

# RFC-0004 §POST /issuance — CSR is a JWT signed by the subject's key.

__all__ = [
    "CSRRequest",
    "CertificateSigningRequest",
    "build_csr",
    "build_subject_auth",
    "verify_csr",
    "verify_subject_auth",
]

DEFAULT_CSR_TTL_SECONDS = 600
DEFAULT_AUTH_TTL_SECONDS = 60


class CSRRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    level: str = Field(pattern=r"^urn:")
    subject_type: Literal["person", "organization"] = Field(alias="subjectType")


class CertificateSigningRequest(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    iss: str = Field(pattern=r"^did:")
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    aud: str = Field(pattern=r"^did:")
    request: CSRRequest

    def to_claims(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude_none=True)


def build_csr(
    *,
    holder_key: Ed25519KeyPair,
    holder_did: str,
    sca_did: str,
    level: str,
    subject_type: Literal["person", "organization"],
    issued_at: int | None = None,
    ttl_seconds: int = DEFAULT_CSR_TTL_SECONDS,
) -> str:
    iat = issued_at if issued_at is not None else int(time.time())
    csr = CertificateSigningRequest(
        iss=holder_did,
        iat=iat,
        exp=iat + ttl_seconds,
        aud=sca_did,
        request=CSRRequest(level=level, subjectType=subject_type),
    )
    return sign_jwt(csr.to_claims(), holder_key)


async def verify_csr(
    token: str,
    *,
    resolver: Resolver,
    expected_audience: str,
    now: int | None = None,
    leeway: int = 0,
) -> CertificateSigningRequest:
    """Verify a CSR JWT — caller is the SCA."""
    moment = now if now is not None else int(time.time())
    try:
        claims = decode_unverified_claims(token)
        header = decode_header(token)
    except JWTError as exc:
        raise CSRInvalid(f"CSR is not a valid JWT: {exc}") from exc
    try:
        csr = CertificateSigningRequest.model_validate(claims)
    except Exception as exc:
        raise CSRInvalid(f"CSR payload invalid: {exc}") from exc
    if csr.aud != expected_audience:
        raise CSRInvalid(f"CSR aud {csr.aud!r} does not match SCA DID {expected_audience!r}")
    holder_doc = await resolver.resolve(csr.iss)
    holder_key = holder_doc.find_key(header.get("kid"))
    try:
        verify_jwt(
            token,
            holder_key,
            audience=expected_audience,
            issuer=csr.iss,
            leeway=leeway,
            verify_exp=True,
        )
    except JWTError as exc:
        raise CSRInvalid(str(exc)) from exc
    if csr.exp < moment - leeway:
        raise CSRInvalid("CSR expired")
    return csr


# ---------- Subject-auth JWT (RFC-0004 §Common: subject authentication) ----------


class SubjectAuthClaims(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    iss: str = Field(pattern=r"^did:")
    aud: str = Field(pattern=r"^did:")
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    jti: str
    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    purpose: Literal["sca-request"]


def build_subject_auth(
    *,
    holder_key: Ed25519KeyPair,
    holder_did: str,
    sca_did: str,
    issued_at: int | None = None,
    ttl_seconds: int = DEFAULT_AUTH_TTL_SECONDS,
) -> str:
    if ttl_seconds > DEFAULT_AUTH_TTL_SECONDS:
        raise ValueError(f"subject-auth JWT TTL must be ≤ {DEFAULT_AUTH_TTL_SECONDS}s per RFC-0004")
    iat = issued_at if issued_at is not None else int(time.time())
    claims = SubjectAuthClaims(
        iss=holder_did,
        aud=sca_did,
        iat=iat,
        exp=iat + ttl_seconds,
        jti=f"urn:uuid:{uuid.uuid4()}",
        shadownet_v="0.1",
        purpose="sca-request",
    )
    return sign_jwt(claims.model_dump(by_alias=True), holder_key)


async def verify_subject_auth(
    token: str,
    *,
    resolver: Resolver,
    expected_audience: str,
    now: int | None = None,
    leeway: int = 0,
) -> SubjectAuthClaims:
    moment = now if now is not None else int(time.time())
    try:
        claims = decode_unverified_claims(token)
        header = decode_header(token)
    except JWTError as exc:
        raise CSRInvalid(f"subject-auth JWT invalid: {exc}") from exc
    try:
        auth = SubjectAuthClaims.model_validate(claims)
    except Exception as exc:
        raise CSRInvalid(f"subject-auth payload invalid: {exc}") from exc
    if auth.aud != expected_audience:
        raise CSRInvalid("subject-auth aud does not match SCA")
    if auth.exp - auth.iat > DEFAULT_AUTH_TTL_SECONDS + leeway:
        raise CSRInvalid("subject-auth TTL exceeds 60s cap")
    holder_doc = await resolver.resolve(auth.iss)
    key = holder_doc.find_key(header.get("kid"))
    try:
        verify_jwt(
            token,
            key,
            audience=expected_audience,
            issuer=auth.iss,
            leeway=leeway,
            verify_exp=True,
        )
    except JWTError as exc:
        raise CSRInvalid(str(exc)) from exc
    if auth.exp < moment - leeway:
        raise CSRInvalid("subject-auth expired")
    return auth
