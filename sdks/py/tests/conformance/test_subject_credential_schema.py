"""Validate that our SubjectCredential model round-trips against the spec schema."""

from __future__ import annotations

import json
import time
import uuid

import jsonschema

from shadownet.vc.credential import (
    CredentialStatus,
    SubjectCredential,
    new_credential,
)


def _load_schema(specs_path) -> dict:
    schema_path = specs_path / "schemas" / "credentials" / "subject-credential.schema.json"
    return json.loads(schema_path.read_text())


def test_minimal_credential_validates_against_schema(specs_path) -> None:
    schema = _load_schema(specs_path)
    cred = new_credential(
        issuer="did:web:sca.shadownet.example",
        subject="did:key:z6MkSubjectPubkey123",
        level="urn:shadownet:level:L1",
        subject_type="person",
        issued_at=int(time.time()),
        jti=f"urn:uuid:{uuid.uuid4()}",
    )
    payload = cred.model_dump(by_alias=True, exclude_none=True)
    jsonschema.validate(payload, schema)


def test_credential_with_status_validates_against_schema(specs_path) -> None:
    schema = _load_schema(specs_path)
    cred = new_credential(
        issuer="did:web:sca.shadownet.example",
        subject="did:key:z6MkSubjectPubkey123",
        level="urn:shadownet:level:L2",
        subject_type="person",
        issued_at=int(time.time()),
        jti=f"urn:uuid:{uuid.uuid4()}",
        status=CredentialStatus(
            statusListIndex="12345",
            statusListCredential="https://sca.shadownet.example/status/2026-q3",
        ),
    )
    payload = cred.model_dump(by_alias=True, exclude_none=True)
    jsonschema.validate(payload, schema)


def test_spec_example_payload_parses_into_model(specs_path) -> None:
    """The illustrative payload from RFC-0003 §JWT shape parses without loss."""
    raw = {
        "iss": "did:web:sca.shadownet.example",
        "sub": "did:key:z6MkSubjectPubkey123",
        "iat": 1756684800,
        "exp": 1759276800,
        "jti": "urn:uuid:5b7c1c4a-0000-0000-0000-000000000000",
        "shadownet:v": "0.1",
        "vc": {
            "@context": [
                "https://www.w3.org/ns/credentials/v2",
                "https://shadownet.example/contexts/v1",
            ],
            "type": ["VerifiableCredential", "ShadownetSubjectCredential"],
            "credentialSubject": {
                "id": "did:key:z6MkSubjectPubkey123",
                "level": "urn:shadownet:level:L2",
                "subjectType": "person",
            },
            "credentialStatus": {
                "type": "BitstringStatusListEntry",
                "statusListIndex": "12345",
                "statusListCredential": "https://sca.shadownet.example/status/2026-q3",
            },
        },
    }
    schema = _load_schema(specs_path)
    jsonschema.validate(raw, schema)
    parsed = SubjectCredential.model_validate(raw)
    re_serialized = parsed.model_dump(by_alias=True, exclude_none=True)
    jsonschema.validate(re_serialized, schema)
