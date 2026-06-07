"""Process-wide switchboard for autonomous Shadownet exchanges.

Gates inbound messages (known contact vs stranger), bounds each exchange with loop
guards (per-``contextId`` and a per-contact aggregate that survives contextId
fan-out), holds the layered standing directives the adapter injects each turn, and
tracks a per-exchange pause/stop lifecycle. It never reasons or makes a move; the
full Hermes agent does that. Runtime exchange state is in-memory and re-derived
after a restart, but directives persist under ``HERMES_HOME`` so a safety directive
does not fail open across a restart.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shadownet_hermes_plugin import _paths

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["Decision", "ExchangeEngine", "get_engine", "reset_engine"]

_log = logging.getLogger(__name__)

# Anti-runaway backstops, not a conversation-length policy: the agent decides when
# an exchange is done; these only catch a peer that loops forever. The per-contact
# budget is the real guard — a peer that mints a fresh contextId per round (fan-out)
# would reset the per-context counter forever, so the aggregate across a contact's
# contexts is what actually bounds the spend.
_DEFAULT_MAX_TURNS = 50
_DEFAULT_MAX_CONTACT_TURNS = 12
_DEFAULT_IDLE_SECONDS = 900.0
_SEEN_CAP = 512


@dataclass(frozen=True)
class Decision:
    """How an inbound message should be handled."""

    action: str  # "autonomous" | "human" | "skip"
    reason: str = ""
    first_turn: bool = False


@dataclass
class _Run:
    """In-flight per-``contextId`` exchange state."""

    context_id: str
    contact: str
    turn_count: int = 0
    last_ts: float = 0.0
    status: str = "active"  # "active" | "paused"
    seen_ids: list[str] = field(default_factory=list)


class ExchangeEngine:
    """Classifies inbound messages, bounds exchanges, and holds standing directives."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, _Run] = {}
        # Per-contact aggregate turn budget across all of a contact's contexts —
        # the backstop that survives contextId fan-out.
        self._contact_turns: dict[str, int] = {}
        self._contact_last_ts: dict[str, float] = {}
        self._global = ""
        self._by_contact: dict[str, str] = {}
        self._by_context: dict[str, str] = {}
        self._kickoffs: list[dict[str, str]] = []
        self._load_directives()

    def request_kickoff(self, *, target: str, contact: str, instruction: str) -> None:
        """Queue an operator-initiated turn for the adapter to run in the background.

        ``target`` is the contact's existing ``contextId`` (continue the thread) or
        the contact shadowname (open a fresh thread). Drained by the adapter loop.
        """
        with self._lock:
            self._kickoffs.append(
                {"target": target, "contact": contact, "instruction": instruction}
            )

    def take_kickoffs(self) -> list[dict[str, str]]:
        """Drain and return the queued operator kickoffs."""
        with self._lock:
            out = self._kickoffs[:]
            self._kickoffs.clear()
            return out

    def context_for_contact(self, contact: str) -> str:
        """The most-recent live contextId for a contact, or '' if none is tracked."""
        with self._lock:
            for run in reversed(list(self._runs.values())):
                if run.contact == contact:
                    return run.context_id
        return ""

    def _max_turns(self) -> int:
        try:
            return max(1, int(os.environ.get("SHADOWNET_MAX_AUTO_TURNS", "")))
        except ValueError:
            return _DEFAULT_MAX_TURNS

    def _max_contact_turns(self) -> int:
        try:
            return max(1, int(os.environ.get("SHADOWNET_MAX_CONTACT_TURNS", "")))
        except ValueError:
            return _DEFAULT_MAX_CONTACT_TURNS

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
        """Classify an inbound message into autonomous / human / skip.

        ``status`` is the sidecar's ``InboxItem.status``: ``"inbox"`` is a known
        contact (autonomous), anything else is surfaced to the human. Known-contact
        messages advance both the per-context and the per-contact turn counters under
        the max-turns guards; a duplicate ``message_id`` or a paused exchange is skipped.
        """
        if status != "inbox":
            return Decision(action="human", reason=status or "no_status")
        stamp = time.time() if now is None else now
        idle = self._idle_seconds()
        with self._lock:
            run = self._runs.get(context_id)
            if run is None:
                run = _Run(context_id=context_id, contact=contact)
                self._runs[context_id] = run
            if run.status == "paused":
                return Decision(action="skip", reason="paused")
            if run.last_ts and stamp - run.last_ts > idle:
                # A long-quiet exchange starts a fresh round budget rather than
                # inheriting a stale (possibly maxed) count.
                run.turn_count = 0
                run.seen_ids.clear()
            c_last = self._contact_last_ts.get(contact, 0.0)
            if c_last and stamp - c_last > idle:
                self._contact_turns[contact] = 0
            if message_id and message_id in run.seen_ids:
                return Decision(action="skip", reason="duplicate")
            if message_id:
                run.seen_ids.append(message_id)
                if len(run.seen_ids) > _SEEN_CAP:
                    del run.seen_ids[: len(run.seen_ids) - _SEEN_CAP]
            if run.turn_count >= self._max_turns():
                return Decision(action="human", reason="max_turns")
            if self._contact_turns.get(contact, 0) >= self._max_contact_turns():
                # Fan-out backstop: a peer minting a fresh contextId per round can't
                # keep resetting the per-context counter past this aggregate cap.
                return Decision(action="human", reason="contact_max_turns")
            first = run.turn_count == 0
            run.turn_count += 1
            run.last_ts = stamp
            self._contact_turns[contact] = self._contact_turns.get(contact, 0) + 1
            self._contact_last_ts[contact] = stamp
            return Decision(action="autonomous", reason="contact", first_turn=first)

    def set_directive(self, *, scope: str, text: str, target: str = "") -> None:
        """Set a standing directive at ``global`` / ``contact`` / ``session`` scope (empty text clears)."""
        with self._lock:
            if scope == "global":
                self._global = text
            elif scope == "contact":
                self._assign(self._by_contact, target, text)
            elif scope == "session":
                self._assign(self._by_context, target, text)
            else:
                raise ValueError(f"unknown directive scope: {scope!r}")
        self._save_directives()

    @staticmethod
    def _assign(store: dict[str, str], key: str, text: str) -> None:
        if text:
            store[key] = text
        else:
            store.pop(key, None)

    def directives_for(self, contact: str, context_id: str) -> str:
        """Assemble the global, per-contact, and per-session directives into one block."""
        with self._lock:
            layers = (
                self._global,
                self._by_contact.get(contact, ""),
                self._by_context.get(context_id, ""),
            )
        return "\n".join(f"[standing instruction] {t}" for t in layers if t)

    def set_status(self, context_id: str, status: str) -> bool:
        """Set an exchange's lifecycle status (e.g. ``paused`` / ``active``)."""
        with self._lock:
            run = self._runs.get(context_id)
            if run is None:
                return False
            run.status = status
            return True

    def active(self) -> list[dict[str, object]]:
        """Snapshot of in-flight exchanges for the concierge view."""
        with self._lock:
            return [
                {
                    "contact": r.contact,
                    "contextId": r.context_id,
                    "turnCount": r.turn_count,
                    "status": r.status,
                }
                for r in self._runs.values()
            ]

    def end(self, context_id: str) -> bool:
        """Forget an exchange (operator stop). Returns True if one was tracked."""
        with self._lock:
            return self._runs.pop(context_id, None) is not None

    def end_contact(self, contact: str) -> int:
        """Forget all of a contact's in-flight exchanges. Returns the count removed."""
        with self._lock:
            cids = [cid for cid, r in self._runs.items() if r.contact == contact]
            for cid in cids:
                self._runs.pop(cid, None)
            self._contact_turns.pop(contact, None)
            self._contact_last_ts.pop(contact, None)
            return len(cids)

    def _store_path(self) -> Path:
        return _paths.hermes_home() / "shadownet" / "directives.json"

    def _load_directives(self) -> None:
        try:
            raw = json.loads(self._store_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._global = str(raw.get("global", "") or "")
        self._by_contact = {str(k): str(v) for k, v in (raw.get("by_contact") or {}).items()}
        self._by_context = {str(k): str(v) for k, v in (raw.get("by_context") or {}).items()}

    def _save_directives(self) -> None:
        path = self._store_path()
        with self._lock:
            payload = json.dumps(
                {
                    "global": self._global,
                    "by_contact": self._by_contact,
                    "by_context": self._by_context,
                },
                indent=2,
            )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            _log.warning("shadownet: failed to persist directives: %s", exc)


_engine: ExchangeEngine | None = None
_engine_guard = threading.Lock()


def get_engine() -> ExchangeEngine:
    """Return the process-wide engine shared by the adapter and the channel bridge."""
    global _engine
    if _engine is None:
        with _engine_guard:
            if _engine is None:
                _engine = ExchangeEngine()
    return _engine


def reset_engine() -> None:
    """Drop the singleton so the next ``get_engine()`` rebuilds it (tests only)."""
    global _engine
    _engine = None
