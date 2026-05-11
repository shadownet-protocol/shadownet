from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from shadownet.connect.bundle import IntegrationBundle
from shadownet.connect.fastapi import (
    DEFAULT_HOST_TEMPLATES,
    HostTemplate,
    build_connect_router,
)

VALID_TOKEN = "tok_abc"


def _bundle_for(token: str) -> IntegrationBundle:
    return IntegrationBundle.model_validate(
        {
            "shadownet:v": "0.1",
            "did": "did:web:app.example",
            "shadowname": f"user-of-{token}@app.example",
            "mcp_endpoint": "https://app.example/u/alice/mcp",
            "webhook_secret": "wh-secret",
            "supported_features": ["mcp", "webhook", "inbox-wait", "bundle"],
            "tool_names": ["social_send", "social_inbox", "social_inbox_wait"],
            "event_names": ["inbox.message"],
            "version": "0.3.0",
        }
    )


def _build_app(
    *,
    valid: set[str] | None = None,
    extra_hosts: dict[str, HostTemplate] | None = None,
    handoff_resolver=None,
) -> FastAPI:
    accepted = valid if valid is not None else {VALID_TOKEN}

    async def resolver(token: str):
        if token in accepted:
            return _bundle_for(token)
        return None

    app = FastAPI()
    app.include_router(
        build_connect_router(
            bundle_builder=resolver,
            host_templates=extra_hosts,
            handoff_resolver=handoff_resolver,
        )
    )
    return app


def test_bundle_endpoint_returns_json() -> None:
    client = TestClient(_build_app())
    r = client.get(
        "/v1/account/me/integration-bundle",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["shadownet:v"] == "0.1"
    assert body["shadowname"] == f"user-of-{VALID_TOKEN}@app.example"
    assert "inbox-wait" in body["supported_features"]


def test_bundle_legacy_alias_works() -> None:
    client = TestClient(_build_app())
    r = client.get(
        "/v1/account/tenants/me/integration-bundle",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["shadowname"] == f"user-of-{VALID_TOKEN}@app.example"


def test_bundle_missing_auth_returns_401() -> None:
    client = TestClient(_build_app())
    r = client.get("/v1/account/me/integration-bundle")
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "missing_bearer_token"


def test_bundle_invalid_token_returns_401() -> None:
    client = TestClient(_build_app(valid=set()))
    r = client.get(
        "/v1/account/me/integration-bundle",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "invalid_token"


def test_bundle_non_bearer_scheme_returns_401() -> None:
    client = TestClient(_build_app())
    r = client.get(
        "/v1/account/me/integration-bundle",
        headers={"Authorization": "Basic abc"},
    )
    assert r.status_code == 401


def test_connect_raw_returns_bundle_json() -> None:
    client = TestClient(_build_app())
    r = client.get(
        "/connect/raw",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert r.status_code == 200
    assert r.json()["shadowname"] == f"user-of-{VALID_TOKEN}@app.example"


def test_connect_index_lists_known_hosts_as_json() -> None:
    client = TestClient(_build_app())
    r = client.get("/connect", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert "hosts" in body
    assert "hermes-agent" in body["hosts"]
    assert "raw" in body["hosts"]


def test_connect_index_returns_html_when_requested() -> None:
    client = TestClient(_build_app())
    r = client.get(
        "/connect",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "Accept": "text/html",
        },
    )
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "hermes-agent" in r.text


def test_connect_hermes_agent_returns_text_snippet() -> None:
    client = TestClient(_build_app())
    r = client.get(
        "/connect/hermes-agent",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert r.status_code == 200
    assert "hermes plugins install" in r.text
    assert "SHADOWNET_TOKEN" in r.text
    assert "SHADOWNET_SIDECAR_BASE_URL" in r.text


def test_connect_hermes_agent_html() -> None:
    client = TestClient(_build_app())
    r = client.get(
        "/connect/hermes-agent",
        headers={
            "Authorization": f"Bearer {VALID_TOKEN}",
            "Accept": "text/html",
        },
    )
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Hermes Agent install" in r.text


def test_unknown_host_returns_404_with_index_link() -> None:
    client = TestClient(_build_app())
    r = client.get(
        "/connect/nope",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_host"
    assert "Link" in r.headers
    assert 'rel="up"' in r.headers["Link"]


def test_custom_host_template_can_be_registered() -> None:
    class _CustomTemplate:
        def render_text(self, bundle):
            return f"# custom for {bundle.shadowname}"

        def render_html(self, bundle):
            return f"<p>custom for {bundle.shadowname}</p>"

    client = TestClient(_build_app(extra_hosts={"custom-host": _CustomTemplate()}))
    r = client.get(
        "/connect/custom-host",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert r.status_code == 200
    assert f"user-of-{VALID_TOKEN}" in r.text


def test_default_host_templates_contains_raw() -> None:
    """Sanity: any caller depending on DEFAULT_HOST_TEMPLATES gets 'raw' for free."""
    assert "raw" in DEFAULT_HOST_TEMPLATES
    assert "hermes-agent" in DEFAULT_HOST_TEMPLATES


def test_handoff_endpoint_returns_501_when_not_configured() -> None:
    """Without a handoff_resolver, the endpoint is not mounted at all (404)."""
    client = TestClient(_build_app())
    r = client.post(
        "/v1/account/connect/handoff/SHORT-CODE-123",
        json={"client_nonce": "n" * 32},
    )
    assert r.status_code == 404


def test_handoff_endpoint_resolves_valid_code() -> None:
    async def resolver(code: str, nonce: str) -> str | None:
        if code == "GOOD-CODE-1234" and len(nonce) >= 16:
            return "minted-token"
        return None

    client = TestClient(_build_app(handoff_resolver=resolver))
    r = client.post(
        "/v1/account/connect/handoff/GOOD-CODE-1234",
        json={"client_nonce": "n" * 32},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token"] == "minted-token"
    assert body["expires_in"] == 600


def test_handoff_rejects_short_nonce() -> None:
    async def resolver(code: str, nonce: str) -> str | None:
        return "minted-token"

    client = TestClient(_build_app(handoff_resolver=resolver))
    r = client.post(
        "/v1/account/connect/handoff/GOOD-CODE-1234",
        json={"client_nonce": "short"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "missing_or_short_client_nonce"


def test_handoff_invalid_code_returns_404() -> None:
    async def resolver(code: str, nonce: str) -> str | None:
        return None

    client = TestClient(_build_app(handoff_resolver=resolver))
    r = client.post(
        "/v1/account/connect/handoff/BAD-CODE-1234",
        json={"client_nonce": "n" * 32},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "handoff_invalid_or_expired"
