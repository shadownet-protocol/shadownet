"""ASGI A2A test peer.

Hosts:

- `GET  /.well-known/agent-card.json` — A2A agent card (RFC-0006).
- `POST /a2a/{method}`              — JSON-RPC inbound A2A endpoint.
                                       Headers and body are recorded for test
                                       inspection; response is scriptable.
- `POST /webhooks/inbox`            — Receives Sidecar webhook deliveries
                                       (RFC-0007). Verifies HMAC and stores
                                       the event for assertion.

The peer is started by a session-scoped pytest fixture (`peer` in
`tests/sidecar/conftest.py`) on a random localhost port. Tests interact
with it via the :class:`PeerHandle` returned by :func:`spawn_peer`.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import uvicorn
from shadownet.a2a.session import mint_session_token
from shadownet.crypto.jwt import sign_jwt
from shadownet.webhook.verify import WebhookEvent, verify_webhook
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from shadownet_conformance.logging import get_logger
from shadownet_conformance.peer.identity import PeerIdentity

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from starlette.requests import Request

_logger = get_logger(__name__)


@dataclass(slots=True)
class A2AReceived:
    method: str
    headers: dict[str, str]
    body: bytes
    json_body: dict[str, Any] | None


@dataclass(slots=True)
class WebhookDelivered:
    headers: dict[str, str]
    body: bytes
    event: WebhookEvent | None
    verification_error: str | None = None


@dataclass(slots=True)
class _State:
    a2a_requests: list[A2AReceived] = field(default_factory=list)
    webhooks: list[WebhookDelivered] = field(default_factory=list)
    a2a_response_status: int = 200
    a2a_response_body: dict[str, Any] = field(
        default_factory=lambda: {"jsonrpc": "2.0", "id": "1", "result": {"status": "ok"}}
    )


class Peer:
    """Stateful peer holding identity + recorded interactions."""

    def __init__(self) -> None:
        self.identity = PeerIdentity()
        self._state = _State()
        self._lock = threading.Lock()

    # --- inbound recording ---

    def record_a2a(self, received: A2AReceived) -> None:
        with self._lock:
            self._state.a2a_requests.append(received)

    def record_webhook(self, delivered: WebhookDelivered) -> None:
        with self._lock:
            self._state.webhooks.append(delivered)

    def all_a2a_requests(self) -> list[A2AReceived]:
        with self._lock:
            return list(self._state.a2a_requests)

    def last_a2a_request(self) -> A2AReceived | None:
        with self._lock:
            return self._state.a2a_requests[-1] if self._state.a2a_requests else None

    def delivered_webhooks(self) -> list[WebhookDelivered]:
        with self._lock:
            return list(self._state.webhooks)

    def reset(self) -> None:
        with self._lock:
            self._state.a2a_requests.clear()
            self._state.webhooks.clear()
            self._state.a2a_response_status = 200
            self._state.a2a_response_body = {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {"status": "ok"},
            }

    # --- outbound minting ---

    def session_token_for(self, audience_did: str, *, ttl_seconds: int = 300) -> str:
        return mint_session_token(
            holder_key=self.identity.keypair,
            holder_did=self.identity.did,
            audience_did=audience_did,
            ttl_seconds=ttl_seconds,
        )

    def presentation_for(self, audience_did: str, nonce: str, *, ttl_seconds: int = 120) -> str:
        """Mint a VP carrying the peer's credential + fresh freshness proof.

        We mint the VP directly via sign_jwt rather than shadownet-py's
        `mint_presentation` because tests need to control the iat/exp
        boundary precisely (e.g. to test that an expired VP is rejected).
        """
        import time

        iat = int(time.time())
        claims = {
            "iss": self.identity.did,
            "aud": audience_did,
            "iat": iat,
            "exp": iat + ttl_seconds,
            "nonce": nonce,
            "vp": {
                "@context": ["https://www.w3.org/ns/credentials/v2"],
                "type": ["VerifiablePresentation"],
                "verifiableCredential": [
                    self.identity.credential_jwt(),
                    self.identity.freshness_jwt(),
                ],
            },
        }
        return sign_jwt(
            claims,
            self.identity.keypair,
            header_extras={"typ": "vp+jwt", "kid": self.identity.kid},
        )

    # --- response scripting ---

    def script_a2a_response(self, *, status: int, body: Mapping[str, Any]) -> None:
        with self._lock:
            self._state.a2a_response_status = status
            self._state.a2a_response_body = dict(body)

    def next_a2a_response(self) -> tuple[int, dict[str, Any]]:
        with self._lock:
            return self._state.a2a_response_status, dict(self._state.a2a_response_body)


def build_app(peer: Peer) -> Starlette:
    """Wire the ASGI routes onto a Starlette app bound to ``peer``."""

    async def agent_card(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "name": "shadownet-conformance test peer",
                "url": "/a2a",
                "did": peer.identity.did,
                "publicKey": peer.identity.keypair.public_jwk(),
                "shadownet:v": "0.1",
            }
        )

    async def a2a_inbound(request: Request) -> Response:
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            json_body: dict[str, Any] | None = json.loads(body) if body else None
        except json.JSONDecodeError:
            json_body = None
        method = request.path_params.get("method", "")
        peer.record_a2a(A2AReceived(method=method, headers=headers, body=body, json_body=json_body))
        status, payload = peer.next_a2a_response()
        return JSONResponse(payload, status_code=status)

    async def webhook_inbox(request: Request) -> Response:
        body = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        delivered = WebhookDelivered(headers=headers, body=body, event=None)
        try:
            delivered.event = verify_webhook(
                headers=dict(request.headers),
                body=body,
                secret=peer.identity.webhook_secret,
            )
        except Exception as exc:
            delivered.verification_error = str(exc)
            peer.record_webhook(delivered)
            return JSONResponse({"error": "invalid"}, status_code=400)
        peer.record_webhook(delivered)
        return JSONResponse({"ok": True})

    routes = [
        Route("/.well-known/agent-card.json", agent_card, methods=["GET"]),
        Route("/a2a/{method}", a2a_inbound, methods=["POST"]),
        Route("/webhooks/inbox", webhook_inbox, methods=["POST"]),
    ]
    return Starlette(routes=routes)


@dataclass(slots=True)
class PeerHandle:
    peer: Peer
    base_url: str
    stop: Callable[[], None]

    @property
    def agent_card_url(self) -> str:
        return f"{self.base_url}/.well-known/agent-card.json"

    @property
    def a2a_url(self) -> str:
        return f"{self.base_url}/a2a"

    @property
    def webhook_url(self) -> str:
        return f"{self.base_url}/webhooks/inbox"


def spawn_peer(*, host: str = "127.0.0.1") -> PeerHandle:
    """Boot the peer on a random port; return a handle plus a stop callback.

    Runs uvicorn in a background thread with its own event loop. Blocks
    until the server reports ready (via a polling loop on the chosen port).
    """
    peer = Peer()
    app = build_app(peer)
    port = _free_port(host)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    ready = threading.Event()

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _wrapped() -> None:
            ready.set()
            await server.serve()

        try:
            loop.run_until_complete(_wrapped())
        finally:
            loop.close()

    thread = threading.Thread(target=_run, name="shadownet-peer", daemon=True)
    thread.start()
    if not ready.wait(timeout=5.0):
        raise RuntimeError("peer thread failed to start within 5s")
    _wait_for_socket(host, port, timeout=5.0)

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=5.0)

    return PeerHandle(peer=peer, base_url=f"http://{host}:{port}", stop=stop)


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _wait_for_socket(host: str, port: int, *, timeout: float) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect((host, port))
                return
            except OSError:
                time.sleep(0.05)
    raise RuntimeError(f"peer at {host}:{port} did not accept connections within {timeout}s")
