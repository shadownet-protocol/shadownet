from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

import time
import urllib.parse

from fastapi import Depends, FastAPI, Request, status
from fastapi.testclient import TestClient

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import sign_jwt
from shadownet.oauth.fastapi import (
    ConsentResult,
    build_bearer_auth_dependency,
    build_oauth_router,
    build_resource_metadata_router,
    oauth_challenge_headers,
)
from shadownet.oauth.pkce import generate_code_verifier, s256_challenge
from shadownet.oauth.scopes import SCOPE_TOOLS_READ
from shadownet.oauth.server import (
    AuthorizationRequest,
    AuthorizationServer,
    AuthorizationServerSettings,
    ResourceServer,
    TokenClaims,
)


def _build_app(server: AuthorizationServer) -> tuple[FastAPI, ResourceServer]:
    app = FastAPI()

    async def consent(_request: Request, parsed: AuthorizationRequest) -> ConsentResult:
        # Approve every request; treat client_id as the subject so the
        # token's sub is predictable in assertions.
        return ConsentResult.approved(
            subject=f"did:web:app.example#{parsed.client_id}",
            scopes=frozenset({SCOPE_TOOLS_READ}),
        )

    app.include_router(build_oauth_router(server, consent_handler=consent))
    app.include_router(
        build_resource_metadata_router(
            resource=server.settings.resource,
            authorization_servers=[server.settings.issuer],
            scopes_supported=list(server.settings.scopes_supported),
        )
    )
    rs = ResourceServer.from_authorization_server(server)
    require = build_bearer_auth_dependency(
        rs,
        resource_metadata_url="https://app.example/u/alice/.well-known/oauth-protected-resource",
        required_scopes=frozenset({SCOPE_TOOLS_READ}),
    )

    @app.get("/mcp-test")
    async def _mcp(_claims: TokenClaims = Depends(require)) -> dict[str, object]:  # noqa: B008
        return {"ok": True, "sub": _claims.sub}

    return app, rs


@pytest.fixture
def server() -> AuthorizationServer:
    key = Ed25519KeyPair.from_seed(b"\x22" * 32)
    settings = AuthorizationServerSettings(
        issuer="https://app.example/u/alice",
        resource="https://app.example/u/alice/mcp",
        signing_key=key,
        key_id="kid-1",
        authorization_endpoint="https://app.example/u/alice/oauth/authorize",
        token_endpoint="https://app.example/u/alice/oauth/token",
        registration_endpoint="https://app.example/u/alice/oauth/register",
        revocation_endpoint="https://app.example/u/alice/oauth/revoke",
    )
    return AuthorizationServer(settings)


def test_as_metadata_endpoint(server: AuthorizationServer) -> None:
    app, _ = _build_app(server)
    client = TestClient(app)
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    assert body["issuer"] == server.settings.issuer
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "authorization_code" in body["grant_types_supported"]
    assert "refresh_token" in body["grant_types_supported"]


def test_prm_endpoint(server: AuthorizationServer) -> None:
    app, _ = _build_app(server)
    client = TestClient(app)
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    body = r.json()
    assert body["resource"] == server.settings.resource
    assert body["authorization_servers"] == [server.settings.issuer]
    assert body["bearer_methods_supported"] == ["header"]


