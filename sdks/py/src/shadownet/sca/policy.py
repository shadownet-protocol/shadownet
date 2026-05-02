from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# RFC-0004 §Policy document.

__all__ = ["LevelPolicy", "SCAPolicy"]


class LevelPolicy(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    level: str = Field(pattern=r"^urn:")
    method: str = Field(pattern=r"^urn:")
    rate_limit: str | None = Field(default=None, alias="rateLimit")
    credential_lifetime_days: int | None = Field(default=None, alias="credentialLifetimeDays", ge=1)


class SCAPolicy(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    issuer: str = Field(pattern=r"^did:")
    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    levels: tuple[LevelPolicy, ...]
    freshness_window_seconds: int = Field(alias="freshnessWindowSeconds", ge=1)
    status_list_base: str = Field(alias="statusListBase")

    def method_for(self, level: str) -> str | None:
        for entry in self.levels:
            if entry.level == level:
                return entry.method
        return None
