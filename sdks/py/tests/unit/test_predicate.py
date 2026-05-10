from __future__ import annotations

import time

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.did.key import derive_did_key
from shadownet.did.resolver import Resolver
from shadownet.sca.predicate import (
    MAX_PREDICATE_DEPTH,
    AllPredicate,
    AnyPredicate,
    IssuerLeaf,
    LevelLeaf,
    NotPredicate,
    PredicateTooDeep,
    SubjectTypeLeaf,
    evaluate_predicate,
    parse_predicate,
)
from shadownet.vc.credential import issue_credential, new_credential
from shadownet.vc.presentation import mint_presentation, verify_presentation


def _issuer():
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


def _subject():
    kp = Ed25519KeyPair.generate()
    return kp, derive_did_key(kp.public_bytes)


def test_parse_level_leaf() -> None:
    p = parse_predicate({"level": "urn:shadownet:level:L2"})
    assert p == LevelLeaf(level="urn:shadownet:level:L2")


def test_parse_subject_type_leaf() -> None:
    p = parse_predicate({"subjectType": "organization"})
    assert p == SubjectTypeLeaf(subject_type="organization")


def test_parse_issuer_leaf() -> None:
    p = parse_predicate({"issuer": "did:web:sca.example"})
    assert p == IssuerLeaf(issuer="did:web:sca.example")


def test_parse_compound_all() -> None:
    p = parse_predicate(
        {
            "all": [
                {"level": "urn:shadownet:level:L2"},
                {"issuer": "did:web:sca.example"},
            ]
        }
    )
    assert isinstance(p, AllPredicate)
    assert len(p.children) == 2


def test_parse_not() -> None:
    p = parse_predicate({"not": {"level": "urn:shadownet:level:L1"}})
    assert isinstance(p, NotPredicate)
    assert p.child == LevelLeaf(level="urn:shadownet:level:L1")


def test_parse_rejects_unknown_key() -> None:
    with pytest.raises(ValueError):
        parse_predicate({"unknown": "x"})


def test_parse_rejects_invalid_subject_type() -> None:
    with pytest.raises(ValueError):
        parse_predicate({"subjectType": "robot"})


def test_parse_rejects_empty_compound() -> None:
    with pytest.raises(ValueError):
        parse_predicate({"all": []})


def test_parse_rejects_multi_key_object() -> None:
    with pytest.raises(ValueError):
        parse_predicate({"level": "x", "issuer": "y"})


def test_parse_depth_limit() -> None:
    # Build a deeply-nested 'not' predicate exceeding MAX_PREDICATE_DEPTH.
    leaf: object = {"level": "urn:shadownet:level:L1"}
    for _ in range(MAX_PREDICATE_DEPTH + 2):
        leaf = {"not": leaf}
    with pytest.raises(PredicateTooDeep):
        parse_predicate(leaf)


def test_parse_depth_at_limit_ok() -> None:
    # exactly MAX_PREDICATE_DEPTH levels should succeed.
    leaf: object = {"level": "urn:shadownet:level:L1"}
    for _ in range(MAX_PREDICATE_DEPTH - 1):
        leaf = {"not": leaf}
    parse_predicate(leaf)


async def test_evaluate_against_real_presentation() -> None:
    issuer_kp, issuer_did = _issuer()
    subject_kp, subject_did = _subject()
    verifier = derive_did_key(Ed25519KeyPair.generate().public_bytes)

    cred = new_credential(
        issuer=issuer_did,
        subject=subject_did,
        level="urn:shadownet:level:L2",
        subject_type="person",
        issued_at=int(time.time()),
    )
    cred_jwt = issue_credential(issuer_key=issuer_kp, issuer_kid=issuer_did, credential=cred)
    vp_jwt = mint_presentation(
        holder_key=subject_kp,
        holder_did=subject_did,
        audience_did=verifier,
        credentials=[cred_jwt],
    )
    presentation = await verify_presentation(
        vp_jwt, resolver=Resolver(), expected_audience=verifier
    )

    assert evaluate_predicate(LevelLeaf(level="urn:shadownet:level:L2"), presentation)
    assert not evaluate_predicate(LevelLeaf(level="urn:shadownet:level:L3"), presentation)
    assert evaluate_predicate(
        AnyPredicate(
            children=(
                LevelLeaf(level="urn:shadownet:level:L2"),
                LevelLeaf(level="urn:shadownet:level:L3"),
            )
        ),
        presentation,
    )
    assert evaluate_predicate(
        AllPredicate(
            children=(
                LevelLeaf(level="urn:shadownet:level:L2"),
                IssuerLeaf(issuer=issuer_did),
            )
        ),
        presentation,
    )
    assert evaluate_predicate(
        NotPredicate(child=SubjectTypeLeaf(subject_type="organization")),
        presentation,
    )
    # L2 does NOT imply L1 — predicate for L1 alone should fail.
    assert not evaluate_predicate(LevelLeaf(level="urn:shadownet:level:L1"), presentation)