def test_dcr_then_authorize_then_token(server: AuthorizationServer) -> None:
    app, _ = _build_app(server)
    client = TestClient(app)
    reg = client.post(
        "/oauth/register",
        json={
            "client_name": "Test",
            "redirect_uris": ["http://localhost:0/cb"],
            "scope": "mcp:tools.read offline_access",
        },
    )
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]

    verifier = generate_code_verifier()
    challenge = s256_challenge(verifier)
    q = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost:0/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": server.settings.resource,
            "scope": "mcp:tools.read",
            "state": "xyz",
        }
    )
    r = client.get(f"/oauth/authorize?{q}", follow_redirects=False)
    assert r.status_code == 302
    redirect = urllib.parse.urlparse(r.headers["location"])
    qs = urllib.parse.parse_qs(redirect.query)
    assert qs["state"] == ["xyz"]
    code = qs["code"][0]

    t = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "http://localhost:0/cb",
            "code_verifier": verifier,
            "resource": server.settings.resource,
        },
    )
    assert t.status_code == 200
    body = t.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert t.headers.get("cache-control") == "no-store"

    # The token works on the protected MCP route.
    mcp = client.get("/mcp-test", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert mcp.status_code == 200
    assert mcp.json()["sub"].endswith(client_id)


def test_unauthenticated_request_returns_401_with_challenge(server: AuthorizationServer) -> None:
    app, _ = _build_app(server)
    client = TestClient(app)
    r = client.get("/mcp-test")
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
    auth = r.headers["www-authenticate"]
    assert auth.startswith("Bearer ")
    assert 'realm="mcp"' in auth
    assert "resource_metadata=" in auth


def test_insufficient_scope_returns_403(server: AuthorizationServer) -> None:
    app, _rs = _build_app(server)
    # Mint a token with a scope set that does not satisfy the route.
    reg = TestClient(app).post(
        "/oauth/register",
        json={"client_name": "T", "redirect_uris": ["http://localhost:0/cb"]},
    )
    client_id = reg.json()["client_id"]
    # Issue a token manually that has an unrelated scope.
    claims = {
        "iss": server.settings.issuer,
        "aud": server.settings.resource,
        "sub": "sub",
        "client_id": client_id,
        "scope": "mcp:unrelated",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "jti": "j1",
    }
    token = sign_jwt(claims, server.settings.signing_key, header_extras={"kid": "kid-1"})
    r = TestClient(app).get("/mcp-test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert "insufficient_scope" in r.headers["www-authenticate"]


def test_authorize_invalid_pkce_method_rejected(server: AuthorizationServer) -> None:
    app, _ = _build_app(server)
    client = TestClient(app)
    reg = client.post(
        "/oauth/register",
        json={"client_name": "T", "redirect_uris": ["http://localhost:0/cb"]},
    )
    client_id = reg.json()["client_id"]
    q = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost:0/cb",
            "code_challenge": "x" * 43,
            "code_challenge_method": "plain",
            "resource": server.settings.resource,
        }
    )
    r = client.get(f"/oauth/authorize?{q}", follow_redirects=False)
    assert r.status_code == 302
    redirect = urllib.parse.urlparse(r.headers["location"])
    qs = urllib.parse.parse_qs(redirect.query)
    assert qs["error"] == ["invalid_request"]


def test_authorize_wrong_resource_invalid_target(server: AuthorizationServer) -> None:
    app, _ = _build_app(server)
    client = TestClient(app)
    reg = client.post(
        "/oauth/register",
        json={"client_name": "T", "redirect_uris": ["http://localhost:0/cb"]},
    )
    client_id = reg.json()["client_id"]
    q = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "http://localhost:0/cb",
            "code_challenge": s256_challenge(generate_code_verifier()),
            "code_challenge_method": "S256",
            "resource": "https://other.example/mcp",
        }
    )
    r = client.get(f"/oauth/authorize?{q}", follow_redirects=False)
    assert r.status_code == 302
    redirect = urllib.parse.urlparse(r.headers["location"])
    qs = urllib.parse.parse_qs(redirect.query)
    assert qs["error"] == ["invalid_target"]


def test_token_endpoint_unsupported_grant(server: AuthorizationServer) -> None:
    app, _ = _build_app(server)
    client = TestClient(app)
    r = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": "x"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_grant_type"


def test_revoke_endpoint(server: AuthorizationServer) -> None:
    app, _ = _build_app(server)
    client = TestClient(app)
    r = client.post("/oauth/revoke", data={"token": "totally-unknown"})
    # RFC 7009 § 2.2 — unknown tokens still return 200.
    assert r.status_code == 200


def test_revoke_requires_token_parameter(server: AuthorizationServer) -> None:
    app, _ = _build_app(server)
    client = TestClient(app)
    r = client.post("/oauth/revoke", data={})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


def test_challenge_header_helper_escapes_quotes() -> None:
    headers = oauth_challenge_headers(
        resource_metadata_url='https://app/.well-known/x"injected',
        error="invalid_token",
        error_description='it said "no"',
        scope="mcp:tools.read mcp:tools.write",
    )
    value = headers["WWW-Authenticate"]
    assert value.startswith("Bearer ")
    # Quotes inside values must be backslash-escaped.
    assert '\\"' in value
