from __future__ import annotations

import pytest

from shadownet.oauth.errors import (
    InvalidClient,
    InvalidGrant,
    InvalidRedirectURI,
    InvalidRequest,
    InvalidScope,
    InvalidTarget,
    InvalidToken,
    UnauthorizedClient,
)
from shadownet.oauth.models import ClientRegistrationRequest
from shadownet.oauth.pkce import generate_code_verifier, s256_challenge
from shadownet.oauth.scopes import (
    SCOPE_OFFLINE_ACCESS,
    SCOPE_TOOLS_READ,
    SCOPE_TOOLS_WRITE,
)
from shadownet.oauth.server import (
    AuthorizationConsent,
    AuthorizationRequest,
    AuthorizationServer,
    AuthorizationServerSettings,
    ResourceServer,
    validate_redirect_uri,
)


async def _registered_public_client(server: AuthorizationServer) -> tuple[str, str]:
    response = await server.register_client(
        ClientRegistrationRequest.model_validate(
            {
                "client_name": "Test Public Client",
                "redirect_uris": ["http://localhost:0/callback"],
                "token_endpoint_auth_method": "none",
                "scope": "mcp:tools.read mcp:tools.write offline_access",
            }
        )
    )
    assert response.client_secret is None
    return response.client_id, response.redirect_uris[0]


def _build_request(
    *,
    client_id: str,
    redirect_uri: str,
    verifier: str,
    resource: str,
    scope: str | None = None,
    state: str | None = None,
) -> AuthorizationRequest:
    return AuthorizationRequest(
        response_type="code",
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=s256_challenge(verifier),
        code_challenge_method="S256",
        resource=resource,
        scope=scope,
        state=state,
    )


async def test_metadata_includes_required_fields(auth_server: AuthorizationServer) -> None:
    meta = auth_server.metadata()
    assert meta.issuer == "https://app.example/u/alice"
    assert meta.code_challenge_methods_supported == ["S256"]
    assert "authorization_code" in meta.grant_types_supported
    assert "refresh_token" in meta.grant_types_supported
    assert meta.registration_endpoint is not None
    assert meta.revocation_endpoint is not None
    assert "none" in meta.token_endpoint_auth_methods_supported


async def test_registers_public_client(auth_server: AuthorizationServer) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    assert client_id.startswith("cid_")
    assert redirect_uri == "http://localhost:0/callback"


async def test_register_rejects_unknown_scope(auth_server: AuthorizationServer) -> None:
    with pytest.raises(InvalidScope):
        await auth_server.register_client(
            ClientRegistrationRequest.model_validate(
                {
                    "client_name": "x",
                    "redirect_uris": ["http://localhost:0/cb"],
                    "scope": "mcp:tools.read invalid.scope",
                }
            )
        )


async def test_register_rejects_non_loopback_http_redirect(
    auth_server: AuthorizationServer,
) -> None:
    with pytest.raises(InvalidRequest):
        await auth_server.register_client(
            ClientRegistrationRequest.model_validate(
                {
                    "client_name": "x",
                    "redirect_uris": ["http://evil.example/cb"],
                }
            )
        )


async def test_authorize_requires_pkce_s256(auth_server: AuthorizationServer) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    request = _build_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=generate_code_verifier(),
        resource=auth_server.settings.resource,
    )
    # Mutate to plain
    request_plain = request.model_copy(update={"code_challenge_method": "plain"})
    with pytest.raises(InvalidRequest):
        await auth_server.validate_authorization_request(request_plain)


async def test_authorize_rejects_wrong_resource(auth_server: AuthorizationServer) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    request = _build_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=generate_code_verifier(),
        resource="https://other.example/mcp",
    )
    with pytest.raises(InvalidTarget):
        await auth_server.validate_authorization_request(request)


async def test_authorize_rejects_unregistered_redirect(
    auth_server: AuthorizationServer,
) -> None:
    client_id, _ = await _registered_public_client(auth_server)
    request = _build_request(
        client_id=client_id,
        redirect_uri="https://impostor.example/cb",
        verifier=generate_code_verifier(),
        resource=auth_server.settings.resource,
    )
    with pytest.raises(InvalidRedirectURI):
        await auth_server.validate_authorization_request(request)


async def test_authorize_accepts_loopback_redirect_with_any_port(
    auth_server: AuthorizationServer,
) -> None:
    # RFC-0009 § Security considerations relaxes port equality for
    # loopback redirect URIs.
    client_id, _ = await _registered_public_client(auth_server)
    request = _build_request(
        client_id=client_id,
        redirect_uri="http://localhost:54321/callback",
        verifier=generate_code_verifier(),
        resource=auth_server.settings.resource,
    )
    client = await auth_server.validate_authorization_request(request)
    assert client.client_id == client_id


async def test_validate_redirect_uri_loopback_helper() -> None:
    assert validate_redirect_uri(
        registered=("http://localhost:0/callback",),
        presented="http://localhost:9876/callback",
    )
    assert not validate_redirect_uri(
        registered=("http://localhost:0/callback",),
        presented="http://localhost:9876/elsewhere",
    )
    assert validate_redirect_uri(
        registered=("https://example.com/cb",),
        presented="https://example.com/cb",
    )
    assert not validate_redirect_uri(
        registered=("https://example.com/cb",),
        presented="https://example.com/cb?injected",
    )


