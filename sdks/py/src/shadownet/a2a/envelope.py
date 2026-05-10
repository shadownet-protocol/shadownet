from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# RFC-0006 §Message envelope (Shadownet extensions).
# Schema: shadownet-specs/schemas/messages/envelope.schema.json

ENVELOPE_PART_TYPE = "shadownet/v1+envelope"
ENVELOPE_MEDIA_TYPE = "application/json"

__all__ = [
    "ENVELOPE_MEDIA_TYPE",
    "ENVELOPE_PART_TYPE",
    "ShadownetEnvelope",
    "decode_envelope_part",
    "envelope_part",
]


class ShadownetEnvelope(BaseModel):
    """The ``data`` of a part with type ``shadownet/v1+envelope``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    shadownet_v: Literal["0.1"] = Field(alias="shadownet:v")
    intent_id: str = Field(alias="intentId", pattern=r"^urn:")
    session_id: str | None = Field(default=None, alias="sessionId", pattern=r"^urn:")
    interaction: str = Field(pattern=r"^urn:")
    payload: dict[str, Any]


def envelope_part(envelope: ShadownetEnvelope) -> dict[str, Any]:
    """Wrap an envelope as an A2A message ``part``."""
    return {
        "type": ENVELOPE_PART_TYPE,
        "mediaType": ENVELOPE_MEDIA_TYPE,
        "data": envelope.model_dump(by_alias=True, exclude_none=True),
    }


def decode_envelope_part(part: dict[str, Any]) -> ShadownetEnvelope:
    """Parse an A2A part claimed to carry a Shadownet envelope.

    Raises :class:`ValueError` if ``part.type`` does not match ``ENVELOPE_PART_TYPE``.
    """
    part_type = part.get("type")
    if part_type != ENVELOPE_PART_TYPE:
        raise ValueError(
            f"part.type {part_type!r} is not a Shadownet envelope ({ENVELOPE_PART_TYPE!r})"
        )
    data = part.get("data")
    if not isinstance(data, dict):
        raise ValueError("envelope part is missing a JSON 'data' object")
    return ShadownetEnvelope.model_validate(data)
