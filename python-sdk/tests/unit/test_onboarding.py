from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest
import respx

from shadownet.onboarding import (
    ConnectURIError,
    HandoffError,
    HandoffExpiredError,
    HandoffRateLimitedError,
    HandoffUnknownError,
    RefreshError,
    RefreshInvalidError,
    RefreshRateLimitedError,
    parse_connect_uri,
    redeem_handoff,
    refresh_access_token,
)

VALID_MCP = "https://app.sh4dow.org/mcp/alice"
VALID_HANDOFF = "8K3J9-W2L1Q-Y5R7T-V1234"


def _connect(token: str | None = None, handoff: str | None = None, mcp: str = VALID_MCP) -> str:
    parts = [f"mcp={quote(mcp, safe='')}"]
    if token is not None:
        parts.append(f"token={quote(token, safe='')}")
    if handoff is not None:
        parts.append(f"handoff={handoff}")
    return "shadow://connect?" + "&".join(parts)


class TestParseConnectURI:
    def test_inline_token(self) -> None:
        result = parse_connect_uri(_connect(token="eyJabc"))
        assert result.is_inline is True
        assert result.access_token == "eyJabc"
        assert result.handoff_code is None
        assert result.mcp_endpoint == VALID_MCP

    def test_handoff(self) -> None:
        result = parse_connect_uri(_connect(handoff=VALID_HANDOFF))
        assert result.is_handoff is True
        assert result.handoff_code == VALID_HANDOFF
        assert result.access_token is None

    def test_loopback_endpoint(self) -> None:
        uri = _connect(token="abc", mcp="http://localhost:7777/mcp")
        result = parse_connect_uri(uri)
        assert result.mcp_endpoint == "http://localhost:7777/mcp"

    def test_127_loopback(self) -> None:
        uri = _connect(token="abc", mcp="http://127.0.0.1:7777/mcp")
        assert parse_connect_uri(uri).mcp_endpoint == "http://127.0.0.1:7777/mcp"

    def test_ipv6_loopback(self) -> None:
        uri = _connect(token="abc", mcp="http://[::1]:7777/mcp")
        assert parse_connect_uri(uri).mcp_endpoint == "http://[::1]:7777/mcp"

    def test_trailing_slash_authority(self) -> None:
        # "shadow://connect/?mcp=..." also legal per §3.1.
        uri = "shadow://connect/?mcp=" + quote(VALID_MCP, safe="") + "&token=abc"
        assert parse_connect_uri(uri).access_token == "abc"

    @pytest.mark.parametrize(
        "uri",
        [
            "https://connect?mcp=https%3A%2F%2Fx&token=abc",
            "shadow://other?mcp=https%3A%2F%2Fx&token=abc",
            "shadow://connect/path?mcp=https%3A%2F%2Fx&token=abc",
        ],
    )
    def test_grammar_violations(self, uri: str) -> None:
        with pytest.raises(ConnectURIError):
            parse_connect_uri(uri)

    def test_fragment_rejected(self) -> None:
        with pytest.raises(ConnectURIError, match="fragment"):
            parse_connect_uri(_connect(token="abc") + "#extra")

    def test_both_token_and_handoff_rejected(self) -> None:
        with pytest.raises(ConnectURIError, match="exactly one"):
            parse_connect_uri(_connect(token="abc", handoff=VALID_HANDOFF))

    def test_neither_token_nor_handoff_rejected(self) -> None:
        with pytest.raises(ConnectURIError, match="exactly one"):
            parse_connect_uri("shadow://connect?mcp=" + quote(VALID_MCP, safe=""))

    def test_duplicate_mcp_rejected(self) -> None:
        uri = (
            "shadow://connect?mcp="
            + quote(VALID_MCP, safe="")
            + "&mcp=https%3A%2F%2Fother&token=abc"
        )
        with pytest.raises(ConnectURIError, match="duplicate"):
            parse_connect_uri(uri)

    def test_missing_mcp_rejected(self) -> None:
        with pytest.raises(ConnectURIError, match="'mcp'"):
            parse_connect_uri("shadow://connect?token=abc")

    def test_plaintext_mcp_rejected(self) -> None:
        with pytest.raises(ConnectURIError, match="https"):
            parse_connect_uri(_connect(token="abc", mcp="http://evil.example/mcp"))

    def test_invalid_handoff_grammar(self) -> None:
        with pytest.raises(ConnectURIError, match="handoff"):
            parse_connect_uri(_connect(handoff="short"))


