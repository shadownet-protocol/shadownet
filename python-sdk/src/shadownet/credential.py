"""Credentials — RFC 0001 §6.

One credential kind in v0.2: ``org_affiliation``. Plain JWS-compact JWT, no
W3C VC wrapping. Schema mirrors
``shadownet-specs/schemas/credentials/credential.schema.json``.

Verification is split into three concerns so callers can inject them:

  * **Issuer-key resolution** (``resolve_issuer_key``): given an issuer
    domain, return the multibase Ed25519 key. The default looks the key up
    via the provider DNS record (§4.2).
  * **Issuer-org authorization** (``check_issuer_authorized_for_org``):
    apply the §6.6 rules. Same-domain and sub-domain checks are local;
    DNS-delegate checks require a TXT lookup, which the default does.
  * **Revocation** (``check_revoked``): consult the status list (§6.4).
    Delegated to ``shadownet.status``.
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable

from shadownet.crypto.ed25519 import Ed25519KeyPair, SignatureError
from shadownet.crypto.jwt import (
    JWTError,
    decode_header,
    decode_unverified_claims,
    sign_jwt,
)
from shadownet.errors import ShadownetError
from shadownet.identifiers import (
    Identifier,
    IssuerIdentifier,
    is_public_key_identifier,
    is_subdomain_of,
    parse_public_key,
)
from shadownet.provider import (
    ProviderResolutionError,
    lookup_provider_record,
)

__all__ = [
    "CREDENTIAL_TYP",
    "MAX_LIFETIME_SECONDS",
    "ORG_AFFILIATION",
    "CredentialError",
    "CredentialPayload",
    "RevocationPointer",
    "VerifiedCredential",
    "default_issuer_authorization_check",
    "default_issuer_key_resolver",
    "mint_credential",
    "verify_credential",
]


CREDENTIAL_TYP: Final = "shadownet-cred+jwt"
ORG_AFFILIATION: Final = "org_affiliation"
# §6.3: org_affiliation max lifetime is 30 days.
MAX_LIFETIME_SECONDS: Final = 30 * 24 * 60 * 60
# §2: all time comparisons tolerate ±60 s of skew.
DEFAULT_LEEWAY_SECONDS: Final = 60


class CredentialError(ShadownetError):
    """A credential failed to parse, verify, or satisfy policy."""


class RevocationPointer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    epoch: str = Field(min_length=1)
    idx: int = Field(ge=0)


class CredentialPayload(BaseModel):
    """Decoded payload of a ``shadownet-cred+jwt``.

    ``iss`` and ``org`` accept either a domain (Shadowname-mode issuer / org)
    or a multibase Ed25519 public key (keyed issuer / Hub). ``sub`` accepts
    a Shadowname or a public key (direct-mode Shadow).
    """

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    iss: IssuerIdentifier
    sub: Identifier
    kind: Annotated[str, Field(pattern="^[a-z_]+$")]
    org: IssuerIdentifier
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    rev: RevocationPointer


class VerifiedCredential(BaseModel):
    """A credential whose signature, claims, lifetime, and §6.6 authorization
    have been checked. Revocation and trust-store policy are layered on by
    the caller (see ``shadownet.trust``)."""

    model_config = ConfigDict(frozen=True)
    payload: CredentialPayload
    issuer_key: str
    raw_jws: str


def mint_credential(
    payload: CredentialPayload,
    issuer_key: Ed25519KeyPair,
) -> str:
    if payload.exp - payload.iat > MAX_LIFETIME_SECONDS:
        raise CredentialError(
            f"{ORG_AFFILIATION} lifetime exceeds 30 days (exp - iat = {payload.exp - payload.iat}s)"
        )
    if payload.kind != ORG_AFFILIATION:
        raise CredentialError(f"unknown credential kind {payload.kind!r}")
    claims = payload.model_dump()
    return sign_jwt(claims, issuer_key, header_extras={"typ": CREDENTIAL_TYP})


def verify_credential(
    token: str,
    *,
    now: int | None = None,
    leeway: int = DEFAULT_LEEWAY_SECONDS,
    resolve_issuer_key: Callable[[str], str] | None = None,
    check_issuer_authorized_for_org: Callable[[str, str], None] | None = None,
) -> VerifiedCredential:
    """Run §6 validation steps 1-6 against ``token``.

    Steps 7 (revocation) and 8 (trust store) are not the responsibility of
    this function; see ``shadownet.status`` and ``shadownet.trust``.
    """
    try:
        header = decode_header(token)
    except JWTError as exc:
        raise CredentialError(f"invalid JWS: {exc}") from exc
    if header.get("typ") != CREDENTIAL_TYP:
        raise CredentialError(f"typ must be {CREDENTIAL_TYP!r}, got {header.get('typ')!r}")
    if header.get("alg") != "EdDSA":
        raise CredentialError(f"alg must be EdDSA, got {header.get('alg')!r}")

    try:
        unverified = decode_unverified_claims(token)
    except JWTError as exc:
        raise CredentialError(f"unable to decode claims: {exc}") from exc

    try:
        payload = CredentialPayload.model_validate(unverified)
    except ValidationError as exc:
        raise CredentialError(f"credential payload invalid: {exc}") from exc

    if payload.kind != ORG_AFFILIATION:
        raise CredentialError(f"unknown credential kind {payload.kind!r}")

    if payload.exp - payload.iat > MAX_LIFETIME_SECONDS:
        raise CredentialError("credential lifetime exceeds 30 days")

    current = int(time.time()) if now is None else now
    if payload.exp < current - leeway:
        raise CredentialError("credential expired")
    if payload.iat > current + leeway:
        raise CredentialError("credential iat in the future")

    resolver = resolve_issuer_key or default_issuer_key_resolver
    issuer_key_multibase = resolver(payload.iss)
    issuer_pk_bytes = parse_public_key(issuer_key_multibase)
    issuer_key = Ed25519KeyPair.from_public_bytes(issuer_pk_bytes)

    try:
        _verify_jws_signature(token, issuer_key)
    except (JWTError, SignatureError) as exc:
        raise CredentialError(f"signature verification failed: {exc}") from exc

    authorize = check_issuer_authorized_for_org or default_issuer_authorization_check
    authorize(payload.iss, payload.org)

    return VerifiedCredential(payload=payload, issuer_key=issuer_key_multibase, raw_jws=token)


def default_issuer_key_resolver(issuer: str) -> str:
    """Resolve the issuer's signing key.

    Keyed issuers (RFC 0001 §3.3): ``iss`` IS the verification key, returned
    verbatim. Domain issuers: DNS-resolve ``_shadownet.<iss>`` TXT (§4.2) and
    return the published ``pk``.
    """
    if is_public_key_identifier(issuer):
        return issuer
    try:
        record = lookup_provider_record(issuer)
    except ProviderResolutionError as exc:
        raise CredentialError(f"could not resolve issuer {issuer!r}: {exc}") from exc
    if not record.provider_keys:
        raise CredentialError(f"issuer {issuer!r} has no provider key")
    return record.provider_keys[0]


def default_issuer_authorization_check(issuer: str, org: str) -> None:
    """Apply RFC 0001 §6.6.

    Rule 1 — ``iss == org`` — is the **only** path open to keyed issuers and
    the trivial case for domain issuers. Rules 2 (sub-domain) and 3 (DNS
    delegate) are domain-only.
    """
    if issuer == org:
        return
    if is_public_key_identifier(issuer) or is_public_key_identifier(org):
        raise CredentialError(
            f"keyed issuer {issuer!r} not authorized to attest for org {org!r} "
            "(only iss == org accepted for keyed issuers per §6.6)"
        )
    if is_subdomain_of(issuer, org):
        return
    try:
        org_record = lookup_provider_record(org)
    except ProviderResolutionError as exc:
        raise CredentialError(f"could not verify issuer authorization for {org!r}: {exc}") from exc
    if issuer.lower() in (d.lower() for d in org_record.delegates):
        return
    raise CredentialError(f"issuer {issuer!r} is not authorized to attest for org {org!r}")


def _verify_jws_signature(token: str, issuer_key: Ed25519KeyPair) -> None:
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("malformed JWS compact serialization")
    signing_input = (parts[0] + "." + parts[1]).encode("ascii")
    sig = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    issuer_key.verify(sig, signing_input)
