"""Validate the A2A envelope model against the spec schema."""

from __future__ import annotations

import json

import jsonschema

from shadownet.a2a.envelope import ShadownetEnvelope


def _load_schema(specs_path) -> dict:
    schema_path = specs_path / "schemas" / "messages" / "envelope.schema.json"
    return json.loads(schema_path.read_text())


def test_envelope_validates_against_schema(specs_path) -> None:
    schema = _load_schema(specs_path)
    env = ShadownetEnvelope(
        **{
            "shadownet:v": "0.1",
            "intentId": "urn:uuid:int-001",
            "interaction": "urn:shadownet:int:scheduling.v0-draft",
            "payload": {"kind": "propose"},
        }
    )
    payload = env.model_dump(by_alias=True, exclude_none=True)
    jsonschema.validate(payload, schema)


def test_envelope_with_session_id_validates(specs_path) -> None:
    schema = _load_schema(specs_path)
    env = ShadownetEnvelope(
        **{
            "shadownet:v": "0.1",
            "intentId": "urn:uuid:int-001",
            "sessionId": "urn:uuid:session-001",
            "interaction": "urn:shadownet:int:scheduling.v0-draft",
            "payload": {"kind": "respond"},
        }
    )
    payload = env.model_dump(by_alias=True, exclude_none=True)
    jsonschema.validate(payload, schema)


def test_spec_example_envelope_parses(specs_path) -> None:
    """RFC-0006 §Message envelope sample."""
    raw = {
        "shadownet:v": "0.1",
        "intentId": "urn:uuid:int-001",
        "interaction": "urn:shadownet:int:scheduling.v1",
        "payload": {"any": "shape"},
    }
    schema = _load_schema(specs_path)
    jsonschema.validate(raw, schema)
    parsed = ShadownetEnvelope.model_validate(raw)
    jsonschema.validate(parsed.model_dump(by_alias=True, exclude_none=True), schema)
