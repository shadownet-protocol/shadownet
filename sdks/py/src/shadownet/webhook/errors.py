from __future__ import annotations

from shadownet.errors import ShadownetError

__all__ = [
    "WebhookError",
    "WebhookReplayWindowError",
    "WebhookSignatureError",
    "WebhookURLInvalid",
]


class WebhookError(ShadownetError):
    """Base for webhook errors."""


class WebhookSignatureError(WebhookError):
    """An incoming webhook's HMAC did not match."""


class WebhookReplayWindowError(WebhookError):
    """An incoming webhook's timestamp is outside the configured replay window."""


class WebhookURLInvalid(WebhookError):
    """The webhook URL is not allowed (must be https or http://localhost)."""
