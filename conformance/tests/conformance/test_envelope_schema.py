# RFC-0006 §Schema (envelope), §Message envelope (Shadownet extensions)

"""Validate the canonical A2A envelope shape against the spec schema.

Coverage spans both envelope forms RFC-0006 defines:

- **Free-form text** (the default at v0.1): no `interaction`, payload carries
  `text` + optional `hints`.
- **Typed Interaction Profile**: `interaction` set to a URI, payload follows
  that profile's schema.

Tests also exercise verifier obligations: missing `interaction` MUST validate;
unknown `interaction` MUST validate (the verifier may surface as opaque but
must not reject at the envelope layer).
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from shadownet_conformance.config import resolve_schemas_root


@pytest.fixture(scope="module")
def schema(conformance_config) -> dict[str, object]:
    schemas_root = resolve_schemas_root(conformance_config.specs_path)
    return json.loads((schemas_root / "messages" / "envelope.schema.json").read_text())


# ---------------------------------------------------------------------------
# Reference envelopes
# ---------------------------------------------------------------------------

# Typed envelope: interaction names a profile, payload is opaque to RFC-0006.
TYPED_ENVELOPE: dict[str, object] = {
    "shadownet:v": "0.1",
    "intentId": "urn:uuid:00000000-0000-4000-8000-aaaaaaaaaaaa",
    "sessionId": "urn:uuid:00000000-0000-4000-8000-bbbbbbbbbbbb",
    "interaction": "urn:shadownet:int:scheduling.v1",
    "payload": {
        "kind": "propose",
        "title": "Test event",
    },
}

# Free-form envelope (the v0.1 default): no `interaction`, text payload.
FREE_FORM_ENVELOPE: dict[str, object] = {
    "shadownet:v": "0.1",
    "intentId": "urn:uuid:00000000-0000-4000-8000-cccccccccccc",
    "payload": {
        "text": "Hey — Sarah's free Thursday or Friday next week if you want to grab dinner.",
        "hints": {"deadline": "2026-05-15T17:00:00Z"},
    },
}


# ---------------------------------------------------------------------------
# Typed envelope (Interaction Profile path)
# ---------------------------------------------------------------------------


@pytest.mark.rfc("0006", section="Schema", requirement="typed_envelope_validates")
def test_typed_envelope_validates(schema: dict[str, object]):
    jsonschema.validate(TYPED_ENVELOPE, schema)


@pytest.mark.rfc("0006", section="Schema", requirement="typed_envelope_min_required")
def test_minimal_typed_envelope_validates(schema: dict[str, object]):
    minimal: dict[str, object] = {
        "shadownet:v": "0.1",
        "intentId": "urn:uuid:11111111-1111-4111-8111-111111111111",
        "interaction": "urn:shadownet:int:test.v0",
        "payload": {},
    }
    jsonschema.validate(minimal, schema)


# ---------------------------------------------------------------------------
# Free-form envelope (default path at v0.1)
# ---------------------------------------------------------------------------


@pytest.mark.rfc("0006", section="MessageEnvelope", requirement="free_form_validates")
def test_free_form_envelope_validates(schema: dict[str, object]):
    """RFC-0006 §Default form: free-form text. `interaction` MAY be absent."""
    jsonschema.validate(FREE_FORM_ENVELOPE, schema)


@pytest.mark.rfc("0006", section="MessageEnvelope", requirement="free_form_text_only")
def test_free_form_text_only_envelope_validates(schema: dict[str, object]):
    """Minimal free-form envelope: no `interaction`, no `hints` — just `text`."""
    minimal_free_form: dict[str, object] = {
        "shadownet:v": "0.1",
        "intentId": "urn:uuid:22222222-2222-4222-8222-222222222222",
        "payload": {"text": "ping"},
    }
    jsonschema.validate(minimal_free_form, schema)


@pytest.mark.rfc("0006", section="VerifierObligations", requirement="missing_interaction_accepted")
def test_envelope_without_interaction_validates(schema: dict[str, object]):
    """Verifiers MUST accept envelopes with no `interaction`."""
    no_interaction = {k: v for k, v in TYPED_ENVELOPE.items() if k != "interaction"}
    jsonschema.validate(no_interaction, schema)


@pytest.mark.rfc("0006", section="VerifierObligations", requirement="unknown_interaction_accepted")
def test_envelope_with_unknown_interaction_validates(schema: dict[str, object]):
    """Verifiers MUST NOT reject envelopes solely because `interaction` names an unknown profile."""
    unknown = {**TYPED_ENVELOPE, "interaction": "urn:example:never-seen-this-profile.v999"}
    jsonschema.validate(unknown, schema)


# ---------------------------------------------------------------------------
# Required-field + additional-property guards
# ---------------------------------------------------------------------------


@pytest.mark.rfc("0006", section="Schema", requirement="envelope_rejects_unknown_top_level")
def test_envelope_rejects_extra_top_level_field(schema: dict[str, object]):
    """envelope.schema.json sets additionalProperties=false."""
    bad = {**TYPED_ENVELOPE, "stowaway": "not-allowed"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


@pytest.mark.rfc("0006", section="Schema", requirement="envelope_rejects_wrong_version")
def test_envelope_rejects_wrong_version(schema: dict[str, object]):
    bad = {**TYPED_ENVELOPE, "shadownet:v": "0.2"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


@pytest.mark.rfc("0006", section="Schema", requirement="envelope_requires_payload")
def test_envelope_requires_payload(schema: dict[str, object]):
    """`payload` is required in both free-form and typed envelopes."""
    bad = {k: v for k, v in TYPED_ENVELOPE.items() if k != "payload"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


@pytest.mark.rfc("0006", section="Schema", requirement="envelope_requires_intent_id")
def test_envelope_requires_intent_id(schema: dict[str, object]):
    """`intentId` is required."""
    bad = {k: v for k, v in TYPED_ENVELOPE.items() if k != "intentId"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
