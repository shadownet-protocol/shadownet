from __future__ import annotations

import pytest
from pydantic import ValidationError

from shadownet.mcp import (
    AcceptPlanInput,
    AcceptPlanV1Data,
    AddContactInput,
    AddContactOutput,
    BodySlot,
    ConfirmPlanInput,
    ConfirmPlanV1Data,
    ContactProfile,
    CoordinateInput,
    CoordinateV1Data,
    GeoCoordinate,
    InboxItem,
    InboxWaitInput,
    InboxWaitOutput,
    PlanObject,
    PlanWhere,
    SendInput,
    SendOutput,
)
from shadownet.mcp.intents import (
    ACCEPT_PLAN_V1_URI,
    CONFIRM_PLAN_V1_URI,
    COORDINATE_V1_URI,
)
from shadownet.mcp.notifications import NOTIFICATION_NAMESPACE, InboxMessageEvent


class TestBodySlot:
    def test_all_optional(self) -> None:
        assert BodySlot().model_dump() == {"text": None, "intent": None, "data": None}

    def test_extra_allowed(self) -> None:
        b = BodySlot.model_validate({"text": "hi", "extra": "ok"})
        assert b.text == "hi"


class TestSendInputOutput:
    def test_wire_alias(self) -> None:
        wire = {
            "to": "bob@example.org",
            "body": {"text": "hi"},
            "contextId": "ctx-1",
        }
        parsed = SendInput.model_validate(wire)
        assert parsed.to == "bob@example.org"
        assert parsed.context_id == "ctx-1"
        assert parsed.model_dump(by_alias=True)["contextId"] == "ctx-1"

    def test_send_output_status_enum(self) -> None:
        with pytest.raises(ValidationError):
            SendOutput.model_validate({"messageId": "m", "contextId": "c", "status": "MAYBE"})


class TestContactProfile:
    def test_notes_length_limit(self) -> None:
        with pytest.raises(ValidationError):
            ContactProfile(notes="x" * 4097)

    def test_priority_enum(self) -> None:
        assert ContactProfile(priority="low").priority == "low"
        with pytest.raises(ValidationError):
            ContactProfile(priority="urgent")

    def test_wire_alias_expires_at(self) -> None:
        p = ContactProfile.model_validate({"expiresAt": "2026-08-01T00:00:00Z"})
        assert p.expires_at == "2026-08-01T00:00:00Z"
        assert p.model_dump(by_alias=True)["expiresAt"] == "2026-08-01T00:00:00Z"


class TestAddContact:
    def test_defaults(self) -> None:
        inp = AddContactInput(name="alice@sh4dow.org")
        assert inp.grants == ("messaging",)

    def test_trust_warning_passthrough(self) -> None:
        out = AddContactOutput.model_validate(
            {
                "shadowname": "alice@sh4dow.org",
                "trustWarning": {"untrustedIssuers": ["acme.example"]},
            }
        )
        assert out.trust_warning == {"untrustedIssuers": ("acme.example",)}


class TestInbox:
    def test_inbox_item_wire(self) -> None:
        item = InboxItem.model_validate(
            {
                "messageId": "m1",
                "contextId": "ctx-1",
                "from": "alice@sh4dow.org",
                "receivedAt": "2026-05-30T00:00:00Z",
                "status": "inbox",
                "body": {"text": "hi"},
            }
        )
        assert item.sender == "alice@sh4dow.org"
        assert item.body.text == "hi"

    def test_inbox_wait_underscore_wire(self) -> None:
        # RFC 0002 §4: inbox_wait arguments use snake_case on the wire.
        inp = InboxWaitInput.model_validate({"timeout_seconds": 30, "last_event_id": "abc"})
        assert inp.timeout_seconds == 30
        out = InboxWaitOutput(events=(), next_event_id="abc")
        assert out.next_event_id == "abc"


class TestIntents:
    def test_uris_match_spec(self) -> None:
        assert COORDINATE_V1_URI == "urn:shadownet:intent:coordinate_v1"
        assert CONFIRM_PLAN_V1_URI == "urn:shadownet:intent:confirm_plan_v1"
        assert ACCEPT_PLAN_V1_URI == "urn:shadownet:intent:accept_plan_v1"

    def test_coordinate_data(self) -> None:
        data = CoordinateV1Data(activity="dinner", details="Thursday evening")
        assert data.activity == "dinner"

    def test_plan_object(self) -> None:
        plan = PlanObject(
            activity="dinner",
            when="2026-05-14T18:00:00Z/PT3H",
            where=PlanWhere(city="Berlin", type="restaurant"),
            participants=("alice@sh4dow.org", "bob@example.org"),
        )
        assert plan.activity == "dinner"

    def test_confirm_plan_inherits_plan(self) -> None:
        data = ConfirmPlanV1Data(
            activity="coffee",
            when="2026-05-15T10:00:00Z",
            participants=("alice@sh4dow.org",),
        )
        assert data.activity == "coffee"

    def test_accept_plan_alias(self) -> None:
        data = AcceptPlanV1Data.model_validate({"acceptsMessageId": "m1"})
        assert data.accepts_message_id == "m1"
        assert data.model_dump(by_alias=True) == {"acceptsMessageId": "m1"}

    def test_geo_coordinate(self) -> None:
        geo = GeoCoordinate(lat=52.5, lon=13.4)
        assert geo.lat == 52.5


class TestNotifications:
    def test_namespace(self) -> None:
        assert NOTIFICATION_NAMESPACE == "notifications/shadownet/"

    def test_inbox_message_event(self) -> None:
        ev = InboxMessageEvent.model_validate(
            {
                "eventId": "e1",
                "messageId": "m1",
                "contextId": "ctx-1",
                "from": "alice@sh4dow.org",
                "status": "inbox",
            }
        )
        assert ev.sender == "alice@sh4dow.org"


class TestCoordinateConfirmAcceptInputs:
    def test_coordinate_input(self) -> None:
        ci = CoordinateInput(name="bob@example.org", activity="coffee", details="downtown")
        assert ci.activity == "coffee"

    def test_confirm_plan_input_wire(self) -> None:
        ci = ConfirmPlanInput.model_validate(
            {"name": "bob@example.org", "contextId": "ctx-1", "plan": {"activity": "x"}}
        )
        assert ci.context_id == "ctx-1"

    def test_accept_plan_input_wire(self) -> None:
        ai = AcceptPlanInput.model_validate(
            {"name": "bob@example.org", "contextId": "ctx-1", "acceptsMessageId": "m1"}
        )
        assert ai.accepts_message_id == "m1"
        assert ai.model_dump(by_alias=True)["acceptsMessageId"] == "m1"
