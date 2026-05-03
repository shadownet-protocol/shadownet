# RFC-0006 §Schema (envelope)

"""Validate the canonical A2A envelope shape against the spec schema."""

from __future__ import annotations

import json

import jsonschema
import pytest

from shadownet_conformance.config import resolve_schemas_root


@pytest.fixture(scope="module")
def schema(conformance_config) -> dict[str, object]:
    schemas_root = resolve_schemas_root(conformance_config.specs_path)
    return json.loads((schemas_root / "messages" / "envelope.schema.json").read_text())


# RFC-0006 illustrative envelope payload.
CANONICAL_ENVELOPE: dict[str, object] = {
    "shadownet:v": "0.1",
    "intentId": "urn:uuid:00000000-0000-4000-8000-aaaaaaaaaaaa",
    "sessionId": "urn:uuid:00000000-0000-4000-8000-bbbbbbbbbbbb",
    "interaction": "urn:shadownet:int:scheduling.v1",
    "payload": {
        "kind": "propose",
        "title": "Test event",
    },
}


@pytest.mark.rfc("0006", section="Schema", requirement="envelope_validates")
def test_canonical_envelope_validates(schema: dict[str, object]):
    jsonschema.validate(CANONICAL_ENVELOPE, schema)


@pytest.mark.rfc("0006", section="Schema", requirement="envelope_min_required")
def test_minimal_envelope_validates(schema: dict[str, object]):
    minimal: dict[str, object] = {
        "shadownet:v": "0.1",
        "intentId": "urn:uuid:11111111-1111-4111-8111-111111111111",
        "interaction": "urn:shadownet:int:test.v0",
        "payload": {},
    }
    jsonschema.validate(minimal, schema)


@pytest.mark.rfc("0006", section="Schema", requirement="envelope_rejects_unknown_top_level")
def test_envelope_rejects_extra_top_level_field(schema: dict[str, object]):
    """envelope.schema.json sets additionalProperties=false."""
    bad = {**CANONICAL_ENVELOPE, "stowaway": "not-allowed"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


@pytest.mark.rfc("0006", section="Schema", requirement="envelope_rejects_wrong_version")
def test_envelope_rejects_wrong_version(schema: dict[str, object]):
    bad = {**CANONICAL_ENVELOPE, "shadownet:v": "0.2"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


@pytest.mark.rfc("0006", section="Schema", requirement="envelope_requires_payload")
def test_envelope_requires_payload(schema: dict[str, object]):
    bad = {k: v for k, v in CANONICAL_ENVELOPE.items() if k != "payload"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
