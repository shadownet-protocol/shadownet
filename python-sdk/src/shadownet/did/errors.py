from __future__ import annotations

from shadownet.errors import ShadownetError

__all__ = [
    "DIDDocumentTooLarge",
    "DIDError",
    "DIDMethodUnsupported",
    "DIDNotResolvable",
    "DIDSyntaxError",
]


class DIDError(ShadownetError):
    """Base class for DID-related errors."""


class DIDSyntaxError(DIDError):
    """A DID string is malformed."""


class DIDMethodUnsupported(DIDError):
    """The DID method is not registered with the resolver."""


class DIDNotResolvable(DIDError):
    """The DID exists syntactically but its document could not be retrieved."""


class DIDDocumentTooLarge(DIDError):
    """A did:web document exceeded the 16 KiB cap (RFC-0002)."""
