"""MCP tool input / output models — RFC 0002 §4, §6.

One pair of pydantic models per tool. Wire keys follow the spec (camelCase
where stated); Python attributes are snake_case with aliases otherwise.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AcceptPlanInput",
    "AcceptPlanOutput",
    "AddContactInput",
    "AddContactOutput",
    "BodySlot",
    "ConfirmPlanInput",
    "ConfirmPlanOutput",
    "ContactDetailInput",
    "ContactDetailOutput",
    "ContactProfile",
    "ContactSummary",
    "ContactsInput",
    "ContactsOutput",
    "CoordinateInput",
    "CoordinateOutput",
    "CredentialSummary",
    "GrantInput",
    "GrantOutput",
    "IdentityOutput",
    "InboxInput",
    "InboxItem",
    "InboxOutput",
    "InboxWaitInput",
    "InboxWaitOutput",
    "ResolveInput",
    "ResolveOutput",
    "RespondInput",
    "RespondOutput",
    "SendInput",
    "SendOutput",
    "SetContactProfileInput",
    "SetContactProfileOutput",
]


_BaseConfig = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
_BaseConfigOpen = ConfigDict(extra="allow", frozen=True, populate_by_name=True)


class BodySlot(BaseModel):
    """Envelope ``body`` slot — RFC 0001 §8.5."""

    model_config = _BaseConfigOpen

    text: str | None = None
    intent: str | None = None
    data: dict[str, Any] | None = None


class CredentialSummary(BaseModel):
    model_config = _BaseConfig

    kind: Literal["org_affiliation"] = "org_affiliation"
    issuer: str
    org: str
    expires_at: str = Field(alias="expiresAt")


class ContactProfile(BaseModel):
    """RFC 0002 §6 — local-only metadata. Never serialized over the wire."""

    model_config = _BaseConfig

    notes: Annotated[str | None, Field(max_length=4096)] = None
    priority: Literal["low", "normal", "high"] | None = None
    tags: tuple[str, ...] = ()
    expires_at: str | None = Field(default=None, alias="expiresAt")


class ContactSummary(BaseModel):
    model_config = _BaseConfig

    shadowname: str
    display_name: str | None = Field(default=None, alias="displayName")
    grants: tuple[str, ...] = ()
    last_seen: str | None = Field(default=None, alias="lastSeen")


class IdentityOutput(BaseModel):
    model_config = _BaseConfig

    shadowname: str
    pk: str
    credentials: tuple[CredentialSummary, ...] = ()


class ResolveInput(BaseModel):
    model_config = _BaseConfig
    name: str


class ResolveOutput(BaseModel):
    model_config = _BaseConfig
    shadowname: str
    pk: str
    endpoint: str


class ContactsInput(BaseModel):
    model_config = _BaseConfig
    query: str | None = None


class ContactsOutput(BaseModel):
    model_config = _BaseConfig
    contacts: tuple[ContactSummary, ...] = ()


class ContactDetailInput(BaseModel):
    model_config = _BaseConfig
    name: str


class ContactDetailOutput(BaseModel):
    model_config = _BaseConfig
    shadowname: str
    display_name: str | None = Field(default=None, alias="displayName")
    pk: str
    endpoint: str
    grants: tuple[str, ...] = ()
    credentials: tuple[CredentialSummary, ...] = ()
    profile: ContactProfile | None = None
    added_at: str = Field(alias="addedAt")
    last_seen: str | None = Field(default=None, alias="lastSeen")


class AddContactInput(BaseModel):
    model_config = _BaseConfig

    name: str
    display_name: str | None = Field(default=None, alias="displayName")
    grants: tuple[str, ...] = ("messaging",)
    profile: ContactProfile | None = None


class AddContactOutput(BaseModel):
    model_config = _BaseConfig

    shadowname: str
    trust_warning: dict[str, tuple[str, ...]] | None = Field(default=None, alias="trustWarning")


class GrantInput(BaseModel):
    model_config = _BaseConfig
    name: str
    grant: str
    allowed: bool


class GrantOutput(BaseModel):
    model_config = _BaseConfig
    ok: Literal[True] = True


class SetContactProfileInput(BaseModel):
    model_config = _BaseConfig
    name: str
    profile: ContactProfile


class SetContactProfileOutput(BaseModel):
    model_config = _BaseConfig
    ok: Literal[True] = True


class SendInput(BaseModel):
    model_config = _BaseConfig

    to: str
    body: BodySlot
    context_id: str | None = Field(default=None, alias="contextId")


class SendOutput(BaseModel):
    model_config = _BaseConfig

    message_id: str = Field(alias="messageId")
    context_id: str = Field(alias="contextId")
    status: Literal["accepted", "rejected"]
    error: str | None = None


class RespondInput(BaseModel):
    model_config = _BaseConfig
    context_id: str = Field(alias="contextId")
    body: BodySlot


class RespondOutput(BaseModel):
    model_config = _BaseConfig

    message_id: str = Field(alias="messageId")
    status: Literal["accepted", "rejected"]
    error: str | None = None


class CoordinateInput(BaseModel):
    model_config = _BaseConfig
    name: str
    activity: str
    details: str | None = None


class CoordinateOutput(BaseModel):
    model_config = _BaseConfig
    message_id: str = Field(alias="messageId")
    context_id: str = Field(alias="contextId")


class ConfirmPlanInput(BaseModel):
    model_config = _BaseConfigOpen
    name: str
    context_id: str = Field(alias="contextId")
    plan: dict[str, Any]


class ConfirmPlanOutput(BaseModel):
    model_config = _BaseConfig
    message_id: str = Field(alias="messageId")


class AcceptPlanInput(BaseModel):
    model_config = _BaseConfig
    name: str
    context_id: str = Field(alias="contextId")
    accepts_message_id: str = Field(alias="acceptsMessageId")


class AcceptPlanOutput(BaseModel):
    model_config = _BaseConfig
    message_id: str = Field(alias="messageId")


class InboxInput(BaseModel):
    model_config = _BaseConfig

    since: str | None = None
    contact: str | None = None
    intent: str | None = None
    include_review: bool = Field(default=False, alias="includeReview")
    limit: int = 50


class InboxItem(BaseModel):
    model_config = _BaseConfigOpen

    message_id: str = Field(alias="messageId")
    context_id: str = Field(alias="contextId")
    sender: str = Field(alias="from")
    received_at: str = Field(alias="receivedAt")
    status: Literal["inbox", "stranger_review"]
    body: BodySlot


class InboxOutput(BaseModel):
    model_config = _BaseConfig
    items: tuple[InboxItem, ...] = ()
    next_since: str | None = Field(default=None, alias="nextSince")


class InboxWaitInput(BaseModel):
    """Wire field names use snake_case here per RFC 0002 §4."""

    model_config = _BaseConfig

    timeout_seconds: int | None = None
    last_event_id: str | None = None


class InboxWaitOutput(BaseModel):
    model_config = _BaseConfigOpen

    events: tuple[dict[str, Any], ...] = ()
    next_event_id: str | None = None
