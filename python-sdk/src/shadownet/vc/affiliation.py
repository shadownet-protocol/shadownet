from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from shadownet.crypto.jwt import JWTError, decode_unverified_claims, sign_jwt, verify_jwt
from shadownet.vc.credential import (
    SHADOWNET_VC_CONTEXT,
    W3C_VC_V2_CONTEXT,
    CredentialStatus,
)
from shadownet.vc.errors import CredentialInvalid

if TYPE_CHECKING:
    from shadownet.crypto.ed25519 import Ed25519KeyPair
    from shadownet.did.resolver import Resolver

# RFC-0003 §AffiliationCredential — vc+jwt, EdDSA. Schema:
# shadownet-specs/schemas/credentials/affiliation-credential.schema.json

SHADOWNET_AFFILIATION_VC_TYPE = "ShadownetAffiliationCredential"

# §Lifetime: SHOULD ≤ 30 days. The 30-day SHOULD-cap is the absolute upper
# bound this SDK applies on construction; operators can set tighter via
# lifetime_seconds.
MAX_AFFILIATION_LIFETIME_SECONDS = 30 * 24 * 3600

__all__ = [
    "MAX_AFFILIATION_LIFETIME_SECONDS",
    "SHADOWNET_AFFILIATION_VC_TYPE",
    "AffiliationCredential",
    "AffiliationCredentialSubject",
    "decode_affiliation_credential",
    "issue_affiliation_credential",
    "new_affiliation_credential",
    "verify_affiliation_credential",
]


