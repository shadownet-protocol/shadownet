from __future__ import annotations

from shadownet.oauth.scopes import (
    SCOPE_INBOX_WAIT,
    SCOPE_TOOLS_READ,
    SCOPE_TOOLS_WRITE,
    parse_scope_string,
    required_scopes_for_tool,
    scope_string,
)


def test_required_scopes_for_known_tools():
    assert required_scopes_for_tool("social_contacts") == frozenset({SCOPE_TOOLS_READ})
    assert required_scopes_for_tool("social_send") == frozenset({SCOPE_TOOLS_WRITE})
    assert required_scopes_for_tool("social_inbox_wait") == frozenset({SCOPE_INBOX_WAIT})


def test_required_scopes_unknown_tool_returns_empty():
    assert required_scopes_for_tool("future_tool") == frozenset()


def test_parse_and_render_round_trip():
    raw = "mcp:tools.read offline_access mcp:tools.write"
    parsed = parse_scope_string(raw)
    assert parsed == {SCOPE_TOOLS_READ, "offline_access", SCOPE_TOOLS_WRITE}
    # Deterministic ordering for stable AS metadata bytes.
    assert scope_string(parsed) == "mcp:tools.read mcp:tools.write offline_access"


def test_parse_empty_string():
    assert parse_scope_string("") == frozenset()
    assert parse_scope_string(None) == frozenset()