async def test_full_authorization_code_flow_with_offline_access(
    auth_server: AuthorizationServer, resource_server: ResourceServer
) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    verifier = generate_code_verifier()
    request = _build_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=verifier,
        resource=auth_server.settings.resource,
        scope="mcp:tools.read mcp:tools.write offline_access",
        state="opaque-state",
    )
    consent = AuthorizationConsent(
        subject="did:web:app.example#alice",
        granted_scopes=frozenset({SCOPE_TOOLS_READ, SCOPE_TOOLS_WRITE, SCOPE_OFFLINE_ACCESS}),
    )
    code = await auth_server.issue_code(request, consent)
    response = await auth_server.exchange_authorization_code(
        code=code.code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=auth_server.settings.resource,
    )
    assert response.access_token
    assert response.refresh_token is not None
    assert response.expires_in == auth_server.settings.access_token_ttl_seconds
    # Resource server can validate the access token end-to-end.
    claims = resource_server.validate(
        response.access_token, required_scopes=frozenset({SCOPE_TOOLS_READ})
    )
    assert claims.aud == auth_server.settings.resource
    assert claims.iss == auth_server.settings.issuer
    assert SCOPE_TOOLS_READ in claims.scope_set
    # And rejects insufficient scope.
    with pytest.raises(InvalidScope):
        resource_server.validate(
            response.access_token,
            required_scopes=frozenset({"mcp:nonexistent"}),
        )


async def test_code_single_use(auth_server: AuthorizationServer) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    verifier = generate_code_verifier()
    request = _build_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=verifier,
        resource=auth_server.settings.resource,
    )
    consent = AuthorizationConsent(subject="sub", granted_scopes=frozenset({SCOPE_TOOLS_READ}))
    code = await auth_server.issue_code(request, consent)
    await auth_server.exchange_authorization_code(
        code=code.code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=auth_server.settings.resource,
    )
    with pytest.raises(InvalidGrant):
        await auth_server.exchange_authorization_code(
            code=code.code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=verifier,
            resource=auth_server.settings.resource,
        )


async def test_pkce_verifier_mismatch_rejected(auth_server: AuthorizationServer) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    request = _build_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=generate_code_verifier(),
        resource=auth_server.settings.resource,
    )
    consent = AuthorizationConsent(subject="sub", granted_scopes=frozenset({SCOPE_TOOLS_READ}))
    code = await auth_server.issue_code(request, consent)
    with pytest.raises(InvalidGrant):
        await auth_server.exchange_authorization_code(
            code=code.code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=generate_code_verifier(),  # wrong verifier
            resource=auth_server.settings.resource,
        )


async def test_refresh_rotation_and_replay_revocation(
    auth_server: AuthorizationServer,
) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    verifier = generate_code_verifier()
    request = _build_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=verifier,
        resource=auth_server.settings.resource,
        scope="mcp:tools.read offline_access",
    )
    consent = AuthorizationConsent(
        subject="sub", granted_scopes=frozenset({SCOPE_TOOLS_READ, SCOPE_OFFLINE_ACCESS})
    )
    code = await auth_server.issue_code(request, consent)
    initial = await auth_server.exchange_authorization_code(
        code=code.code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=auth_server.settings.resource,
    )
    assert initial.refresh_token is not None

    rotated = await auth_server.exchange_refresh_token(
        refresh_token=initial.refresh_token,
        client_id=client_id,
    )
    assert rotated.refresh_token is not None
    assert rotated.refresh_token != initial.refresh_token

    # Replay of the original refresh token revokes the whole family.
    with pytest.raises(InvalidGrant):
        await auth_server.exchange_refresh_token(
            refresh_token=initial.refresh_token,
            client_id=client_id,
        )
    # The freshly-rotated token is now also revoked.
    with pytest.raises(InvalidGrant):
        await auth_server.exchange_refresh_token(
            refresh_token=rotated.refresh_token,
            client_id=client_id,
        )


async def test_refresh_cannot_expand_scope(auth_server: AuthorizationServer) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    verifier = generate_code_verifier()
    request = _build_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=verifier,
        resource=auth_server.settings.resource,
        scope="mcp:tools.read offline_access",
    )
    consent = AuthorizationConsent(
        subject="sub", granted_scopes=frozenset({SCOPE_TOOLS_READ, SCOPE_OFFLINE_ACCESS})
    )
    code = await auth_server.issue_code(request, consent)
    pair = await auth_server.exchange_authorization_code(
        code=code.code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=auth_server.settings.resource,
    )
    assert pair.refresh_token is not None
    with pytest.raises(InvalidScope):
        await auth_server.exchange_refresh_token(
            refresh_token=pair.refresh_token,
            client_id=client_id,
            requested_scope=frozenset({SCOPE_TOOLS_READ, "mcp:tools.write"}),
        )


