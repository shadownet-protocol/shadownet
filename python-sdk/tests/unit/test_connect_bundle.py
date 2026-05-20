from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from shadownet.connect.bundle import (
    BUNDLE_PATH,
    IntegrationBundle,
    fetch_integration_bundle,
)
from shadownet.connect.errors import BundleFetchError, BundleSchemaError

BASE = "https://app.example"
TOKEN = "tok-abc"


def _bundle_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "shadownet:v": "0.1",
        "did": "did:web:app.example",
        "shadowname": "alice@app.example",
        "mcp_endpoint": f"{BASE}/u/alice/mcp",
        "webhook_secret": "wh-secret",
        "supported_features": ["mcp", "webhook", "inbox-wait", "bundle"],
        "tool_names": ["social_send", "social_inbox", "social_inbox_wait"],
        "event_names": ["inbox.message"],
        "version": "0.3.0",
    }
    payload.update(overrides)
    return payload


def _client_returning(
    *, status: int = 200, json: dict[str, object] | None = None, raise_exc: Exception | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_exc is not None:
            raise raise_exc
        assert request.url.path == BUNDLE_PATH
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(status, json=json)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_round_trip() -> None:
    async with _client_returning(json=_bundle_payload()) as client:
        bundle = await fetch_integration_bundle(client, base_url=BASE, token=TOKEN)
    assert bundle.shadowname == "alice@app.example"
    assert bundle.supports_inbox_wait is True
    assert bundle.supports_webhook is True
    assert "inbox-wait" in bundle.supported_features


async def test_strip_trailing_slash() -> None:
    async with _client_returning(json=_bundle_payload()) as client:
        bundle = await fetch_integration_bundle(client, base_url=f"{BASE}///", token=TOKEN)
    assert bundle.shadowname == "alice@app.example"


async def test_bundle_without_inbox_wait() -> None:
    """Sidecar that pre-dates RFC-0007 amendment D: no 'inbox-wait' feature."""
    payload = _bundle_payload(supported_features=["mcp", "webhook", "bundle"])
    async with _client_returning(json=payload) as client:
        bundle = await fetch_integration_bundle(client, base_url=BASE, token=TOKEN)
    assert bundle.supports_inbox_wait is False
    assert bundle.supports_webhook is True


async def test_bundle_with_mcp_notifications() -> None:
    """Sidecar advertising notifications/shadownet/* push (TS plugins use this)."""
    payload = _bundle_payload(
        supported_features=["mcp", "webhook", "inbox-wait", "mcp-notifications", "bundle"]
    )
    async with _client_returning(json=payload) as client:
        bundle = await fetch_integration_bundle(client, base_url=BASE, token=TOKEN)
    assert bundle.supports_mcp_notifications is True
    assert bundle.supports_inbox_wait is True


async def test_401_is_fetch_error() -> None:
    async with _client_returning(status=401, json={"error": "unauthorized"}) as client:
        with pytest.raises(BundleFetchError, match="invalid or expired token"):
            await fetch_integration_bundle(client, base_url=BASE, token=TOKEN)


async def test_404_explains_pre_amendment_sidecar() -> None:
    async with _client_returning(status=404, json={"error": "not found"}) as client:
        with pytest.raises(BundleFetchError, match="RFC-0007 amendment A"):
            await fetch_integration_bundle(client, base_url=BASE, token=TOKEN)


async def test_5xx_is_fetch_error() -> None:
    async with _client_returning(status=502, json={}) as client:
        with pytest.raises(BundleFetchError, match="HTTP 502"):
            await fetch_integration_bundle(client, base_url=BASE, token=TOKEN)


async def test_network_error_is_fetch_error() -> None:
    async with _client_returning(raise_exc=httpx.ConnectError("dns fail")) as client:
        with pytest.raises(BundleFetchError, match="dns fail"):
            await fetch_integration_bundle(client, base_url=BASE, token=TOKEN)


async def test_invalid_json_is_schema_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BundleSchemaError, match="not JSON"):
            await fetch_integration_bundle(client, base_url=BASE, token=TOKEN)


async def test_missing_required_field_is_schema_error() -> None:
    payload = _bundle_payload()
    del payload["shadowname"]
    async with _client_returning(json=payload) as client:
        with pytest.raises(BundleSchemaError, match="did not validate"):
            await fetch_integration_bundle(client, base_url=BASE, token=TOKEN)


