"""Inbound MCP notifications — RFC 0002 §7.

Path 1: Sidecar SHOULD push under ``notifications/shadownet/<event-name>``
on new inbound activity. Both events carry an opaque ``event_id`` that is
byte-identical to the one delivered via ``inbox_wait`` so consumers
de-duplicate across transports.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "NOTIFICATION_NAMESPACE",
    "InboxMessageEvent",
    "InboxWaitEvent",
    "TaskUpdateEvent",
]


NOTIFICATION_NAMESPACE: Final = "notifications/shadownet/"


class InboxMessageEvent(BaseModel):
    """``notifications/shadownet/inbox.message`` payload."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    event_id: str = Field(alias="eventId")
    message_id: str = Field(alias="messageId")
    context_id: str = Field(alias="contextId")
    sender: str = Field(alias="from")
    intent: str | None = None
    status: str  # "inbox" | "stranger_review"


class TaskUpdateEvent(BaseModel):
    """``notifications/shadownet/task.update`` payload."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    event_id: str = Field(alias="eventId")
    context_id: str = Field(alias="contextId")
    task_id: str = Field(alias="taskId")
    status: str


class InboxWaitEvent(BaseModel):
    """One entry in ``inbox_wait`` results — generalizes both event types
    above for the long-poll path."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    event_id: str = Field(alias="eventId")
    event: str  # event name from §7 Events table
    occurred_at: int = Field(alias="occurredAt")
    data: dict[str, object]
