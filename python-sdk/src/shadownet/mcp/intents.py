"""Intent payload models — RFC 0002 §5.

The three v0.2 intent URIs and their ``body.data`` schemas:

  - ``urn:shadownet:intent:coordinate_v1``    — propose an activity.
  - ``urn:shadownet:intent:confirm_plan_v1``  — confirm a specific plan.
  - ``urn:shadownet:intent:accept_plan_v1``   — accept a peer's plan.

PlanObject and GeoCoordinate are the shared types referenced by the above.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ACCEPT_PLAN_V1_URI",
    "CONFIRM_PLAN_V1_URI",
    "COORDINATE_V1_URI",
    "AcceptPlanV1Data",
    "ConfirmPlanV1Data",
    "CoordinateV1Data",
    "GeoCoordinate",
    "PlanObject",
    "PlanWhere",
]


COORDINATE_V1_URI: Final = "urn:shadownet:intent:coordinate_v1"
CONFIRM_PLAN_V1_URI: Final = "urn:shadownet:intent:confirm_plan_v1"
ACCEPT_PLAN_V1_URI: Final = "urn:shadownet:intent:accept_plan_v1"


class GeoCoordinate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lat: float
    lon: float


class PlanWhere(BaseModel):
    """Location field of a PlanObject — all sub-fields optional."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    city: str | None = None
    type: str | None = None
    address: str | None = None
    name: str | None = None
    geo: GeoCoordinate | None = None


class PlanObject(BaseModel):
    """RFC 0002 §5.0 shared shape consumed by confirm_plan_v1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    activity: str
    when: str
    where: PlanWhere = Field(default_factory=PlanWhere)
    participants: tuple[str, ...]
    notes: str | None = None


class CoordinateV1Data(BaseModel):
    """``body.data`` for ``urn:shadownet:intent:coordinate_v1``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    activity: str
    details: str | None = None


class ConfirmPlanV1Data(PlanObject):
    """``body.data`` for ``urn:shadownet:intent:confirm_plan_v1``."""


class AcceptPlanV1Data(BaseModel):
    """``body.data`` for ``urn:shadownet:intent:accept_plan_v1``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepts_message_id: str = Field(alias="acceptsMessageId")
