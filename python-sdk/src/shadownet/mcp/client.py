"""Typed streamable-HTTP MCP client for the RFC 0002 Sidecar surface.

Wraps the upstream ``mcp`` SDK's ``streamablehttp_client`` + ``ClientSession``
with helpers that call each Shadownet tool by its canonical name and parse the
result through the matching pydantic model.

Usage::

    async with ShadownetMCPClient.connect(
        endpoint="https://app.sh4dow.org/mcp/alice",
        access_token="...",
    ) as client:
        identity = await client.identity()
        result = await client.send(SendInput(to="bob@example.org", body=BodySlot(text="hi")))
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from shadownet.errors import ShadownetError
from shadownet.mcp.tools import (
    AddContactInput,
    AddContactOutput,
    ContactDetailInput,
    ContactDetailOutput,
    ContactsInput,
    ContactsOutput,
    GrantInput,
    GrantOutput,
    IdentityOutput,
    InboxInput,
    InboxOutput,
    InboxWaitInput,
    InboxWaitOutput,
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
    from collections.abc import AsyncIterator

    from mcp import ClientSession

__all__ = ["ShadownetMCPClient", "ToolError"]


class ToolError(ShadownetError):
    """A Sidecar MCP tool call failed or returned a malformed result."""


T = TypeVar("T", bound=BaseModel)


class ShadownetMCPClient:
    """Typed wrapper around an MCP ``ClientSession`` for the v0.2 surface."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    @classmethod
    @asynccontextmanager
    async def connect(
        cls,
        *,
        endpoint: str,
        access_token: str,
    ) -> AsyncIterator[ShadownetMCPClient]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"Authorization": f"Bearer {access_token}"}
        async with (
            streamablehttp_client(endpoint, headers=headers) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield cls(session)

    async def identity(self) -> IdentityOutput:
        return await self._call("identity", {}, IdentityOutput)

    async def resolve(self, arg: ResolveInput) -> ResolveOutput:
        return await self._call("resolve", arg, ResolveOutput)

    async def contacts(self, arg: ContactsInput | None = None) -> ContactsOutput:
        return await self._call("contacts", arg or ContactsInput(), ContactsOutput)

    async def contact_detail(self, arg: ContactDetailInput) -> ContactDetailOutput:
        return await self._call("contact_detail", arg, ContactDetailOutput)

    async def add_contact(self, arg: AddContactInput) -> AddContactOutput:
        return await self._call("add_contact", arg, AddContactOutput)

    async def grant(self, arg: GrantInput) -> GrantOutput:
        return await self._call("grant", arg, GrantOutput)

    async def set_contact_profile(self, arg: SetContactProfileInput) -> SetContactProfileOutput:
        return await self._call("set_contact_profile", arg, SetContactProfileOutput)

    async def send(self, arg: SendInput) -> SendOutput:
        return await self._call("send", arg, SendOutput)

    async def respond(self, arg: RespondInput) -> RespondOutput:
        return await self._call("respond", arg, RespondOutput)

    async def inbox(self, arg: InboxInput | None = None) -> InboxOutput:
        return await self._call("inbox", arg or InboxInput(), InboxOutput)

    async def inbox_wait(self, arg: InboxWaitInput | None = None) -> InboxWaitOutput:
        return await self._call("inbox_wait", arg or InboxWaitInput(), InboxWaitOutput)

    async def _call(
        self,
        name: str,
        arg: BaseModel | dict[str, Any],
        output_model: type[T],
    ) -> T:
        if isinstance(arg, BaseModel):
            payload = arg.model_dump(by_alias=True, exclude_none=True)
        else:
            payload = dict(arg)
        result = await self._session.call_tool(name, arguments=payload)
        if getattr(result, "isError", False):
            raise ToolError(_describe_error(result))
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            raise ToolError(f"tool {name!r} returned no structuredContent")
        try:
            return output_model.model_validate(structured)
        except ValidationError as exc:
            raise ToolError(f"tool {name!r} returned malformed result: {exc}") from exc


def _describe_error(result: object) -> str:
    content = getattr(result, "content", None)
    if isinstance(content, list) and content:
        first = content[0]
        text = getattr(first, "text", None)
        if isinstance(text, str) and text:
            return text
    return "tool call failed (no further detail)"
