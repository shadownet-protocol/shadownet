# RFC-0004 §Required-level predicates

"""Predicate evaluator conformance tests.

These tests are pure logic: they run with no targets configured and exercise
the predicate AST defined by RFC-0004 against committed credential fixtures.

If any conformance impl evaluates the same `(predicate, presentation)` pair
to a different boolean, the impl is wrong and these tests must catch it.
"""

from __future__ import annotations

import pytest
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
from shadownet.vc.credential import SubjectCredential, decode_credential
from shadownet.vc.presentation import VerifiablePresentation, VerifiedPresentation

from shadownet_conformance.fixtures import load_jwt

L1 = "urn:shadownet:level:L1"
L2 = "urn:shadownet:level:L2"
L3 = "urn:shadownet:level:L3"
O1 = "urn:shadownet:level:O1"
SCA = "did:web:sca.shadownet.example"
OTHER_SCA = "did:web:other-sca.example"


def _credential(rel_path: str) -> SubjectCredential:
    return decode_credential(load_jwt(rel_path))


def _vp(*credentials: SubjectCredential) -> VerifiedPresentation:
    """Build a synthetic VerifiedPresentation around already-decoded credentials.

    The predicate evaluator only inspects ``credentials``; the outer
    ``presentation`` value is required by the dataclass shape but is otherwise
    not consulted by predicate evaluation.
    """
    # The model requires at least one verifiableCredential; we pass a known
    # fixture JWT to satisfy validation. The evaluator does not parse it.
    placeholder = VerifiablePresentation.model_validate(
        {
            "iss": credentials[0].sub,
            "aud": "did:key:z6Mkpredicate-test",
            "iat": 0,
            "exp": 60,
            "nonce": "00",
            "vp": {
                "@context": ["https://www.w3.org/ns/credentials/v2"],
                "type": ["VerifiablePresentation"],
                "verifiableCredential": [load_jwt("credentials/alice_l1.jwt")],
            },
        }
    )
    return VerifiedPresentation(
        holder_did=credentials[0].sub,
        credentials=tuple(credentials),
        freshness_proofs=(),
        presentation=placeholder,
    )


@pytest.fixture
def alice_l2() -> SubjectCredential:
    return _credential("credentials/alice_l2.jwt")


@pytest.fixture
def alice_l1() -> SubjectCredential:
    return _credential("credentials/alice_l1.jwt")


@pytest.fixture
def bob_l1() -> SubjectCredential:
    return _credential("credentials/bob_l1.jwt")


@pytest.fixture
def acme_o1() -> SubjectCredential:
    return _credential("credentials/acme_o1.jwt")


# ---------------------------------------------------------------------------
# Leaf evaluation
# ---------------------------------------------------------------------------


@pytest.mark.rfc("0004", section="Predicate", requirement="leaf_level_match")
def test_level_leaf_matches_when_present(alice_l2: SubjectCredential):
    """RFC-0004 §Evaluation: a level leaf is satisfied iff at least one credential matches."""
    assert evaluate_predicate(LevelLeaf(level=L2), _vp(alice_l2)) is True


@pytest.mark.rfc("0004", section="Predicate", requirement="leaf_level_no_match")
def test_level_leaf_fails_when_absent(alice_l2: SubjectCredential):
    assert evaluate_predicate(LevelLeaf(level=L3), _vp(alice_l2)) is False


@pytest.mark.rfc("0004", section="Predicate", requirement="leaf_no_level_implication")
def test_l2_does_not_imply_l1(alice_l2: SubjectCredential):
    """RFC-0004 §Evaluation: L2 does NOT imply L1; verifiers MUST enumerate via 'any'."""
    assert evaluate_predicate(LevelLeaf(level=L1), _vp(alice_l2)) is False


@pytest.mark.rfc("0004", section="Predicate", requirement="leaf_issuer_match")
def test_issuer_leaf(alice_l2: SubjectCredential):
    assert evaluate_predicate(IssuerLeaf(issuer=SCA), _vp(alice_l2)) is True
    assert evaluate_predicate(IssuerLeaf(issuer=OTHER_SCA), _vp(alice_l2)) is False


@pytest.mark.rfc("0004", section="Predicate", requirement="leaf_subject_type_match")
def test_subject_type_leaf(alice_l2: SubjectCredential, acme_o1: SubjectCredential):
    assert evaluate_predicate(SubjectTypeLeaf(subject_type="person"), _vp(alice_l2)) is True
    assert evaluate_predicate(SubjectTypeLeaf(subject_type="organization"), _vp(alice_l2)) is False
    assert evaluate_predicate(SubjectTypeLeaf(subject_type="organization"), _vp(acme_o1)) is True


# ---------------------------------------------------------------------------
# Combinators
# ---------------------------------------------------------------------------


@pytest.mark.rfc("0004", section="Predicate", requirement="any_satisfied_by_first")
def test_any_short_circuits_on_match(alice_l2: SubjectCredential):
    pred = AnyPredicate(children=(LevelLeaf(level=L2), LevelLeaf(level=L3)))
    assert evaluate_predicate(pred, _vp(alice_l2)) is True


