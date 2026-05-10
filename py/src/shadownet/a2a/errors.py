from __future__ import annotations

from shadownet.errors import ShadownetError

# RFC-0006 §Errors — wire error codes. These represent the *response* the
# server returns to the client; they are distinct from the verification
# exceptions raised internally by `shadownet.vc` (which can be caught and
# mapped to one of these by an A2A server adapter).

__all__ = [
    "A2AError",
    "FreshnessStaleError",
    "LevelInsufficientError",
    "PeerOfflineError",
    "PresentationInvalidError",
    "PresentationRequiredError",
    "RateLimitedError",
    "RevokedError",
    "UnknownIntentError",
]


class A2AError(ShadownetError):
    """Base for A2A-profile wire errors (RFC-0006 §Errors)."""

    code: str = ""
    http_status: int = 500

    def to_response(self) -> tuple[int, dict[str, str]]:
        """Return ``(http_status, body)`` matching RFC-0006's JSON error shape."""
        return self.http_status, {
            "error": self.code,
            "detail": str(self),
            "shadownet:v": "0.1",
        }


class PresentationRequiredError(A2AError):
    """No valid VP cached; client should retry with a VP bound to ``nonce``."""

    code = "presentation_required"
    http_status = 401

    def __init__(self, nonce: str, detail: str | None = None) -> None:
        super().__init__(detail or "presentation_required")
        self.nonce = nonce

    def to_response(self) -> tuple[int, dict[str, str]]:
        status, body = super().to_response()
        body["nonce"] = self.nonce
        return status, body


class PresentationInvalidError(A2AError):
    code = "presentation_invalid"
    http_status = 401


class LevelInsufficientError(A2AError):
    code = "level_insufficient"
    http_status = 403


class RevokedError(A2AError):
    code = "revoked"
    http_status = 403


class FreshnessStaleError(A2AError):
    code = "freshness_stale"
    http_status = 403


class UnknownIntentError(A2AError):
    code = "unknown_intent"
    http_status = 404


class RateLimitedError(A2AError):
    code = "rate_limited"
    http_status = 429


class PeerOfflineError(A2AError):
    code = "peer_offline"
    http_status = 503
