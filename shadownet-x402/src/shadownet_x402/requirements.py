"""The x402 PaymentRequirements quoted by the resource server."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from shadownet_x402.errors import SettlementError

if TYPE_CHECKING:
    from shadownet_x402.config import Settings


class PaymentRequirements(BaseModel):
    """A single x402 ``accepts[]`` entry describing how to pay for a resource."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    scheme: str = "exact"
    network: str
    asset: str
    amount: int = Field(alias="maxAmountRequired")
    pay_to: str = Field(alias="payTo")
    resource: str
    max_timeout_seconds: int = Field(default=60, alias="maxTimeoutSeconds")


def build_payment_requirements(settings: Settings, *, resource_url: str) -> PaymentRequirements:
    """Quote the price for ``resource_url`` from settings."""
    if settings.pay_to is None:
        raise SettlementError("settings.pay_to is required to quote a payment")
    return PaymentRequirements(
        network=settings.network_caip2,
        asset=str(settings.asset_id),
        amount=settings.price_micro,
        pay_to=settings.pay_to,
        resource=resource_url,
    )
