"""RFC-0009 OAuth 2.1 authorization profile for Sidecar MCP endpoints.

This module is the reference implementation of [RFC-0009](https://github.com/shadownet-protocol/shadownet-specs/blob/main/rfcs/0009-authorization.md):
a strict superset of the MCP authorization specification at
`/specification/latest/basic/authorization` composed from OAuth 2.1, RFC
9728 (Protected Resource Metadata), RFC 8414 (AS Metadata), RFC 7636
(PKCE), RFC 8707 (Resource Indicators), RFC 7591 (Dynamic Client
Registration) and RFC 7009 (Token Revocation).

Two surfaces are exposed:

- :mod:`shadownet.oauth.server` — server-side primitives. Sidecar
  operators wire an :class:`~shadownet.oauth.server.AuthorizationServer`
  into their FastAPI app via :func:`~shadownet.oauth.server.build_oauth_router`
  and validate inbound bearer tokens through
  :class:`~shadownet.oauth.server.ResourceServer`. The reference
  Authorization Server is co-located with the Sidecar; deployments may
  also point the PRM document at an external AS.

- :mod:`shadownet.oauth.client` — client-side primitives. Host agents
  written in Python (or scripting consumers of the SDK) use
  :class:`~shadownet.oauth.client.OAuthClient` to perform PKCE-protected
  authorization-code flows, dynamic client registration and refresh-token
  exchanges against any RFC-0009 Sidecar.

Scope identifiers (`mcp:tools.read`, `mcp:tools.write`, `mcp:inbox.wait`,
`offline_access`) are exported as module-level constants so callers
can avoid stringly-typed scope sets.
"""

from __future__ import annotations

from shadownet.oauth.errors import (
    InvalidClient,
    InvalidGrant,
    InvalidRedirectURI,
    InvalidRequest,
    InvalidScope,
    InvalidToken,
    OAuthError,
    UnauthorizedClient,
    UnsupportedGrantType,
)
from shadownet.oauth.scopes import (
    DEFAULT_SCOPES,
    SCOPE_INBOX_WAIT,
    SCOPE_OFFLINE_ACCESS,
    SCOPE_TOOLS_READ,
    SCOPE_TOOLS_WRITE,
    TOOL_SCOPE_REQUIREMENTS,
    required_scopes_for_tool,
)

__all__ = [
    "DEFAULT_SCOPES",
    "SCOPE_INBOX_WAIT",
    "SCOPE_OFFLINE_ACCESS",
    "SCOPE_TOOLS_READ",
    "SCOPE_TOOLS_WRITE",
    "TOOL_SCOPE_REQUIREMENTS",
    "InvalidClient",
    "InvalidGrant",
    "InvalidRedirectURI",
    "InvalidRequest",
    "InvalidScope",
    "InvalidToken",
    "OAuthError",
    "UnauthorizedClient",
    "UnsupportedGrantType",
    "required_scopes_for_tool",
]
