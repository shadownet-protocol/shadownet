from __future__ import annotations

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
    token. Patterns mirror ``schemas/onboarding/integration-bundle.schema.json``
    in shadownet-specs.

    Capability discovery is via ``supported_features`` — plugins SHOULD check
    ``supports_inbox_wait`` before opening the long-poll loop and
    ``supports_webhook`` before issuing ``social_set_webhook``.
    """

    # RFC-0008 schema sets ``additionalProperties: false``. Mirror that here:
    # an unexpected field signals a malformed response, not a forward-compat
    # extension. (Forward-compat for the *values* — supported_features,
    # tool_names, event_names — is handled at the array level.)
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    # RFC-0002 + RFC-0008: did:key for individuals, did:web for organizations.
    did: str = Field(pattern=r"^did:(key|web):")
    # RFC-0005: local@provider form.
    shadowname: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,63}@[A-Za-z0-9.-]+$")
    mcp_endpoint: str = Field(pattern=r"^https://")
    # RFC-0009 § Relationship to RFC-0008: when oauth-authorize is in
    # supported_features the bundle MUST advertise the RFC 9728 PRM URL.
    # The cross-field invariant is checked in ``_check_oauth_invariant``.
    protected_resource_metadata: str | None = Field(default=None, pattern=r"^https://")
    webhook_secret: str | None
    supported_features: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    event_names: list[str] = Field(default_factory=list)
    version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_oauth_invariant(self) -> IntegrationBundle:
        # RFC-0009 § Relationship to RFC-0008: the PRM URL field MUST be
        # present iff `oauth-authorize` is advertised. Either side missing
        # is a malformed bundle.
        oauth = "oauth-authorize" in self.supported_features
        prm = self.protected_resource_metadata is not None
        if oauth and not prm:
            raise ValueError(
                "supported_features includes 'oauth-authorize' but "
                "protected_resource_metadata is missing (RFC-0009)"
            )
        if prm and not oauth:
            raise ValueError(
                "protected_resource_metadata is set but supported_features "
                "does not include 'oauth-authorize' (RFC-0009)"
            )
        return self

    @property
    def supports_inbox_wait(self) -> bool:
        """Whether the sidecar advertises the ``social_inbox_wait`` long-poll tool."""
        return "inbox-wait" in self.supported_features

    @property
    def supports_webhook(self) -> bool:
        return "webhook" in self.supported_features

    @property
    def supports_mcp_notifications(self) -> bool:
        """Whether the sidecar pushes ``notifications/shadownet/*`` events.

        TS plugins (OpenClaw) can subscribe via ``setNotificationHandler``;
        Python plugins fall back to :attr:`supports_inbox_wait` due to a
        Python MCP SDK validation limitation.
        """
        return "mcp-notifications" in self.supported_features

    @property
    def supports_oauth_authorize(self) -> bool:
        """Whether the sidecar advertises the RFC-0009 OAuth 2.1 profile.

        When ``True``, :attr:`protected_resource_metadata` is the URL of
        the RFC 9728 PRM document and OAuth-capable host agents SHOULD
        prefer it over RFC-0008 paste-based onboarding.
        """
        return "oauth-authorize" in self.supported_features


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
