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


def test_default_host_templates_contains_well_known_slugs() -> None:
    """Sanity: defaults include the slugs RFC-0008 examples ship with."""
    for slug in ("hermes-agent", "claude-code", "cursor", "raw"):
        assert slug in DEFAULT_HOST_TEMPLATES


def test_connect_host_json_carries_shadownet_v_marker() -> None:
    """RFC-0008: every application/json response on /connect/<host> MUST
    carry a top-level ``shadownet:v: 0.1`` field."""
    client = TestClient(_build_app())
    for slug in ("hermes-agent", "claude-code", "cursor"):
        r = client.get(
            f"/connect/{slug}",
            headers={
                "Authorization": f"Bearer {VALID_TOKEN}",
                "Accept": "application/json",
            },
        )
        assert r.status_code == 200, slug
        assert r.json()["shadownet:v"] == "0.1", slug


def test_connect_raw_json_is_the_bundle() -> None:
    client = TestClient(_build_app())
    r = client.get(
        "/connect/raw",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["shadownet:v"] == "0.1"
    assert "mcp_endpoint" in body


def test_connect_index_json_carries_marker() -> None:
    """The index endpoint's JSON form also carries the universal marker."""
    client = TestClient(_build_app())
    r = client.get("/connect", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert body["shadownet:v"] == "0.1"
    assert "hermes-agent" in body["hosts"]


def test_claude_code_json_has_mcp_server_config() -> None:
    """RFC-0008 well-known-hosts.md: claude-code JSON form returns
    ``{ "shadownet:v": "0.1", "mcpServerConfig": {...} }``."""
    client = TestClient(_build_app())
    r = client.get(
        "/connect/claude-code",
        headers={"Authorization": f"Bearer {VALID_TOKEN}", "Accept": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["shadownet:v"] == "0.1"
    assert "mcpServerConfig" in body
    assert "shadownet" in body["mcpServerConfig"]
    assert body["mcpServerConfig"]["shadownet"]["url"].startswith("https://")


def test_hermes_agent_json_has_config_schema() -> None:
    """RFC-0008 well-known-hosts.md: hermes-agent JSON form returns
    ``{ "shadownet:v": "0.1", "configSchema": {...} }``."""
    client = TestClient(_build_app())
    r = client.get(
        "/connect/hermes-agent",
        headers={"Authorization": f"Bearer {VALID_TOKEN}", "Accept": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["shadownet:v"] == "0.1"
    assert "configSchema" in body
    assert "SHADOWNET_TOKEN" in body["configSchema"]


def test_raw_slug_cannot_be_overridden() -> None:
    """RFC-0008 examples/well-known-hosts.md reserves ``raw`` — operator
    templates supplied under this slug MUST be ignored."""

    class _Hijack:
        def render_text(self, bundle):
            return "MALICIOUS"

        def render_html(self, bundle):
            return "<p>malicious</p>"

        def render_json(self, bundle):
            return {"malicious": True}

    client = TestClient(_build_app(extra_hosts={"raw": _Hijack()}))
    r = client.get("/connect/raw", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
    assert r.status_code == 200
    # The default raw template (canonical bundle JSON) is what we get,
    # not the operator-supplied template.
    body = r.json()
    assert "mcp_endpoint" in body
    assert "malicious" not in body


def test_handoff_endpoint_not_mounted_when_unconfigured() -> None:
    """Without a handoff_resolver, the endpoint is not mounted at all (404)."""
    client = TestClient(_build_app())
    r = client.post("/v1/account/connect/handoff/SHORT-CODE-123456", json={})
    assert r.status_code == 404


def test_handoff_endpoint_resolves_valid_code() -> None:
    """RFC-0008: server takes only the code; request body MAY be empty."""

    async def resolver(code: str) -> str | None:
        if code == "GOOD-CODE-12345678":
            return "minted-token"
        return None

    client = TestClient(_build_app(handoff_resolver=resolver))
    r = client.post("/v1/account/connect/handoff/GOOD-CODE-12345678", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["shadownet:v"] == "0.1"
    assert body["token"] == "minted-token"
    # RFC-0008 RECOMMENDED TTL is 15 minutes; that's the default our
    # router advertises in expires_in.
    assert body["expires_in"] == 15 * 60


def test_handoff_ignores_client_nonce_per_spec() -> None:
    """RFC-0008: v0.1 servers MUST IGNORE the reserved field ``client_nonce``
    if present. (Before the spec landed, our router REQUIRED it and
    rejected absences with 400 — that's the bug this test guards
    against regressing.)
    """

    async def resolver(code: str) -> str | None:
        return "minted-token"

    client = TestClient(_build_app(handoff_resolver=resolver))
    # Whether the client sends client_nonce or not, the server resolves
    # the code identically.
    r1 = client.post(
        "/v1/account/connect/handoff/GOOD-CODE-12345678", json={"client_nonce": "n" * 32}
    )
    r2 = client.post("/v1/account/connect/handoff/GOOD-CODE-12345678", json={})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["token"] == r2.json()["token"]


def test_handoff_invalid_code_returns_404() -> None:
    async def resolver(code: str) -> str | None:
        return None

    client = TestClient(_build_app(handoff_resolver=resolver))
    r = client.post("/v1/account/connect/handoff/BAD-CODE-12345678", json={})
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "handoff_invalid_or_expired"


def test_handoff_custom_ttl() -> None:
    """Operators may override the advertised TTL."""

    async def resolver(code: str) -> str | None:
        return "minted-token"

    app = _build_app(handoff_resolver=resolver)
    # Rebuild router with custom TTL by calling build_connect_router directly.
    from shadownet.connect.fastapi import build_connect_router

    custom_app = FastAPI()
    accepted = {VALID_TOKEN}

    async def bb(token: str):
        return _bundle_for(token) if token in accepted else None

    custom_app.include_router(
        build_connect_router(bundle_builder=bb, handoff_resolver=resolver, handoff_ttl_seconds=300)
    )
    client = TestClient(custom_app)
    r = client.post("/v1/account/connect/handoff/GOOD-CODE-12345678", json={})
    assert r.status_code == 200
    assert r.json()["expires_in"] == 300
    # Reference the unused fixture so ruff doesn't complain.
    _ = app
