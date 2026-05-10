from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
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

# RFC-0007 — the contract a Sidecar implementation fulfills so the registration
# layer in `shadownet.mcp.register` can wire its methods onto a FastMCP server.
# Optional methods (`social_present`, `social_audit`) are opted into via flags
# on `register_shadownet_tools`.

__all__ = ["Sidecar"]


@runtime_checkable
class Sidecar(Protocol):
    async def social_contacts(self, input: ContactsInput) -> ContactsOutput: ...

    async def social_contact_detail(self, contact_id: str) -> ContactDetail: ...

    async def social_resolve(self, input: ResolveInput) -> ResolveOutput: ...

    async def social_add_contact(self, input: AddContactInput) -> AddContactOutput: ...

    async def social_send(self, input: SendInput) -> SendOutput: ...

    async def social_inbox(self, input: InboxInput) -> InboxOutput: ...

    async def social_respond(self, input: RespondInput) -> RespondOutput: ...

    async def social_grant(self, input: GrantInput) -> GrantOutput: ...

    async def social_identity(self) -> IdentityOutput: ...

    async def social_set_webhook(self, input: SetWebhookInput) -> SetWebhookOutput: ...

    # Optional surfaces — implementations MAY provide these if they declare
    # `include_optional={"present", "audit"}` on registration.
    async def social_present(self, input: PresentInput) -> PresentOutput: ...

    async def social_audit(self) -> AuditOutput: ...
