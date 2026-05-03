# RFC-0003 §JWT shape

"""Validate every committed credential fixture against the canonical JSON Schema.

The schema is the empirical contract for the wire shape; if a fixture stops
validating, either the schema or the fixture is wrong — and one of them must
move before any code does.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

import jsonschema
import pytest
from shadownet.crypto.jwt import decode_unverified_claims

from shadownet_conformance.fixtures import fixture_path, load_jwt

if TYPE_CHECKING:
    from pathlib import Path

CREDENTIAL_FIXTURES = (
    "credentials/alice_l1.jwt",
    "credentials/alice_l2.jwt",
    "credentials/acme_o1.jwt",
    "credentials/bob_l1.jwt",
)


def _schema(specs_path: Path) -> dict[str, object]:
    return json.loads(
        (specs_path / "schemas" / "credentials" / "subject-credential.schema.json").read_text()
    )


@pytest.fixture(scope="module")
def schema(conformance_config) -> dict[str, object]:
    return _schema(conformance_config.specs_path)


@pytest.mark.parametrize("rel_path", CREDENTIAL_FIXTURES)
@pytest.mark.rfc("0003", section="JWT shape", requirement="schema_validates")
def test_credential_fixture_validates(rel_path: str, schema: dict[str, object]):
    """Every committed credential fixture MUST validate against the canonical schema."""
    token = load_jwt(rel_path)
    claims = decode_unverified_claims(token)
    jsonschema.validate(claims, schema)


@pytest.mark.rfc("0003", section="JWT shape", requirement="header_typ_vc_jwt")
def test_credential_header_typ_is_vc_jwt():
    """RFC-0003 §Header: typ MUST be 'vc+jwt'."""
    for rel in CREDENTIAL_FIXTURES:
        token = load_jwt(rel)
        header_b64 = token.split(".", 1)[0]
        # base64url-decode with padding restored
        header_json = base64.urlsafe_b64decode(header_b64 + "==").decode()
        header = json.loads(header_json)
        assert header.get("typ") == "vc+jwt", (
            f"{rel}: typ must be 'vc+jwt', got {header.get('typ')}"
        )
        assert header.get("alg") == "EdDSA", f"{rel}: alg must be 'EdDSA', got {header.get('alg')}"


@pytest.mark.rfc("0003", section="JWT shape", requirement="org_subject_uses_did_web")
def test_organization_credential_uses_did_web_subject():
    """RFC-0003: subjectType=organization MUST have a did:web subject."""
    token = load_jwt("credentials/acme_o1.jwt")
    claims = decode_unverified_claims(token)
    assert claims["sub"].startswith("did:web:")
    assert claims["vc"]["credentialSubject"]["subjectType"] == "organization"


@pytest.mark.rfc("0003", section="JWT shape", requirement="all_required_claims_present")
def test_required_top_level_claims_present():
    required = {"iss", "sub", "iat", "exp", "jti", "shadownet:v", "vc"}
    for rel in CREDENTIAL_FIXTURES:
        claims = decode_unverified_claims(load_jwt(rel))
        missing = required - set(claims)
        assert not missing, f"{rel}: missing required claims: {missing}"


def test_schemas_directory_is_present(conformance_config):
    """If --specs-path is mis-configured the rest of the suite has no contract to test against."""
    schema_path = conformance_config.specs_path / "schemas"
    assert schema_path.is_dir(), (
        f"schemas/ not found under {conformance_config.specs_path}; "
        "set --specs-path or SHADOWNET_CONFORMANCE_SPECS_PATH to your shadownet-specs checkout"
    )


def test_fixture_files_resolve():
    """Sanity: the fixture loader can find every credential fixture cited above."""
    for rel in CREDENTIAL_FIXTURES:
        assert fixture_path(rel).is_file()
