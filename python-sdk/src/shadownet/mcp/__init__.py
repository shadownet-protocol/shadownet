"""MCP control surface — RFC 0002 client-side primitives.

Typed pydantic models for every tool's input / output shape, the v0.2
intent payload models, ContactProfile, PlanObject, and a thin streamable-HTTP
MCP client wrapper. Server-side tool registration (``mcp.server.fastmcp``
decorators) is the Sidecar's concern; shadownet-local owns that surface.
"""

from __future__ import annotations

from shadownet.mcp.client import ShadownetMCPClient, ToolError
from shadownet.mcp.intents import (
    AcceptPlanV1Data,
    ConfirmPlanV1Data,
    CoordinateV1Data,
    GeoCoordinate,
    PlanObject,
    PlanWhere,
)
from shadownet.mcp.notifications import (
    NOTIFICATION_NAMESPACE,
    InboxMessageEvent,
    InboxWaitEvent,
    TaskUpdateEvent,
)
from shadownet.mcp.tools import (
    AddContactInput,
    AddContactOutput,
    BodySlot,
    ContactDetailInput,
    ContactDetailOutput,
    ContactProfile,
    ContactsInput,
    ContactsOutput,
    ContactSummary,
    CredentialSummary,
    GrantInput,
    GrantOutput,
    IdentityOutput,
    InboxInput,
    InboxItem,
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

__all__ = [
    "NOTIFICATION_NAMESPACE",
    "AcceptPlanV1Data",
    "AddContactInput",
    "AddContactOutput",
    "BodySlot",
    "ConfirmPlanV1Data",
    "ContactDetailInput",
    "ContactDetailOutput",
    "ContactProfile",
    "ContactSummary",
    "ContactsInput",
    "ContactsOutput",
    "CoordinateV1Data",
    "CredentialSummary",
    "GeoCoordinate",
    "GrantInput",
    "GrantOutput",
    "IdentityOutput",
    "InboxInput",
    "InboxItem",
    "InboxMessageEvent",
    "InboxOutput",
    "InboxWaitEvent",
    "InboxWaitInput",
    "InboxWaitOutput",
    "PlanObject",
    "PlanWhere",
    "ResolveInput",
    "ResolveOutput",
    "RespondInput",
    "RespondOutput",
    "SendInput",
    "SendOutput",
    "SetContactProfileInput",
    "SetContactProfileOutput",
    "ShadownetMCPClient",
    "TaskUpdateEvent",
    "ToolError",
]
