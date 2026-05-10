from __future__ import annotations

import pytest
from pydantic import ValidationError

from shadownet.trust import TrustEntry, TrustStore


def test_accepts_listed_pair() -> None:
    store = TrustStore.from_pairs(
        [
            (
                "did:web:sca.example",
                ["urn:shadownet:level:L1", "urn:shadownet:level:L2"],
            )
        ]
    )
    assert store.accepts("did:web:sca.example", "urn:shadownet:level:L1")
    assert store.accepts("did:web:sca.example", "urn:shadownet:level:L2")
    assert not store.accepts("did:web:sca.example", "urn:shadownet:level:L3")
    assert not store.accepts("did:web:other.example", "urn:shadownet:level:L1")


def test_levels_are_not_implicitly_ordered() -> None:
    # RFC-0004 §Evaluation: L2 does NOT imply L1.
    store = TrustStore.from_pairs([("did:web:sca.example", ["urn:shadownet:level:L2"])])
    assert not store.accepts("did:web:sca.example", "urn:shadownet:level:L1")


def test_entry_requires_at_least_one_level() -> None:
    with pytest.raises(ValidationError):
        TrustEntry(issuer="did:web:sca.example", acceptedLevels=())


def test_entry_requires_did_iss() -> None:
    with pytest.raises(ValidationError):
        TrustEntry(issuer="not-a-did", acceptedLevels=("urn:shadownet:level:L1",))


def test_issuers_listing() -> None:
    store = TrustStore.from_pairs(
        [
            ("did:web:a.example", ["urn:shadownet:level:L1"]),
            ("did:web:b.example", ["urn:shadownet:level:L2"]),
        ]
    )
    assert store.issuers() == ("did:web:a.example", "did:web:b.example")
