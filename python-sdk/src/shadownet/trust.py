"""Trust store and acceptance policy — RFC 0001 §7.

A trust store is a flat list of ``(issuer-domain, accepted-kinds)`` tuples; an
acceptance policy is a pair of kind lists (``fromContact`` and ``fromStranger``
on the wire — RFC 0001 §2 names JSON keys camelCase). The §7.3 evaluation
predicate is implemented by :func:`is_credential_trusted`.

The reference policy ships with an empty trust store (RFC 0001 §7.1) and
stranger requirements of ``[org_affiliation]`` (RFC 0001 §7.2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shadownet.identifiers import (
    InvalidIdentifierError,
    IssuerIdentifier,
    canonicalize_issuer_or_org_identifier,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from shadownet.credential import VerifiedCredential

__all__ = [
    "DEFAULT_STRANGER_KINDS",
    "AcceptancePolicy",
    "TrustEntry",
    "TrustStore",
    "is_credential_trusted",
    "satisfies_policy",
]


DEFAULT_STRANGER_KINDS: tuple[str, ...] = ("org_affiliation",)


class TrustEntry(BaseModel):
    """One ``(issuer, accept)`` entry — RFC 0001 §7.1.

    ``issuer`` accepts a domain (e.g. ``acme.example``) or a multibase
    Ed25519 public key (keyed Hub, e.g. ``z6MkPeerHub...``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    issuer: IssuerIdentifier
    accept: tuple[str, ...] = Field(min_length=1)

    @field_validator("accept", mode="before")
    @classmethod
    def _normalize(cls, value: Iterable[str]) -> tuple[str, ...]:
        # Deduplicate while preserving order — configs stay readable.
        seen: list[str] = []
        for kind in value:
            if kind not in seen:
                seen.append(kind)
        return tuple(seen)


class TrustStore(BaseModel):
    """Flat list of TrustEntry — RFC 0001 §7.1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[TrustEntry, ...] = ()

    def accepts(self, issuer: str, kind: str) -> bool:
        # Domains canonicalize to lowercase; multibase pubkeys are
        # case-sensitive and pass through unchanged.
        try:
            target = canonicalize_issuer_or_org_identifier(issuer)
        except InvalidIdentifierError:
            return False
        return any(entry.issuer == target and kind in entry.accept for entry in self.entries)


class AcceptancePolicy(BaseModel):
    """RFC 0001 §7.2. Empty ``from_stranger`` means strangers are rejected.

    Wire keys are camelCase per RFC 0001 §2; Python attributes are snake_case.
    Round-trip via ``model_dump(by_alias=True)`` / ``model_validate(...)``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    from_contact: tuple[str, ...] = Field(default=(), alias="fromContact")
    from_stranger: tuple[str, ...] = Field(default=DEFAULT_STRANGER_KINDS, alias="fromStranger")

    def required_kinds(self, *, is_contact: bool) -> tuple[str, ...]:
        return self.from_contact if is_contact else self.from_stranger


def is_credential_trusted(
    credential: VerifiedCredential,
    trust_store: TrustStore,
) -> bool:
    return trust_store.accepts(credential.payload.iss, credential.payload.kind)


def satisfies_policy(
    credentials: Iterable[VerifiedCredential],
    trust_store: TrustStore,
    *,
    required_kinds: Iterable[str],
) -> bool:
    """§7.3 evaluation: a credential set satisfies when at least one credential
    is in the trust store AND covers one of the required kinds.

    Signature, lifetime, §6.6 authorization, and revocation must already have
    been checked.
    """
    required = set(required_kinds)
    if not required:
        return True
    for credential in credentials:
        if not is_credential_trusted(credential, trust_store):
            continue
        if credential.payload.kind in required:
            return True
    return False
