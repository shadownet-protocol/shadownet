"""Credential mint/verify wire path — RFC 0001 §6.

Round-trip tests for the v0.2 credential JWT shape with both domain-issuer
and keyed-issuer flavors. Issuer-key resolution and §6.6 authorization checks
are injected to keep these tests pure-Python (no DNS, no AgentCard fetch).
"""

from __future__ import annotations

import time

import pytest
from shadownet.credential import (
    ORG_AFFILIATION,
    CredentialError,
    CredentialPayload,
    RevocationPointer,
    mint_credential,
    verify_credential,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key


def _resolver(pk: str):
    return lambda _: pk


def _authorize_ok(_iss: str, _org: str) -> None:
    return None


@pytest.mark.rfc("0001", section="6.1", requirement="org_affiliation round-trip")
def test_org_affiliation_round_trip() -> None:
    issuer_key = Ed25519KeyPair.generate()
    issuer_pk = encode_public_key(issuer_key.public_bytes)
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
    verified = verify_credential(
        jws,
        resolve_issuer_key=_resolver(issuer_pk),
        check_issuer_authorized_for_org=_authorize_ok,
    )
    assert verified.payload.sub == "alice@sh4dow.org"
    assert verified.payload.kind == ORG_AFFILIATION


@pytest.mark.rfc("0001", section="6.6", requirement="keyed iss MUST equal keyed org")
def test_keyed_issuer_rule_1_only() -> None:
    hub_key = Ed25519KeyPair.generate()
    hub_pk = encode_public_key(hub_key.public_bytes)
    now = int(time.time())
    payload = CredentialPayload(
        iss=hub_pk,
        sub="alice@sh4dow.org",
        kind=ORG_AFFILIATION,
        org=hub_pk,
        iat=now,
        exp=now + 3600,
        rev=RevocationPointer(epoch="e", idx=0),
    )
    jws = mint_credential(payload, hub_key)
    verify_credential(
        jws,
        resolve_issuer_key=lambda iss: iss,  # keyed iss is the verification key
        check_issuer_authorized_for_org=lambda iss, org: (
            None if iss == org else (_ for _ in ()).throw(CredentialError("not authorized"))
        ),
    )


@pytest.mark.rfc("0001", section="6.3", requirement="org_affiliation lifetime <= 30 days")
def test_lifetime_over_30_days_rejected() -> None:
    issuer_key = Ed25519KeyPair.generate()
    now = int(time.time())
    payload = CredentialPayload(
        iss="acme.example",
        sub="alice@sh4dow.org",
        kind=ORG_AFFILIATION,
        org="acme.example",
        iat=now,
        exp=now + 31 * 24 * 60 * 60,
        rev=RevocationPointer(epoch="e", idx=0),
    )
    with pytest.raises(CredentialError, match="30 days"):
        mint_credential(payload, issuer_key)


@pytest.mark.rfc("0001", section="6", requirement="unknown kind rejected")
def test_unknown_kind_rejected_at_verify() -> None:
    from shadownet.crypto.jwt import sign_jwt

    issuer_key = Ed25519KeyPair.generate()
    issuer_pk = encode_public_key(issuer_key.public_bytes)
    now = int(time.time())
    claims = {
        "iss": "acme.example",
        "sub": "alice@sh4dow.org",
        "kind": "personhood",  # v0.1 kind, no longer admissible
        "org": "acme.example",
        "iat": now,
        "exp": now + 3600,
        "rev": {"epoch": "e", "idx": 0},
    }
    jws = sign_jwt(claims, issuer_key, header_extras={"typ": "shadownet-cred+jwt"})
    with pytest.raises(CredentialError, match="unknown credential kind"):
        verify_credential(
            jws,
            resolve_issuer_key=_resolver(issuer_pk),
            check_issuer_authorized_for_org=_authorize_ok,
        )
