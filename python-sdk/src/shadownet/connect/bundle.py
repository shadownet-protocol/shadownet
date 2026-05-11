from __future__ import annotations

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shadownet.connect.errors import BundleFetchError, BundleSchemaError
from shadownet.logging import get_logger

# RFC-0007 amendment A — Integration bundle endpoint.
#
#   GET <base>/v1/account/me/integration-bundle
#   Authorization: Bearer <token>
#   → 200 application/json with the schema below.
#
# Promoted from optional to MUST in the one-token release. Sidecars at
# protocol version 0.1 that pre-date the amendment return 404; callers MUST
# fall back to manual configuration in that case.

BUNDLE_PATH = "/v1/account/me/integration-bundle"

__all__ = ["BUNDLE_PATH", "IntegrationBundle", "fetch_integration_bundle"]

_log = get_logger(__name__)


class IntegrationBundle(BaseModel):
    """Per-tenant bootstrap payload returned by the integration-bundle endpoint.

    Plugins fetch this once at install time using the user's account bearer
    token. Every field except ``stream_endpoint`` and ``webhook_secret`` is
    REQUIRED — those two are nullable so a sidecar can advertise that it does
    not support RFC-0008 streaming or that no webhook subscriber is wired yet.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True, frozen=True)

    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    did: str = Field(min_length=1)
    shadowname: str = Field(min_length=1)
    mcp_endpoint: str = Field(min_length=1)
    stream_endpoint: str | None = None
    webhook_secret: str | None = None
    supported_features: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    event_names: list[str] = Field(default_factory=list)
    version: str = Field(min_length=1)

    @property
    def supports_stream(self) -> bool:
        """Whether the sidecar advertises RFC-0008 outbound stream support."""
        return "stream" in self.supported_features and self.stream_endpoint is not None

    @property
    def supports_webhook(self) -> bool:
        return "webhook" in self.supported_features


async def fetch_integration_bundle(
    http: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
) -> IntegrationBundle:
    """Fetch the per-tenant integration bundle.

    Args:
        http: caller-provided async client (lets the caller share connection
            pools, set timeouts, inject test transports).
        base_url: sidecar base URL, e.g. ``https://app.sh4dow.org``. Trailing
            slashes are stripped.
        token: account bearer token.

    Raises:
        BundleFetchError: network failure, 4xx/5xx, or pre-amendment sidecar
            returning 404.
        BundleSchemaError: response is not JSON or does not match the
            normative schema.
    """
    url = f"{base_url.rstrip('/')}{BUNDLE_PATH}"
    try:
        response = await http.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
    except httpx.HTTPError as exc:
        raise BundleFetchError(f"failed to fetch {url}: {exc}") from exc

    if response.status_code == 401:
        raise BundleFetchError(f"{url} returned 401 — invalid or expired token")
    if response.status_code == 404:
        raise BundleFetchError(
            f"{url} returned 404 — sidecar at {base_url} does not implement "
            "RFC-0007 amendment A; fall back to manual configuration"
        )
    if response.status_code != 200:
        raise BundleFetchError(f"{url} returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise BundleSchemaError(f"bundle response is not JSON: {exc}") from exc

    try:
        bundle = IntegrationBundle.model_validate(payload)
    except ValidationError as exc:
        raise BundleSchemaError(f"bundle response did not validate: {exc}") from exc

    _log.debug(
        "fetched integration bundle for %s (features=%s)",
        bundle.shadowname,
        bundle.supported_features,
    )
    return bundle
