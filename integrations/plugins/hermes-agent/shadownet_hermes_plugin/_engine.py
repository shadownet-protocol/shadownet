"""In-memory autonomous-exchange engine.

Decides whether an inbound free-form message is handled autonomously (a known
contact) or surfaced to the human (a stranger), bounds the in-flight exchange
per ``contextId`` with loop guards, and maps a contact's ``chat_id`` to its
active ``contextId`` so the adapter can thread replies with ``respond()``.

This is a switchboard + circuit-breaker, NOT an agent: it never reasons and
never generates a move. The full Hermes agent does that (a normal turn run via
``handle_message`` in the shadownet session, guided by the skill + the contact's
profile notes), and the agent owns the normal "when to stop / whether to surface"
decisions. This module only does what the agent cannot or should not: gate on
contact membership (a security boundary — a stranger must never trigger a paid
agent turn), thread replies on the right ``contextId``, drop duplicate
re-deliveries, and backstop a runaway loop.

Holds NO durable state and does NO I/O: contact/trust authority is the
sidecar's (read off ``InboxItem.status`` + ``contact_detail`` by the adapter),
and the conversation itself persists in the Hermes session + the sidecar
thread. Pure, restart-safe state machine — on restart it starts fresh and
re-derives from the next inbound event.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

__all__ = ["Decision", "ExchangeEngine"]

# A HIGH anti-runaway backstop, not a conversation-length policy: the agent
# decides when an exchange is done; this only catches a peer that loops forever.
_DEFAULT_MAX_TURNS = 50
_DEFAULT_IDLE_SECONDS = 900.0
_SEEN_CAP = 512


@dataclass(frozen=True)
class Decision:
    """How an inbound free-form message should be handled."""

    action: str  # "autonomous" | "human" | "skip"
    reason: str = ""
    first_turn: bool = False


@dataclass
class _Run:
    context_id: str
    contact: str
    turn_count: int = 0
    last_ts: float = 0.0
    seen_ids: list[str] = field(default_factory=list)


class ExchangeEngine:
    """Tracks in-flight autonomous exchanges and classifies inbound messages."""

    def __init__(self) -> None:
        self._runs: dict[str, _Run] = {}
        self._ctx_by_chat: dict[str, str] = {}

    def _max_turns(self) -> int:
        try:
            return max(1, int(os.environ.get("SHADOWNET_MAX_AUTO_TURNS", "")))
        except ValueError:
            return _DEFAULT_MAX_TURNS

    def _idle_seconds(self) -> float:
        try:
            return max(1.0, float(os.environ.get("SHADOWNET_AUTO_IDLE_SECONDS", "")))
        except ValueError:
            return _DEFAULT_IDLE_SECONDS

    def decide(
        self,
        *,
        status: str,
        contact: str,
        context_id: str,
        message_id: str = "",
        now: float | None = None,
    ) -> Decision:
        """Classify an inbound free-form message into autonomous / human / skip.

        ``status`` is the sidecar's ``InboxItem.status``: ``"inbox"`` is a known
        contact (autonomous), ``"stranger_review"`` is surfaced to the human.
        Known-contact messages advance the per-context turn counter and are
        bounded by the max-turns guard; a duplicate ``message_id`` is skipped.
        """
        stamp = time.time() if now is None else now

        if status != "inbox":
            # stranger_review (or any non-inbox status) is never handled
            # autonomously — the human decides.
            return Decision(action="human", reason=status or "no_status")

        run = self._runs.get(context_id)
        if run is None:
            run = _Run(context_id=context_id, contact=contact)
            self._runs[context_id] = run

        if run.last_ts and stamp - run.last_ts > self._idle_seconds():
            # The exchange went quiet long enough that a fresh message starts a
            # new round budget rather than inheriting a stale (possibly maxed) count.
            run.turn_count = 0
            run.seen_ids.clear()

        if message_id and message_id in run.seen_ids:
            return Decision(action="skip", reason="duplicate")
        if message_id:
            run.seen_ids.append(message_id)
            if len(run.seen_ids) > _SEEN_CAP:
                del run.seen_ids[: len(run.seen_ids) - _SEEN_CAP]

        if run.turn_count >= self._max_turns():
            return Decision(action="human", reason="max_turns")

        first = run.turn_count == 0
        run.turn_count += 1
        run.last_ts = stamp
        self._ctx_by_chat[contact] = context_id
        return Decision(action="autonomous", reason="contact", first_turn=first)

    def active_context_for(self, chat_id: str) -> str | None:
        """The active exchange's ``contextId`` for a contact, for ``respond()`` threading."""
        return self._ctx_by_chat.get(chat_id)

    def active(self) -> list[tuple[str, str, int]]:
        """``(contact, context_id, turn_count)`` for each in-flight exchange."""
        return [(r.contact, r.context_id, r.turn_count) for r in self._runs.values()]

    def end(self, context_id: str) -> bool:
        """Forget an exchange (e.g. operator stop). Returns True if one was tracked."""
        run = self._runs.pop(context_id, None)
        if run is None:
            return False
        if self._ctx_by_chat.get(run.contact) == context_id:
            del self._ctx_by_chat[run.contact]
        return True

    def end_contact(self, contact: str) -> int:
        """Forget all of a contact's in-flight exchanges. Returns the count removed."""
        context_ids = [cid for cid, r in self._runs.items() if r.contact == contact]
        for cid in context_ids:
            self._runs.pop(cid, None)
        self._ctx_by_chat.pop(contact, None)
        return len(context_ids)
