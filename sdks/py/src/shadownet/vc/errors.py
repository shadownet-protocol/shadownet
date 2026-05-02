from __future__ import annotations

from shadownet.errors import ShadownetError

__all__ = [
    "CredentialInvalid",
    "FreshnessExpired",
    "PresentationInvalid",
    "Revoked",
    "StatusListUnavailable",
]


class CredentialInvalid(ShadownetError):
    """A Verifiable Credential failed validation (signature, schema, expiry, …)."""


class FreshnessExpired(CredentialInvalid):
    """A required freshness proof is missing or older than the verifier's window."""


class Revoked(CredentialInvalid):
    """The credential's status-list bit is set."""


class StatusListUnavailable(CredentialInvalid):
    """The BitstringStatusList could not be fetched. Per RFC-0003 this fails closed for >L1."""


class PresentationInvalid(ShadownetError):
    """A Verifiable Presentation failed validation (signature, audience, nonce, …)."""
