from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shadownet.crypto.jwt import (
    JWTError,
    decode_header,
    decode_unverified_claims,
    sign_jwt,
    verify_jwt,
)
from shadownet.sns.errors import ShadownameInvalid

if TYPE_CHECKING:
    from shadownet.crypto.ed25519 import Ed25519KeyPair
    from shadownet.did.resolver import Resolver

# RFC-0005 §Records — JSON record signed as a JWT by the provider.

__all__ = [
    "PublicKeyJWK",
    "SNSRecord",
    "SignedSNSRecord",
    "parse_shadowname",
    "sign_record",
    "verify_record",
]

_LOCAL_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,63}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?(:\d+)?$")
_MIN_TTL = 60
_MAX_TTL = 86400


def parse_shadowname(shadowname: str) -> tuple[str, str]:
    """Split a shadowname into ``(local, provider)``. Local part is lowercased per RFC-0005."""
    if shadowname.count("@") != 1:
        raise ShadownameInvalid("shadowname must contain exactly one '@'")
    local, provider = shadowname.split("@", 1)
    if not _LOCAL_RE.match(local):
        raise ShadownameInvalid("local part must be 1-63 chars from [A-Za-z0-9_.-]")
    if not _HOST_RE.match(provider):
        raise ShadownameInvalid("provider must be a valid host[:port]")
    return local.lower(), provider.lower()


class PublicKeyJWK(BaseModel):
    model_config = ConfigDict(extra="allow")

    kty: Literal["OKP"]
    crv: Literal["Ed25519"]
    x: str


class SNSRecord(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    shadowname: str
    did: str = Field(pattern=r"^did:")
    endpoint: str
    public_key: PublicKeyJWK = Field(alias="publicKey")
    subject_type: Literal["person", "organization"] = Field(alias="subjectType")
    ttl: int = Field(ge=_MIN_TTL, le=_MAX_TTL)
    issued_at: int = Field(alias="issuedAt", ge=0)
    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")

    @field_validator("shadowname")
    @classmethod
    def _validate_grammar(cls, value: str) -> str:
        parse_shadowname(value)
        return value.lower()


class SignedSNSRecord(BaseModel):
    """The JWT envelope wrapping an :class:`SNSRecord`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    iss: str = Field(pattern=r"^did:")
    sub: str
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    record: SNSRecord


def sign_record(
    *,
    provider_key: Ed25519KeyPair,
    provider_did: str,
    record: SNSRecord,
    issued_at: int | None = None,
) -> str:
    iat = issued_at if issued_at is not None else int(time.time())
    if record.issued_at != iat:
        record = record.model_copy(update={"issued_at": iat})
    envelope = SignedSNSRecord(
        iss=provider_did,
        sub=record.shadowname,
        iat=iat,
        exp=iat + record.ttl,
        shadownet_v="0.1",
        record=record,
    )
    return sign_jwt(envelope.model_dump(by_alias=True), provider_key)


async def verify_record(
    token: str,
    *,
    expected_provider_did: str,
    resolver: Resolver,
    now: int | None = None,
    leeway: int = 0,
) -> SNSRecord:
    """Verify the JWT envelope signature and return the inner :class:`SNSRecord`.

    The provider DID is compared to ``expected_provider_did`` — callers MUST
    derive that from the shadowname's host part (e.g. via :func:`parse_shadowname`)
    or the resolver they used.
    """
    moment = now if now is not None else int(time.time())
    try:
        claims = decode_unverified_claims(token)
        header = decode_header(token)
    except JWTError as exc:
        raise ShadownameInvalid(f"SNS record JWT invalid: {exc}") from exc
    try:
        envelope = SignedSNSRecord.model_validate(claims)
    except Exception as exc:
        raise ShadownameInvalid(f"SNS record payload invalid: {exc}") from exc
    if envelope.iss != expected_provider_did:
        raise ShadownameInvalid(
            f"record signed by {envelope.iss!r}, expected {expected_provider_did!r}"
        )
    if envelope.exp - envelope.iat != envelope.record.ttl:
        raise ShadownameInvalid("envelope exp-iat must equal record.ttl")
    provider_doc = await resolver.resolve(envelope.iss)
    key = provider_doc.find_key(header.get("kid"))
    try:
        verify_jwt(token, key, issuer=envelope.iss, leeway=leeway, verify_exp=True)
    except JWTError as exc:
        raise ShadownameInvalid(str(exc)) from exc
    if envelope.exp < moment - leeway:
        raise ShadownameInvalid("SNS record expired")
    return envelope.record
