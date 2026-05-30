"""A2A AgentCard fetch and verification — Shadownet RFC 0001 §5, A2A §8.4.

The provider hosts a signed A2A AgentCard at ``<ep>/identity/<local>`` (RFC 0001
§5.2). The card carries the Shadow's signing key as ``shadownet:pk`` (§5.3) and
the A2A endpoint at ``supportedInterfaces[0].url`` (§5.4). The signature is per
A2A §8.4: JWS over the JCS-canonicalized card with ``signatures`` excluded and
empty/default values stripped.

Shadownet narrows the A2A signing surface: ``kid`` MUST be
``shadownet@<provider-domain>`` (RFC 0001 §5.2) and ``alg`` MUST be ``EdDSA``
(RFC 0001 §4.1).

Reference implementation we cross-checked against: a2a-python
``src/a2a/utils/signing.py`` (see python-sdk/CLAUDE.md).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import httpx

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.errors import ShadownetError
from shadownet.identifiers import (
    InvalidIdentifierError,
    MultibasePublicKey,
    parse_public_key,
    parse_shadowname,
)
from shadownet.jcs import canonicalize

if TYPE_CHECKING:
    from shadownet.provider import ProviderRecord

__all__ = [
    "AgentCardError",
    "AgentCardSignatureError",
    "FetchedAgentCard",
    "fetch_agent_card_json",
    "fetch_and_verify_agent_card",
    "verify_agent_card",
]


SHADOWNET_EXTENSION_URI: Final = "urn:shadownet:0.2"
EXPECTED_ALG: Final = "EdDSA"
EXPECTED_TYP: Final = "JOSE"
AGENT_CARD_MEDIA_TYPE: Final = "application/a2a+json"
DEFAULT_FETCH_TIMEOUT: Final = 10.0


class AgentCardError(ShadownetError):
    """AgentCard fetch, parse, or verify failed."""


class AgentCardSignatureError(AgentCardError):
    """AgentCard signature did not verify against the provider's key."""


@dataclass(frozen=True, slots=True)
class FetchedAgentCard:
    """Verified AgentCard for a Shadowname."""

    shadowname: str
    shadow_public_key: MultibasePublicKey
    endpoint_url: str
    cache_max_age: int | None
    etag: str | None
    raw: dict[str, Any]


