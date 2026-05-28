from __future__ import annotations

from shadownet.sca.predicate import (
    AffiliationLeaf,
    AllPredicate,
    LevelLeaf,
    parse_predicate,
)
from shadownet.trust import (
    DEFAULT_SUBSTITUTE_FOR_PERSONHOOD,
    InstitutionalEntry,
    InstitutionalPolicy,
    InstitutionalTrustStore,
)


def test_default_institutional_policy() -> None:
    p = InstitutionalPolicy()
    assert p.accept_domain_controlled is True
    assert p.substitute_for_personhood == DEFAULT_SUBSTITUTE_FOR_PERSONHOOD
    assert p.deny_listed is False


def test_institutional_store_accepts_default_did_web() -> None:
    s = InstitutionalTrustStore()
    assert s.accepts("did:web:any.example") is True


def test_institutional_store_denies_listed_org() -> None:
    s = InstitutionalTrustStore(
        overrides=(
            InstitutionalEntry(
                org="did:web:bad-actor.example",
                policy=InstitutionalPolicy(deny_listed=True),
            ),
        )
    )
    assert s.accepts("did:web:bad-actor.example") is False
    assert s.accepts("did:web:any.example") is True


def test_institutional_store_allowlist_override_with_substitution() -> None:
    s = InstitutionalTrustStore(
        default=InstitutionalPolicy(accept_domain_controlled=False),
        overrides=(
            InstitutionalEntry(
                org="did:web:acme.example",
                policy=InstitutionalPolicy(
                    accept_domain_controlled=True,
                    substitute_for_personhood="urn:shadownet:level:L2",
                ),
            ),
        ),
    )
    assert s.accepts("did:web:acme.example") is True
    assert s.accepts("did:web:other.example") is False
    assert (
        s.policy_for("did:web:acme.example").substitute_for_personhood == "urn:shadownet:level:L2"
    )


def test_parse_affiliation_leaf() -> None:
    p = parse_predicate({"affiliation": "did:web:acme.example"})
    assert p == AffiliationLeaf(affiliation="did:web:acme.example")


def test_parse_affiliation_leaf_rejects_non_did_web() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_predicate({"affiliation": "did:key:zXyz"})


def test_parse_composite_with_affiliation() -> None:
    p = parse_predicate(
        {
            "all": [
                {"level": "urn:shadownet:level:L1"},
                {"affiliation": "did:web:acme.example"},
            ]
        }
    )
    assert isinstance(p, AllPredicate)
    assert any(isinstance(c, LevelLeaf) for c in p.children)
    assert any(isinstance(c, AffiliationLeaf) for c in p.children)
