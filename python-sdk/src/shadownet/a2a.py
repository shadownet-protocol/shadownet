"""A2A binding — RFC 0001 §8.1, §8.2, §8.7, §8.8.

Sender-side: construct an A2A message, embed the Shadownet envelope JWS in
``metadata["urn:shadownet:0.2"]``, and POST ``message:send`` to the recipient's
endpoint with the right A2A headers.

Receiver-side: extract the envelope JWS from the incoming A2A request and
return a ``Message`` response (not a ``Task`` — RFC 0001 §8.7 inherits A2A's
agent-opacity principle).

Errors round-trip as RFC 7807 ``application/problem+json`` with the canonical
URN identifiers from RFC 0001 §8.8 (every code maps to one HTTP status).

This module declares its own thin pydantic models for the A2A surface used by
Shadownet rather than pulling in the protobuf-generated types from
``a2a-sdk``. Consumers needing the full A2A type surface can still import
from ``a2a.types`` directly.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from typing import TYPE_CHECKING, Any, Final

import httpx
from pydantic import BaseModel, ConfigDict, Field

from shadownet.envelope import (
    ENVELOPE_EXTENSION_URI,
    EnvelopePayload,
    compute_msg_hash,
    mint_envelope,
)
from shadownet.errors import ShadownetError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = [
    "A2A_VERSION",
    "AGENT_CARD_MEDIA_TYPE",
    "PROBLEM_JSON_MEDIA_TYPE",
    "WIRE_ERROR_REGISTRY",
    "A2AMessage",
    "BuiltMessage",
    "CredsRejectedError",
    "CredsRequiredError",
    "ParseError",
    "PolicyError",
    "RateLimitedError",
    "ReplayError",
    "ShadownetWireError",
    "SignatureError",
    "TextPart",
    "TransportError",
    "TransportRetryExhausted",
    "UnknownRecipientError",
    "acceptance_headers",
    "asend_envelope",
    "asend_with_retries",
    "build_acceptance_response",
    "build_and_sign_message",
    "build_outbound_message",
    "extract_envelope_jws",
    "problem_response",
    "send_envelope",
    "send_with_retries",
    "wire_error_from_problem",
]


A2A_VERSION: Final = "1.0"
A2A_MEDIA_TYPE: Final = "application/a2a+json"
AGENT_CARD_MEDIA_TYPE: Final = "application/a2a+json"
PROBLEM_JSON_MEDIA_TYPE: Final = "application/problem+json"
DEFAULT_TIMEOUT: Final = 30.0


class TextPart(BaseModel):
    """A2A TextPart."""

    model_config = ConfigDict(extra="allow", frozen=True)
    text: str


class A2AMessage(BaseModel):
    """A2A ``Message`` (the subset Shadownet cares about).

    Wire keys are A2A's camelCase ``messageId`` / ``contextId`` / ``taskId``;
    Python attributes are snake_case ``message_id`` / ``context_id`` / ``task_id``
    with pydantic aliases so ``model_validate`` and ``model_dump(by_alias=True)``
    round-trip the wire form.
    """

    model_config = ConfigDict(extra="allow", frozen=False, populate_by_name=True)

    message_id: str = Field(alias="messageId")
    role: str
    parts: list[dict[str, Any]] = Field(default_factory=list)
    context_id: str | None = Field(default=None, alias="contextId")
    task_id: str | None = Field(default=None, alias="taskId")
    extensions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        out = self.model_dump(exclude_none=True, by_alias=True)
        return {k: v for k, v in out.items() if v != [] or k == "parts"}


class BuiltMessage(BaseModel):
    """A signed A2A message ready for POST."""

    model_config = ConfigDict(frozen=True)
    message: dict[str, Any]
    envelope_jws: str
    envelope_payload: EnvelopePayload


# ---- Wire errors (RFC 0001 §8.8 / RFC 7807) ------------------------------


class TransportError(ShadownetError):
    """The HTTP request never reached a Shadownet peer (connect / read /
    DNS / TLS failure).

    Distinct from :class:`ShadownetWireError`: those are well-formed
    problem+json responses from the peer (don't retry), while a
    :class:`TransportError` means we never heard from anyone (retry per
    §8.10).
    """


class ShadownetWireError(ShadownetError):
    """Base for errors that map to a Shadownet RFC 7807 code."""

    code: str = ""
    http_status: int = 500

    def problem_body(
        self, *, detail: str | None = None, include_detail: bool = False
    ) -> dict[str, Any]:
        """Build the RFC 7807 body.

        Agent-opacity (RFC 0001 §11): the body MUST NOT leak sender / messageId
        / stranger-vs-contact signals. Internal exception messages routinely
        embed those, so by default we ship only type + title + status. Pass
        ``detail=...`` to send a curated string, or ``include_detail=True`` to
        forward ``str(self)`` verbatim (useful in trusted dev/CI mirrors —
        never on a public receiver).
        """
        body: dict[str, Any] = {
            "type": f"urn:shadownet:error:{self.code}",
            "title": (self.__doc__ or self.__class__.__name__).strip(),
            "status": self.http_status,
        }
        if detail is not None:
            body["detail"] = detail
        elif include_detail and str(self):
            body["detail"] = str(self)
        return body


class ParseError(ShadownetWireError):
    """A2A request, envelope JWS, payload, or msgHash invalid."""

    code = "parse_error"
    http_status = 400


class SignatureError(ShadownetWireError):
    """Envelope signature does not validate."""

    code = "signature"
    http_status = 401


class CredsRequiredError(ShadownetWireError):
    """No cached credentials for sender; sender SHOULD retry with creds."""

    code = "creds_required"
    http_status = 401


class CredsRejectedError(ShadownetWireError):
    """Credentials present but none satisfy receiver policy."""

    code = "creds_rejected"
    http_status = 403


class PolicyError(ShadownetWireError):
    """Receiver policy rejects this sender."""

    code = "policy"
    http_status = 403


class ReplayError(ShadownetWireError):
    """(from, messageId) already seen."""

    code = "replay"
    http_status = 409


class UnknownRecipientError(ShadownetWireError):
    """to is not served by this URL."""

    code = "unknown_recipient"
    http_status = 404


class RateLimitedError(ShadownetWireError):
    """Rate limit hit."""

    code = "rate_limited"
    http_status = 429


WIRE_ERROR_REGISTRY: dict[str, type[ShadownetWireError]] = {
    cls.code: cls
    for cls in (
        ParseError,
        SignatureError,
        CredsRequiredError,
        CredsRejectedError,
        PolicyError,
        ReplayError,
        UnknownRecipientError,
        RateLimitedError,
    )
}


def problem_response(
    error: ShadownetWireError,
    *,
    detail: str | None = None,
    include_detail: bool = False,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Return ``(status, body, headers)`` for an RFC 7807 error response.

    By default the body carries only type + title + status to satisfy RFC 0001
    §11 agent-opacity (no sender / messageId / classification leak). Pass an
    explicit ``detail=`` to send a curated string, or ``include_detail=True``
    to forward the exception's message verbatim. Receivers facing untrusted
    senders should keep both off.
    """
    headers = {
        "Content-Type": PROBLEM_JSON_MEDIA_TYPE,
        "A2A-Extensions": ENVELOPE_EXTENSION_URI,
    }
    return (
        error.http_status,
        error.problem_body(detail=detail, include_detail=include_detail),
        headers,
    )


def wire_error_from_problem(body: dict[str, Any]) -> ShadownetWireError:
    """Reconstruct the appropriate :class:`ShadownetWireError` from a
    problem+json body. Unknown URN suffixes raise a generic ParseError so
    callers always get a typed exception."""
    raw = body.get("type", "")
    if not isinstance(raw, str) or not raw.startswith("urn:shadownet:error:"):
        return ParseError(f"unrecognized error type: {raw!r}")
    code = raw[len("urn:shadownet:error:") :]
    detail = body.get("detail")
    cls = WIRE_ERROR_REGISTRY.get(code)
    if cls is None:
        return ParseError(f"unknown error code: {code!r}")
    return cls(detail if isinstance(detail, str) else cls.__doc__ or code)


# ---- Sender ---------------------------------------------------------------


def build_outbound_message(
    *,
    body_text: str | None,
    context_id: str | None = None,
    task_id: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
    message_id: str | None = None,
) -> A2AMessage:
    parts: list[dict[str, Any]] = []
    if body_text is not None:
        parts.append({"text": body_text})
    return A2AMessage(
        message_id=message_id or _new_id(),
        role="ROLE_USER",
        parts=parts,
        context_id=context_id,
        task_id=task_id,
        extensions=[ENVELOPE_EXTENSION_URI],
        metadata=dict(extra_metadata) if extra_metadata else {},
    )


def build_and_sign_message(
    message: A2AMessage,
    payload_template: EnvelopePayload,
    sender_key: Any,
) -> BuiltMessage:
    """Stamp the message with ``msgHash`` and embed a signed envelope.

    ``payload_template`` carries the v, from, to, iat, exp, body, creds for the
    envelope; ``msgHash`` is recomputed from the message and replaces whatever
    placeholder the template carried.
    """
    raw = message.to_wire()
    msg_hash = compute_msg_hash(raw)
    final_payload = payload_template.model_copy(update={"msg_hash": msg_hash})
    envelope_jws = mint_envelope(final_payload, sender_key)
    raw["metadata"] = dict(raw.get("metadata") or {})
    raw["metadata"][ENVELOPE_EXTENSION_URI] = envelope_jws
    return BuiltMessage(
        message=raw,
        envelope_jws=envelope_jws,
        envelope_payload=final_payload,
    )


def send_envelope(
    built: BuiltMessage,
    agent_url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> A2AMessage:
    url, body, headers = _send_envelope_request(built, agent_url)
    owned: httpx.Client | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.Client(timeout=timeout)
        response = c.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise TransportError(f"transport failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            owned.close()

    return _interpret_response(response, url)


async def asend_envelope(
    built: BuiltMessage,
    agent_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> A2AMessage:
    """Async sibling of :func:`send_envelope` using ``httpx.AsyncClient``."""
    url, body, headers = _send_envelope_request(built, agent_url)
    owned: httpx.AsyncClient | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.AsyncClient(timeout=timeout)
        response = await c.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise TransportError(f"transport failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            await owned.aclose()

    return _interpret_response(response, url)


def _send_envelope_request(
    built: BuiltMessage, agent_url: str
) -> tuple[str, dict[str, Any], dict[str, str]]:
    url = agent_url.rstrip("/") + "/message:send"
    body = {"message": built.message}
    headers = {
        "Content-Type": A2A_MEDIA_TYPE,
        "Accept": A2A_MEDIA_TYPE,
        "A2A-Version": A2A_VERSION,
        "A2A-Extensions": ENVELOPE_EXTENSION_URI,
    }
    return url, body, headers


# RFC 0001 §8.10 defaults: initial 30s, doubling, ±25% jitter, 24h budget.
RETRY_INITIAL_DELAY: Final = 30.0
RETRY_MAX_DELAY: Final = 3600.0
RETRY_TOTAL_BUDGET: Final = 24 * 3600.0
RETRY_JITTER: Final = 0.25


class TransportRetryExhausted(ShadownetError):
    """The §8.10 retry budget was spent before the receiver became reachable."""


def send_with_retries(
    builder: Callable[[], BuiltMessage],
    agent_url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    initial_delay: float = RETRY_INITIAL_DELAY,
    max_delay: float = RETRY_MAX_DELAY,
    total_budget: float = RETRY_TOTAL_BUDGET,
    jitter: float = RETRY_JITTER,
    rng: random.Random | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> A2AMessage:
    """Send with the RFC 0001 §8.10 retry-with-remint policy.

    On :class:`TransportError`, wait an exponentially-backed, jittered delay
    and try again. Each attempt calls ``builder()`` to mint a fresh
    :class:`BuiltMessage` (the §8.3 envelope expiry window does not extend
    through retries — ``iat`` / ``exp`` / ``messageId`` MUST be regenerated
    per attempt; the body, ``to``, and ``contextId`` stay stable across them).

    Protocol-level rejections (any :class:`ShadownetWireError`) are NOT
    retried; the receiver already gave a decisive answer. Raises
    :class:`TransportRetryExhausted` when the cumulative wall time would
    exceed ``total_budget``.
    """
    r = rng or random.Random()  # noqa: S311 — jitter, not crypto
    start = monotonic()
    delay = initial_delay
    last_transport_error: BaseException | None = None
    while True:
        built = builder()
        try:
            return send_envelope(built, agent_url, client=client, timeout=timeout)
        except ShadownetWireError:
            raise
        except TransportError as exc:
            last_transport_error = exc

        elapsed = monotonic() - start
        if elapsed >= total_budget:
            raise TransportRetryExhausted(
                f"§8.10 retry budget exhausted ({elapsed:.0f}s); last error: {last_transport_error}"
            ) from last_transport_error
        jittered = delay * (1.0 + r.uniform(-jitter, jitter))
        wait = min(max(jittered, 0.0), max(0.0, total_budget - elapsed))
        sleep(wait)
        delay = min(delay * 2.0, max_delay)


async def asend_with_retries(
    builder: Callable[[], BuiltMessage],
    agent_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    initial_delay: float = RETRY_INITIAL_DELAY,
    max_delay: float = RETRY_MAX_DELAY,
    total_budget: float = RETRY_TOTAL_BUDGET,
    jitter: float = RETRY_JITTER,
    rng: random.Random | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> A2AMessage:
    """Async sibling of :func:`send_with_retries`."""
    r = rng or random.Random()  # noqa: S311
    start = monotonic()
    delay = initial_delay
    last_transport_error: BaseException | None = None
    while True:
        built = builder()
        try:
            return await asend_envelope(built, agent_url, client=client, timeout=timeout)
        except ShadownetWireError:
            raise
        except TransportError as exc:
            last_transport_error = exc

        elapsed = monotonic() - start
        if elapsed >= total_budget:
            raise TransportRetryExhausted(
                f"§8.10 retry budget exhausted ({elapsed:.0f}s); last error: {last_transport_error}"
            ) from last_transport_error
        jittered = delay * (1.0 + r.uniform(-jitter, jitter))
        wait = min(max(jittered, 0.0), max(0.0, total_budget - elapsed))
        await sleep(wait)
        delay = min(delay * 2.0, max_delay)


def _interpret_response(response: httpx.Response, url: str) -> A2AMessage:
    if response.status_code == 200:
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ParseError(f"response from {url!r} is not JSON: {exc}") from exc
        message = body.get("message") if isinstance(body, dict) else None
        if not isinstance(message, dict):
            raise ParseError(f"response from {url!r} missing 'message'")
        try:
            return A2AMessage.model_validate(message)
        except Exception as exc:
            raise ParseError(f"response message invalid: {exc}") from exc

    if response.headers.get("Content-Type", "").startswith(PROBLEM_JSON_MEDIA_TYPE):
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ParseError(f"problem+json body unparseable: {exc}") from exc
        raise wire_error_from_problem(body if isinstance(body, dict) else {})

    raise ParseError(f"{url!r} returned HTTP {response.status_code} without problem+json body")


# ---- Receiver -------------------------------------------------------------


def extract_envelope_jws(request_body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return ``(envelope_jws, a2a_message)`` from a parsed message:send body.

    Raises :class:`ParseError` if the structure is missing required fields.
    """
    message = request_body.get("message") if isinstance(request_body, dict) else None
    if not isinstance(message, dict):
        raise ParseError("message:send body missing 'message'")
    extensions = message.get("extensions") or []
    if not isinstance(extensions, list) or ENVELOPE_EXTENSION_URI not in extensions:
        raise ParseError(f"message.extensions must include {ENVELOPE_EXTENSION_URI!r}")
    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ParseError("message.metadata must be an object")
    envelope = metadata.get(ENVELOPE_EXTENSION_URI)
    if not isinstance(envelope, str) or envelope.count(".") != 2:
        raise ParseError(
            f"message.metadata[{ENVELOPE_EXTENSION_URI!r}] missing or not a JWS-compact string"
        )
    return envelope, message


def build_acceptance_response(
    *,
    context_id: str | None,
    text: str = "accepted",
    message_id: str | None = None,
) -> dict[str, Any]:
    """RFC 0001 §8.7: minimal Message response. Returns the wire-shape dict."""
    body: dict[str, Any] = {
        "messageId": message_id or _new_id(),
        "role": "ROLE_AGENT",
        "parts": [{"text": text}],
    }
    if context_id is not None:
        body["contextId"] = context_id
    return {"message": body}


def acceptance_headers() -> dict[str, str]:
    return {
        "Content-Type": A2A_MEDIA_TYPE,
        "A2A-Extensions": ENVELOPE_EXTENSION_URI,
    }


def _new_id() -> str:
    # A2A messageId is opaque per-conversation; uppercase hex of a UUID4 is a
    # safe placeholder until callers provide ULID/UUID generation themselves.
    return uuid.uuid4().hex.upper()
