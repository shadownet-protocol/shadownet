from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shadownet.errors import ShadownetError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from shadownet.vc.credential import SubjectCredential
    from shadownet.vc.presentation import VerifiedPresentation

# RFC-0004 §Required-level predicates. Grammar:
#   predicate ::= leaf | "all" | "any" | "not"
#   leaf      ::= { "level": <uri> } | { "issuer": <did> } | { "subjectType": "person"|"organization" }
#   all/any   ::= { "all"|"any": [predicate, ...] }   (≥1 child)
#   not       ::= { "not": predicate }
# Maximum depth: 4. Deeper predicates MUST be rejected as `predicate_too_deep`.

MAX_PREDICATE_DEPTH = 4

__all__ = [
    "MAX_PREDICATE_DEPTH",
    "AllPredicate",
    "AnyPredicate",
    "IssuerLeaf",
    "LevelLeaf",
    "NotPredicate",
    "PredicateTooDeep",
    "RequiredLevelPredicate",
    "SubjectTypeLeaf",
    "evaluate_predicate",
    "parse_predicate",
]


class PredicateTooDeep(ShadownetError):
    """A required-level predicate exceeded MAX_PREDICATE_DEPTH (RFC-0004)."""


@dataclass(frozen=True, slots=True)
class LevelLeaf:
    level: str


@dataclass(frozen=True, slots=True)
class IssuerLeaf:
    issuer: str


@dataclass(frozen=True, slots=True)
class SubjectTypeLeaf:
    subject_type: str  # "person" | "organization"


@dataclass(frozen=True, slots=True)
class AllPredicate:
    children: tuple[RequiredLevelPredicate, ...]


@dataclass(frozen=True, slots=True)
class AnyPredicate:
    children: tuple[RequiredLevelPredicate, ...]


@dataclass(frozen=True, slots=True)
class NotPredicate:
    child: RequiredLevelPredicate


RequiredLevelPredicate = (
    LevelLeaf | IssuerLeaf | SubjectTypeLeaf | AllPredicate | AnyPredicate | NotPredicate
)


def parse_predicate(value: object, *, _depth: int = 1) -> RequiredLevelPredicate:
    """Parse a JSON-decoded predicate dict into the typed AST. Validates depth."""
    if _depth > MAX_PREDICATE_DEPTH:
        raise PredicateTooDeep(f"predicate exceeds maximum depth of {MAX_PREDICATE_DEPTH}")
    if not isinstance(value, dict):
        raise ValueError(f"predicate must be a JSON object, got {type(value).__name__}")
    if len(value) != 1:
        raise ValueError("predicate object MUST have exactly one key")
    ((key, body),) = value.items()
    if key == "level":
        if not isinstance(body, str):
            raise ValueError("level leaf MUST be a string URI")
        return LevelLeaf(level=body)
    if key == "issuer":
        if not isinstance(body, str):
            raise ValueError("issuer leaf MUST be a string DID")
        return IssuerLeaf(issuer=body)
    if key == "subjectType":
        if body not in {"person", "organization"}:
            raise ValueError("subjectType leaf MUST be 'person' or 'organization'")
        return SubjectTypeLeaf(subject_type=body)
    if key in {"all", "any"}:
        if not isinstance(body, list) or not body:
            raise ValueError(f"'{key}' MUST contain a non-empty list of predicates")
        children = tuple(parse_predicate(child, _depth=_depth + 1) for child in body)
        return (AllPredicate if key == "all" else AnyPredicate)(children=children)
    if key == "not":
        return NotPredicate(child=parse_predicate(body, _depth=_depth + 1))
    raise ValueError(f"unknown predicate key {key!r}")


def evaluate_predicate(
    predicate: RequiredLevelPredicate,
    presentation: VerifiedPresentation,
) -> bool:
    """Return True iff ``presentation`` satisfies ``predicate`` per RFC-0004 §Evaluation.

    Inner credentials in ``presentation.credentials`` are already trust- and
    integrity-checked; this evaluator only matches them against leaves.
    """
    return _eval(predicate, presentation.credentials)


def _eval(p: RequiredLevelPredicate, credentials: Iterable[SubjectCredential]) -> bool:
    creds = list(credentials)
    match p:
        case LevelLeaf(level=level):
            return any(c.level == level for c in creds)
        case IssuerLeaf(issuer=issuer):
            return any(c.iss == issuer for c in creds)
        case SubjectTypeLeaf(subject_type=st):
            return any(c.subject_type == st for c in creds)
        case AllPredicate(children=children):
            return all(_eval(child, creds) for child in children)
        case AnyPredicate(children=children):
            return any(_eval(child, creds) for child in children)
        case NotPredicate(child=child):
            return not _eval(child, creds)
