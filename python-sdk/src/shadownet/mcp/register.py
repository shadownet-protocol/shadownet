from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shadownet.logging import get_logger
from shadownet.mcp.tools import (
    INBOX_WAIT_MAX_TIMEOUT_SECONDS,
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
    InboxWaitInput,
    InboxWaitOutput,
    PresentInput,
    PresentOutput,
    QuarantineListInput,
    QuarantineListOutput,
    QuarantineReviewInput,
    QuarantineReviewOutput,
    ResolveInput,
    ResolveOutput,
    RespondInput,
    RespondOutput,
    SendInput,
    SendOutput,
    SetContactProfileInput,
    SetContactProfileOutput,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mcp.server.fastmcp import FastMCP

    from shadownet.mcp.protocol import Sidecar

# RFC-0007 wiring layer. Every required tool name is normative; argument shapes
# are normative for required arguments. Optional tools (`social_present`,
# `social_audit`, `social_inbox_wait`) are off by default.

OPTIONAL_TOOLS = frozenset({"present", "audit", "inbox_wait"})

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
        profile: dict[str, Any] | None = None,
    ) -> AddContactOutput:
        return await sidecar.social_add_contact(
            AddContactInput.model_validate(
                {
                    "shadowname": shadowname,
                    "displayName": displayName,
                    "grants": grants or [],
                    "profile": profile,
                }
            )
        )

    @server.tool(
        name="social_send",
        description=(
            "Send a Shadownet-enveloped message to a contact over A2A. "
            "REQUIRED args: `contactId` (string, from social_contacts / "
            "social_resolve) and `payload` (object). For free-form text "
            'messages, set `payload={"text": "your message here"}` — do NOT '
            "pass the text as a top-level `message` or `body` kwarg, those "
            "are rejected. Optional: `interaction` (URN for typed "
            "Interaction Profiles per RFC-0006) and `intentId` (continue "
            "an existing intent). Example call: social_send(contactId="
            '"alice@example.org", payload={"text": "Hello Alice"}).'
        ),
    )
    async def _send(
        contactId: str,
        payload: dict[str, Any],
        interaction: str | None = None,
        intentId: str | None = None,
    ) -> SendOutput:
        # RFC-0006 / RFC-0007: interaction is optional; omit for the
        # default free-form envelope (payload = {"text": ..., "hints"?: ...}).
        return await sidecar.social_send(
            SendInput.model_validate(
                {
                    "contactId": contactId,
                    "interaction": interaction,
                    "intentId": intentId,
                    "payload": payload,
                }
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
            InboxInput.model_validate(
                {
                    "since": since,
                    "interaction": interaction,
                    "contactId": contactId,
                    "limit": limit,
                }
            )
        )

    @server.tool(
        name="social_respond",
        description=(
            "Respond within an existing intent. REQUIRED args: `intentId` "
            "(string, from the inbound event you're responding to) and "
            "`payload` (object). For a coordination response, use "
            '`payload={"type": "response", "status": "agreed", '
            '"plan": {"activity": "...", "date": "...", "time": "..."}}`. '
            "Do NOT pass `message` or `body` as top-level kwargs — they "
            "will be rejected."
        ),
    )
    async def _respond(intentId: str, payload: dict[str, Any]) -> RespondOutput:
        return await sidecar.social_respond(
            RespondInput.model_validate({"intentId": intentId, "payload": payload})
        )

    @server.tool(name="social_grant", description="Grant or revoke a per-contact permission.")
    async def _grant(contactId: str, grant: str, allowed: bool) -> GrantOutput:
        return await sidecar.social_grant(
            GrantInput.model_validate({"contactId": contactId, "grant": grant, "allowed": allowed})
        )

    @server.tool(name="social_identity", description="Return the Sidecar's own identity.")
    async def _identity() -> IdentityOutput:
        return await sidecar.social_identity()

    @server.tool(
        name="social_quarantine_list",
        description=(
            "List pending quarantined inbound — items the receiver has held "
            "because the sender is not a known contact (RFC-0006 §Routing "
            "and quarantine). Summaries are sender-supplied; this tool MUST "
            "NOT trigger any LLM processing of the held items."
        ),
    )
    async def _quarantine_list(
        since: int | None = None, limit: int | None = None
    ) -> QuarantineListOutput:
        return await sidecar.social_quarantine_list(
            QuarantineListInput.model_validate({"since": since, "limit": limit})
        )

    @server.tool(
        name="social_quarantine_review",
        description=(
            "Review a quarantined item: accept (add to contacts), reject, or "
            "reject_and_block. On accept, `grants` default to ['messaging'] "
            "and `profile` is a local-only ContactProfile per RFC-0007 "
            "§Contact profile. Requires explicit user direction; the host "
            "agent MUST NOT auto-process quarantine items."
        ),
    )
    async def _quarantine_review(
        quarantineId: str,
        decision: str,
        displayName: str | None = None,
        grants: list[str] | None = None,
        profile: dict[str, Any] | None = None,
    ) -> QuarantineReviewOutput:
        return await sidecar.social_quarantine_review(
            QuarantineReviewInput.model_validate(
                {
                    "quarantineId": quarantineId,
                    "decision": decision,
                    "displayName": displayName,
                    "grants": grants or [],
                    "profile": profile,
                }
            )
        )

    @server.tool(
        name="social_set_contact_profile",
        description=(
            "Update the local-only ContactProfile for an existing contact "
            "(notes / priority / collaborate_on / expires_at). The profile "
            "is never transmitted to peers per RFC-0007 §Contact profile."
        ),
    )
    async def _set_contact_profile(
        contactId: str, profile: dict[str, Any]
    ) -> SetContactProfileOutput:
        return await sidecar.social_set_contact_profile(
            SetContactProfileInput.model_validate({"contactId": contactId, "profile": profile})
        )

    if "inbox_wait" in optional:

        @server.tool(
            name="social_inbox_wait",
            description=(
                "Long-poll for inbox events (RFC-0007 amendment D). Holds the call "
                "open until events arrive or the timeout elapses. Background "
                "workers only — do not invoke from an LLM reasoning loop."
            ),
        )
        async def _inbox_wait(
            timeout_seconds: int = 30,
            last_event_id: str | None = None,
        ) -> InboxWaitOutput:
            # Clamp server-side per RFC-0007 amendment D so misbehaving clients
            # can't pin a connection beyond the documented limit.
            clamped = max(0, min(timeout_seconds, INBOX_WAIT_MAX_TIMEOUT_SECONDS))
            return await sidecar.social_inbox_wait(
                InboxWaitInput.model_validate(
                    {"timeout_seconds": clamped, "last_event_id": last_event_id}
                )
            )

    if "present" in optional:

        @server.tool(
            name="social_present",
            description="Explicitly trigger a credential presentation to a peer.",
        )
        async def _present(contactId: str, nonce: str | None = None) -> PresentOutput:
            return await sidecar.social_present(
                PresentInput.model_validate({"contactId": contactId, "nonce": nonce})
            )

    if "audit" in optional:

        @server.tool(
            name="social_audit",
            description="Return a structured audit log of host-agent actions.",
        )
        async def _audit() -> AuditOutput:
            return await sidecar.social_audit()

    _log.debug("registered RFC-0007 tools (optional=%s)", sorted(optional) if optional else "[]")