class TestRedeemHandoff:
    @respx.mock
    def test_success_full_body(self) -> None:
        respx.post(
            "https://app.sh4dow.org/.well-known/shadownet/onboard/handoff/" + VALID_HANDOFF
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "accessToken": "eyJabc",
                    "expiresAt": "2026-05-31T10:00:00Z",
                    "refreshToken": "rfRoT",
                },
            )
        )
        result = redeem_handoff("https://app.sh4dow.org", VALID_HANDOFF)
        assert result.access_token == "eyJabc"
        assert result.expires_at == "2026-05-31T10:00:00Z"
        assert result.refresh_token == "rfRoT"

    @respx.mock
    def test_success_minimal_body(self) -> None:
        respx.post(
            "https://app.sh4dow.org/.well-known/shadownet/onboard/handoff/" + VALID_HANDOFF
        ).mock(return_value=httpx.Response(200, json={"accessToken": "eyJabc"}))
        result = redeem_handoff("https://app.sh4dow.org", VALID_HANDOFF)
        assert result.access_token == "eyJabc"
        assert result.expires_at is None
        assert result.refresh_token is None

    @respx.mock
    def test_404(self) -> None:
        respx.post(
            "https://app.sh4dow.org/.well-known/shadownet/onboard/handoff/" + VALID_HANDOFF
        ).mock(return_value=httpx.Response(404, json={"error": "handoff_unknown"}))
        with pytest.raises(HandoffUnknownError):
            redeem_handoff("https://app.sh4dow.org", VALID_HANDOFF)

    @respx.mock
    def test_410(self) -> None:
        respx.post(
            "https://app.sh4dow.org/.well-known/shadownet/onboard/handoff/" + VALID_HANDOFF
        ).mock(return_value=httpx.Response(410, json={"error": "handoff_expired"}))
        with pytest.raises(HandoffExpiredError):
            redeem_handoff("https://app.sh4dow.org", VALID_HANDOFF)

    @respx.mock
    def test_429(self) -> None:
        respx.post(
            "https://app.sh4dow.org/.well-known/shadownet/onboard/handoff/" + VALID_HANDOFF
        ).mock(return_value=httpx.Response(429))
        with pytest.raises(HandoffRateLimitedError):
            redeem_handoff("https://app.sh4dow.org", VALID_HANDOFF)

    def test_invalid_code_short_circuits(self) -> None:
        with pytest.raises(HandoffError, match="grammar"):
            redeem_handoff("https://app.sh4dow.org", "short")

    @respx.mock
    def test_malformed_success_body(self) -> None:
        respx.post(
            "https://app.sh4dow.org/.well-known/shadownet/onboard/handoff/" + VALID_HANDOFF
        ).mock(return_value=httpx.Response(200, json={"missing": "fields"}))
        with pytest.raises(HandoffError, match="malformed"):
            redeem_handoff("https://app.sh4dow.org", VALID_HANDOFF)


class TestRefreshAccessToken:
    @respx.mock
    def test_success_rotates(self) -> None:
        route = respx.post("https://app.sh4dow.org/.well-known/shadownet/onboard/refresh").mock(
            return_value=httpx.Response(
                200,
                json={
                    "accessToken": "eyJ-new",
                    "expiresAt": "2026-06-01T10:00:00Z",
                    "refreshToken": "rfRoT-new",
                },
            )
        )
        result = refresh_access_token("https://app.sh4dow.org", "rfRoT-old")
        assert result.access_token == "eyJ-new"
        assert result.refresh_token == "rfRoT-new"
        request = route.calls[0].request
        assert request.headers["authorization"] == "Bearer rfRoT-old"

    @respx.mock
    def test_401(self) -> None:
        respx.post("https://app.sh4dow.org/.well-known/shadownet/onboard/refresh").mock(
            return_value=httpx.Response(401, json={"error": "refresh_invalid"})
        )
        with pytest.raises(RefreshInvalidError):
            refresh_access_token("https://app.sh4dow.org", "rfRoT")

    @respx.mock
    def test_429(self) -> None:
        respx.post("https://app.sh4dow.org/.well-known/shadownet/onboard/refresh").mock(
            return_value=httpx.Response(429)
        )
        with pytest.raises(RefreshRateLimitedError):
            refresh_access_token("https://app.sh4dow.org", "rfRoT")

    @respx.mock
    def test_unexpected_status(self) -> None:
        respx.post("https://app.sh4dow.org/.well-known/shadownet/onboard/refresh").mock(
            return_value=httpx.Response(500)
        )
        with pytest.raises(RefreshError, match="HTTP 500"):
            refresh_access_token("https://app.sh4dow.org", "rfRoT")
