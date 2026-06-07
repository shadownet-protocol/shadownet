"""FastAPI adapter mapping a PaywallResult to an HTTP response."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from shadownet_x402.server import Challenge, Refused

if TYPE_CHECKING:
    from fastapi import Request, Response

    from shadownet_x402.server import PaywallResult
    from shadownet_x402.settlement import SettleOutcome


def read_payment_headers(request: Request) -> dict[str, str | None]:
    """Pull the Shadow identity + payment headers off a request."""
    return {
        "credential": request.headers.get("Shadow-Credential"),
        "pop": request.headers.get("Shadow-PoP"),
        "x_payment": request.headers.get("X-PAYMENT"),
    }


def to_response(result: PaywallResult, *, body: dict[str, Any] | None = None) -> Response:
    """Render a PaywallResult: 402 challenge, refusal, or 200 with the receipt."""
    if isinstance(result, Challenge):
        content = {
            "x402Version": 1,
            "accepts": [result.requirements.model_dump(by_alias=True)],
            "resource": result.requirements.resource,
        }
        return JSONResponse(
            status_code=402, content=content, headers={"Shadow-Nonce": result.nonce}
        )
    if isinstance(result, Refused):
        return JSONResponse(status_code=result.status, content={"error": result.reason})
    headers = {}
    if result.outcome.transaction is not None:
        headers["X-PAYMENT-RESPONSE"] = _encode_receipt(result.outcome)
    return JSONResponse(status_code=200, content=body or {}, headers=headers)


def _encode_receipt(outcome: SettleOutcome) -> str:
    payload = {
        "success": outcome.success,
        "transaction": outcome.transaction,
        "network": outcome.network,
        "payer": outcome.payer,
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
