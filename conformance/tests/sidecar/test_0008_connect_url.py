# RFC-0008 §Connect URL scheme

"""shadownet://connect URL parser MUST behave identically across implementations (RFC-0008, draft)."""

from __future__ import annotations

import pytest

from shadownet.connect.url import (
    ConnectURL,
    ConnectURLInvalid,
    format_connect_url,
    parse_connect_url,
)

# RFC-0008 is its own conformance class independent of RFC-0007 (per the
# RFC's § Conformance class). These are pure parser tests against the
# python-sdk reference implementation — they do NOT require a sidecar
# target, so they intentionally do NOT carry the class_ marker. The
# draft marker keeps them off the default CI lane until the RFC
# graduates.
pytestmark = [pytest.mark.draft]


# RFC-0008 grammar: handoff = [A-Za-z0-9._~-]{16,128}.
VALID_HANDOFF = "8K3J9-W2L1Q-Y5R7T"


@pytest.mark.rfc("0008", section="connect-url", requirement="inline_round_trip")
def test_inline_round_trip():
    """An inline (?token=) URL MUST round-trip through format / parse."""
    url = format_connect_url(base_url="https://app.example", token="tok-abc")
    parsed = parse_connect_url(url)
    assert parsed == ConnectURL(base_url="https://app.example", token="tok-abc")
    assert parsed.is_inline


@pytest.mark.rfc("0008", section="connect-url", requirement="handoff_round_trip")
def test_handoff_round_trip():
    """A handoff (?handoff=) URL MUST round-trip through format / parse."""
    url = format_connect_url(base_url="https://app.example", handoff=VALID_HANDOFF)
    parsed = parse_connect_url(url)
    assert parsed == ConnectURL(base_url="https://app.example", handoff=VALID_HANDOFF)
    assert parsed.is_handoff


@pytest.mark.rfc("0008", section="connect-url", requirement="reject_wrong_scheme")
def test_rejects_wrong_scheme():
    """Scheme MUST be exactly 'shadownet'."""
    with pytest.raises(ConnectURLInvalid):
        parse_connect_url("https://connect?base=https://x&token=t")


@pytest.mark.rfc("0008", section="connect-url", requirement="reject_both_token_and_handoff")
def test_rejects_both_token_and_handoff():
    """Exactly one of token or handoff MUST be set."""
    with pytest.raises(ConnectURLInvalid):
        parse_connect_url(
            f"shadownet://connect?base=https://x&token=t&handoff={VALID_HANDOFF}"
        )


@pytest.mark.rfc("0008", section="connect-url", requirement="reject_neither")
def test_rejects_neither_token_nor_handoff():
    """Exactly one of token or handoff MUST be set."""
    with pytest.raises(ConnectURLInvalid):
        parse_connect_url("shadownet://connect?base=https://x")


@pytest.mark.rfc("0008", section="connect-url", requirement="reject_non_http_base")
def test_rejects_non_http_base():
    """base parameter MUST be an http(s) URL."""
    with pytest.raises(ConnectURLInvalid):
        parse_connect_url("shadownet://connect?base=ftp://x&token=t")


@pytest.mark.rfc("0008", section="connect-url", requirement="reject_fragment")
def test_rejects_fragment():
    """RFC-0008: fragment MUST NOT be present."""
    with pytest.raises(ConnectURLInvalid):
        parse_connect_url("shadownet://connect?base=https://x&token=t#frag")


@pytest.mark.rfc("0008", section="connect-url", requirement="reject_short_handoff")
def test_rejects_short_handoff():
    """RFC-0008 grammar: handoff MUST match [A-Za-z0-9._~-]{16,128}."""
    with pytest.raises(ConnectURLInvalid):
        parse_connect_url("shadownet://connect?base=https://x&handoff=tooshort")


@pytest.mark.rfc("0008", section="connect-url", requirement="reject_http_non_loopback")
def test_rejects_http_for_non_loopback():
    """RFC-0008: http:// allowed only for loopback hosts."""
    with pytest.raises(ConnectURLInvalid):
        parse_connect_url("shadownet://connect?base=http://example.com&token=t")


@pytest.mark.rfc("0008", section="connect-url", requirement="accept_http_loopback")
@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_accepts_http_for_loopback(host: str):
    """RFC-0008: localhost/127.0.0.1/::1 are the only valid http:// hosts."""
    parsed = parse_connect_url(f"shadownet://connect?base=http://{host}:8080&token=t")
    assert parsed.base_url == f"http://{host}:8080"
