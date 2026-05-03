from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from shadownet.crypto.jwt import JWTError, decode_unverified_claims, sign_jwt, verify_jwt
from shadownet.vc.errors import CredentialInvalid

if TYPE_CHECKING:
    from shadownet.crypto.ed25519 import Ed25519KeyPair
    from shadownet.did.resolver import Resolver

# RFC-0003 — Subject Credential, vc+jwt, EdDSA. Schema:
# shadownet-specs/schemas/credentials/subject-credential.schema.json

# Canonical-domain JSON-LD context URL. The Shadownet protocol is anchored at
# sh4dow.org. This constant MUST match the URL the spec lists in its example
# credential payload AND what every other SDK (Go, TS) emits, since interop is
# by string match.
SHADOWNET_VC_CONTEXT = "https://sh4dow.org/contexts/v1"
W3C_VC_V2_CONTEXT = "https://www.w3.org/ns/credentials/v2"

__all__ = [
    "SHADOWNET_VC_CONTEXT",
    "W3C_VC_V2_CONTEXT",
    "CredentialStatus",
    "CredentialSubject",
    "SubjectCredential",
    "decode_credential",
    "issue_credential",
    "verify_credential",
]


SubjectType = Literal["person", "organization"]


class CredentialSubject(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str = Field(pattern=r"^did:")
    level: str = Field(pattern=r"^urn:")
    subject_type: SubjectType = Field(alias="subjectType")


class CredentialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["BitstringStatusListEntry"] = "BitstringStatusListEntry"
    status_list_index: str = Field(alias="statusListIndex", pattern=r"^[0-9]+$")
    status_list_credential: str = Field(alias="statusListCredential")


class _VCBody(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    context: Annotated[list[str], Field(alias="@context", min_length=1)]
    type: Annotated[list[str], Field(min_length=1)]
    credential_subject: CredentialSubject = Field(alias="credentialSubject")
    credential_status: CredentialStatus | None = Field(default=None, alias="credentialStatus")

    @field_validator("context")
    @classmethod
    def _has_v2_context(cls, value: list[str]) -> list[str]:
        if "https://www.w3.org/ns/credentials/v2" not in value:
            raise ValueError("missing W3C VC v2 @context")
        return value

    @field_validator("type")
    @classmethod
    def _has_vc_type(cls, value: list[str]) -> list[str]:
        if "VerifiableCredential" not in value:
            raise ValueError("missing 'VerifiableCredential' type")
        return value


class SubjectCredential(BaseModel):
    """Decoded payload of a Shadownet Subject Credential JWT."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    iss: str = Field(pattern=r"^did:")
    sub: str = Field(pattern=r"^did:")
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    jti: str
    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    vc: _VCBody

    @field_validator("vc")
    @classmethod
    def _subject_id_matches_sub(cls, value: _VCBody, info: ValidationInfo) -> _VCBody:
        sub = info.data.get("sub")
        if sub is not None and value.credential_subject.id != sub:
            raise ValueError("vc.credentialSubject.id must equal sub")
        return value

    @property
    def level(self) -> str:
        return self.vc.credential_subject.level

    @property
    def subject_type(self) -> SubjectType:
        return self.vc.credential_subject.subject_type

    @property
    def status(self) -> CredentialStatus | None:
        return self.vc.credential_status

    def to_claims(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude_none=True)


def issue_credential(
    *,
    issuer_key: Ed25519KeyPair,
    issuer_kid: str,
    credential: SubjectCredential,
) -> str:
    """Sign ``credential`` as a vc+jwt with the issuer's key.

    ``issuer_kid`` MUST be a DID URL resolving to the issuer's signing key (RFC-0003 §Header).
    """
    return sign_jwt(
        credential.to_claims(),
        issuer_key,
        header_extras={"typ": "vc+jwt", "kid": issuer_kid},
    )


def decode_credential(token: str) -> SubjectCredential:
    """Parse a credential JWT *without* verifying its signature."""
    try:
        claims = decode_unverified_claims(token)
    except JWTError as exc:
        raise CredentialInvalid(f"credential is not a valid JWT: {exc}") from exc
    try:
        return SubjectCredential.model_validate(claims)
    except Exception as exc:
        raise CredentialInvalid(f"credential payload is invalid: {exc}") from exc


async def verify_credential(
    token: str,
    *,
    resolver: Resolver,
    now: int | None = None,
    leeway: int = 0,
) -> SubjectCredential:
    """Verify a credential JWT end-to-end and return the decoded payload.

    Steps (RFC-0003 §JWT shape, RFC-0004 §Trust evaluation step 3):
    - parse + schema-check
    - resolve issuer DID document, find the signing key (by ``kid`` if given)
    - verify EdDSA signature
    - check ``exp/iat`` (leeway optional)
    - structural rule: ``subjectType=organization`` requires a ``did:web`` subject
    """
    credential = decode_credential(token)
    try:
        from shadownet.crypto.jwt import decode_header

        header = decode_header(token)
    except JWTError as exc:
        raise CredentialInvalid(str(exc)) from exc
    issuer_doc = await resolver.resolve(credential.iss)
    key = issuer_doc.find_key(header.get("kid"))
    try:
        verify_jwt(token, key, issuer=credential.iss, leeway=leeway, verify_exp=True)
    except JWTError as exc:
        raise CredentialInvalid(str(exc)) from exc
    if credential.subject_type == "organization" and not credential.sub.startswith("did:web:"):
        raise CredentialInvalid("organization subject must use did:web")
    if now is not None and credential.exp < now - leeway:
        raise CredentialInvalid("credential expired")
    return credential


def new_credential(
    *,
    issuer: str,
    subject: str,
    level: str,
    subject_type: SubjectType,
    status: CredentialStatus | None = None,
    lifetime_seconds: int = 90 * 24 * 3600,
    issued_at: int | None = None,
    jti: str | None = None,
) -> SubjectCredential:
    """Build a fresh :class:`SubjectCredential` with sensible defaults."""
    iat = issued_at if issued_at is not None else int(time.time())
    return SubjectCredential.model_validate(
        {
            "iss": issuer,
            "sub": subject,
            "iat": iat,
            "exp": iat + lifetime_seconds,
            "jti": jti or f"urn:uuid:{uuid.uuid4()}",
            "shadownet:v": "0.1",
            "vc": {
                "@context": [
                    W3C_VC_V2_CONTEXT,
                    SHADOWNET_VC_CONTEXT,
                ],
                "type": ["VerifiableCredential", "ShadownetSubjectCredential"],
                "credentialSubject": {
                    "id": subject,
                    "level": level,
                    "subjectType": subject_type,
                },
                **({"credentialStatus": status.model_dump(by_alias=True)} if status else {}),
            },
        }
    )
