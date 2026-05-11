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
    url = format_connect_url(base_url="https://app.example", handoff="ABC123")
    parsed = parse_connect_url(url)
    assert parsed == ConnectURL(base_url="https://app.example", handoff="ABC123")
    assert parsed.is_handoff is True
    assert parsed.is_inline is False


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
