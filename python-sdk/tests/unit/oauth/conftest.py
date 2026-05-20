from __future__ import annotations

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.oauth.server import (
    AuthorizationServer,
    AuthorizationServerSettings,
    ResourceServer,
)


@pytest.fixture
def signing_key() -> Ed25519KeyPair:
    # Deterministic seed so test failures point at a fixed key for
    # debugging; not a real secret.
    return Ed25519KeyPair.from_seed(b"\x11" * 32)


@pytest.fixture
def settings(signing_key: Ed25519KeyPair) -> AuthorizationServerSettings:
    return AuthorizationServerSettings(
        issuer="https://app.example/u/alice",
        resource="https://app.example/u/alice/mcp",
        signing_key=signing_key,
        key_id="rk-test-1",
        authorization_endpoint="https://app.example/u/alice/oauth/authorize",
        token_endpoint="https://app.example/u/alice/oauth/token",
        registration_endpoint="https://app.example/u/alice/oauth/register",
        revocation_endpoint="https://app.example/u/alice/oauth/revoke",
        jwks_uri=None,
    )


@pytest.fixture
def auth_server(settings: AuthorizationServerSettings) -> AuthorizationServer:
    return AuthorizationServer(settings)


@pytest.fixture
def resource_server(auth_server: AuthorizationServer) -> ResourceServer:
    return ResourceServer.from_authorization_server(auth_server)