class AffiliationCredentialSubject(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str = Field(pattern=r"^did:")
    affiliation: str = Field(pattern=r"^did:web:")
    role: str | None = None
    groups: list[str] | None = None


class _AffiliationVCBody(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    context: Annotated[list[str], Field(alias="@context", min_length=1)]
    type: Annotated[list[str], Field(min_length=1)]
    credential_subject: AffiliationCredentialSubject = Field(alias="credentialSubject")
    credential_status: CredentialStatus | None = Field(default=None, alias="credentialStatus")

    @field_validator("context")
    @classmethod
    def _has_v2_context(cls, value: list[str]) -> list[str]:
        if W3C_VC_V2_CONTEXT not in value:
            raise ValueError("missing W3C VC v2 @context")
        return value

    @field_validator("type")
    @classmethod
    def _has_affiliation_type(cls, value: list[str]) -> list[str]:
        if "VerifiableCredential" not in value:
            raise ValueError("missing 'VerifiableCredential' type")
        if SHADOWNET_AFFILIATION_VC_TYPE not in value:
            raise ValueError(f"missing '{SHADOWNET_AFFILIATION_VC_TYPE}' type")
        return value


class AffiliationCredential(BaseModel):
    """Decoded payload of an AffiliationCredential JWT (RFC-0003 §AffiliationCredential)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    iss: str = Field(pattern=r"^did:web:")
    sub: str = Field(pattern=r"^did:")
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    jti: str
    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    vc: _AffiliationVCBody

    @field_validator("vc")
    @classmethod
    def _subject_id_matches_sub(
        cls, value: _AffiliationVCBody, info: ValidationInfo
    ) -> _AffiliationVCBody:
        sub = info.data.get("sub")
        if sub is not None and value.credential_subject.id != sub:
            raise ValueError("vc.credentialSubject.id must equal sub")
        return value

    @property
    def affiliation(self) -> str:
        return self.vc.credential_subject.affiliation

    @property
    def role(self) -> str | None:
        return self.vc.credential_subject.role

    @property
    def groups(self) -> list[str]:
        return list(self.vc.credential_subject.groups or [])

    @property
    def status(self) -> CredentialStatus | None:
        return self.vc.credential_status

    def to_claims(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude_none=True)


def issue_affiliation_credential(
    *,
    issuer_key: Ed25519KeyPair,
    issuer_kid: str,
    credential: AffiliationCredential,
) -> str:
    """Sign ``credential`` as a vc+jwt with the issuer's key.

    ``issuer_kid`` MUST be a DID URL resolving to the issuer's signing key —
    typically the affiliation org's own did:web key, or a delegated SCA's.
    """
    return sign_jwt(
        credential.to_claims(),
        issuer_key,
        header_extras={"typ": "vc+jwt", "kid": issuer_kid},
    )


def decode_affiliation_credential(token: str) -> AffiliationCredential:
    """Parse an AffiliationCredential JWT *without* verifying its signature."""
    try:
        claims = decode_unverified_claims(token)
    except JWTError as exc:
        raise CredentialInvalid(f"affiliation credential is not a valid JWT: {exc}") from exc
    try:
        return AffiliationCredential.model_validate(claims)
    except Exception as exc:
        raise CredentialInvalid(f"affiliation credential payload is invalid: {exc}") from exc


async def verify_affiliation_credential(
    token: str,
    *,
    resolver: Resolver,
    now: int | None = None,
    leeway: int = 0,
) -> AffiliationCredential:
    """Verify an AffiliationCredential JWT end-to-end and return the decoded payload.

    Steps (RFC-0003 §AffiliationCredential §Verifier acceptance):

    1. Parse + schema-check the JWT.
    2. Resolve the issuer DID document; verify the EdDSA signature.
    3. Domain control: when ``iss != credentialSubject.affiliation``, resolve
       the affiliation org's DID document and require ``iss`` to appear in
       ``shadownet:delegatedIssuers``.
    4. Standard ``exp`` check.

    Institutional trust (deny-list / substitute-for-personhood policy) is
    enforced at the predicate / Verifier layer, not here.
    """
    credential = decode_affiliation_credential(token)
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
    if credential.iss != credential.affiliation:
        org_doc = await resolver.resolve(credential.affiliation)
        if not org_doc.is_delegated_issuer(credential.iss):
            raise CredentialInvalid(
                f"issuer {credential.iss!r} is not listed in {credential.affiliation!r} "
                "shadownet:delegatedIssuers"
            )
    if not credential.sub.startswith(("did:key:", "did:web:")):
        raise CredentialInvalid("affiliation subject must be did:key or did:web")
    if now is not None and credential.exp < now - leeway:
        raise CredentialInvalid("affiliation credential expired")
    return credential


def new_affiliation_credential(
    *,
    issuer: str,
    subject: str,
    affiliation: str,
    role: str | None = None,
    groups: list[str] | None = None,
    status: CredentialStatus | None = None,
    lifetime_seconds: int = MAX_AFFILIATION_LIFETIME_SECONDS,
    issued_at: int | None = None,
    jti: str | None = None,
) -> AffiliationCredential:
    """Build a fresh :class:`AffiliationCredential` with sensible defaults."""
    if lifetime_seconds > MAX_AFFILIATION_LIFETIME_SECONDS:
        raise ValueError(f"affiliation lifetime_seconds {lifetime_seconds} exceeds 30-day cap")
    iat = issued_at if issued_at is not None else int(time.time())
    subject_fields: dict[str, object] = {"id": subject, "affiliation": affiliation}
    if role is not None:
        subject_fields["role"] = role
    if groups is not None:
        subject_fields["groups"] = list(groups)
    payload: dict[str, object] = {
        "iss": issuer,
        "sub": subject,
        "iat": iat,
        "exp": iat + lifetime_seconds,
        "jti": jti or f"urn:uuid:{uuid.uuid4()}",
        "shadownet:v": "0.1",
        "vc": {
            "@context": [W3C_VC_V2_CONTEXT, SHADOWNET_VC_CONTEXT],
            "type": ["VerifiableCredential", SHADOWNET_AFFILIATION_VC_TYPE],
            "credentialSubject": subject_fields,
            **({"credentialStatus": status.model_dump(by_alias=True)} if status else {}),
        },
    }
    return AffiliationCredential.model_validate(payload)
