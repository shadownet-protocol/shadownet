from __future__ import annotations

from shadownet.errors import ShadownetError

__all__ = [
    "InvalidClient",
    "InvalidGrant",
    "InvalidRequest",
    "InvalidScope",
    "InvalidTarget",
    "InvalidToken",
    "OAuthError",
    "UnauthorizedClient",
    "UnsupportedGrantType",
]


class OAuthError(ShadownetError):
    """Base for shadownet.oauth errors.

    Carries the OAuth 2.1 wire code so handlers can render the standard
    JSON / WWW-Authenticate error response without re-classifying. The
    HTTP status is callsite-determined (401 for token-presentation
    failures, 400 for authorization/token-endpoint failures, 403 for
    insufficient_scope).
    """

    code: str = "server_error"

    def __init__(self, description: str | None = None) -> None:
        super().__init__(description or self.code)
        self.description = description


class InvalidRequest(OAuthError):
    code = "invalid_request"


class InvalidClient(OAuthError):
    code = "invalid_client"


class InvalidRedirectURI(OAuthError):
    """The presented redirect_uri does not match the client's registered set.

    Distinct from :class:`InvalidRequest` because OAuth 2.1 § 4.1.2.1
    forbids redirecting back to a redirect URI we have not validated.
    Callers MUST render this error directly to the user-agent.
    """

    code = "invalid_request"


class InvalidGrant(OAuthError):
    code = "invalid_grant"


class UnauthorizedClient(OAuthError):
    code = "unauthorized_client"


class UnsupportedGrantType(OAuthError):
    code = "unsupported_grant_type"


class InvalidScope(OAuthError):
    code = "invalid_scope"


class InvalidTarget(OAuthError):
    """RFC 8707 § 2.2 — the requested resource is not one the AS will issue tokens for."""

    code = "invalid_target"


class InvalidToken(OAuthError):
    """RFC 6750 § 3.1 — the access token is missing, malformed, expired, or revoked."""

    code = "invalid_token"
