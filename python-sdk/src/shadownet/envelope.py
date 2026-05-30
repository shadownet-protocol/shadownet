"""Envelope — RFC 0001 §8.

The envelope is the Shadownet message wrapper carried as a JWS-compact string
in the surrounding A2A message's ``metadata["urn:shadownet:0.2"]``. It is
signed by the sender's Shadow key, bound to the surrounding A2A message via
``msgHash``, and (on first contact / after cache expiry) accompanied by the
sender's credentials.

Schema mirrors ``shadownet-specs/schemas/messages/envelope.schema.json``.
"""

from __future__ import annotations

import base64
import hashlib
import time
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shadownet.crypto.ed25519 import Ed25519KeyPair, SignatureError
from shadownet.crypto.jwt import (
    JWTError,
    decode_header,
    decode_unverified_claims,
    sign_jwt,
)
from shadownet.errors import ShadownetError
from shadownet.identifiers import Shadowname  # noqa: TC001  pydantic needs Shadowname at runtime
from shadownet.jcs import canonicalize

__all__ = [
    "ENVELOPE_EXTENSION_URI",
    "ENVELOPE_TYP",
    "MAX_LIFETIME_SECONDS",
    "EnvelopeBody",
    "EnvelopeError",
    "EnvelopePayload",
    "compute_msg_hash",
    "mint_envelope",
    "verify_envelope",
]


ENVELOPE_TYP: Final = "shadownet-env+jwt"
ENVELOPE_EXTENSION_URI: Final = "urn:shadownet:0.2"
# §8.3: exp - iat MUST be ≤ 300 seconds.
MAX_LIFETIME_SECONDS: Final = 300
# §2: ±60 s clock skew tolerance.
DEFAULT_LEEWAY_SECONDS: Final = 60


class EnvelopeError(ShadownetError):
    """Envelope failed to mint, parse, or verify."""


class EnvelopeBody(BaseModel):
    """Body slot of an envelope — RFC 0001 §8.5."""

    model_config = ConfigDict(extra="allow", frozen=True)

    text: str | None = None
    intent: str | None = None
    data: dict[str, Any] | None = None


class EnvelopePayload(BaseModel):
    """Decoded payload of a ``shadownet-env+jwt``."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    v: Annotated[str, Field(pattern=r"^0\.2$")]
    sender: Shadowname = Field(alias="from")
    recipient: Shadowname = Field(alias="to")
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    msg_hash: Annotated[str, Field(pattern=r"^sha256:[A-Za-z0-9_-]+$")] = Field(alias="msgHash")
    body: EnvelopeBody
    creds: tuple[str, ...] = ()


def compute_msg_hash(message: dict[str, Any]) -> str:
    """``msgHash`` per RFC 0001 §8.4.

    The canonical input is the A2A message with the Shadownet metadata key
    removed; fields absent from the message are omitted from the canonical
    input (not encoded as null).
    """
    canonical_input: dict[str, Any] = {}
    for key in ("messageId", "role", "parts", "contextId", "taskId"):
        if key in message:
            canonical_input[key] = message[key]
    if "metadata" in message:
        metadata = message["metadata"]
        canonical_input["metadata"] = {
            k: v for k, v in metadata.items() if k != ENVELOPE_EXTENSION_URI
        }
    digest = hashlib.sha256(canonicalize(canonical_input)).digest()
    return "sha256:" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def mint_envelope(
    payload: EnvelopePayload,
    subject_key: Ed25519KeyPair,
) -> str:
    if payload.exp - payload.iat > MAX_LIFETIME_SECONDS:
        raise EnvelopeError(
            f"envelope lifetime exceeds 300 seconds (exp - iat = {payload.exp - payload.iat}s)"
        )
    if payload.exp <= payload.iat:
        raise EnvelopeError("envelope exp must be greater than iat")
    claims = payload.model_dump(by_alias=True)
    if not payload.creds:
        # ``creds`` is optional on the wire (§8.3); omit on this hop.
        claims.pop("creds", None)
    return sign_jwt(
        claims,
        subject_key,
        header_extras={"typ": ENVELOPE_TYP, "kid": payload.sender},
    )


def verify_envelope(
    token: str,
    sender_key: Ed25519KeyPair,
    *,
    expected_recipient: str,
    now: int | None = None,
    leeway: int = DEFAULT_LEEWAY_SECONDS,
) -> EnvelopePayload:
    """Run §8.6 envelope-side checks (steps 3-7).

    The A2A request parse (step 1), extension declaration (step 1), `kid` /
    `from` resolution against the AgentCard (step 5), credential checks
    (step 9), and policy classification (step 10) are layered on by the
    receiver pipeline; this function focuses on the envelope JWS itself.
    """
    try:
        header = decode_header(token)
    except JWTError as exc:
        raise EnvelopeError(f"invalid JWS header: {exc}") from exc
    if header.get("typ") != ENVELOPE_TYP:
        raise EnvelopeError(f"typ must be {ENVELOPE_TYP!r}, got {header.get('typ')!r}")
    if header.get("alg") != "EdDSA":
        raise EnvelopeError(f"alg must be EdDSA, got {header.get('alg')!r}")

    try:
        unverified = decode_unverified_claims(token)
    except JWTError as exc:
        raise EnvelopeError(f"unable to decode envelope claims: {exc}") from exc

    try:
        payload = EnvelopePayload.model_validate(unverified)
    except ValidationError as exc:
        raise EnvelopeError(f"envelope payload invalid: {exc}") from exc

    if header.get("kid") != payload.sender:
        raise EnvelopeError(f"kid must equal from ({payload.sender!r}), got {header.get('kid')!r}")

    if payload.recipient != expected_recipient:
        raise EnvelopeError(
            f"envelope to={payload.recipient!r} does not match this URL ({expected_recipient!r})"
        )

    if payload.exp - payload.iat > MAX_LIFETIME_SECONDS:
        raise EnvelopeError("envelope lifetime exceeds 300 seconds")

    current = int(time.time()) if now is None else now
    if payload.exp < current - leeway:
        raise EnvelopeError("envelope expired")
    if payload.iat > current + leeway:
        raise EnvelopeError("envelope iat in the future")

    try:
        _verify_jws_signature(token, sender_key)
    except (JWTError, SignatureError) as exc:
        raise EnvelopeError(f"signature verification failed: {exc}") from exc

    return payload


def _verify_jws_signature(token: str, key: Ed25519KeyPair) -> None:
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("malformed JWS compact serialization")
    signing_input = (parts[0] + "." + parts[1]).encode("ascii")
    sig = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    key.verify(sig, signing_input)
