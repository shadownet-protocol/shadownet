from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from shadownet.credential import (
    ORG_AFFILIATION,
    CredentialPayload,
    RevocationPointer,
    VerifiedCredential,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key
from shadownet.trust import (
    DEFAULT_STRANGER_KINDS,
    AcceptancePolicy,
    TrustEntry,
    TrustStore,
    is_credential_trusted,
    satisfies_policy,
)


def _credential(*, iss: str = "acme.example", kind: str = ORG_AFFILIATION) -> VerifiedCredential:
    now = int(time.time())
    payload = CredentialPayload(
        iss=iss,
        sub="alice@sh4dow.org",
        kind=kind,
        org=iss,
        iat=now,
        exp=now + 3600,
        rev=RevocationPointer(epoch="e", idx=0),
    )
    return VerifiedCredential(
        payload=payload,
        issuer_key=encode_public_key(Ed25519KeyPair.generate().public_bytes),
        raw_jws="header.payload.sig",
    )


class TestTrustEntry:
    def test_dedup(self) -> None:
        entry = TrustEntry(issuer="acme.example", accept=("org_affiliation", "org_affiliation"))
        assert entry.accept == ("org_affiliation",)

    def test_requires_at_least_one_kind(self) -> None:
        with pytest.raises(ValidationError):
            TrustEntry(issuer="acme.example", accept=())


class TestTrustStore:
    def test_default_empty(self) -> None:
        store = TrustStore()
        assert store.entries == ()
        assert is_credential_trusted(_credential(), store) is False

    def test_accepts_matching_entry(self) -> None:
        store = TrustStore(
            entries=(TrustEntry(issuer="acme.example", accept=("org_affiliation",)),)
        )
        assert is_credential_trusted(_credential(), store) is True

    def test_rejects_wrong_issuer(self) -> None:
        store = TrustStore(
            entries=(TrustEntry(issuer="other.example", accept=("org_affiliation",)),)
        )
        assert is_credential_trusted(_credential(), store) is False

    def test_rejects_wrong_kind(self) -> None:
        store = TrustStore(entries=(TrustEntry(issuer="acme.example", accept=("freshness",)),))
        assert is_credential_trusted(_credential(), store) is False

    def test_issuer_case_insensitive(self) -> None:
        store = TrustStore(
            entries=(TrustEntry(issuer="Acme.Example", accept=("org_affiliation",)),)
        )
        assert is_credential_trusted(_credential(), store) is True


class TestAcceptancePolicy:
    def test_default_from_stranger(self) -> None:
        policy = AcceptancePolicy()
        assert policy.from_stranger == DEFAULT_STRANGER_KINDS
        assert policy.from_contact == ()

    def test_required_kinds_routing(self) -> None:
        policy = AcceptancePolicy(fromContact=(), fromStranger=("org_affiliation",))
        assert policy.required_kinds(is_contact=True) == ()
        assert policy.required_kinds(is_contact=False) == ("org_affiliation",)

    def test_wire_alias_roundtrip(self) -> None:
        # JSON keys are camelCase per RFC 0001 §2.
        wire = {"fromContact": ["org_affiliation"], "fromStranger": []}
        policy = AcceptancePolicy.model_validate(wire)
        assert policy.from_contact == ("org_affiliation",)
        assert policy.from_stranger == ()
        assert policy.model_dump(by_alias=True) == {
            "fromContact": ("org_affiliation",),
            "fromStranger": (),
        }


class TestSatisfiesPolicy:
    def test_no_required_kinds_passes(self) -> None:
        assert satisfies_policy([], TrustStore(), required_kinds=()) is True

    def test_matching_credential_satisfies(self) -> None:
        store = TrustStore(
            entries=(TrustEntry(issuer="acme.example", accept=("org_affiliation",)),)
        )
        assert satisfies_policy([_credential()], store, required_kinds=("org_affiliation",)) is True

    def test_untrusted_issuer_fails(self) -> None:
        store = TrustStore(
            entries=(TrustEntry(issuer="other.example", accept=("org_affiliation",)),)
        )
        assert (
            satisfies_policy([_credential()], store, required_kinds=("org_affiliation",)) is False
        )

    def test_kind_mismatch_fails(self) -> None:
        store = TrustStore(entries=(TrustEntry(issuer="acme.example", accept=("future_kind",)),))
        assert satisfies_policy([_credential()], store, required_kinds=("future_kind",)) is False

    def test_one_match_in_set_satisfies(self) -> None:
        store = TrustStore(
            entries=(TrustEntry(issuer="acme.example", accept=("org_affiliation",)),)
        )
        creds = [
            _credential(iss="other.example"),
            _credential(),
        ]
        assert satisfies_policy(creds, store, required_kinds=("org_affiliation",)) is True
