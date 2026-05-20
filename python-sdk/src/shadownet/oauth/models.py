"""Pydantic wire models for RFC-0009 discovery + token endpoint payloads.

These are the documents the Sidecar serves to clients and that hosts
consume during discovery. Each model maps 1-1 with a section of the
referenced RFC; field aliases are present where the wire name uses
characters Python can't put in attribute names (e.g. ``shadownet:v``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# RFC-0009 § Discovery — only the ``header`` bearer method is permitted.
# Pulled out as a named constant so the Literal narrowing in
# ``bearer_methods_supported`` is satisfied without mypy churn.
_HEADER_METHOD: Literal["header"] = "header"

__all__ = [
    "AuthorizationServerMetadata",
    "ClientRegistrationRequest",
    "ClientRegistrationResponse",
    "ProtectedResourceMetadata",
    "TokenResponse",
]


class ProtectedResourceMetadata(BaseModel):
    """RFC 9728 § 3.1 — the document advertised at the PRM URL.

    Sidecars MUST serve this at
    ``<origin>/u/<shadowname>/.well-known/oauth-protected-resource``.
    Only fields RFC-0009 § Discovery uses are modelled; clients tolerate
    extra fields per JSON Schema's default open-world semantics.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    resource: str
    authorization_servers: list[str] = Field(min_length=1)
    scopes_supported: list[str] = Field(default_factory=list)
    bearer_methods_supported: list[Literal["header"]] = Field(
        default_factory=lambda: [_HEADER_METHOD]
    )
    resource_documentation: str | None = None
    # RFC-0009 § Token validation — when the AS exposes its JWKS the
    # PRM document may carry the URL so the resource server can pin to
    # a specific JWKS without re-reading AS metadata.
    jwks_uri: str | None = None


class AuthorizationServerMetadata(BaseModel):
    """RFC 8414 § 2 — the document advertised at the AS metadata URL.

    Field set covers everything an OAuth-2.1 client written against an
    off-the-shelf SDK might inspect: endpoints, supported grants,
    response types, code-challenge methods, token-endpoint auth methods
    and DCR availability.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    revocation_endpoint: str | None = None
    introspection_endpoint: str | None = None
    jwks_uri: str | None = None
    scopes_supported: list[str] = Field(default_factory=list)
    response_types_supported: list[str] = Field(default_factory=lambda: ["code"])
    grant_types_supported: list[str] = Field(
        default_factory=lambda: ["authorization_code", "refresh_token"]
    )
    code_challenge_methods_supported: list[str] = Field(default_factory=lambda: ["S256"])
    token_endpoint_auth_methods_supported: list[str] = Field(
        default_factory=lambda: ["none", "client_secret_post"]
    )
    # RFC-0009 § Client registration — advertised when CIMD is supported.
    client_id_metadata_document_supported: bool | None = None
    # RFC 8628 — set when the device-grant Shadownet extension is enabled.
    device_authorization_endpoint: str | None = None


class ClientRegistrationRequest(BaseModel):
    """RFC 7591 § 3.1 — the body POSTed to the DCR endpoint."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    redirect_uris: list[str] = Field(min_length=1)
    client_name: str | None = None
    client_uri: str | None = None
    logo_uri: str | None = None
    grant_types: list[str] = Field(default_factory=lambda: ["authorization_code", "refresh_token"])
    response_types: list[str] = Field(default_factory=lambda: ["code"])
    token_endpoint_auth_method: Literal["none", "client_secret_post", "client_secret_basic"] = (
        "none"  # noqa: S105 — OAuth wire value, not a credential
    )
    scope: str | None = None
    software_id: str | None = None
    software_version: str | None = None


class ClientRegistrationResponse(BaseModel):
    """RFC 7591 § 3.2.1 — the body the AS returns on successful DCR."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    client_id: str
    client_id_issued_at: int
    client_secret: str | None = None
    client_secret_expires_at: int | None = None
    redirect_uris: list[str]
    client_name: str | None = None
    grant_types: list[str]
    response_types: list[str]
    token_endpoint_auth_method: str
    scope: str | None = None


class TokenResponse(BaseModel):
    """OAuth 2.1 § 4.1.3 — the body the token endpoint returns on success."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 — token_type is the literal OAuth scheme name
    expires_in: int
    refresh_token: str | None = None
    scope: str | None = None
