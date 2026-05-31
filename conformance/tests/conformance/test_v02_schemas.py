"""Schema validation for v0.2 wire artifacts — RFC 0001 §6.1, §6.5, §8.3.

Each test takes an artifact freshly minted by the v0.2 ``shadownet`` SDK and
validates it against the JSON Schema shipped alongside the spec. The schemas
are embedded under ``shadownet_conformance/_specs/`` so the suite runs from a
wheel without an external checkout. Tests are pure-Python; no live targets.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from shadownet.agentcard import (
    build_direct_signed_agent_card,
    build_signed_agent_card,
)
from shadownet.credential import (
    ORG_AFFILIATION,
    CredentialPayload,
    RevocationPointer,
    mint_credential,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import decode_unverified_claims
from shadownet.csr import CsrPayload, CsrRequest, mint_csr
from shadownet.envelope import EnvelopeBody, EnvelopePayload, mint_envelope
from shadownet.identifiers import encode_public_key

from shadownet_conformance.config import resolve_schemas_root


def _bundled_schemas() -> Path:
    pkg = Path(__file__).resolve().parents[2] / "src" / "shadownet_conformance" / "_specs"
    if pkg.is_dir():
        return pkg
    # Wheel layout.
    import shadownet_conformance

    return Path(shadownet_conformance.__file__).resolve().parent / "_specs"


def _validator(rel_path: str) -> Draft202012Validator:
    root = _bundled_schemas()
    import contextlib

    with contextlib.suppress(FileNotFoundError):
        # Honor an external --specs-path override if it points at a checkout.
        root = resolve_schemas_root(root.parent)
    schema = json.loads((root / rel_path).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _claims(jws: str) -> dict[str, Any]:
    return decode_unverified_claims(jws)


@pytest.mark.rfc("0001", section="6.1", requirement="credential schema")
def test_credential_payload_matches_schema() -> None:
    issuer_key = Ed25519KeyPair.generate()
    now = int(time.time())
    payload = CredentialPayload(
        iss="acme.example",
        sub="alice@sh4dow.org",
        kind=ORG_AFFILIATION,
        org="acme.example",
        iat=now,
        exp=now + 3600,
        rev=RevocationPointer(epoch="2026q2", idx=42),
    )
    jws = mint_credential(payload, issuer_key)
    _validator("credentials/credential.schema.json").validate(_claims(jws))


@pytest.mark.rfc("0001", section="6.1", requirement="credential accepts keyed iss")
def test_credential_with_keyed_issuer_matches_schema() -> None:
    key = Ed25519KeyPair.generate()
    pk = encode_public_key(key.public_bytes)
    now = int(time.time())
    payload = CredentialPayload(
        iss=pk,
        sub="alice@sh4dow.org",
        kind=ORG_AFFILIATION,
        org=pk,
        iat=now,
        exp=now + 3600,
        rev=RevocationPointer(epoch="2026q2", idx=1),
    )
    jws = mint_credential(payload, key)
    _validator("credentials/credential.schema.json").validate(_claims(jws))


@pytest.mark.rfc("0001", section="6.5", requirement="csr schema")
def test_csr_payload_matches_schema() -> None:
    subject_key = Ed25519KeyPair.generate()
    now = int(time.time())
    payload = CsrPayload(
        iss="alice@sh4dow.org",
        aud="acme.example",
        iat=now,
        exp=now + 300,
        req=CsrRequest(kind=ORG_AFFILIATION, org="acme.example"),
    )
    jws = mint_csr(payload, subject_key)
    _validator("credentials/csr.schema.json").validate(_claims(jws))


@pytest.mark.rfc("0001", section="8.3", requirement="envelope schema")
def test_envelope_payload_matches_schema() -> None:
    sender_key = Ed25519KeyPair.generate()
    now = int(time.time())
    payload = EnvelopePayload(
        v="0.2",
        sender="alice@sh4dow.org",
        recipient="bob@example.org",
        msg_hash="sha256:AbCdEfGh-_AAAA",
        iat=now,
        exp=now + 60,
        body=EnvelopeBody(text="Hi Bob"),
    )
    jws = mint_envelope(payload, sender_key)
    _validator("messages/envelope.schema.json").validate(_claims(jws))


@pytest.mark.rfc("0001", section="5.3", requirement="shadowname-mode agentcard extension")
def test_shadowname_agent_card_matches_extension_schema() -> None:
    provider_key = Ed25519KeyPair.generate()
    shadow_key = Ed25519KeyPair.generate()
    card = build_signed_agent_card(
        name="Alice",
        description="Alice's Shadow",
        version="1.0.0",
        a2a_url="https://shadow.sh4dow.org/v1/a2a/alice",
        shadow_public_key=encode_public_key(shadow_key.public_bytes),
        provider_key=provider_key,
        provider_domain="sh4dow.org",
    )
    _validator("agentcard/shadownet-extension.schema.json").validate(card)


@pytest.mark.rfc("0001", section="5.3", requirement="direct-mode agentcard extension")
def test_direct_agent_card_matches_extension_schema() -> None:
    shadow_key = Ed25519KeyPair.generate()
    card = build_direct_signed_agent_card(
        name="Bob",
        description="Bob's Shadow",
        version="1.0.0",
        a2a_url="https://bob-vps.example.com:8443/a2a",
        shadow_key=shadow_key,
    )
    _validator("agentcard/shadownet-extension.schema.json").validate(card)
