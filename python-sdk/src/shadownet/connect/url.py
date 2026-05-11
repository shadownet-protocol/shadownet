from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from shadownet.connect.errors import ConnectURLInvalid

# RFC-0007 amendment B — shadownet://connect URL scheme.
#
# Two forms, both supported:
#   shadownet://connect?base=<https-url>&token=<jwt>       (inline)
#   shadownet://connect?base=<https-url>&handoff=<code>    (handoff)
#
# Inline is fine for direct user pastes; handoff lets the producer mint a
# short-code that the plugin trades for the real token over a one-shot
# challenge call to <base>/v1/account/connect/handoff/<code>.
#
# Exactly one of token / handoff is present.

CONNECT_SCHEME = "shadownet"
CONNECT_HOST = "connect"

__all__ = [
    "CONNECT_HOST",
    "CONNECT_SCHEME",
    "ConnectURL",
    "format_connect_url",
    "parse_connect_url",
]


@dataclass(frozen=True, slots=True)
class ConnectURL:
    """Parsed shadownet://connect URL.

    Exactly one of ``token`` or ``handoff`` is set; the other is ``None``.
    Use :attr:`is_handoff` / :attr:`is_inline` to dispatch.
    """

    base_url: str
    token: str | None = None
    handoff: str | None = None

    @property
    def is_handoff(self) -> bool:
        return self.handoff is not None

    @property
    def is_inline(self) -> bool:
        return self.token is not None


def parse_connect_url(url: str) -> ConnectURL:
    """Parse a ``shadownet://connect?...`` URL into a :class:`ConnectURL`.

    Raises :class:`ConnectURLInvalid` on any deviation from RFC-0007
    amendment B (wrong scheme, wrong host, missing/duplicate ``base``,
    both/neither of ``token`` and ``handoff``, malformed ``base``).
    """
    parsed = urlparse(url)
    if parsed.scheme != CONNECT_SCHEME:
        raise ConnectURLInvalid(
            f"scheme must be {CONNECT_SCHEME!r}, got {parsed.scheme!r}"
        )
    if parsed.netloc != CONNECT_HOST:
        raise ConnectURLInvalid(f"host must be {CONNECT_HOST!r}, got {parsed.netloc!r}")
    # Path is permitted but must be empty or "/" — anything else is ambiguous
    # versus future amendments that might add path segments.
    if parsed.path not in ("", "/"):
        raise ConnectURLInvalid(f"unexpected path component: {parsed.path!r}")

    query = parse_qs(parsed.query, keep_blank_values=False)
    base_values = query.get("base") or []
    if len(base_values) != 1:
        raise ConnectURLInvalid("exactly one 'base' parameter required")
    base = base_values[0]

    base_parsed = urlparse(base)
    if base_parsed.scheme not in ("http", "https"):
        raise ConnectURLInvalid(
            f"base must use http(s) scheme, got {base_parsed.scheme!r}"
        )
    if not base_parsed.netloc:
        raise ConnectURLInvalid("base URL missing host")

    token_values = query.get("token") or []
    handoff_values = query.get("handoff") or []
    if len(token_values) > 1 or len(handoff_values) > 1:
        raise ConnectURLInvalid(
            "'token' and 'handoff' allow at most one value each"
        )
    if bool(token_values) == bool(handoff_values):
        raise ConnectURLInvalid("exactly one of 'token' or 'handoff' must be set")

    return ConnectURL(
        base_url=base.rstrip("/"),
        token=token_values[0] if token_values else None,
        handoff=handoff_values[0] if handoff_values else None,
    )


def format_connect_url(
    *,
    base_url: str,
    token: str | None = None,
    handoff: str | None = None,
) -> str:
    """Build a ``shadownet://connect?...`` URL.

    Symmetric with :func:`parse_connect_url`: ``parse_connect_url(format_connect_url(**kw)) == ConnectURL(**kw)``
    holds for every valid input. Exactly one of ``token`` / ``handoff`` must
    be set.
    """
    if bool(token) == bool(handoff):
        raise ConnectURLInvalid("exactly one of 'token' or 'handoff' must be set")
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConnectURLInvalid(f"invalid base URL: {base_url!r}")

    params: dict[str, str] = {"base": base_url.rstrip("/")}
    if token is not None:
        params["token"] = token
    else:
        # Either token or handoff is non-None per the check above; if token is
        # None then handoff must be non-None.
        assert handoff is not None
        params["handoff"] = handoff

    return urlunparse(
        (CONNECT_SCHEME, CONNECT_HOST, "", "", urlencode(params), "")
    )
