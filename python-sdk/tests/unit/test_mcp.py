from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from shadownet.mcp.protocol import Sidecar
from shadownet.mcp.register import register_shadownet_tools
from shadownet.mcp.tools import (
    AddContactInput,
    AddContactOutput,
    AuditEntry,
    AuditOutput,
    Contact,
    ContactDetail,
    ContactsInput,
    ContactsOutput,
    GrantInput,
    GrantOutput,
    IdentityOutput,
    InboxInput,
    InboxOutput,
    InboxWaitEvent,
    InboxWaitInput,
    InboxWaitOutput,
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


class FakeSidecar:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def social_contacts(self, input: ContactsInput) -> ContactsOutput:
        self.calls.append(("social_contacts", input))
        return ContactsOutput(
            contacts=[
                Contact(id="ctc_alice", shadowname="alice@x.example", did="did:key:z6MkAlice")
            ]
        )

    async def social_contact_detail(self, contact_id: str) -> ContactDetail:
        self.calls.append(("social_contact_detail", contact_id))
        return ContactDetail(
            id=contact_id,
            shadowname="alice@x.example",
            did="did:key:z6MkAlice",
            endpoint="https://x.example/a2a",
            publicKey={"kty": "OKP", "crv": "Ed25519", "x": "abc"},
            credentials=[],
            grants=["messaging"],
        )

    async def social_resolve(self, input: ResolveInput) -> ResolveOutput:
        self.calls.append(("social_resolve", input))
        return ResolveOutput(
            did="did:key:z6MkAlice",
            endpoint="https://x.example/a2a",
            publicKey={"kty": "OKP", "crv": "Ed25519", "x": "abc"},
            subjectType="person",
            ttl=300,
        )

    async def social_add_contact(self, input: AddContactInput) -> AddContactOutput:
        self.calls.append(("social_add_contact", input))
        return AddContactOutput(id="ctc_new", shadowname=input.shadowname, did="did:key:z6MkAlice")

    async def social_send(self, input: SendInput) -> SendOutput:
        self.calls.append(("social_send", input))
        return SendOutput(intentId=input.intent_id or "urn:uuid:int-001", taskId="task-001")

    async def social_inbox(self, input: InboxInput) -> InboxOutput:
        self.calls.append(("social_inbox", input))
        return InboxOutput(items=[])

    async def social_respond(self, input: RespondInput) -> RespondOutput:
        self.calls.append(("social_respond", input))
        return RespondOutput(taskId="task-002")

    async def social_grant(self, input: GrantInput) -> GrantOutput:
        self.calls.append(("social_grant", input))
        return GrantOutput()

    async def social_identity(self) -> IdentityOutput:
        self.calls.append(("social_identity", None))
        return IdentityOutput(
            did="did:key:z6MkSelf",
            shadowname="self@x.example",
            publicKey={"kty": "OKP", "crv": "Ed25519", "x": "abc"},
            credentials=[],
        )

    async def social_set_webhook(self, input: SetWebhookInput) -> SetWebhookOutput:
        self.calls.append(("social_set_webhook", input))
        return SetWebhookOutput()

    async def social_inbox_wait(self, input: InboxWaitInput) -> InboxWaitOutput:
        self.calls.append(("social_inbox_wait", input))
        return InboxWaitOutput(
            events=[
                InboxWaitEvent(
                    eventId="evt-1",
                    event="inbox.message",
                    occurredAt=1,
                    data={"from": "alice@x.example", "body": "hi"},
                )
            ],
            nextEventId="evt-1",
        )

    async def social_present(self, input: PresentInput) -> PresentOutput:
        self.calls.append(("social_present", input))
        return PresentOutput(presentationJwt="vp-jwt")

    async def social_audit(self) -> AuditOutput:
        self.calls.append(("social_audit", None))
        return AuditOutput(
            entries=[AuditEntry(timestamp=0, tool="social_send", input={}, success=True)]
        )


def test_fakesidecar_implements_protocol() -> None:
    assert isinstance(FakeSidecar(), Sidecar)


async def test_register_required_tools() -> None:
    server = FastMCP(name="test")
    sidecar = FakeSidecar()
    register_shadownet_tools(server, sidecar)
    tools = await server.list_tools()
    names = {t.name for t in tools}
    required = {
        "social_contacts",
        "social_contact_detail",
        "social_resolve",
        "social_add_contact",
        "social_send",
        "social_inbox",
        "social_respond",
        "social_grant",
        "social_identity",
        "social_set_webhook",
    }
    assert required <= names
    # Optional tools NOT registered by default.
    assert "social_present" not in names
    assert "social_audit" not in names


async def test_register_with_optional() -> None:
    server = FastMCP(name="test")
    sidecar = FakeSidecar()
    register_shadownet_tools(server, sidecar, include_optional={"present", "audit"})
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert "social_present" in names
    assert "social_audit" in names


def test_register_rejects_unknown_optional() -> None:
    server = FastMCP(name="test")
    with pytest.raises(ValueError):
        register_shadownet_tools(server, FakeSidecar(), include_optional={"bogus"})


async def test_call_social_send_dispatches_to_sidecar() -> None:
    server = FastMCP(name="test")
    sidecar = FakeSidecar()
    register_shadownet_tools(server, sidecar)
    result = await server.call_tool(
        "social_send",
        {
            "contactId": "ctc_alice",
            "interaction": "urn:shadownet:int:scheduling.v0-draft",
            "payload": {"kind": "propose"},
        },
    )
    assert ("social_send", sidecar.calls[0][1]) in [(c[0], c[1]) for c in sidecar.calls]
    # Result is the structured output (dict) plus the textual JSON content.
    body = result[0][0].text  # first content block
    parsed = json.loads(body)
    assert parsed["taskId"] == "task-001"


async def test_set_webhook_rejects_disallowed_url() -> None:
    server = FastMCP(name="test")
    sidecar = FakeSidecar()
    register_shadownet_tools(server, sidecar)
    # Plain http to a non-localhost host -> WebhookURLInvalid raised inside the tool.
    with pytest.raises(Exception):  # noqa: B017 -- mcp wraps + re-raises
        await server.call_tool(
            "social_set_webhook",
            {
                "url": "http://attacker.example/x",
                "secret": "x" * 32,
            },
        )


async def test_set_webhook_unregister_with_empty_url() -> None:
    server = FastMCP(name="test")
    sidecar = FakeSidecar()
    register_shadownet_tools(server, sidecar)
    await server.call_tool("social_set_webhook", {"url": "", "secret": "x" * 32})
    assert any(name == "social_set_webhook" for name, _ in sidecar.calls)


# --- social_inbox_wait (RFC-0007 amendment D) -------------------------------


async def test_inbox_wait_not_registered_without_optional() -> None:
    """The long-poll tool is opt-in via include_optional={'inbox_wait'}."""
    server = FastMCP(name="test")
    register_shadownet_tools(server, FakeSidecar())
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert "social_inbox_wait" not in names


async def test_inbox_wait_registered_with_optional() -> None:
    server = FastMCP(name="test")
    register_shadownet_tools(server, FakeSidecar(), include_optional={"inbox_wait"})
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert "social_inbox_wait" in names


async def test_inbox_wait_dispatches_to_sidecar() -> None:
    server = FastMCP(name="test")
    sidecar = FakeSidecar()
    register_shadownet_tools(server, sidecar, include_optional={"inbox_wait"})
    result = await server.call_tool(
        "social_inbox_wait", {"timeout_seconds": 5, "lastEventId": "evt-0"}
    )
    body = result[0][0].text
    parsed = json.loads(body)
    assert parsed["events"][0]["eventId"] == "evt-1"
    assert parsed["nextEventId"] == "evt-1"
    # The sidecar received the InboxWaitInput with the cursor we passed.
    inbox_wait_calls = [c for c in sidecar.calls if c[0] == "social_inbox_wait"]
    assert len(inbox_wait_calls) == 1
    received_input: InboxWaitInput = inbox_wait_calls[0][1]  # type: ignore[assignment]
    assert received_input.last_event_id == "evt-0"
    assert received_input.timeout_seconds == 5


async def test_inbox_wait_clamps_oversized_timeout() -> None:
    """Per RFC-0007 amendment D, server clamps timeout_seconds to ≤90."""
    server = FastMCP(name="test")
    sidecar = FakeSidecar()
    register_shadownet_tools(server, sidecar, include_optional={"inbox_wait"})
    await server.call_tool("social_inbox_wait", {"timeout_seconds": 600})
    received: InboxWaitInput = sidecar.calls[-1][1]  # type: ignore[assignment]
    assert received.timeout_seconds == 90


async def test_inbox_wait_clamps_negative_timeout() -> None:
    """Negative client values are clamped to 0 (server returns immediately)."""
    server = FastMCP(name="test")
    sidecar = FakeSidecar()
    register_shadownet_tools(server, sidecar, include_optional={"inbox_wait"})
    await server.call_tool("social_inbox_wait", {"timeout_seconds": -5})
    received: InboxWaitInput = sidecar.calls[-1][1]  # type: ignore[assignment]
    assert received.timeout_seconds == 0
