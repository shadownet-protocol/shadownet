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

    # RFC-0007 amendment D — long-poll inbound. Sidecars implementing the
    # amendment expose this; ``register_shadownet_tools`` gates registration
    # on ``include_optional={"inbox_wait"}``.
    async def social_inbox_wait(self, input: InboxWaitInput) -> InboxWaitOutput: ...

    # RFC-0007 §social_quarantine_list / §social_quarantine_review /
    # §social_set_contact_profile — landed with the enterprise +
    # cost-containment amendment set. social_quarantine_list is a read tool
    # (mcp:tools.read); the others are write tools (mcp:tools.write).
    async def social_quarantine_list(self, input: QuarantineListInput) -> QuarantineListOutput: ...

    async def social_quarantine_review(
        self, input: QuarantineReviewInput
    ) -> QuarantineReviewOutput: ...

    async def social_set_contact_profile(
        self, input: SetContactProfileInput
    ) -> SetContactProfileOutput: ...

    # Optional surfaces — implementations MAY provide these if they declare
    # the corresponding key in `include_optional` on registration.
    # `present` — social_present
    # `audit`   — social_audit
    async def social_present(self, input: PresentInput) -> PresentOutput: ...

    async def social_audit(self) -> AuditOutput: ...
