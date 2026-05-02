from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

# RFC-0004 §Trust store, §Trust evaluation.

__all__ = ["TrustEntry", "TrustStore"]


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
                TrustEntry(issuer=issuer, acceptedLevels=tuple(levels)) for issuer, levels in pairs
            )
        )

    def accepts(self, issuer: str, level: str) -> bool:
        for entry in self.entries:
            if entry.issuer == issuer and level in entry.accepted_levels:
                return True
        return False

    def issuers(self) -> tuple[str, ...]:
        return tuple(entry.issuer for entry in self.entries)
