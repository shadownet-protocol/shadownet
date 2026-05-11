from __future__ import annotations

from shadownet.errors import ShadownetError

__all__ = [
    "BundleFetchError",
    "BundleSchemaError",
    "ConnectError",
    "ConnectURLInvalid",
    "MCPSessionError",
]


class ConnectError(ShadownetError):
    """Base for shadownet.connect errors."""


class BundleFetchError(ConnectError):
    """The integration-bundle endpoint could not be fetched (network, auth, missing)."""


class BundleSchemaError(ConnectError):
    """The integration-bundle response did not validate against the RFC-0007 schema."""


class ConnectURLInvalid(ConnectError):
    """A shadownet://connect URL is malformed."""


class MCPSessionError(ConnectError):
    """The Shadownet MCP session could not be established or has failed terminally."""
