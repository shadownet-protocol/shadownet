from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shadownet_conformance.errors import ConformanceError

if TYPE_CHECKING:
    from pathlib import Path

SEED_BYTES = 32


class ManifestError(ConformanceError):
    """Raised when seeds.toml or manifest.toml are malformed."""


class Seed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hex: str

    @field_validator("hex")
    @classmethod
    def _hex_is_32_bytes(cls, value: str) -> str:
        try:
            raw = bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError(f"seed hex is not valid hex: {exc}") from exc
        if len(raw) != SEED_BYTES:
            raise ValueError(f"seed hex must decode to {SEED_BYTES} bytes, got {len(raw)}")
        return value.lower()

    def to_bytes(self) -> bytes:
        return bytes.fromhex(self.hex)


class SeedsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    seeds: dict[str, Seed]


FixtureKind = Literal["key", "credential", "freshness", "presentation", "sns_record", "status_list"]


class FixtureEntry(BaseModel):
    """One declarative fixture entry from manifest.toml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: FixtureKind
    out: str
    spec: dict[str, Any]


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    fixtures: Annotated[list[FixtureEntry], Field(min_length=1)]


def load_seeds(path: Path) -> dict[str, bytes]:
    """Parse seeds.toml and return a {name: 32-byte seed} dict."""
    raw = _read_toml(path)
    parsed = SeedsFile.model_validate(raw)
    return {name: seed.to_bytes() for name, seed in parsed.seeds.items()}


def load_manifest(path: Path) -> Manifest:
    """Parse the fixture manifest at the given path."""
    raw = _read_toml(path)
    return Manifest.model_validate(raw)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ManifestError(f"file not found: {path}")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"invalid TOML at {path}: {exc}") from exc
