from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# RFC-0007 §Required tools — input/output models for every tool.

__all__ = [
    "AddContactInput",
    "AddContactOutput",
    "AuditOutput",
    "Contact",
    "ContactDetail",
    "ContactsInput",
    "ContactsOutput",
    "GrantInput",
    "GrantOutput",
    "IdentityOutput",
    "InboxInput",
    "InboxItem",
    "InboxOutput",
    "InboxWaitEvent",
    "InboxWaitInput",
    "InboxWaitOutput",
    "PresentInput",
    "PresentOutput",
    "ResolveInput",
    "ResolveOutput",
    "RespondInput",
    "RespondOutput",
    "SendInput",
    "SendOutput",
]


# --- social_contacts ---------------------------------------------------------


class ContactsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str | None = Field(default=None, description="Substring match on name or shadowname.")


class Contact(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    shadowname: str
    did: str
    display_name: str | None = Field(default=None, alias="displayName")
    level: str | None = None
    last_seen: int | None = Field(default=None, alias="lastSeen")


class ContactsOutput(BaseModel):
    contacts: list[Contact]


# --- social_contact_detail ---------------------------------------------------


class ContactDetail(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    shadowname: str
    did: str
    endpoint: str
    public_key: dict[str, str] = Field(alias="publicKey")
    credentials: list[str] = Field(default_factory=list)
    grants: list[str] = Field(default_factory=list)
    notes: str | None = None


# --- social_resolve ----------------------------------------------------------


class ResolveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shadowname: str


class ResolveOutput(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    did: str
    endpoint: str
    public_key: dict[str, str] = Field(alias="publicKey")
    subject_type: str = Field(alias="subjectType")
    ttl: int


# --- social_add_contact ------------------------------------------------------


class AddContactInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    shadowname: str
    display_name: str | None = Field(default=None, alias="displayName")
    grants: list[str] = Field(default_factory=list)


class AddContactOutput(BaseModel):
    id: str
    shadowname: str
    did: str


# --- social_send -------------------------------------------------------------


class SendInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    contact_id: str = Field(alias="contactId")
    # RFC-0006 / RFC-0007: `interaction` is OPTIONAL. Default envelope is
    # free-form text (payload = {"text": "...", "hints"?: {...}}); typed
    # Interaction Profiles become an opt-in for cases where structure
    # prevents ambiguity. When present, the value MUST be a URN per
    # RFC-0006 § Interaction Profiles.
    interaction: str | None = Field(default=None, pattern=r"^urn:")
    intent_id: str | None = Field(default=None, alias="intentId")
    payload: dict[str, Any]


class SendOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    intent_id: str = Field(alias="intentId")
    task_id: str = Field(alias="taskId")


# --- social_inbox ------------------------------------------------------------


class InboxInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    since: int | None = Field(default=None, ge=0)
    interaction: str | None = None
    contact_id: str | None = Field(default=None, alias="contactId")
    limit: int | None = Field(default=None, ge=1, le=1000)


class InboxItem(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    contact_id: str = Field(alias="contactId")
    intent_id: str = Field(alias="intentId")
    interaction: str
    payload: dict[str, Any]
    received_at: int = Field(alias="receivedAt", ge=0)


class InboxOutput(BaseModel):
    items: list[InboxItem]


# --- social_inbox_wait (RFC-0007 amendment D) -------------------------------


# Server-side maximum hold time. RFC-0007 amendment D requires the sidecar
# to clamp the client-supplied ``timeout_seconds`` to ≤90 seconds — beyond
# that, idle-kill behaviour of TCP middleboxes becomes unreliable.
INBOX_WAIT_MAX_TIMEOUT_SECONDS = 90
INBOX_WAIT_DEFAULT_TIMEOUT_SECONDS = 30


class InboxWaitInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    timeout_seconds: int = Field(default=INBOX_WAIT_DEFAULT_TIMEOUT_SECONDS, ge=0)
    last_event_id: str | None = None


class InboxWaitEvent(BaseModel):
    """A single event delivered through the long-poll channel.

    Payload shape mirrors the event schema defined in RFC-0007 § Events.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    event_id: str = Field(min_length=1)
    event: str = Field(min_length=1)
    occurred_at: int = Field(ge=0, alias="occurredAt")
    data: dict[str, Any]


class InboxWaitOutput(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    events: list[InboxWaitEvent] = Field(default_factory=list)
    next_event_id: str | None = None


# --- social_respond ----------------------------------------------------------


class RespondInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    intent_id: str = Field(alias="intentId")
    payload: dict[str, Any]


class RespondOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")


# --- social_grant ------------------------------------------------------------


class GrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    contact_id: str = Field(alias="contactId")
    grant: str
    allowed: bool


class GrantOutput(BaseModel):
    ok: bool = True


# --- social_identity ---------------------------------------------------------


class IdentityOutput(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    did: str
    shadowname: str | None = None
    public_key: dict[str, str] = Field(alias="publicKey")
    credentials: list[str] = Field(default_factory=list)


# --- optional tools ----------------------------------------------------------


class PresentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    contact_id: str = Field(alias="contactId")
    nonce: str | None = None


class PresentOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    presentation_jwt: str = Field(alias="presentationJwt")


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    timestamp: int = Field(ge=0)
    tool: str
    input: dict[str, Any]
    success: bool


class AuditOutput(BaseModel):
    entries: list[AuditEntry]


__all__.append("AuditEntry")
