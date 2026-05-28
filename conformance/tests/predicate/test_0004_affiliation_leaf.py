# RFC-0004 §Required-level predicates §Affiliation leaf

"""Conformance tests for the new ``{"affiliation": <did:web>}`` predicate leaf.

These tests are pure logic: they construct AffiliationCredentials in memory
(rather than load fixture JWTs) so they don't depend on the cross-SDK
regen pipeline being extended. The wire-level fixture tests will follow
once the regen CLI ships an affiliation_credential kind.
"""

from __future__ import annotations

import time
import uuid

import pytest
from shadownet.sca.predicate import (
    AffiliationLeaf,
    AllPredicate,
    AnyPredicate,
    LevelLeaf,
    NotPredicate,
    evaluate_predicate,
    parse_predicate,
)
from shadownet.vc.affiliation import AffiliationCredential
from shadownet.vc.credential import SubjectCredential
from shadownet.vc.presentation import VerifiablePresentation, VerifiedPresentation

L1 = "urn:shadownet:level:L1"
ACME = "did:web:acme.example"
GLOBEX = "did:web:globex.example"


def _aff(affiliation: str = ACME, subject: str = "did:key:z6MkAlice") -> AffiliationCredential:
    now = int(time.time())
    return AffiliationCredential.model_validate(
        {
            "iss": affiliation,
            "sub": subject,
            "iat": now,
            "exp": now + 7 * 24 * 3600,
            "jti": f"urn:uuid:{uuid.uuid4()}",
            "shadownet:v": "0.1",
            "vc": {
                "@context": [
                    "https://www.w3.org/ns/credentials/v2",
                    "https://sh4dow.org/contexts/v1",
                ],
                "type": ["VerifiableCredential", "ShadownetAffiliationCredential"],
                "credentialSubject": {"id": subject, "affiliation": affiliation},
            },
        }
    )


def _subj(level: str = L1, subject: str = "did:key:z6MkAlice") -> SubjectCredential:
    now = int(time.time())
    return SubjectCredential.model_validate(
        {
            "iss": "did:web:sca.sh4dow.org",
            "sub": subject,
            "iat": now,
            "exp": now + 7 * 24 * 3600,
            "jti": f"urn:uuid:{uuid.uuid4()}",
            "shadownet:v": "0.1",
            "vc": {
                "@context": [
                    "https://www.w3.org/ns/credentials/v2",
                    "https://sh4dow.org/contexts/v1",
                ],
                "type": ["VerifiableCredential", "ShadownetSubjectCredential"],
                "credentialSubject": {"id": subject, "level": level, "subjectType": "person"},
            },
        }
    )


def _vp(
    *,
    subjects: tuple[SubjectCredential, ...] = (),
    affiliations: tuple[AffiliationCredential, ...] = (),
) -> VerifiedPresentation:
    holder = subjects[0].sub if subjects else (affiliations[0].sub if affiliations else "did:key:z")
    placeholder = VerifiablePresentation.model_validate(
        {
            "iss": holder,
            "aud": "did:key:z6Mkpredicate-test",
            "iat": 0,
            "exp": 60,
            "nonce": "00",
            "vp": {
                "@context": ["https://www.w3.org/ns/credentials/v2"],
                "type": ["VerifiablePresentation"],
                "verifiableCredential": ["placeholder.jwt"],
            },
        }
    )
    return VerifiedPresentation(
        holder_did=holder,
        credentials=subjects,
        affiliations=affiliations,
        freshness_proofs=(),
        presentation=placeholder,
    )


@pytest.mark.rfc("0004", section="Predicate", requirement="leaf_affiliation_match")
@pytest.mark.affiliation
def test_affiliation_leaf_matches_when_present() -> None:
    pres = _vp(affiliations=(_aff(affiliation=ACME),))
    assert evaluate_predicate(AffiliationLeaf(affiliation=ACME), pres) is True


@pytest.mark.rfc("0004", section="Predicate", requirement="leaf_affiliation_no_match")
@pytest.mark.affiliation
def test_affiliation_leaf_fails_when_absent() -> None:
    pres = _vp(affiliations=(_aff(affiliation=GLOBEX),))
    assert evaluate_predicate(AffiliationLeaf(affiliation=ACME), pres) is False


@pytest.mark.rfc("0004", section="Predicate", requirement="leaf_affiliation_ignores_subjects")
@pytest.mark.affiliation
def test_affiliation_leaf_does_not_match_against_subject_credentials() -> None:
    # An affiliation leaf MUST NOT match against SubjectCredentials, even if a
    # SubjectCredential's issuer DID happens to equal the org DID under test.
    pres = _vp(subjects=(_subj(level=L1),), affiliations=())
    assert evaluate_predicate(AffiliationLeaf(affiliation=ACME), pres) is False


@pytest.mark.rfc("0004", section="Predicate", requirement="parse_affiliation_leaf")
@pytest.mark.affiliation
def test_parse_affiliation_leaf_from_json() -> None:
    p = parse_predicate({"affiliation": ACME})
    assert p == AffiliationLeaf(affiliation=ACME)


@pytest.mark.rfc("0004", section="Predicate", requirement="composite_level_and_affiliation")
@pytest.mark.affiliation
def test_all_combines_level_and_affiliation() -> None:
    pres = _vp(
        subjects=(_subj(level=L1),),
        affiliations=(_aff(affiliation=ACME),),
    )
    pred = AllPredicate(
        children=(LevelLeaf(level=L1), AffiliationLeaf(affiliation=ACME)),
    )
    assert evaluate_predicate(pred, pres) is True
    pred_missing = AllPredicate(
        children=(LevelLeaf(level=L1), AffiliationLeaf(affiliation=GLOBEX)),
    )
    assert evaluate_predicate(pred_missing, pres) is False


@pytest.mark.rfc("0004", section="Predicate", requirement="any_admits_either_path")
@pytest.mark.affiliation
def test_any_admits_either_subject_or_affiliation() -> None:
    pres = _vp(affiliations=(_aff(affiliation=ACME),))
    pred = AnyPredicate(children=(LevelLeaf(level=L1), AffiliationLeaf(affiliation=ACME)))
    assert evaluate_predicate(pred, pres) is True


@pytest.mark.rfc("0004", section="Predicate", requirement="not_excludes_denied_orgs")
@pytest.mark.affiliation
def test_not_excludes_denied_orgs() -> None:
    pres = _vp(affiliations=(_aff(affiliation=GLOBEX),))
    pred = NotPredicate(child=AffiliationLeaf(affiliation=ACME))
    assert evaluate_predicate(pred, pres) is True
