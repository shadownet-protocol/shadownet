from __future__ import annotations

import pytest
from pydantic import ValidationError

from shadownet.mcp.tools import (
    EVENT_QUARANTINE_PENDING,
    AddContactInput,
    ContactProfile,
    GrantInput,
    QuarantineListInput,
    QuarantineReviewInput,
    SetContactProfileInput,
)


def test_grant_accepts_coordinate_verb() -> None:
    g = GrantInput.model_validate({"contactId": "c1", "grant": "coordinate", "allowed": True})
    assert g.grant == "coordinate"


def test_grant_rejects_unknown_verb() -> None:
    with pytest.raises(ValidationError):
        GrantInput.model_validate({"contactId": "c1", "grant": "vouch", "allowed": True})


def test_add_contact_accepts_profile() -> None:
    inp = AddContactInput.model_validate(
        {
            "shadowname": "alice@x.example",
            "grants": ["messaging"],
            "profile": {"notes": "trusted introducer", "priority": "high"},
        }
    )
    assert inp.profile is not None
    assert inp.profile.notes == "trusted introducer"
    assert inp.profile.priority == "high"


def test_contact_profile_rejects_oversize_notes() -> None:
    with pytest.raises(ValidationError):
        ContactProfile.model_validate({"notes": "x" * 5000})


def test_quarantine_review_accept_with_profile() -> None:
    r = QuarantineReviewInput.model_validate(
        {
            "quarantineId": "q-1",
            "decision": "accept",
            "displayName": "Alex",
            "grants": ["messaging"],
            "profile": {"notes": "trusted intro"},
        }
    )
    assert r.decision == "accept"
    assert r.profile is not None
    assert r.profile.notes == "trusted intro"


def test_quarantine_review_reject_and_block() -> None:
    r = QuarantineReviewInput.model_validate(
        {"quarantineId": "q-2", "decision": "reject_and_block"}
    )
    assert r.decision == "reject_and_block"
    assert r.profile is None


def test_quarantine_list_defaults_are_open() -> None:
    inp = QuarantineListInput.model_validate({})
    assert inp.since is None
    assert inp.limit is None


def test_set_contact_profile_requires_profile() -> None:
    inp = SetContactProfileInput.model_validate({"contactId": "c1", "profile": {"priority": "low"}})
    assert inp.profile.priority == "low"


def test_quarantine_pending_event_name() -> None:
    assert EVENT_QUARANTINE_PENDING == "quarantine.pending"