async def test_offline_access_not_granted_no_refresh_token(
    auth_server: AuthorizationServer,
) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    verifier = generate_code_verifier()
    request = _build_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=verifier,
        resource=auth_server.settings.resource,
        scope="mcp:tools.read",
    )
    consent = AuthorizationConsent(subject="sub", granted_scopes=frozenset({SCOPE_TOOLS_READ}))
    code = await auth_server.issue_code(request, consent)
    pair = await auth_server.exchange_authorization_code(
        code=code.code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=auth_server.settings.resource,
    )
    assert pair.refresh_token is None


async def test_revoke_blocks_future_refresh(auth_server: AuthorizationServer) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    verifier = generate_code_verifier()
    request = _build_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=verifier,
        resource=auth_server.settings.resource,
        scope="mcp:tools.read offline_access",
    )
    consent = AuthorizationConsent(
        subject="sub", granted_scopes=frozenset({SCOPE_TOOLS_READ, SCOPE_OFFLINE_ACCESS})
    )
    code = await auth_server.issue_code(request, consent)
    pair = await auth_server.exchange_authorization_code(
        code=code.code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        resource=auth_server.settings.resource,
    )
    assert pair.refresh_token is not None
    await auth_server.revoke(pair.refresh_token, client_id=client_id)
    with pytest.raises(InvalidGrant):
        await auth_server.exchange_refresh_token(
            refresh_token=pair.refresh_token, client_id=client_id
        )


async def test_resource_server_rejects_audience_mismatch(
    auth_server: AuthorizationServer, signing_key
) -> None:
    # Mint a token bound to a different resource, then verify the
    # resource server refuses it.
    from shadownet.crypto.jwt import sign_jwt

    claims = {
        "iss": auth_server.settings.issuer,
        "aud": "https://impostor.example/mcp",
        "sub": "sub",
        "client_id": "cid_x",
        "scope": "mcp:tools.read",
        "iat": 1700000000,
        "exp": 1700000000 + 3600,
        "jti": "j",
    }
    token = sign_jwt(claims, signing_key, header_extras={"kid": "rk-test-1", "typ": "at+jwt"})
    rs = ResourceServer.from_authorization_server(auth_server)
    with pytest.raises(InvalidToken):
        rs.validate(token)


async def test_resource_server_rejects_unknown_kid(auth_server: AuthorizationServer) -> None:
    from shadownet.crypto.jwt import sign_jwt

    claims = {
        "iss": auth_server.settings.issuer,
        "aud": auth_server.settings.resource,
        "sub": "sub",
        "client_id": "cid_x",
        "scope": "mcp:tools.read",
        "iat": 1700000000,
        "exp": 1700000000 + 3600,
        "jti": "j",
    }
    token = sign_jwt(
        claims, auth_server.settings.signing_key, header_extras={"kid": "rk-other", "typ": "at+jwt"}
    )
    rs = ResourceServer.from_authorization_server(auth_server)
    with pytest.raises(InvalidToken):
        rs.validate(token)


async def test_register_disabled_when_off(signing_key) -> None:
    # Build a fresh server with DCR disabled.
    disabled = AuthorizationServerSettings(
        issuer="https://app.example/u/alice",
        resource="https://app.example/u/alice/mcp",
        signing_key=signing_key,
        key_id="kid",
        authorization_endpoint="https://app.example/u/alice/oauth/authorize",
        token_endpoint="https://app.example/u/alice/oauth/token",
        allow_dynamic_client_registration=False,
    )
    server = AuthorizationServer(disabled)
    assert server.metadata().registration_endpoint is None
    with pytest.raises(InvalidRequest):
        await server.register_client(
            ClientRegistrationRequest.model_validate(
                {"client_name": "x", "redirect_uris": ["http://localhost:0/cb"]}
            )
        )


async def test_get_client_raises_for_unknown(auth_server: AuthorizationServer) -> None:
    with pytest.raises(InvalidClient):
        await auth_server.get_client("never-registered")


async def test_consent_narrows_to_granted_subset(auth_server: AuthorizationServer) -> None:
    client_id, redirect_uri = await _registered_public_client(auth_server)
    verifier = generate_code_verifier()
    request = _build_request(
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=verifier,
        resource=auth_server.settings.resource,
        scope="mcp:tools.read mcp:tools.write",
    )
    consent = AuthorizationConsent(subject="sub", granted_scopes=frozenset({SCOPE_TOOLS_READ}))
    code = await auth_server.issue_code(request, consent)
    assert code.scope == frozenset({SCOPE_TOOLS_READ})


async def test_authorization_code_grant_must_be_enabled_on_client(
    auth_server: AuthorizationServer,
) -> None:
    response = await auth_server.register_client(
        ClientRegistrationRequest.model_validate(
            {
                "client_name": "code-disabled",
                "redirect_uris": ["http://localhost:0/cb"],
                "grant_types": ["refresh_token"],  # no authorization_code
                "response_types": ["code"],
            }
        )
    )
    request = _build_request(
        client_id=response.client_id,
        redirect_uri="http://localhost:0/cb",
        verifier=generate_code_verifier(),
        resource=auth_server.settings.resource,
    )
    with pytest.raises(UnauthorizedClient):
        await auth_server.validate_authorization_request(request)
