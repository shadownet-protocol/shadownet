from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shadownet.crypto.jwt import (
    JWTError,
    decode_header,
    decode_unverified_claims,
    sign_jwt,
    verify_jwt,
)
from shadownet.did.key import parse_did_key
from shadownet.vc.credential import SubjectCredential, verify_credential
from shadownet.vc.errors import CredentialInvalid, PresentationInvalid
from shadownet.vc.freshness import FreshnessProof, freshness_required, verify_freshness

if TYPE_CHECKING:
    from shadownet.crypto.ed25519 import Ed25519KeyPair
    from shadownet.did.resolver import Resolver
    from shadownet.trust import TrustStore
    from shadownet.vc.status_list import StatusListClient

# RFC-0003 §Presentation, RFC-0006 §Verifiable Presentation.

DEFAULT_PRESENTATION_TTL = 120  # ≤120s per RFC-0003

__all__ = [
    "DEFAULT_PRESENTATION_TTL",
    "VerifiablePresentation",
    "VerifiedPresentation",
    "mint_presentation",
    "verify_presentation",
]


class _VPBody(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    context: list[str] = Field(alias="@context", min_length=1)
    type: list[str] = Field(min_length=1)
    verifiable_credential: list[str] = Field(alias="verifiableCredential", min_length=1)

    @field_validator("type")
    @classmethod
    def _has_vp_type(cls, value: list[str]) -> list[str]:
        if "VerifiablePresentation" not in value:
            raise ValueError("missing 'VerifiablePresentation' type")
        return value


class VerifiablePresentation(BaseModel):
    """Decoded payload of a Verifiable Presentation JWT."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    iss: str = Field(pattern=r"^did:")
    aud: str = Field(pattern=r"^did:")
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    nonce: str | None = None
    shadownet_v: Literal["0.1"] | None = Field(default=None, alias="shadownet:v")
    vp: _VPBody

    def to_claims(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude_none=True)

    @property
    def credential_jwts(self) -> list[str]:
        return self.vp.verifiable_credential


@dataclass(frozen=True, slots=True)
class VerifiedPresentation:
    """Outcome of a successful VP verification.

    ``credentials`` are credentials that passed every check (signature, expiry,
    freshness, revocation) AND, if a trust store was supplied, whose
    ``(issuer, level)`` is accepted by it.
    """

    holder_did: str
    credentials: tuple[SubjectCredential, ...]
    freshness_proofs: tuple[FreshnessProof, ...]
    presentation: VerifiablePresentation


def mint_presentation(
    *,
    holder_key: Ed25519KeyPair,
    holder_did: str,
    audience_did: str,
    credentials: list[str],
    freshness_proofs: list[str] | None = None,
    nonce: str | None = None,
    issued_at: int | None = None,
    ttl_seconds: int = DEFAULT_PRESENTATION_TTL,
) -> str:
    """Mint a VP JWT signed by the holder, bundling credentials + freshness proofs."""
    iat = issued_at if issued_at is not None else int(time.time())
    bundle = list(credentials)
    if freshness_proofs:
        bundle.extend(freshness_proofs)
    payload = VerifiablePresentation.model_validate(
        {
            "iss": holder_did,
            "aud": audience_did,
            "iat": iat,
            "exp": iat + ttl_seconds,
            "nonce": nonce or uuid.uuid4().hex,
            "shadownet:v": "0.1",
            "vp": {
                "@context": ["https://www.w3.org/ns/credentials/v2"],
                "type": ["VerifiablePresentation"],
                "verifiableCredential": bundle,
            },
        }
    )
    return sign_jwt(payload.to_claims(), holder_key, header_extras={"typ": "vp+jwt"})


async def verify_presentation(
    token: str,
    *,
    resolver: Resolver,
    expected_audience: str,
    expected_nonce: str | None = None,
    now: int | None = None,
    leeway: int = 0,
    trust_store: TrustStore | None = None,
    status_list_client: StatusListClient | None = None,
    freshness_window_seconds: int = 24 * 3600,
) -> VerifiedPresentation:
    """Verify a VP and return the credentials that survived end-to-end checks.

    RFC-0006 §Verifiable Presentation + RFC-0004 §Trust evaluation. Concretely:

    - Outer JWS verified against the VP ``iss`` DID.
    - ``aud`` MUST equal ``expected_audience``; if ``expected_nonce`` is
      supplied the VP nonce MUST match.
    - Each inner credential is fully verified (signature, expiry, subject-type).
    - If a credential has a ``credentialStatus``, the bit is checked via
      ``status_list_client``. Above L1 this is fail-closed.
    - If a credential is older than ``freshness_window_seconds``, a matching
      freshness proof MUST be present and valid.
    - If ``trust_store`` is supplied, credentials whose ``(issuer, level)`` are
      not in it are dropped from ``credentials`` (still returned for visibility
      via ``presentation.credential_jwts``).
    """
    moment = now if now is not None else int(time.time())
    vp = _decode_outer(token)

    if vp.aud != expected_audience:
        raise PresentationInvalid(
            f"VP aud {vp.aud!r} does not match expected {expected_audience!r}"
        )
    if expected_nonce is not None and vp.nonce != expected_nonce:
        raise PresentationInvalid("VP nonce does not match verifier-supplied nonce")
    if vp.exp < moment - leeway:
        raise PresentationInvalid("VP expired")

    holder_doc = await resolver.resolve(vp.iss)
    holder_key = holder_doc.find_key(decode_header(token).get("kid"))
    try:
        verify_jwt(token, holder_key, audience=expected_audience, issuer=vp.iss, leeway=leeway)
    except JWTError as exc:
        raise PresentationInvalid(str(exc)) from exc

    if (
        vp.iss.startswith("did:key:")
        and parse_did_key(vp.iss).public_bytes != holder_key.public_bytes
    ):
        raise PresentationInvalid("VP iss did:key does not match resolved verification method")

    credentials, freshness_proofs = await _split_inner_jwts(vp, resolver, moment, leeway)

    accepted: list[SubjectCredential] = []
    for cred_jwt, cred in credentials:
        if freshness_required(cred, now=moment, window_seconds=freshness_window_seconds):
            matching = next(
                (
                    fp_jwt
                    for fp_jwt, fp in freshness_proofs
                    if fp.sub == cred.jti and fp.iss == cred.iss
                ),
                None,
            )
            if matching is None:
                raise PresentationInvalid(
                    f"credential {cred.jti} is older than freshness window but no freshness proof was presented"
                )
            await verify_freshness(
                matching,
                cred,
                resolver=resolver,
                now=moment,
                window_seconds=freshness_window_seconds,
            )

        if cred.status is not None:
            fail_closed = cred.level != "urn:shadownet:level:L1"
            if status_list_client is None and fail_closed:
                raise PresentationInvalid(
                    "status_list_client is required to verify a credential above L1"
                )
            if status_list_client is not None:
                await status_list_client.check_not_revoked(
                    cred.status.status_list_credential,
                    int(cred.status.status_list_index),
                    fail_closed=fail_closed,
                )

        if trust_store is not None and not trust_store.accepts(cred.iss, cred.level):
            # Untrusted-at-this-policy: drop silently from accepted set.
            # Visible to the caller via presentation.credential_jwts.
            _ = cred_jwt  # silence unused-warning when path not taken
            continue
        accepted.append(cred)

    return VerifiedPresentation(
        holder_did=vp.iss,
        credentials=tuple(accepted),
        freshness_proofs=tuple(fp for _, fp in freshness_proofs),
        presentation=vp,
    )


def _decode_outer(token: str) -> VerifiablePresentation:
    try:
        claims = decode_unverified_claims(token)
        header = decode_header(token)
    except JWTError as exc:
        raise PresentationInvalid(f"VP is not a valid JWT: {exc}") from exc
    if header.get("typ") not in {"vp+jwt", "JWT", None}:
        raise PresentationInvalid(f"unexpected VP typ {header.get('typ')!r}")
    try:
        return VerifiablePresentation.model_validate(claims)
    except Exception as exc:
        raise PresentationInvalid(f"VP payload invalid: {exc}") from exc


async def _split_inner_jwts(
    vp: VerifiablePresentation,
    resolver: Resolver,
    moment: int,
    leeway: int,
) -> tuple[
    list[tuple[str, SubjectCredential]],
    list[tuple[str, FreshnessProof]],
]:
    credentials: list[tuple[str, SubjectCredential]] = []
    freshness: list[tuple[str, FreshnessProof]] = []
    for jwt_str in vp.credential_jwts:
        try:
            inner_header = decode_header(jwt_str)
            inner_claims = decode_unverified_claims(jwt_str)
        except JWTError as exc:
            raise PresentationInvalid(f"inner JWT invalid: {exc}") from exc
        if inner_header.get("typ") == "vc+jwt" or "vc" in inner_claims:
            try:
                cred = await verify_credential(
                    jwt_str, resolver=resolver, now=moment, leeway=leeway
                )
            except CredentialInvalid as exc:
                raise PresentationInvalid(f"embedded credential invalid: {exc}") from exc
            if cred.sub != vp.iss:
                raise PresentationInvalid(
                    "credential subject does not match VP iss (holder ≠ subject)"
                )
            credentials.append((jwt_str, cred))
        elif inner_claims.get("shadownet:freshness") == "v1":
            try:
                freshness.append((jwt_str, FreshnessProof.model_validate(inner_claims)))
            except Exception as exc:
                raise PresentationInvalid(f"freshness proof invalid: {exc}") from exc
        else:
            raise PresentationInvalid("unrecognized JWT in vp.verifiableCredential")
    return credentials, freshness
