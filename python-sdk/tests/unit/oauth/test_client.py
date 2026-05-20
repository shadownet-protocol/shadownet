from __future__ import annotations

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI, Request

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.oauth.client import Discovery, OAuthClient, discover
from shadownet.oauth.fastapi import (
    ConsentResult,
    build_oauth_router,
    build_resource_metadata_router,
)
from shadownet.oauth.scopes import SCOPE_OFFLINE_ACCESS, SCOPE_TOOLS_READ
from shadownet.oauth.server import (
    AuthorizationRequest,
    AuthorizationServer,
    AuthorizationServerSettings,
)


@pytest.fixture
def app() -> tuple[FastAPI, AuthorizationServer]:
    key = Ed25519KeyPair.from_seed(b"\x33" * 32)
    settings = AuthorizationServerSettings(
        issuer="https://app.example/u/alice",
        resource="https://app.example/u/alice/mcp",
        signing_key=key,
        key_id="kid-c",
        authorization_endpoint="https://app.example/u/alice/oauth/authorize",
        token_endpoint="https://app.example/u/alice/oauth/token",
        registration_endpoint="https://app.example/u/alice/oauth/register",
        revocation_endpoint="https://app.example/u/alice/oauth/revoke",
    )
    server = AuthorizationServer(settings)
    application = FastAPI()

    async def consent(_request: Request, parsed: AuthorizationRequest) -> ConsentResult:
        return ConsentResult.approved(
            subject="did:web:app.example#alice",
            scopes=frozenset({SCOPE_TOOLS_READ, SCOPE_OFFLINE_ACCESS}),
        )

    application.include_router(
        build_oauth_router(server, consent_handler=consent, path_prefix="/u/alice")
    )
    application.include_router(
        build_resource_metadata_router(
            resource=server.settings.resource,
            authorization_servers=[server.settings.issuer],
            scopes_supported=list(server.settings.scopes_supported),
            path="/u/alice/.well-known/oauth-protected-resource",
        )
    )
    return application, server


@pytest.fixture
async def http(app: tuple[FastAPI, AuthorizationServer]) -> httpx.AsyncClient:
    application, _server = app
    # Use an ASGI transport so the client talks to FastAPI in-process —
    # no real network, but real HTTP semantics.
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="https://app.example") as client:
        yield client


async def test_discover_loads_prm_and_as_metadata(
    http: httpx.AsyncClient, app: tuple[FastAPI, AuthorizationServer]
) -> None:
    _, server = app
    _ = server
    disc = await discover(
        http,
        protected_resource_metadata_url=(
            "https://app.example/u/alice/.well-known/oauth-protected-resource"
        ),
    )
    assert disc.resource == server.settings.resource
    assert disc.issuer == server.settings.issuer
    assert disc.supports_pkce_s256
    assert disc.supports_dcr


async def test_oauth_client_full_dcr_then_code_then_refresh(
    http: httpx.AsyncClient, app: tuple[FastAPI, AuthorizationServer]
) -> None:
    _, _server = app
    disc = await discover(
        http,
        protected_resource_metadata_url=(
            "https://app.example/u/alice/.well-known/oauth-protected-resource"
        ),
    )
    oauth = OAuthClient(http, disc)
    reg = await oauth.register(
        client_name="cli",
        redirect_uris=["http://localhost:5000/cb"],
        scope=frozenset({SCOPE_TOOLS_READ, SCOPE_OFFLINE_ACCESS}),
    )
    assert reg.client_id.startswith("cid_")
    url, pending = oauth.start_authorization(
        scope=frozenset({SCOPE_TOOLS_READ, SCOPE_OFFLINE_ACCESS}),
    )
    # Drive the authorization GET; consent handler auto-approves so we
    # get the redirect with the code.
    resp = await http.get(url, follow_redirects=False)
    assert resp.status_code == 302
    from urllib.parse import parse_qs, urlparse

    location = urlparse(resp.headers["location"])
    assert location.scheme == "http"
    assert location.hostname == "localhost"
    qs = parse_qs(location.query)
    code = qs["code"][0]
    received_state = qs["state"][0]

    tokens = await oauth.redeem_code(code=code, pending=pending, received_state=received_state)
    assert tokens.access_token
    assert tokens.refresh_token is not None

    rotated = await oauth.refresh(refresh_token=tokens.refresh_token)
    assert rotated.refresh_token is not None
    assert rotated.refresh_token != tokens.refresh_token


async def test_oauth_client_refuses_to_proceed_without_pkce(http: httpx.AsyncClient) -> None:
    # Build a Discovery whose AS does NOT advertise S256, and verify
    # the client constructor refuses.
    from shadownet.oauth.models import AuthorizationServerMetadata, ProtectedResourceMetadata

    bad = Discovery(
        protected_resource_metadata_url="https://example/.well-known/oauth-protected-resource",
        protected_resource_metadata=ProtectedResourceMetadata(
            resource="https://example/mcp",
            authorization_servers=["https://example"],
            scopes_supported=["mcp:tools.read"],
        ),
        authorization_server_metadata=AuthorizationServerMetadata(
            issuer="https://example",
            authorization_endpoint="https://example/oauth/authorize",
            token_endpoint="https://example/oauth/token",
            code_challenge_methods_supported=[],
        ),
    )
    from shadownet.oauth.client import AuthorizationFailed

    with pytest.raises(AuthorizationFailed):
        OAuthClient(http, bad)
