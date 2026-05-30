"""Certificate signing request — RFC 0001 §6.5.

A Subject proves possession of their signing key by minting a
``shadownet-csr+jwt`` and POSTing it to the issuer's
``/.well-known/shadownet/issue`` endpoint. The issuer either returns a
credential JWS (200), redirects the Subject through an out-of-band ceremony
(409 ceremony_pending), rejects (403 ceremony_failed), or rate-limits (429).

Schema mirrors ``shadownet-specs/schemas/credentials/csr.schema.json``.
Idempotent re-POSTs are expected within the ceremony lifetime (§6.5).
"""

from __future__ import annotations

import base64
import json
import time
from typing import Annotated, Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shadownet.crypto.ed25519 import Ed25519KeyPair, SignatureError
from shadownet.crypto.jwt import (
    JWTError,
    decode_header,
    decode_unverified_claims,
    sign_jwt,
)
from shadownet.errors import ShadownetError
from shadownet.identifiers import (
    Domain,  # noqa: TC001  pydantic needs Domain at runtime
    Shadowname,  # noqa: TC001  pydantic needs Shadowname at runtime
)

__all__ = [
    "CSR_TYP",
    "MAX_CSR_LIFETIME_SECONDS",
    "CeremonyFailedError",
    "CeremonyPendingError",
    "CsrError",
    "CsrPayload",
    "CsrRateLimitedError",
    "CsrRequest",
    "IssuanceResult",
    "build_issuer_url",
    "mint_csr",
    "submit_csr",
    "verify_csr",
]


CSR_TYP: Final = "shadownet-csr+jwt"
ISSUER_MEDIA_TYPE: Final = "application/jose"
# §6.5 RECOMMENDED short-lived: ≤ 600s.
MAX_CSR_LIFETIME_SECONDS: Final = 600
DEFAULT_LEEWAY_SECONDS: Final = 60
DEFAULT_TIMEOUT: Final = 10.0


class CsrError(ShadownetError):
    """CSR failed to mint, verify, or submit."""


class CeremonyPendingError(CsrError):
    """Issuer returned 409 ceremony_pending; complete the ceremony at ``next``."""

    def __init__(self, next_url: str) -> None:
        super().__init__(f"ceremony pending; next={next_url!r}")
        self.next_url = next_url


class CeremonyFailedError(CsrError):
    """Issuer returned 403 ceremony_failed."""


class CsrRateLimitedError(CsrError):
    """Issuer returned 429 rate_limited."""


