"""MCP control surface — RFC 0002 client-side primitives.

Typed pydantic models for every tool's input / output shape, the v0.2 intent
payload models (``body.intent`` / ``body.data`` shapes that ride opaquely
through the control surface), ``ContactProfile``, ``PlanObject``, and a
thin streamable-HTTP MCP client wrapper. Server-side tool registration
(``mcp.server.fastmcp`` decorators) is the Sidecar's concern.

Per RFC 0002 §1 + §3, the control surface is content-agnostic: ``body.intent``
and ``body.data`` are opaque slots on ``send`` / ``inbox``. Intent profile
payload models (:class:`CoordinateV1Data`, :class:`ConfirmPlanV1Data`,
:class:`AcceptPlanV1Data`, :class:`PlanObject`) remain in :mod:`shadownet.mcp.intents`
as application-layer helpers, but the SDK no longer exposes dedicated
``coordinate`` / ``confirm_plan`` / ``accept_plan`` MCP tools — callers
use :class:`SendInput` with ``body.intent`` set to the URI.
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
