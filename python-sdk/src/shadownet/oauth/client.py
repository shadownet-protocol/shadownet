"""Client-side OAuth 2.1 helpers for SDK consumers.

Two surfaces:

- :func:`discover` — fetches the RFC 9728 PRM document and the
  authorization server's metadata, returning a :class:`Discovery` that
  bundles both. Callers typically call this once per Sidecar URL.

- :class:`OAuthClient` — drives the authorization-code-with-PKCE flow,
  dynamic client registration, refresh-token rotation, and token
  revocation. It is transport-agnostic about *how* the user-agent is
  routed to the authorization URL — that is the caller's job. The
  intended pattern for a Python host agent (CLI, script) is to spawn
  a local loopback HTTP server, open the authorization URL in the
  user's browser, wait for the callback, and pass the code to
  :meth:`OAuthClient.redeem_code`.

The helpers compose with :class:`shadownet.connect.session.ShadownetMCPSession`:
an OAuth-acquired access token is just a bearer token that goes into
the session's ``Authorization`` header.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import secrets
import threading
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx

from shadownet.logging import get_logger
from shadownet.oauth.errors import InvalidRequest, OAuthError
from shadownet.oauth.models import (
    AuthorizationServerMetadata,
    ClientRegistrationResponse,
    ProtectedResourceMetadata,
    TokenResponse,
)
from shadownet.oauth.pkce import generate_code_verifier, s256_challenge
from shadownet.oauth.scopes import scope_string

__all__ = [
    "AuthorizationFailed",
    "Discovery",
    "LoopbackCallbackResult",
    "OAuthClient",
    "discover",
    "wait_for_loopback_callback",
]

_log = get_logger(__name__)


class AuthorizationFailed(OAuthError):
    """The authorization server returned an error or the flow timed out."""

    code = "authorization_failed"


@dataclass(frozen=True, slots=True)
class Discovery:
    """The composed result of PRM + AS metadata discovery."""

    protected_resource_metadata_url: str
    protected_resource_metadata: ProtectedResourceMetadata
    authorization_server_metadata: AuthorizationServerMetadata

    @property
    def issuer(self) -> str:
        return self.authorization_server_metadata.issuer

    @property
    def resource(self) -> str:
        return self.protected_resource_metadata.resource

    @property
    def supports_pkce_s256(self) -> bool:
        # Per the MCP authorization spec, clients MUST refuse to
        # proceed when the AS does not advertise PKCE support.
        return "S256" in (self.authorization_server_metadata.code_challenge_methods_supported or [])

    @property
    def supports_dcr(self) -> bool:
        return bool(self.authorization_server_metadata.registration_endpoint)


async def discover(
    http: httpx.AsyncClient,
    *,
    mcp_endpoint: str | None = None,
    protected_resource_metadata_url: str | None = None,
) -> Discovery:
    """Run RFC-0009 discovery for a Sidecar.

    Either pass the MCP endpoint URL (the function probes the
    well-known PRM path) or pass the PRM URL directly (taken from a
    sidecar 401's ``WWW-Authenticate`` ``resource_metadata`` parameter
    or from the integration bundle's ``protected_resource_metadata``
    field). Once the PRM is fetched, the function tries each issuer
    in ``authorization_servers`` until one returns valid AS metadata.
    """
    if not (mcp_endpoint or protected_resource_metadata_url):
        raise ValueError(
            "discover() requires either mcp_endpoint or protected_resource_metadata_url"
        )
    prm_url = protected_resource_metadata_url or _well_known_prm_url(mcp_endpoint or "")
    prm = await _fetch_prm(http, prm_url)
    last_error: Exception | None = None
    for issuer in prm.authorization_servers:
        try:
            metadata = await _fetch_as_metadata(http, issuer)
            return Discovery(
                protected_resource_metadata_url=prm_url,
                protected_resource_metadata=prm,
                authorization_server_metadata=metadata,
            )
        except Exception as exc:
            last_error = exc
            _log.debug("AS metadata fetch failed for %s: %s", issuer, exc)
    raise AuthorizationFailed(
        f"could not load AS metadata from any of {prm.authorization_servers}: {last_error}"
    )


def _well_known_prm_url(mcp_endpoint: str) -> str:
    parsed = urllib.parse.urlparse(mcp_endpoint)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"mcp_endpoint must be an absolute URL: {mcp_endpoint!r}")
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    # MCP spec § Protected Resource Metadata Discovery — when the MCP
    # endpoint has a path component the PRM may be served at the
    # ``/.well-known/oauth-protected-resource/<path>`` form. For
    # Shadownet Sidecars the per-tenant PRM lives at the canonical
    # ``<origin>/u/<shadowname>/.well-known/oauth-protected-resource``
    # location, which both probes happen to recover.
    if path:
        if path.endswith("/mcp"):
            tenant_path = path.rsplit("/", 1)[0]
            return f"{base}{tenant_path}/.well-known/oauth-protected-resource"
        return f"{base}/.well-known/oauth-protected-resource{path}"
    return f"{base}/.well-known/oauth-protected-resource"


async def _fetch_prm(http: httpx.AsyncClient, url: str) -> ProtectedResourceMetadata:
    response = await http.get(url, headers={"Accept": "application/json"})
    if response.status_code != HTTPStatus.OK:
        raise AuthorizationFailed(f"{url} returned HTTP {response.status_code}")
    payload = response.json()
    return ProtectedResourceMetadata.model_validate(payload)


async def _fetch_as_metadata(http: httpx.AsyncClient, issuer: str) -> AuthorizationServerMetadata:
    parsed = urllib.parse.urlparse(issuer)
    if not parsed.scheme or not parsed.netloc:
        raise AuthorizationFailed(f"AS issuer must be an absolute URL: {issuer!r}")
    candidates: list[str] = []
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    # MCP spec: try `/.well-known/oauth-authorization-server` with path
    # insertion first, then path-append OIDC discovery.
    if path:
        candidates.append(f"{base}/.well-known/oauth-authorization-server{path}")
        candidates.append(f"{base}/.well-known/openid-configuration{path}")
        candidates.append(f"{base}{path}/.well-known/openid-configuration")
    else:
        candidates.append(f"{base}/.well-known/oauth-authorization-server")
        candidates.append(f"{base}/.well-known/openid-configuration")
    last: Exception | None = None
    for url in candidates:
        try:
            response = await http.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            last = exc
            continue
        if response.status_code == HTTPStatus.OK:
            return AuthorizationServerMetadata.model_validate(response.json())
        last = AuthorizationFailed(f"{url} returned HTTP {response.status_code}")
    raise AuthorizationFailed(f"no AS metadata at any candidate for issuer {issuer!r}: {last}")


@dataclass(frozen=True, slots=True)
class _ClientCredentials:
    client_id: str
    client_secret: str | None
    redirect_uri: str


@dataclass(slots=True)
class _PendingAuthorization:
    state: str
    code_verifier: str
    redirect_uri: str
    scope: frozenset[str]


class OAuthClient:
    """End-to-end OAuth 2.1 client for a Sidecar.

    Typical CLI flow::

        async with httpx.AsyncClient() as http:
            disc = await discover(http, mcp_endpoint=bundle.mcp_endpoint)
            oauth = OAuthClient(http, disc)
            await oauth.register(
                client_name="my-cli", redirect_uris=["http://localhost:0/callback"]
            )
            url, pending = oauth.start_authorization(scope={"mcp:tools.read"})
            # ... open `url` in user's browser, wait for the callback ...
            tokens = await oauth.redeem_code(code=..., pending=pending)
            session_token = tokens.access_token
    """

    def __init__(self, http: httpx.AsyncClient, discovery: Discovery) -> None:
        if not discovery.supports_pkce_s256:
            raise AuthorizationFailed(
                "authorization server does not advertise PKCE S256 — refusing to proceed"
            )
        self._http = http
        self._discovery = discovery
        self._client: _ClientCredentials | None = None

    @property
    def discovery(self) -> Discovery:
        return self._discovery

    @property
    def client_id(self) -> str:
        if self._client is None:
            raise AuthorizationFailed("client is not registered or pre-configured")
        return self._client.client_id

    def set_client_credentials(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        client_secret: str | None = None,
    ) -> None:
        """Plug in pre-registered client credentials (skipping DCR)."""
        self._client = _ClientCredentials(
            client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri
        )

    async def register(
        self,
        *,
        client_name: str,
        redirect_uris: list[str],
        scope: frozenset[str] | None = None,
        token_endpoint_auth_method: str = "none",  # noqa: S107 — OAuth wire value, not a secret
    ) -> ClientRegistrationResponse:
        """Run RFC 7591 dynamic client registration.

        Raises :class:`AuthorizationFailed` if the AS does not advertise
        a registration endpoint (operators may have disabled DCR; the
        caller should pre-register and use :meth:`set_client_credentials`).
        """
        endpoint = self._discovery.authorization_server_metadata.registration_endpoint
        if not endpoint:
            raise AuthorizationFailed(
                "authorization server does not advertise a registration endpoint"
            )
        body: dict[str, Any] = {
            "client_name": client_name,
            "redirect_uris": list(redirect_uris),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": token_endpoint_auth_method,
        }
        if scope:
            body["scope"] = scope_string(scope)
        response = await self._http.post(
            endpoint, json=body, headers={"Accept": "application/json"}
        )
        if response.status_code not in (HTTPStatus.OK, HTTPStatus.CREATED):
            raise AuthorizationFailed(
                f"client registration failed: HTTP {response.status_code} {response.text}"
            )
        result = ClientRegistrationResponse.model_validate(response.json())
        self._client = _ClientCredentials(
            client_id=result.client_id,
            client_secret=result.client_secret,
            redirect_uri=redirect_uris[0],
        )
        return result

    def start_authorization(
        self,
        *,
        scope: frozenset[str],
        redirect_uri: str | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> tuple[str, _PendingAuthorization]:
        """Build the authorization URL and return it with the PKCE state.

        The returned :class:`_PendingAuthorization` is opaque to
        callers — it is consumed by :meth:`redeem_code` to bind the
        verifier to the code exchange. Persist it across the
        browser-callback boundary (in-memory is fine for a CLI; for a
        long-lived agent, persist somewhere the callback handler can
        read it).
        """
        if self._client is None:
            raise AuthorizationFailed("call register() or set_client_credentials() first")
        verifier = generate_code_verifier()
        challenge = s256_challenge(verifier)
        state = secrets.token_urlsafe(24)
        effective_redirect = redirect_uri or self._client.redirect_uri
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self._client.client_id,
            "redirect_uri": effective_redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": self._discovery.resource,
            "state": state,
        }
        if scope:
            params["scope"] = scope_string(scope)
        if extra_params:
            for k, v in extra_params.items():
                if k in params:
                    raise InvalidRequest(f"extra_params attempted to override {k!r}")
                params[k] = v
        url = self._append_query(
            self._discovery.authorization_server_metadata.authorization_endpoint, params
        )
        pending = _PendingAuthorization(
            state=state,
            code_verifier=verifier,
            redirect_uri=effective_redirect,
            scope=scope,
        )
        return url, pending

    async def redeem_code(
        self,
        *,
        code: str,
        pending: _PendingAuthorization,
        received_state: str | None,
    ) -> TokenResponse:
        """Exchange an authorization code for tokens."""
        if self._client is None:
            raise AuthorizationFailed("client is not registered")
        if received_state is not None and received_state != pending.state:
            raise AuthorizationFailed("state mismatch — possible CSRF attack")
        body: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "client_id": self._client.client_id,
            "code_verifier": pending.code_verifier,
            "resource": self._discovery.resource,
        }
        if self._client.client_secret is not None:
            body["client_secret"] = self._client.client_secret
        return await self._token_call(body)

    async def refresh(self, *, refresh_token: str) -> TokenResponse:
        """Rotate a refresh token."""
        if self._client is None:
            raise AuthorizationFailed("client is not registered")
        body: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client.client_id,
            "resource": self._discovery.resource,
        }
        if self._client.client_secret is not None:
            body["client_secret"] = self._client.client_secret
        return await self._token_call(body)

    async def revoke(self, *, token: str) -> None:
        """RFC 7009 revoke. No-op if the AS does not advertise the endpoint."""
        endpoint = self._discovery.authorization_server_metadata.revocation_endpoint
        if not endpoint or self._client is None:
            return
        body: dict[str, str] = {"token": token, "client_id": self._client.client_id}
        if self._client.client_secret is not None:
            body["client_secret"] = self._client.client_secret
        response = await self._http.post(endpoint, data=body)
        if response.status_code not in (HTTPStatus.OK, HTTPStatus.NO_CONTENT):
            raise AuthorizationFailed(f"revoke failed: HTTP {response.status_code} {response.text}")

    async def _token_call(self, body: dict[str, str]) -> TokenResponse:
        endpoint = self._discovery.authorization_server_metadata.token_endpoint
        response = await self._http.post(
            endpoint,
            data=body,
            headers={"Accept": "application/json"},
        )
        if response.status_code != HTTPStatus.OK:
            try:
                payload = response.json()
            except ValueError:
                payload = {"error": "server_error", "error_description": response.text}
            raise AuthorizationFailed(f"token endpoint returned {response.status_code}: {payload}")
        return TokenResponse.model_validate(response.json())

    @staticmethod
    def _append_query(base: str, params: dict[str, str]) -> str:
        parsed = urllib.parse.urlparse(base)
        existing = parsed.query
        encoded = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        new_query = f"{existing}&{encoded}" if existing else encoded
        return urllib.parse.urlunparse(parsed._replace(query=new_query))


@dataclass(frozen=True, slots=True)
class LoopbackCallbackResult:
    """One captured ``redirect_uri`` callback."""

    code: str | None
    state: str | None
    error: str | None
    error_description: str | None


async def wait_for_loopback_callback(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    path: str = "/callback",
    timeout_seconds: float = 300.0,
    success_html: str | None = None,
) -> tuple[str, asyncio.Future[LoopbackCallbackResult]]:
    """Spawn a one-shot loopback HTTP server to capture an OAuth callback.

    Returns ``(redirect_uri, future)``. The caller passes
    ``redirect_uri`` to :meth:`OAuthClient.start_authorization`, opens
    the resulting URL in a browser, and awaits ``future`` (with a
    timeout) for the callback result.

    The server binds to an ephemeral port when ``port=0``. Only the
    first request is captured; subsequent requests return 404 and the
    server shuts down once the future is fulfilled.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[LoopbackCallbackResult] = loop.create_future()
    success_body = success_html or _DEFAULT_LOOPBACK_HTML

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != path:
                self.send_response(HTTPStatus.NOT_FOUND)
                self.end_headers()
                return
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            result = LoopbackCallbackResult(
                code=_first(query, "code"),
                state=_first(query, "state"),
                error=_first(query, "error"),
                error_description=_first(query, "error_description"),
            )
            body = success_body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            loop.call_soon_threadsafe(_set_future, future, result)

        def log_message(self, format: str, *args: Any) -> None:
            return  # silence default stderr logging

    httpd = HTTPServer((host, port), _Handler)
    actual_port = httpd.server_address[1]
    redirect_uri = f"http://{host}:{actual_port}{path}"
    thread = threading.Thread(target=httpd.serve_forever, name="oauth-loopback", daemon=True)
    thread.start()

    def _shutdown() -> None:
        with contextlib.suppress(Exception):
            httpd.shutdown()
        with contextlib.suppress(Exception):
            httpd.server_close()

    future.add_done_callback(lambda _f: _shutdown())
    loop.call_later(timeout_seconds, _timeout_future, future)
    return redirect_uri, future


def _set_future(
    future: asyncio.Future[LoopbackCallbackResult], value: LoopbackCallbackResult
) -> None:
    if not future.done():
        future.set_result(value)


def _timeout_future(future: asyncio.Future[LoopbackCallbackResult]) -> None:
    if not future.done():
        future.set_exception(AuthorizationFailed("timed out waiting for loopback OAuth callback"))


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    return values[0]


_DEFAULT_LOOPBACK_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Shadownet authorization complete</title></head>
<body style="font:14px/1.5 system-ui,sans-serif;max-width:480px;margin:3rem auto;padding:0 1rem">
<h1 style="font-size:1.25rem">Authorization complete</h1>
<p>You may close this window and return to your terminal.</p>
</body></html>
"""


# Re-exported here for convenience; not part of the public surface in
# the __all__ list above but importable for consumers that need to
# decode access-token claims out-of-band.
_ = base64
