from __future__ import annotations

from shadownet.a2a import (
    PURPOSE_INVITATION,
    ROUTE_DROP,
    ROUTE_INBOX,
    ROUTE_QUARANTINE,
    PeerDeclinedError,
    parse_free_form_payload,
)


def test_routing_decision_constants() -> None:
    assert ROUTE_INBOX == "inbox"
    assert ROUTE_QUARANTINE == "quarantine"
    assert ROUTE_DROP == "drop"


def test_peer_declined_response_shape() -> None:
    status, body = PeerDeclinedError().to_response()
    assert status == 403
    assert body["error"] == "peer_declined"
    assert body["shadownet:v"] == "0.1"


def test_parse_free_form_payload_hints() -> None:
    fp = parse_free_form_payload(
        {
            "text": "Hi, I'm Alex.",
            "hints": {
                "purpose": "invitation",
                "proposed_collaboration": "Project Foo",
                "introducer_contact": "did:key:zBob",
            },
        }
    )
    assert fp.text == "Hi, I'm Alex."
    assert fp.hints is not None
    assert fp.hints.purpose == PURPOSE_INVITATION
    assert fp.hints.proposed_collaboration == "Project Foo"
    assert fp.hints.introducer_contact == "did:key:zBob"


def test_parse_free_form_payload_no_hints() -> None:
    fp = parse_free_form_payload({"text": "plain message"})
    assert fp.text == "plain message"
    assert fp.hints is None


def test_parse_free_form_payload_extra_fields_preserved() -> None:
    fp = parse_free_form_payload({"text": "hi", "custom": {"a": 1}})
    assert fp.text == "hi"
    assert fp.model_extra is not None
    assert fp.model_extra.get("custom") == {"a": 1}