@pytest.mark.rfc("0004", section="Predicate", requirement="any_unsatisfied")
def test_any_unsatisfied(alice_l1: SubjectCredential):
    pred = AnyPredicate(children=(LevelLeaf(level=L2), LevelLeaf(level=L3)))
    assert evaluate_predicate(pred, _vp(alice_l1)) is False


@pytest.mark.rfc("0004", section="Predicate", requirement="all_unsatisfied_when_one_missing")
def test_all_requires_every_child(alice_l2: SubjectCredential):
    pred = AllPredicate(children=(LevelLeaf(level=L2), IssuerLeaf(issuer=OTHER_SCA)))
    assert evaluate_predicate(pred, _vp(alice_l2)) is False


@pytest.mark.rfc("0004", section="Predicate", requirement="all_can_combine_credentials")
def test_all_can_be_satisfied_by_distinct_credentials(
    alice_l2: SubjectCredential, acme_o1: SubjectCredential
):
    """RFC-0004 §Evaluation: every Rᵢ in 'all' MAY be satisfied by a different credential."""
    pred = AllPredicate(
        children=(
            LevelLeaf(level=L2),
            SubjectTypeLeaf(subject_type="organization"),
        )
    )
    assert evaluate_predicate(pred, _vp(alice_l2, acme_o1)) is True


@pytest.mark.rfc("0004", section="Predicate", requirement="not_inverts")
def test_not_inverts(alice_l2: SubjectCredential):
    assert evaluate_predicate(NotPredicate(child=LevelLeaf(level=L3)), _vp(alice_l2)) is True
    assert evaluate_predicate(NotPredicate(child=LevelLeaf(level=L2)), _vp(alice_l2)) is False


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.rfc("0004", section="Predicate", requirement="parse_leaf")
def test_parse_level_leaf():
    assert parse_predicate({"level": L2}) == LevelLeaf(level=L2)


@pytest.mark.rfc("0004", section="Predicate", requirement="parse_any")
def test_parse_any():
    parsed = parse_predicate({"any": [{"level": L2}, {"level": L3}]})
    assert isinstance(parsed, AnyPredicate)
    assert parsed.children == (LevelLeaf(level=L2), LevelLeaf(level=L3))


@pytest.mark.rfc("0004", section="Predicate", requirement="parse_rejects_empty_combinator")
def test_parse_rejects_empty_any():
    with pytest.raises(ValueError, match="non-empty"):
        parse_predicate({"any": []})


@pytest.mark.rfc("0004", section="Predicate", requirement="parse_rejects_unknown_key")
def test_parse_rejects_unknown_key():
    with pytest.raises(ValueError, match="unknown predicate key"):
        parse_predicate({"someday": L2})


@pytest.mark.rfc("0004", section="Predicate", requirement="parse_rejects_multi_key")
def test_parse_rejects_multiple_keys():
    with pytest.raises(ValueError, match="exactly one key"):
        parse_predicate({"level": L2, "issuer": SCA})


@pytest.mark.rfc("0004", section="Predicate", requirement="depth_limit")
def test_depth_5_rejected_as_too_deep():
    """RFC-0004: max depth 4. Depth 5 MUST be rejected as predicate_too_deep."""
    # Build: not(not(not(not(not(level)))))  — 5 levels of nesting
    payload: object = {"level": L2}
    for _ in range(MAX_PREDICATE_DEPTH):
        payload = {"not": payload}
    with pytest.raises(PredicateTooDeep):
        parse_predicate(payload)


@pytest.mark.rfc("0004", section="Predicate", requirement="depth_limit_max_ok")
def test_depth_4_accepted():
    payload: object = {"level": L2}
    for _ in range(MAX_PREDICATE_DEPTH - 1):
        payload = {"not": payload}
    parse_predicate(payload)  # MUST NOT raise


# ---------------------------------------------------------------------------
# RFC-0004 example predicates (golden assertions against committed fixtures)
# ---------------------------------------------------------------------------


@pytest.mark.rfc("0004", section="Predicate", requirement="example_l2_or_l3")
def test_example_l2_or_l3(alice_l2: SubjectCredential, alice_l1: SubjectCredential):
    pred = parse_predicate({"any": [{"level": L2}, {"level": L3}]})
    assert evaluate_predicate(pred, _vp(alice_l2)) is True
    assert evaluate_predicate(pred, _vp(alice_l1)) is False


@pytest.mark.rfc("0004", section="Predicate", requirement="example_org_credential")
def test_example_organization_credential(acme_o1: SubjectCredential, alice_l2: SubjectCredential):
    pred = parse_predicate({"subjectType": "organization"})
    assert evaluate_predicate(pred, _vp(acme_o1)) is True
    assert evaluate_predicate(pred, _vp(alice_l2)) is False


@pytest.mark.rfc("0004", section="Predicate", requirement="example_l2_specific_issuer")
def test_example_l2_from_specific_issuer(alice_l2: SubjectCredential):
    pred = parse_predicate({"all": [{"level": L2}, {"issuer": SCA}]})
    assert evaluate_predicate(pred, _vp(alice_l2)) is True
    pred_other = parse_predicate({"all": [{"level": L2}, {"issuer": OTHER_SCA}]})
    assert evaluate_predicate(pred_other, _vp(alice_l2)) is False
