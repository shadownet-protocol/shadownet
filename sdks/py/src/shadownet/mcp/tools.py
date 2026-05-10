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
    "PresentInput",
    "PresentOutput",
    "ResolveInput",
    "ResolveOutput",
    "RespondInput",
    "RespondOutput",
    "SendInput",
    "SendOutput",
    "SetWebhookInput",
    "SetWebhookOutput",
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
    interaction: str = Field(pattern=r"^urn:")
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


# --- social_set_webhook ------------------------------------------------------


class SetWebhookInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    secret: str = Field(min_length=32)
    events: list[str] | None = None


class SetWebhookOutput(BaseModel):
    ok: bool = True


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
