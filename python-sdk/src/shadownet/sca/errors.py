from __future__ import annotations

from shadownet.errors import ShadownetError

# RFC-0004 error codes — one subclass per code so callers can `except <SpecificError>`.

__all__ = [
    "CSRInvalid",
    "InvalidLevel",
    "NotHolder",
    "RateLimited",
    "SCAError",
    "SCAHTTPError",
    "SessionConsumed",
    "SessionMismatch",
    "SessionNotReady",
    "SubjectBlocked",
    "UnknownJti",
    "code_to_error",
]


class SCAError(ShadownetError):
    """Base for SCA HTTP-level errors."""


class SCAHTTPError(SCAError):
    """An SCA returned an unexpected HTTP status with no Shadownet error code."""


class InvalidLevel(SCAError):
    """RFC-0004 `invalid_level` — level URI not offered by this SCA."""


class SubjectBlocked(SCAError):
    """RFC-0004 `subject_blocked` — subject DID has been banned from this SCA."""


class RateLimited(SCAError):
    """RFC-0004 `rate_limited`."""


class CSRInvalid(SCAError):
    """RFC-0004 `csr_invalid` — CSR signature, audience, or shape failed."""


class SessionMismatch(SCAError):
    """RFC-0004 `session_mismatch` — sessionId does not match CSR subject or level."""


class SessionNotReady(SCAError):
    """RFC-0004 `session_not_ready` — proof session is not yet ready."""


class SessionConsumed(SCAError):
    """RFC-0004 `session_consumed` — session was already used."""


class UnknownJti(SCAError):
    """RFC-0004 `unknown_jti` — this SCA did not issue a credential with that jti."""


class NotHolder(SCAError):
    """RFC-0004 `not_holder` — auth JWT subject is not the credential's subject."""


_REGISTRY: dict[str, type[SCAError]] = {
    "invalid_level": InvalidLevel,
    "subject_blocked": SubjectBlocked,
    "rate_limited": RateLimited,
    "csr_invalid": CSRInvalid,
    "session_mismatch": SessionMismatch,
    "session_not_ready": SessionNotReady,
    "session_consumed": SessionConsumed,
    "unknown_jti": UnknownJti,
    "not_holder": NotHolder,
    "revoked": NotHolder,  # /freshness 403 'revoked' — same family; reuse class
}


def code_to_error(code: str | None, detail: str | None = None) -> SCAError:
    klass = _REGISTRY.get(code or "", SCAError)
    return klass(detail or code or "SCA error")
