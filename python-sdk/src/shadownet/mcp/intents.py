"""Intent payload models for the coordination intent profile.

The four coordination intent URIs and their ``body.data`` schemas:

  - ``coordinate_v1``     — initiator proposes an activity.
  - ``propose_plan_v1``   — receiver proposes a concrete plan.
  - ``confirm_plan_v1``   — initiator confirms the proposed plan.
  - ``accept_plan_v1``    — receiver acknowledges confirmation.

These are application-level intents transported via the generic
``send`` / ``respond`` / ``inbox`` MCP tools. The sidecar treats
``body.intent`` and ``body.data`` as opaque; these models are for
host agents and plugins that implement the coordination flow.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ACCEPT_PLAN_V1_URI",
    "CONFIRM_PLAN_V1_URI",
    "COORDINATE_V1_URI",
    "PROPOSE_PLAN_V1_URI",
    "AcceptPlanV1Data",
    "ConfirmPlanV1Data",
    "CoordinateV1Data",
    "GeoCoordinate",
    "PlanObject",
    "PlanWhere",
    "ProposePlanV1Data",
]


COORDINATE_V1_URI: Final = "urn:shadownet:intent:coordinate_v1"
PROPOSE_PLAN_V1_URI: Final = "urn:shadownet:intent:propose_plan_v1"
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


class ProposePlanV1Data(PlanObject):
    """``body.data`` for ``propose_plan_v1`` — receiver's concrete proposal."""


class ConfirmPlanV1Data(PlanObject):
    """``body.data`` for ``confirm_plan_v1`` — initiator confirms the proposal."""


class AcceptPlanV1Data(BaseModel):
    """``body.data`` for ``accept_plan_v1`` — receiver acknowledges."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepts_message_id: str = Field(alias="acceptsMessageId")
