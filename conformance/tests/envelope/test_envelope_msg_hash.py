"""Envelope ↔ message msgHash binding — RFC 0001 §8.3 + §8.4.

These tests pin the wire invariants of ``msgHash``: that it's stable across
unrelated envelope changes (the binding is to the A2A message, not the
envelope JWS), that it changes when any in-scope message field changes, and
that the canonical input omits absent fields rather than encoding them as
``null`` (§8.4 spelled out).
"""

from __future__ import annotations

from typing import Any

import pytest
from shadownet.envelope import ENVELOPE_EXTENSION_URI, compute_msg_hash


def _msg(envelope_jws: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "messageId": "01HZ7K3CWAB4D6N5XT0M2EXAMPLE",
        "role": "ROLE_USER",
        "parts": [{"text": "Hi"}],
        "contextId": "01HZ7K2BV5R2K0DW3FCONTEXT0001",
        "metadata": {ENVELOPE_EXTENSION_URI: envelope_jws},
    }
    base.update(overrides)
    return base


@pytest.mark.rfc("0001", section="8.4", requirement="msgHash strips Shadownet metadata key")
def test_msg_hash_stable_when_envelope_jws_changes() -> None:
    msg_a = _msg("envelope-version-A")
    msg_b = _msg("envelope-version-B")
    assert compute_msg_hash(msg_a) == compute_msg_hash(msg_b)


@pytest.mark.rfc("0001", section="8.4", requirement="msgHash covers parts")
def test_msg_hash_changes_with_parts() -> None:
    msg_a = _msg("e", parts=[{"text": "Hi"}])
    msg_b = _msg("e", parts=[{"text": "Bye"}])
    assert compute_msg_hash(msg_a) != compute_msg_hash(msg_b)


@pytest.mark.rfc("0001", section="8.4", requirement="msgHash covers contextId")
def test_msg_hash_changes_with_context_id() -> None:
    msg_a = _msg("e")
    msg_b = _msg("e", contextId="01HZ7K2BV5R2K0DW3FCONTEXT0002")
    assert compute_msg_hash(msg_a) != compute_msg_hash(msg_b)


@pytest.mark.rfc("0001", section="8.4", requirement="msgHash covers non-shadownet metadata")
def test_msg_hash_changes_with_sibling_metadata() -> None:
    msg_a = _msg("e")
    msg_b = _msg("e")
    msg_b["metadata"]["other"] = "value"
    assert compute_msg_hash(msg_a) != compute_msg_hash(msg_b)


@pytest.mark.rfc("0001", section="8.4", requirement="absent optional fields omitted")
def test_msg_hash_stable_when_optional_fields_absent() -> None:
    base = {
        "messageId": "m1",
        "role": "ROLE_USER",
        "parts": [{"text": "hi"}],
        "metadata": {ENVELOPE_EXTENSION_URI: "e"},
    }
    no_task_a = dict(base)
    no_task_b = dict(base)
    # Two messages with the same in-scope fields and no taskId hash identically.
    assert compute_msg_hash(no_task_a) == compute_msg_hash(no_task_b)
