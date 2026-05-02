from __future__ import annotations

from typing import TYPE_CHECKING

from shadownet.logging import get_logger
from shadownet.mcp.tools import (
    AddContactInput,
    AddContactOutput,
    AuditOutput,
    ContactDetail,
    ContactsInput,
    ContactsOutput,
    GrantInput,
    GrantOutput,
    IdentityOutput,
    InboxInput,
    InboxOutput,
    PresentInput,
    PresentOutput,
    ResolveInput,
    ResolveOutput,
    RespondInput,
    RespondOutput,
    SendInput,
    SendOutput,
    SetWebhookInput,
    SetWebhookOutput,
)
from shadownet.webhook.verify import ensure_url_allowed

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mcp.server.fastmcp import FastMCP

    from shadownet.mcp.protocol import Sidecar

# RFC-0007 wiring layer. Every required tool name is normative; argument shapes
# are normative for required arguments. Optional tools (`social_present`,
# `social_audit`) are off by default.

OPTIONAL_TOOLS = frozenset({"present", "audit"})

_log = get_logger(__name__)

__all__ = ["OPTIONAL_TOOLS", "register_shadownet_tools"]


def register_shadownet_tools(
    server: FastMCP,
    sidecar: Sidecar,
    *,
    include_optional: Iterable[str] = (),
) -> None:
    """Register all RFC-0007 tools on ``server``, dispatching to ``sidecar``.

    ``include_optional`` is a subset of :data:`OPTIONAL_TOOLS`. Unknown values
    are rejected with :class:`ValueError`.
    """
    optional = frozenset(include_optional)
    unknown = optional - OPTIONAL_TOOLS
    if unknown:
        raise ValueError(f"unknown optional tool(s): {sorted(unknown)}")

    @server.tool(name="social_contacts", description="List known contacts (RFC-0007).")
    async def _contacts(query: str | None = None) -> ContactsOutput:
        return await sidecar.social_contacts(ContactsInput(query=query))

    @server.tool(name="social_contact_detail", description="Full record for one contact.")
    async def _contact_detail(id: str) -> ContactDetail:
        return await sidecar.social_contact_detail(id)

    @server.tool(
        name="social_resolve", description="Resolve a Shadowname via SNS without persisting it."
    )
    async def _resolve(shadowname: str) -> ResolveOutput:
        return await sidecar.social_resolve(ResolveInput(shadowname=shadowname))

    @server.tool(
        name="social_add_contact", description="Add a resolved entity to the contact graph."
    )
    async def _add_contact(
        shadowname: str,
        displayName: str | None = None,
        grants: list[str] | None = None,
    ) -> AddContactOutput:
        return await sidecar.social_add_contact(
            AddContactInput(
                shadowname=shadowname,
                displayName=displayName,
                grants=grants or [],
            )
        )

    @server.tool(name="social_send", description="Send a Shadownet-enveloped message over A2A.")
    async def _send(
        contactId: str,
        interaction: str,
        payload: dict,
        intentId: str | None = None,
    ) -> SendOutput:
        return await sidecar.social_send(
            SendInput(
                contactId=contactId,
                interaction=interaction,
                intentId=intentId,
                payload=payload,
            )
        )

    @server.tool(name="social_inbox", description="List pending inbound messages or task updates.")
    async def _inbox(
        since: int | None = None,
        interaction: str | None = None,
        contactId: str | None = None,
        limit: int | None = None,
    ) -> InboxOutput:
        return await sidecar.social_inbox(
            InboxInput(
                since=since,
                interaction=interaction,
                contactId=contactId,
                limit=limit,
            )
        )

    @server.tool(name="social_respond", description="Respond within an existing intent.")
    async def _respond(intentId: str, payload: dict) -> RespondOutput:
        return await sidecar.social_respond(RespondInput(intentId=intentId, payload=payload))

    @server.tool(name="social_grant", description="Grant or revoke a per-contact permission.")
    async def _grant(contactId: str, grant: str, allowed: bool) -> GrantOutput:
        return await sidecar.social_grant(
            GrantInput(contactId=contactId, grant=grant, allowed=allowed)
        )

    @server.tool(name="social_identity", description="Return the Sidecar's own identity.")
    async def _identity() -> IdentityOutput:
        return await sidecar.social_identity()

    @server.tool(
        name="social_set_webhook",
        description="Register or update the host-agent webhook (url='' to unregister).",
    )
    async def _set_webhook(
        url: str,
        secret: str,
        events: list[str] | None = None,
    ) -> SetWebhookOutput:
        if url != "":
            ensure_url_allowed(url)
        return await sidecar.social_set_webhook(
            SetWebhookInput(url=url, secret=secret, events=events)
        )

    if "present" in optional:

        @server.tool(
            name="social_present",
            description="Explicitly trigger a credential presentation to a peer.",
        )
        async def _present(contactId: str, nonce: str | None = None) -> PresentOutput:
            return await sidecar.social_present(PresentInput(contactId=contactId, nonce=nonce))

    if "audit" in optional:

        @server.tool(
            name="social_audit",
            description="Return a structured audit log of host-agent actions.",
        )
        async def _audit() -> AuditOutput:
            return await sidecar.social_audit()

    _log.debug("registered RFC-0007 tools (optional=%s)", sorted(optional) if optional else "[]")
