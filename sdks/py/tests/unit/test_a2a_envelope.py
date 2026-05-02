from __future__ import annotations

import pytest

from shadownet.a2a.envelope import (
    ENVELOPE_PART_TYPE,
    ShadownetEnvelope,
    decode_envelope_part,
    envelope_part,
)


def test_envelope_round_trip() -> None:
    env = ShadownetEnvelope(
        **{
            "shadownet:v": "0.1",
            "intentId": "urn:uuid:int-001",
            "interaction": "urn:shadownet:int:scheduling.v0-draft",
            "payload": {"kind": "propose", "title": "x"},
        }
    )
    part = envelope_part(env)
    assert part["type"] == ENVELOPE_PART_TYPE
    decoded = decode_envelope_part(part)
    assert decoded.intent_id == "urn:uuid:int-001"
    assert decoded.payload == {"kind": "propose", "title": "x"}


def test_decode_rejects_wrong_part_type() -> None:
    with pytest.raises(ValueError):
        decode_envelope_part({"type": "text/plain", "data": {}})


def test_decode_rejects_missing_data() -> None:
    with pytest.raises(ValueError):
        decode_envelope_part({"type": ENVELOPE_PART_TYPE})


def test_envelope_rejects_extra_fields() -> None:
    with pytest.raises(Exception):  # noqa: B017
        ShadownetEnvelope.model_validate(
            {
                "shadownet:v": "0.1",
                "intentId": "urn:uuid:x",
                "interaction": "urn:shadownet:int:test",
                "payload": {},
                "extraneous": "no",
            }
        )
