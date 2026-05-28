from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

# RFC-0004 §Trust store, §Trust evaluation. The trust store has two
# conceptually distinct surfaces (RFC-0001 §Trust models): issuer trust for
# SubjectCredentials, and institutional trust for AffiliationCredentials.

__all__ = [
    "DEFAULT_SUBSTITUTE_FOR_PERSONHOOD",
    "InstitutionalEntry",
    "InstitutionalPolicy",
    "InstitutionalTrustStore",
    "TrustEntry",
    "TrustStore",
]

DEFAULT_SUBSTITUTE_FOR_PERSONHOOD = "urn:shadownet:level:L1"


class TrustEntry(BaseModel):
    """One ``(issuer DID, accepted levels)`` entry in a verifier's trust store."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    issuer: str = Field(pattern=r"^did:")
    accepted_levels: tuple[str, ...] = Field(alias="acceptedLevels")

    @field_validator("accepted_levels")
    @classmethod
    def _non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("acceptedLevels must contain at least one level URI")
        return value


class TrustStore(BaseModel):
    """A list of issuer/level entries the verifier accepts.

    Trust stores are local to each verifier. Per RFC-0004, there is no implicit
    level ordering — ``L2`` does not imply ``L1``.
    """

    model_config = ConfigDict(extra="forbid")

    entries: tuple[TrustEntry, ...] = ()

    @classmethod
    def from_pairs(cls, pairs: list[tuple[str, list[str]]]) -> TrustStore:
        return cls(
            entries=tuple(
                TrustEntry(issuer=issuer, accepted_levels=tuple(levels)) for issuer, levels in pairs
            )
        )

    def accepts(self, issuer: str, level: str) -> bool:
        for entry in self.entries:
            if entry.issuer == issuer and level in entry.accepted_levels:
                return True
        return False

    def issuers(self) -> tuple[str, ...]:
        return tuple(entry.issuer for entry in self.entries)


class InstitutionalPolicy(BaseModel):
    """Verifier policy applied to an organization's AffiliationCredentials.

    See RFC-0004 §Institutional trust. ``substitute_for_personhood`` is the
    personhood level URI an unsolicited affiliation substitutes for at the
    stranger-handshake gate; the empty string disables substitution.
    """

    model_config = ConfigDict(extra="forbid")

    accept_domain_controlled: bool = True
    substitute_for_personhood: str | None = DEFAULT_SUBSTITUTE_FOR_PERSONHOOD
    deny_listed: bool = False


class InstitutionalEntry(BaseModel):
    """Per-org override entry in :class:`InstitutionalTrustStore`."""

    model_config = ConfigDict(extra="forbid")

    org: str = Field(pattern=r"^did:web:")
    policy: InstitutionalPolicy


class InstitutionalTrustStore(BaseModel):
    """Institutional-trust surface of a verifier's trust store.

    Defaults are deliberately permissive — any did:web org whose document
    resolves is accepted at the L1 floor — mirroring the email model where a
    deliverable domain is the trust anchor. Allowlist overrides are typically
    used to grant a higher substitution level; denylist overrides reject
    regardless.
    """

    model_config = ConfigDict(extra="forbid")

    default: InstitutionalPolicy = InstitutionalPolicy()
    overrides: tuple[InstitutionalEntry, ...] = ()

    def policy_for(self, org: str) -> InstitutionalPolicy:
        for entry in self.overrides:
            if entry.org == org:
                return entry.policy
        return self.default

    def accepts(self, org: str) -> bool:
        policy = self.policy_for(org)
        if policy.deny_listed:
            return False
        if any(entry.org == org for entry in self.overrides):
            return True
        return policy.accept_domain_controlled
