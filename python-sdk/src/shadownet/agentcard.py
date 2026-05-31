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
    "AGENT_CARD_MEDIA_TYPE",
    "DIRECT_AGENT_CARD_PATH",
    "PINNED_SELF_SIGNED_SCHEME",
    "AgentCardError",
    "AgentCardSignatureError",
    "FetchedAgentCard",
    "afetch_agent_card_json",
    "afetch_and_verify_agent_card",
    "afetch_and_verify_direct_agent_card",
    "afetch_direct_agent_card_json",
    "build_direct_signed_agent_card",
    "build_signed_agent_card",
    "build_unsigned_agent_card_body",
    "extract_issuer_endpoint",
    "extract_status_list_base",
    "fetch_agent_card_json",
    "fetch_and_verify_agent_card",
    "fetch_and_verify_direct_agent_card",
    "fetch_direct_agent_card_json",
    "sign_agent_card_body",
    "verify_agent_card",
    "verify_self_signed_agent_card",
]


SHADOWNET_EXTENSION_URI: Final = "urn:shadownet:0.2"
EXPECTED_ALG: Final = "EdDSA"
EXPECTED_TYP: Final = "JOSE"
AGENT_CARD_MEDIA_TYPE: Final = "application/a2a+json"
DIRECT_AGENT_CARD_PATH: Final = "/.well-known/agent-card.json"
PINNED_SELF_SIGNED_SCHEME: Final = "shadownet:pinned-self-signed"
DEFAULT_FETCH_TIMEOUT: Final = 10.0


class AgentCardError(ShadownetError):
    """AgentCard fetch, parse, or verify failed."""


class AgentCardSignatureError(AgentCardError):
    """AgentCard signature did not verify against the provider's key."""


@dataclass(frozen=True, slots=True)
class FetchedAgentCard:
    """Verified AgentCard for a Shadow.

    ``shadowname`` carries the Shadowname when known; for direct-mode Shadows
    it holds the bare multibase public key (the wire identifier).
    """

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
    url = _agent_card_url_for_shadowname(shadowname, provider_record)
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
    return _interpret_card_response(response, url)