async def test_wrong_protocol_version_is_schema_error() -> None:
    payload = _bundle_payload(**{"shadownet:v": "0.2"})
    async with _client_returning(json=payload) as client:
        with pytest.raises(BundleSchemaError):
            await fetch_integration_bundle(client, base_url=BASE, token=TOKEN)


def test_supports_inbox_wait_keys_off_feature_flag() -> None:
    """Capability gating happens via supported_features, not endpoint presence."""
    with_feature = IntegrationBundle.model_validate(
        _bundle_payload(supported_features=["mcp", "inbox-wait"])
    )
    assert with_feature.supports_inbox_wait is True

    without_feature = IntegrationBundle.model_validate(_bundle_payload(supported_features=["mcp"]))
    assert without_feature.supports_inbox_wait is False


def test_bundle_is_frozen() -> None:
    bundle = IntegrationBundle.model_validate(_bundle_payload())
    with pytest.raises(ValidationError):
        bundle.shadowname = "mallory@evil.example"  # type: ignore[misc]


def test_bundle_rejects_unknown_did_method() -> None:
    """RFC-0008: did MUST start with did:key: or did:web:."""
    payload = _bundle_payload(did="did:example:custom")
    with pytest.raises(ValidationError):
        IntegrationBundle.model_validate(payload)


def test_bundle_rejects_non_https_mcp_endpoint() -> None:
    """RFC-0008 schema requires mcp_endpoint to start with https://."""
    payload = _bundle_payload(mcp_endpoint="http://app.example/u/alice/mcp")
    with pytest.raises(ValidationError):
        IntegrationBundle.model_validate(payload)


def test_bundle_rejects_malformed_shadowname() -> None:
    """RFC-0005: shadowname is local@provider."""
    payload = _bundle_payload(shadowname="not-an-shadowname")
    with pytest.raises(ValidationError):
        IntegrationBundle.model_validate(payload)


def test_bundle_rejects_extra_fields() -> None:
    """RFC-0008 schema sets additionalProperties: false."""
    payload = _bundle_payload()
    payload["unexpected"] = "field"
    with pytest.raises(ValidationError):
        IntegrationBundle.model_validate(payload)


def test_bundle_accepts_did_key_for_individuals() -> None:
    """RFC-0002 + RFC-0008: did:key for individuals."""
    payload = _bundle_payload(did="did:key:z6MkSubjectPubkey")
    bundle = IntegrationBundle.model_validate(payload)
    assert bundle.did.startswith("did:key:")


def test_bundle_accepts_explicit_null_webhook_secret() -> None:
    """webhook_secret is REQUIRED but MAY be null when no subscriber exists."""
    payload = _bundle_payload(webhook_secret=None)
    bundle = IntegrationBundle.model_validate(payload)
    assert bundle.webhook_secret is None


def test_bundle_oauth_authorize_invariant_requires_prm() -> None:
    """RFC-0009: oauth-authorize in supported_features REQUIRES protected_resource_metadata."""
    payload = _bundle_payload(supported_features=["mcp", "bundle", "oauth-authorize"])
    with pytest.raises(ValidationError):
        IntegrationBundle.model_validate(payload)


def test_bundle_oauth_authorize_invariant_requires_capability() -> None:
    """RFC-0009: protected_resource_metadata REQUIRES the oauth-authorize feature flag."""
    payload = _bundle_payload(
        protected_resource_metadata="https://app.example/u/alice/.well-known/oauth-protected-resource",
    )
    with pytest.raises(ValidationError):
        IntegrationBundle.model_validate(payload)


def test_bundle_oauth_authorize_full_shape() -> None:
    """RFC-0009: paired oauth-authorize feature flag + PRM URL validates cleanly."""
    payload = _bundle_payload(
        supported_features=["mcp", "bundle", "oauth-authorize"],
        protected_resource_metadata="https://app.example/u/alice/.well-known/oauth-protected-resource",
    )
    bundle = IntegrationBundle.model_validate(payload)
    assert bundle.supports_oauth_authorize is True
    assert bundle.protected_resource_metadata is not None