def fetch_agent_card_json(
    shadowname: str,
    provider_record: ProviderRecord,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> tuple[dict[str, Any], httpx.Headers]:
    canonical = parse_shadowname(shadowname)
    local, provider = canonical.split("@", 1)
    if provider != provider_record.domain:
        raise AgentCardError(
            f"shadowname provider {provider!r} does not match record domain "
            f"{provider_record.domain!r}"
        )

    url = provider_record.endpoint.rstrip("/") + f"/identity/{local}"
    owned: httpx.Client | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.Client(timeout=timeout)
        response = c.get(url, headers={"Accept": AGENT_CARD_MEDIA_TYPE})
    except httpx.HTTPError as exc:
        raise AgentCardError(f"fetch failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            owned.close()

    if response.status_code != 200:
        raise AgentCardError(f"AgentCard {url!r} returned HTTP {response.status_code}")
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise AgentCardError(f"AgentCard {url!r} not valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise AgentCardError(f"AgentCard {url!r} is not a JSON object")
    return body, response.headers


def verify_agent_card(
    card: dict[str, Any],
    provider_record: ProviderRecord,
    shadowname: str,
) -> FetchedAgentCard:
    canonical_name = parse_shadowname(shadowname)
    _, provider = canonical_name.split("@", 1)
    if provider != provider_record.domain:
        raise AgentCardError(
            f"shadowname provider {provider!r} does not match record domain "
            f"{provider_record.domain!r}"
        )

    signatures = card.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise AgentCardSignatureError("AgentCard has no signatures")

    payload_bytes = _canonical_card_payload(card)
    payload_b64 = _b64url(payload_bytes)

    expected_kid = f"shadownet@{provider_record.domain}"
    last_error: Exception | None = None
    for sig in signatures:
        if not isinstance(sig, dict):
            continue
        try:
            _verify_one(sig, payload_b64, provider_record, expected_kid)
            break
        except AgentCardSignatureError as exc:
            last_error = exc
            continue
    else:
        raise AgentCardSignatureError(
            f"no valid signature on AgentCard ({last_error})"
            if last_error
            else "no parseable signatures on AgentCard"
        )

    shadow_pk = _require_str(card, "shadownet:pk")
    try:
        parse_public_key(shadow_pk)
    except InvalidIdentifierError as exc:
        raise AgentCardError(f"invalid shadownet:pk: {exc}") from exc

    version = _require_str(card, "shadownet:v")
    if version != "0.2":
        raise AgentCardError(f"unsupported shadownet:v={version!r}")

    _validate_extensions(card)

    endpoint_url = _supported_interface_url(card)

    return FetchedAgentCard(
        shadowname=canonical_name,
        shadow_public_key=shadow_pk,
        endpoint_url=endpoint_url,
        cache_max_age=None,
        etag=None,
        raw=card,
    )


def fetch_and_verify_agent_card(
    shadowname: str,
    provider_record: ProviderRecord,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> FetchedAgentCard:
    card, headers = fetch_agent_card_json(
        shadowname, provider_record, client=client, timeout=timeout
    )
    verified = verify_agent_card(card, provider_record, shadowname)
    cache_max_age = _parse_max_age(headers.get("Cache-Control"))
    etag = headers.get("ETag")
    return FetchedAgentCard(
        shadowname=verified.shadowname,
        shadow_public_key=verified.shadow_public_key,
        endpoint_url=verified.endpoint_url,
        cache_max_age=cache_max_age,
        etag=etag,
        raw=verified.raw,
    )


# A2A §8.4.1/§8.4.3: strip ``signatures`` and recursively drop empty/default
# values before JCS-canonicalizing.
def _canonical_card_payload(card: dict[str, Any]) -> bytes:
    pruned = _strip_empty({k: v for k, v in card.items() if k != "signatures"})
    if pruned is None:
        raise AgentCardError("AgentCard canonical form is empty")
    return canonicalize(pruned)


def _strip_empty(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            cleaned = _strip_empty(v)
            if cleaned is not None:
                out[k] = cleaned
        return out or None
    if isinstance(value, list):
        cleaned_list = [c for v in value if (c := _strip_empty(v)) is not None]
        return cleaned_list or None
    if isinstance(value, str) and not value:
        return None
    return value


def _verify_one(
    signature_obj: dict[str, Any],
    payload_b64: str,
    provider_record: ProviderRecord,
    expected_kid: str,
) -> None:
    protected_b64 = signature_obj.get("protected")
    signature_b64 = signature_obj.get("signature")
    if not isinstance(protected_b64, str) or not isinstance(signature_b64, str):
        raise AgentCardSignatureError("signature object missing 'protected' or 'signature'")

    try:
        header_bytes = _b64url_decode(protected_b64)
        header = json.loads(header_bytes)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AgentCardSignatureError(f"invalid protected header: {exc}") from exc

    if header.get("alg") != EXPECTED_ALG:
        raise AgentCardSignatureError(f"alg must be {EXPECTED_ALG}, got {header.get('alg')!r}")
    if header.get("typ") != EXPECTED_TYP:
        raise AgentCardSignatureError(f"typ must be {EXPECTED_TYP}, got {header.get('typ')!r}")
    if header.get("kid") != expected_kid:
        raise AgentCardSignatureError(f"kid must be {expected_kid!r}, got {header.get('kid')!r}")

    try:
        signature_bytes = _b64url_decode(signature_b64)
    except ValueError as exc:
        raise AgentCardSignatureError(f"signature not valid base64url: {exc}") from exc

    signing_input = (protected_b64 + "." + payload_b64).encode("ascii")
    last_failure: Exception | None = None
    for provider_pk in provider_record.provider_keys:
        try:
            key = Ed25519KeyPair.from_public_bytes(parse_public_key(provider_pk))
            key.verify(signature_bytes, signing_input)
            return
        except Exception as exc:
            last_failure = exc
            continue
    raise AgentCardSignatureError(
        f"signature did not verify against any provider key ({last_failure})"
    )


def _validate_extensions(card: dict[str, Any]) -> None:
    capabilities = card.get("capabilities")
    if not isinstance(capabilities, dict):
        raise AgentCardError("AgentCard.capabilities missing or not an object")
    extensions = capabilities.get("extensions")
    if not isinstance(extensions, list):
        raise AgentCardError("AgentCard.capabilities.extensions missing or not an array")
    for entry in extensions:
        if (
            isinstance(entry, dict)
            and entry.get("uri") == SHADOWNET_EXTENSION_URI
            and entry.get("required") is True
        ):
            return
    raise AgentCardError(f"AgentCard must declare {SHADOWNET_EXTENSION_URI} extension as required")


def _supported_interface_url(card: dict[str, Any]) -> str:
    interfaces = card.get("supportedInterfaces")
    if not isinstance(interfaces, list) or not interfaces:
        raise AgentCardError("AgentCard.supportedInterfaces missing or empty")
    first = interfaces[0]
    if not isinstance(first, dict):
        raise AgentCardError("AgentCard.supportedInterfaces[0] not an object")
    url = first.get("url")
    if not isinstance(url, str) or not url:
        raise AgentCardError("AgentCard.supportedInterfaces[0].url missing")
    return url


def _require_str(card: dict[str, Any], key: str) -> str:
    value = card.get(key)
    if not isinstance(value, str):
        raise AgentCardError(f"AgentCard missing required string field {key!r}")
    return value


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _parse_max_age(cache_control: str | None) -> int | None:
    if not cache_control:
        return None
    for part in cache_control.split(","):
        token = part.strip().lower()
        if token.startswith("max-age="):
            try:
                return int(token[len("max-age=") :])
            except ValueError:
                return None
    return None
