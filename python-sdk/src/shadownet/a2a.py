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

import json
import uuid
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict, Field

from shadownet.envelope import (
    ENVELOPE_EXTENSION_URI,
    EnvelopePayload,
    compute_msg_hash,
    mint_envelope,
)
from shadownet.errors import ShadownetError

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
    "UnknownRecipientError",
    "acceptance_headers",
    "build_acceptance_response",
    "build_and_sign_message",
    "build_outbound_message",
    "extract_envelope_jws",
    "problem_response",
    "send_envelope",
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


class ShadownetWireError(ShadownetError):
    """Base for errors that map to a Shadownet RFC 7807 code."""

    code: str = ""
    http_status: int = 500

    def problem_body(self, *, detail: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"urn:shadownet:error:{self.code}",
            "title": (self.__doc__ or self.__class__.__name__).strip(),
            "status": self.http_status,
        }
        if detail is not None:
            body["detail"] = detail
        elif str(self):
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
    error: ShadownetWireError, *, detail: str | None = None
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Return (status, body, headers) for an RFC 7807 error response."""
    headers = {
        "Content-Type": PROBLEM_JSON_MEDIA_TYPE,
        "A2A-Extensions": ENVELOPE_EXTENSION_URI,
    }
    return error.http_status, error.problem_body(detail=detail), headers


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
    url = agent_url.rstrip("/") + "/message:send"
    body = {"message": built.message}
    headers = {
        "Content-Type": A2A_MEDIA_TYPE,
        "Accept": A2A_MEDIA_TYPE,
        "A2A-Version": A2A_VERSION,
        "A2A-Extensions": ENVELOPE_EXTENSION_URI,
    }
    owned: httpx.Client | None = None
    try:
        c = client
        if c is None:
            c = owned = httpx.Client(timeout=timeout)
        response = c.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ParseError(f"transport failed for {url!r}: {exc}") from exc
    finally:
        if owned is not None:
            owned.close()

    return _interpret_response(response, url)


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
