from __future__ import annotations

import pytest

from shadownet.connect.errors import ConnectURLInvalid
from shadownet.connect.url import (
    CONNECT_HOST,
    CONNECT_SCHEME,
    ConnectURL,
    format_connect_url,
    parse_connect_url,
)


def test_inline_round_trip() -> None:
    url = format_connect_url(base_url="https://app.example", token="tok-abc")
    parsed = parse_connect_url(url)
    assert parsed == ConnectURL(base_url="https://app.example", token="tok-abc")
    assert parsed.is_inline is True
    assert parsed.is_handoff is False


def test_handoff_round_trip() -> None:
    # RFC-0008 grammar requires 16-128 chars; the well-known example is
    # ``8K3J9-W2L1Q-Y5R7T`` (17 chars).
    handoff = "8K3J9-W2L1Q-Y5R7T"
    url = format_connect_url(base_url="https://app.example", handoff=handoff)
    parsed = parse_connect_url(url)
    assert parsed == ConnectURL(base_url="https://app.example", handoff=handoff)
    assert parsed.is_handoff is True
    assert parsed.is_inline is False


def test_rejects_short_handoff() -> None:
    """RFC-0008 grammar: handoff MUST match [A-Za-z0-9._~-]{16,128}."""
    with pytest.raises(ConnectURLInvalid, match=r"\[A-Za-z0-9\._~-\]"):
        parse_connect_url("shadownet://connect?base=https://x.example&handoff=tooshort")


def test_rejects_handoff_with_disallowed_chars() -> None:
    """RFC-0008 grammar restricts handoff to URL-safe characters."""
    bad = "A" * 16 + "@bad"  # 20 chars but '@' isn't in the allowed set
    with pytest.raises(ConnectURLInvalid, match=r"\[A-Za-z0-9\._~-\]"):
        parse_connect_url(f"shadownet://connect?base=https://x.example&handoff={bad}")


def test_rejects_fragment() -> None:
    """RFC-0008: fragment MUST NOT be present."""
    with pytest.raises(ConnectURLInvalid, match="fragment is not permitted"):
        parse_connect_url("shadownet://connect?base=https://x.example&token=t#frag")


def test_rejects_http_for_non_loopback() -> None:
    """RFC-0008: http:// allowed only for loopback hosts."""
    with pytest.raises(ConnectURLInvalid, match="loopback"):
        parse_connect_url("shadownet://connect?base=http://example.com&token=t")


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "[::1]"],
)
def test_accepts_http_for_loopback(host: str) -> None:
    """RFC-0007 § URL constraints + RFC-0008: localhost/127.0.0.1/::1 allowed."""
    url = f"shadownet://connect?base=http://{host}:8080&token=t"
    parsed = parse_connect_url(url)
    assert parsed.base_url == f"http://{host}:8080"


def test_strips_trailing_slash_from_base() -> None:
    parsed = parse_connect_url(
        f"{CONNECT_SCHEME}://{CONNECT_HOST}?base=https://app.example/&token=t"
    )
    assert parsed.base_url == "https://app.example"


def test_self_host_http_localhost() -> None:
    url = format_connect_url(base_url="http://localhost:8080", token="t")
    parsed = parse_connect_url(url)
    assert parsed.base_url == "http://localhost:8080"


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("https://connect?base=https://x&token=t", "scheme must be"),
        ("shadownet://other?base=https://x&token=t", "host must be"),
        ("shadownet://connect/extra?base=https://x&token=t", "unexpected path"),
        ("shadownet://connect?token=t", "exactly one 'base'"),
        ("shadownet://connect?base=https://x&base=https://y&token=t", "exactly one 'base'"),
        ("shadownet://connect?base=https://x", "exactly one of 'token' or 'handoff'"),
        ("shadownet://connect?base=https://x&token=t&handoff=h", "exactly one of"),
        ("shadownet://connect?base=https://x&token=a&token=b", "at most one"),
        ("shadownet://connect?base=ftp://x&token=t", "http\\(s\\) scheme"),
        ("shadownet://connect?base=https://&token=t", "missing host"),
    ],
)
def test_parse_rejects(url: str, match: str) -> None:
    with pytest.raises(ConnectURLInvalid, match=match):
        parse_connect_url(url)


def test_format_rejects_both_set() -> None:
    with pytest.raises(ConnectURLInvalid, match="exactly one"):
        format_connect_url(base_url="https://x", token="t", handoff="h")


def test_format_rejects_neither_set() -> None:
    with pytest.raises(ConnectURLInvalid, match="exactly one"):
        format_connect_url(base_url="https://x")


def test_format_rejects_bad_base() -> None:
    with pytest.raises(ConnectURLInvalid, match="invalid base URL"):
        format_connect_url(base_url="not-a-url", token="t")


def test_connect_url_is_frozen() -> None:
    parsed = parse_connect_url("shadownet://connect?base=https://x.example&token=t")
    with pytest.raises(AttributeError):
        parsed.token = "evil"  # type: ignore[misc]


def test_inline_token_with_special_chars_round_trips() -> None:
    """JWT-like tokens contain '.', '-', '_' and possibly '=' padding — must survive."""
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.SflKxw_AdQssw5c"
    url = format_connect_url(base_url="https://app.example", token=token)
    parsed = parse_connect_url(url)
    assert parsed.token == token