class CsrRequest(BaseModel):
    """Body of the ``req`` field — the credential being asked for."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Annotated[str, Field(pattern="^[a-z_]+$")]
    org: Domain


class CsrPayload(BaseModel):
    """Decoded payload of a ``shadownet-csr+jwt``."""

    model_config = ConfigDict(extra="allow", frozen=True)

    iss: Shadowname
    aud: Domain
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    req: CsrRequest


class IssuanceResult(BaseModel):
    """Result of a successful issuance — the credential JWS the Subject keeps."""

    model_config = ConfigDict(frozen=True)
    credential: str


def mint_csr(payload: CsrPayload, subject_key: Ed25519KeyPair) -> str:
    if payload.exp - payload.iat > MAX_CSR_LIFETIME_SECONDS:
        raise CsrError(
            f"CSR lifetime exceeds {MAX_CSR_LIFETIME_SECONDS}s (got {payload.exp - payload.iat}s)"
        )
    if payload.exp <= payload.iat:
        raise CsrError("CSR exp must be greater than iat")
    return sign_jwt(payload.model_dump(), subject_key, header_extras={"typ": CSR_TYP})


def verify_csr(
    token: str,
    subject_key: Ed25519KeyPair,
    *,
    expected_audience: str,
    now: int | None = None,
    leeway: int = DEFAULT_LEEWAY_SECONDS,
) -> CsrPayload:
    """Issuer-side: verify a CSR signed by the Subject named in ``iss``.

    Caller supplies the Subject's public key (looked up from the AgentCard
    for ``iss``). The ``expected_audience`` should be the issuer's own
    domain so a CSR aimed at a different issuer is rejected.
    """
    try:
        header = decode_header(token)
    except JWTError as exc:
        raise CsrError(f"invalid JWS header: {exc}") from exc
    if header.get("typ") != CSR_TYP:
        raise CsrError(f"typ must be {CSR_TYP!r}, got {header.get('typ')!r}")
    if header.get("alg") != "EdDSA":
        raise CsrError(f"alg must be EdDSA, got {header.get('alg')!r}")

    try:
        unverified = decode_unverified_claims(token)
    except JWTError as exc:
        raise CsrError(f"unable to decode CSR claims: {exc}") from exc

    try:
        payload = CsrPayload.model_validate(unverified)
    except ValidationError as exc:
        raise CsrError(f"CSR payload invalid: {exc}") from exc

    if payload.aud != expected_audience:
        raise CsrError(f"CSR aud={payload.aud!r} does not match issuer ({expected_audience!r})")
    if payload.exp - payload.iat > MAX_CSR_LIFETIME_SECONDS:
        raise CsrError(f"CSR lifetime exceeds {MAX_CSR_LIFETIME_SECONDS}s")

    current = int(time.time()) if now is None else now
    if payload.exp < current - leeway:
        raise CsrError("CSR expired")
    if payload.iat > current + leeway:
        raise CsrError("CSR iat in the future")

    try:
        _verify_jws_signature(token, subject_key)
    except (JWTError, SignatureError) as exc:
        raise CsrError(f"signature verification failed: {exc}") from exc

    return payload


def build_issuer_url(issuer_domain: str) -> str:
    if not issuer_domain:
        raise CsrError("issuer_domain required")
    return f"https://{issuer_domain}/.well-known/shadownet/issue"


def submit_csr(
    csr_jws: str,
    issuer_domain: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> IssuanceResult:
    """POST ``csr_jws`` to ``<issuer>/.well-known/shadownet/issue``.

    Returns the credential JWS on success. Raises:
      * :class:`CeremonyPendingError` (HTTP 409) — Subject needs to complete
        the issuer's out-of-band ceremony at ``next_url`` and re-POST
      * :class:`CeremonyFailedError` (HTTP 403)
      * :class:`CsrRateLimitedError` (HTTP 429)
      * :class:`CsrError` for any other failure.
    """
    url = build_issuer_url(issuer_domain)
    owned: httpx.Client | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.Client(timeout=timeout)
        response = c.post(
            url,
            content=csr_jws.encode("ascii"),
            headers={"Content-Type": ISSUER_MEDIA_TYPE, "Accept": ISSUER_MEDIA_TYPE},
        )
    except httpx.HTTPError as exc:
        raise CsrError(f"CSR submission failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            owned.close()

    return _interpret_response(response, url)


def _interpret_response(response: httpx.Response, url: str) -> IssuanceResult:
    status = response.status_code
    if status == 200:
        body = response.text.strip()
        if not body or body.count(".") != 2:
            raise CsrError(f"issuer {url!r} 200 response is not a JWS-compact token")
        return IssuanceResult(credential=body)
    if status == 409:
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise CsrError(f"issuer {url!r} 409 missing JSON body: {exc}") from exc
        next_url = data.get("next") if isinstance(data, dict) else None
        if not isinstance(next_url, str) or not next_url:
            raise CsrError(f"issuer {url!r} 409 missing 'next' URL")
        raise CeremonyPendingError(next_url)
    if status == 403:
        raise CeremonyFailedError(f"issuer {url!r} returned 403 ceremony_failed")
    if status == 429:
        raise CsrRateLimitedError(f"issuer {url!r} returned 429 rate_limited")
    raise CsrError(f"issuer {url!r} returned unexpected HTTP {status}")


def _verify_jws_signature(token: str, key: Ed25519KeyPair) -> None:
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("malformed JWS compact serialization")
    signing_input = (parts[0] + "." + parts[1]).encode("ascii")
    sig = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    key.verify(sig, signing_input)