async def afetch_agent_card_json(
    shadowname: str,
    provider_record: ProviderRecord,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> tuple[dict[str, Any], httpx.Headers]:
    """Async sibling of :func:`fetch_agent_card_json` using ``httpx.AsyncClient``."""
    url = _agent_card_url_for_shadowname(shadowname, provider_record)
    owned: httpx.AsyncClient | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.AsyncClient(timeout=timeout)
        response = await c.get(url, headers={"Accept": AGENT_CARD_MEDIA_TYPE})
    except httpx.HTTPError as exc:
        raise AgentCardError(f"fetch failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            await owned.aclose()
    return _interpret_card_response(response, url)


def _agent_card_url_for_shadowname(shadowname: str, provider_record: ProviderRecord) -> str:
    canonical = parse_shadowname(shadowname)
    local, provider = canonical.split("@", 1)
    if provider != provider_record.domain:
        raise AgentCardError(
            f"shadowname provider {provider!r} does not match record domain "
            f"{provider_record.domain!r}"
        )
    return provider_record.endpoint.rstrip("/") + f"/identity/{local}"


def _interpret_card_response(
    response: httpx.Response, url: str
) -> tuple[dict[str, Any], httpx.Headers]:
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
    _verify_signatures(signatures, payload_b64, provider_record.provider_keys, expected_kid)

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


def verify_self_signed_agent_card(
    card: dict[str, Any],
    expected_public_key: str,
) -> FetchedAgentCard:
    """Verify a direct-mode (self-signed) AgentCard — RFC 0001 §5.3.

    The card is signed by the Shadow itself; the JWS ``kid`` MUST equal the
    embedded ``shadownet:pk`` and the URI's pubkey (caller supplies it as
    ``expected_public_key``). The signature verifies against that same key.
    """
    signatures = card.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise AgentCardSignatureError("AgentCard has no signatures")

    payload_bytes = _canonical_card_payload(card)
    payload_b64 = _b64url(payload_bytes)

    _verify_signatures(signatures, payload_b64, (expected_public_key,), expected_public_key)

    shadow_pk = _require_str(card, "shadownet:pk")
    if shadow_pk != expected_public_key:
        raise AgentCardError(
            f"AgentCard shadownet:pk={shadow_pk!r} does not match URI key {expected_public_key!r}"
        )
    try:
        parse_public_key(shadow_pk)
    except InvalidIdentifierError as exc:
        raise AgentCardError(f"invalid shadownet:pk: {exc}") from exc

    version = _require_str(card, "shadownet:v")
    if version != "0.2":
        raise AgentCardError(f"unsupported shadownet:v={version!r}")

    _validate_extensions(card)
    _validate_direct_security_scheme(card)

    endpoint_url = _supported_interface_url(card)

    return FetchedAgentCard(
        shadowname=expected_public_key,
        shadow_public_key=shadow_pk,
        endpoint_url=endpoint_url,
        cache_max_age=None,
        etag=None,
        raw=card,
    )


def _verify_signatures(
    signatures: list[Any],
    payload_b64: str,
    expected_signers: tuple[str, ...],
    expected_kid: str,
) -> None:
    last_error: Exception | None = None
    for sig in signatures:
        if not isinstance(sig, dict):
            continue
        try:
            _verify_one(sig, payload_b64, expected_signers, expected_kid)
            return
        except AgentCardSignatureError as exc:
            last_error = exc
            continue
    raise AgentCardSignatureError(
        f"no valid signature on AgentCard ({last_error})"
        if last_error
        else "no parseable signatures on AgentCard"
    )


def _validate_direct_security_scheme(card: dict[str, Any]) -> None:
    schemes = card.get("securitySchemes")
    if not isinstance(schemes, dict) or PINNED_SELF_SIGNED_SCHEME not in schemes:
        raise AgentCardError(
            f"direct-mode AgentCard must declare "
            f"securitySchemes[{PINNED_SELF_SIGNED_SCHEME!r}] per RFC 0001 §5.4"
        )


def extract_status_list_base(card: dict[str, Any]) -> str | None:
    """Return ``shadownet:statusListBase`` if present (keyed issuers, §6.4)."""
    value = card.get("shadownet:statusListBase")
    return value if isinstance(value, str) and value else None


def extract_issuer_endpoint(card: dict[str, Any]) -> str | None:
    """Return ``shadownet:issueEndpoint`` if present (keyed issuers, §6.5)."""
    value = card.get("shadownet:issueEndpoint")
    return value if isinstance(value, str) and value else None


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
    return _attach_cache_headers(verify_agent_card(card, provider_record, shadowname), headers)


async def afetch_and_verify_agent_card(
    shadowname: str,
    provider_record: ProviderRecord,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> FetchedAgentCard:
    """Async sibling of :func:`fetch_and_verify_agent_card`."""
    card, headers = await afetch_agent_card_json(
        shadowname, provider_record, client=client, timeout=timeout
    )
    return _attach_cache_headers(verify_agent_card(card, provider_record, shadowname), headers)


def _attach_cache_headers(verified: FetchedAgentCard, headers: httpx.Headers) -> FetchedAgentCard:
    return FetchedAgentCard(
        shadowname=verified.shadowname,
        shadow_public_key=verified.shadow_public_key,
        endpoint_url=verified.endpoint_url,
        cache_max_age=_parse_max_age(headers.get("Cache-Control")),
        etag=headers.get("ETag"),
        raw=verified.raw,
    )


def fetch_direct_agent_card_json(
    endpoint_origin: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> tuple[dict[str, Any], httpx.Headers]:
    """``GET <origin>/.well-known/agent-card.json`` for direct-mode resolution.

    Caller is responsible for TLS posture: WebPKI when the endpoint uses a
    CA-issued cert, or fingerprint pinning when the URI carried a
    ``#sha256:`` pin. Configure the ``client`` accordingly.
    """
    url = endpoint_origin.rstrip("/") + DIRECT_AGENT_CARD_PATH
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
    return _interpret_card_response(response, url)


async def afetch_direct_agent_card_json(
    endpoint_origin: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> tuple[dict[str, Any], httpx.Headers]:
    """Async sibling of :func:`fetch_direct_agent_card_json`."""
    url = endpoint_origin.rstrip("/") + DIRECT_AGENT_CARD_PATH
    owned: httpx.AsyncClient | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.AsyncClient(timeout=timeout)
        response = await c.get(url, headers={"Accept": AGENT_CARD_MEDIA_TYPE})
    except httpx.HTTPError as exc:
        raise AgentCardError(f"fetch failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            await owned.aclose()
    return _interpret_card_response(response, url)


def fetch_and_verify_direct_agent_card(
    endpoint_origin: str,
    expected_public_key: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> FetchedAgentCard:
    card, headers = fetch_direct_agent_card_json(endpoint_origin, client=client, timeout=timeout)
    return _attach_cache_headers(verify_self_signed_agent_card(card, expected_public_key), headers)


async def afetch_and_verify_direct_agent_card(
    endpoint_origin: str,
    expected_public_key: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> FetchedAgentCard:
    """Async sibling of :func:`fetch_and_verify_direct_agent_card`."""
    card, headers = await afetch_direct_agent_card_json(
        endpoint_origin, client=client, timeout=timeout
    )
    return _attach_cache_headers(verify_self_signed_agent_card(card, expected_public_key), headers)


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
    expected_signers: tuple[str, ...],
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
    for pk in expected_signers:
        try:
            key = Ed25519KeyPair.from_public_bytes(parse_public_key(pk))
            key.verify(signature_bytes, signing_input)
            return
        except Exception as exc:
            last_failure = exc
            continue
    raise AgentCardSignatureError(
        f"signature did not verify against any expected signer ({last_failure})"
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


def build_unsigned_agent_card_body(
    *,
    name: str,
    description: str,
    version: str,
    a2a_url: str,
    shadow_public_key: str,
    protocol_binding: str = "HTTP+JSON",
    a2a_protocol_version: str = "1.0",
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the AgentCard body Shadownet receivers serve at /identity/<local>.

    The result has the §5.3 required Shadownet extension declaration, the
    ``shadownet:v`` / ``shadownet:pk`` fields, and one supported interface
    pointing at the Shadow's A2A endpoint. Caller signs via
    :func:`sign_agent_card_body`.
    """
    body: dict[str, Any] = {
        "name": name,
        "description": description,
        "version": version,
        "supportedInterfaces": [
            {
                "url": a2a_url,
                "protocolBinding": protocol_binding,
                "protocolVersion": a2a_protocol_version,
            }
        ],
        "capabilities": {
            "extensions": [
                {
                    "uri": SHADOWNET_EXTENSION_URI,
                    "required": True,
                    "description": "Shadownet identity envelope",
                }
            ]
        },
        "shadownet:v": "0.2",
        "shadownet:pk": shadow_public_key,
    }
    if extras:
        body.update(extras)
    return body


def sign_agent_card_body(
    body: dict[str, Any],
    signing_key: Ed25519KeyPair,
    *,
    kid: str | None = None,
    provider_domain: str | None = None,
) -> dict[str, Any]:
    """Attach a JWS signature to an AgentCard body per A2A §8.4.

    Pass either ``kid`` (explicit; used for direct-mode self-signed cards
    where it equals the Shadow's own pubkey) or ``provider_domain`` (which
    is rewritten to ``shadownet@<domain>`` for Shadowname-mode cards).
    """
    if kid is None:
        if provider_domain is None:
            raise AgentCardError("must supply kid or provider_domain")
        kid = f"shadownet@{provider_domain}"
    elif provider_domain is not None:
        raise AgentCardError("supply kid OR provider_domain, not both")

    pruned = _strip_empty({k: v for k, v in body.items() if k != "signatures"})
    if pruned is None:
        raise AgentCardError("AgentCard canonical form is empty")
    payload_b64 = _b64url(canonicalize(pruned))
    header = {"alg": EXPECTED_ALG, "typ": EXPECTED_TYP, "kid": kid}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    signing_input = (header_b64 + "." + payload_b64).encode("ascii")
    signature = signing_key.sign(signing_input)
    out = dict(body)
    out["signatures"] = [
        {"protected": header_b64, "signature": _b64url(signature)},
    ]
    return out


def build_signed_agent_card(
    *,
    name: str,
    description: str,
    version: str,
    a2a_url: str,
    shadow_public_key: str,
    provider_key: Ed25519KeyPair,
    provider_domain: str,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = build_unsigned_agent_card_body(
        name=name,
        description=description,
        version=version,
        a2a_url=a2a_url,
        shadow_public_key=shadow_public_key,
        extras=extras,
    )
    return sign_agent_card_body(body, provider_key, provider_domain=provider_domain)


def build_direct_signed_agent_card(
    *,
    name: str,
    description: str,
    version: str,
    a2a_url: str,
    shadow_key: Ed25519KeyPair,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and self-sign a direct-mode AgentCard — RFC 0001 §5.3, §5.4.

    The card declares ``shadownet:pinned-self-signed`` in ``securitySchemes``
    and is signed by ``shadow_key`` with ``kid`` equal to that key's encoded
    public form (so callers can verify against the URI-supplied pubkey).
    """
    from shadownet.identifiers import encode_public_key

    shadow_public_key = encode_public_key(shadow_key.public_bytes)
    merged_extras: dict[str, Any] = {"securitySchemes": {PINNED_SELF_SIGNED_SCHEME: {}}}
    if extras:
        merged_extras.update(extras)
    body = build_unsigned_agent_card_body(
        name=name,
        description=description,
        version=version,
        a2a_url=a2a_url,
        shadow_public_key=shadow_public_key,
        extras=merged_extras,
    )
    return sign_agent_card_body(body, shadow_key, kid=shadow_public_key)


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
