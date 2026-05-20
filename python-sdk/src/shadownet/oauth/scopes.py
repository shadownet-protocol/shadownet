"""RFC-0009 § Scopes — the normative v0.1 scope set.

The Sidecar enforces these per-tool. The mapping :data:`TOOL_SCOPE_REQUIREMENTS`
mirrors RFC-0009 § Scopes line for line; if the RFC changes the
mapping, this table changes and conformance tests catch any drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "DEFAULT_SCOPES",
    "SCOPE_INBOX_WAIT",
    "SCOPE_OFFLINE_ACCESS",
    "SCOPE_TOOLS_READ",
    "SCOPE_TOOLS_WRITE",
    "TOOL_SCOPE_REQUIREMENTS",
    "parse_scope_string",
    "required_scopes_for_tool",
    "scope_string",
]

SCOPE_TOOLS_READ: Final[str] = "mcp:tools.read"
SCOPE_TOOLS_WRITE: Final[str] = "mcp:tools.write"
SCOPE_INBOX_WAIT: Final[str] = "mcp:inbox.wait"
SCOPE_OFFLINE_ACCESS: Final[str] = "offline_access"

DEFAULT_SCOPES: Final[tuple[str, ...]] = (
    SCOPE_TOOLS_READ,
    SCOPE_TOOLS_WRITE,
    SCOPE_INBOX_WAIT,
    SCOPE_OFFLINE_ACCESS,
)

# RFC-0009 § Scopes — per-tool scope requirements. A tool not in this map
# is treated as requiring no scope (i.e. publicly callable by any valid
# token). The Sidecar's tool registration code calls
# :func:`required_scopes_for_tool` on every tool dispatch.
TOOL_SCOPE_REQUIREMENTS: Final[dict[str, frozenset[str]]] = {
    # mcp:tools.read
    "social_contacts": frozenset({SCOPE_TOOLS_READ}),
    "social_contact_detail": frozenset({SCOPE_TOOLS_READ}),
    "social_identity": frozenset({SCOPE_TOOLS_READ}),
    "social_resolve": frozenset({SCOPE_TOOLS_READ}),
    "social_inbox": frozenset({SCOPE_TOOLS_READ}),
    # mcp:tools.write
    "social_send": frozenset({SCOPE_TOOLS_WRITE}),
    "social_respond": frozenset({SCOPE_TOOLS_WRITE}),
    "social_add_contact": frozenset({SCOPE_TOOLS_WRITE}),
    "social_grant": frozenset({SCOPE_TOOLS_WRITE}),
    "social_set_webhook": frozenset({SCOPE_TOOLS_WRITE}),
    "social_present": frozenset({SCOPE_TOOLS_WRITE}),
    # mcp:inbox.wait
    "social_inbox_wait": frozenset({SCOPE_INBOX_WAIT}),
    # mcp:tools.read for audit reads
    "social_audit": frozenset({SCOPE_TOOLS_READ}),
}


def required_scopes_for_tool(tool_name: str) -> frozenset[str]:
    """Return the scope set a token MUST carry to invoke ``tool_name``.

    Unknown tool names return the empty set — RFC-0009 says nothing about
    future tools so the Sidecar's responsibility is to map them
    explicitly. The reference registration layer in
    :mod:`shadownet.mcp.register` calls this for every tool it wires.
    """
    return TOOL_SCOPE_REQUIREMENTS.get(tool_name, frozenset())


def parse_scope_string(value: str | None) -> frozenset[str]:
    """Parse a space-separated OAuth scope string.

    Empty / ``None`` returns the empty set. Whitespace is the only
    delimiter — RFC 6749 § 3.3 forbids `,` and other punctuation.
    """
    if not value:
        return frozenset()
    return frozenset(part for part in value.split() if part)


def scope_string(scopes: Iterable[str]) -> str:
    """Render a scope set back to the wire form, deterministically ordered.

    Sorted ordering keeps the AS metadata document stable across
    restarts (RFC 8414 documents are typically cached by clients) and
    makes test assertions byte-stable.
    """
    return " ".join(sorted(set(scopes)))
