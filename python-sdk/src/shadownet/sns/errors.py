from __future__ import annotations

from shadownet.errors import ShadownetError

__all__ = [
    "SNSError",
    "ShadownameExpired",
    "ShadownameInvalid",
    "ShadownameNotFound",
    "ShadownameTombstoned",
]


class SNSError(ShadownetError):
    """Base for SNS-related errors."""


class ShadownameInvalid(SNSError):
    """The shadowname does not match the RFC-0005 grammar."""


class ShadownameExpired(ShadownameInvalid):
    """Record was well-formed and validly signed but its `exp` is in the past.

    Subclass of :class:`ShadownameInvalid` so existing handlers keep working;
    catch this specifically when expiry is transient and re-resolvable.
    """


class ShadownameNotFound(SNSError):
    """SNS provider returned 404 for this shadowname."""


class ShadownameTombstoned(SNSError):
    """SNS provider returned 410 — shadowname existed and was deleted."""
