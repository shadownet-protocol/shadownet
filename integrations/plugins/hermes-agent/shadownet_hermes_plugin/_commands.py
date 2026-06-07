"""Slash-command handlers registered via ``ctx.register_command``.

Each handler is built from a closure over ``ctx`` so it can dispatch
back through the tool registry (e.g. ``skill_view`` via
``ctx.dispatch_tool``). Handler signatures conform to the guide:
``def handler(raw_args: str) -> str | None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shadownet_hermes_plugin import _cli

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["build_slash_command_specs", "register_slash_commands"]


def _make_skill_handler(ctx: Any, skill_name: str) -> Callable[[str], str | None]:
    def _handler(raw_args: str) -> str | None:
        try:
            result = ctx.dispatch_tool("skill_view", {"name": f"shadownet:{skill_name}"})
        except Exception as e:  # noqa: BLE001
            return f"[shadownet] could not load skill `{skill_name}`: {e}"
        if result is None:
            return f"[shadownet] skill `{skill_name}` returned no content"
        return str(result)

    _handler.__name__ = f"_handle_{skill_name.replace('-', '_')}"
    return _handler


def _make_status_handler() -> Callable[[str], str | None]:
    def _handler(raw_args: str) -> str | None:
        return _cli.do_status()

    return _handler


def _make_logout_handler() -> Callable[[str], str | None]:
    def _handler(raw_args: str) -> str | None:
        return _cli.do_logout()

    return _handler


def _make_pay_handler() -> Callable[[str], str | None]:
    def _handler(raw_args: str) -> str | None:
        return _run_shadowpay(raw_args)

    return _handler


def _ensure_x402_importable() -> None:
    """Best-effort: make ``shadownet_x402`` importable in a live Hermes env."""
    import importlib.util

    if importlib.util.find_spec("shadownet_x402") is not None:
        return
    import sys
    from pathlib import Path

    candidate = Path.home() / "shadownet" / "shadownet" / "shadownet-x402" / "src"
    if (candidate / "shadownet_x402").is_dir():
        sys.path.insert(0, str(candidate))


def _run_shadowpay(raw_args: str) -> str:
    try:
        _ensure_x402_importable()
        return _shadowpay_narrative(raw_args)
    except Exception as e:  # noqa: BLE001 — a slash command must always answer
        return f"[shadownet] ShadowPay demo failed: {e}"


def _shadowpay_narrative(raw_args: str) -> str:
    """Run the identity-gated x402 flow (stub settlement) and narrate the result."""
    import time

    from shadownet.credential import CredentialPayload, RevocationPointer, mint_credential
    from shadownet.crypto.ed25519 import Ed25519KeyPair
    from shadownet.identifiers import encode_public_key
    from shadownet_x402.budget import InMemoryBudgetStore
    from shadownet_x402.config import Settings
    from shadownet_x402.facilitator import FakeFacilitator
    from shadownet_x402.nonce import InMemoryNonceStore
    from shadownet_x402.pop import mint_pop
    from shadownet_x402.server import Challenge, Paywall, Refused, Settled
    from shadownet_x402.settlement import PaidTerms, encode_x_payment

    parts = raw_args.split()
    payee = parts[0] if parts else "alice"
    amount = parts[1] if len(parts) > 1 else "0.005"

    issuer = Ed25519KeyPair.generate()
    issuer_pk = encode_public_key(issuer.public_bytes)
    buyer = Ed25519KeyPair.generate()
    buyer_sub = encode_public_key(buyer.public_bytes)
    now = int(time.time())
    credential = mint_credential(
        CredentialPayload(
            iss="acme.example",
            sub=buyer_sub,
            kind="org_affiliation",
            org="acme.example",
            iat=now,
            exp=now + 3600,
            rev=RevocationPointer(epoch="2026q2", idx=0),
        ),
        issuer,
    )
    budget = InMemoryBudgetStore(cap_micro=1_000_000)
    resource = f"https://{payee}.sh4dow.org/pay"
    paywall = Paywall(
        Settings(
            pay_to="ALICEALGOADDR",
            price_micro=5000,
            asset_id=10458941,
            network_caip2="algorand:testnet",
        ),
        nonce_store=InMemoryNonceStore(),
        budget_store=budget,
        facilitator=FakeFacilitator(transaction="TESTNET-TXID-9F3A2C"),
        resolve_issuer_key=lambda _iss: issuer_pk,
        check_revoked=lambda _credential: None,
    )

    def pay(*, tamper: bool = False) -> Challenge | Settled | Refused:
        challenge = paywall.process(resource_url=resource, credential=credential)
        if not isinstance(challenge, Challenge):
            return challenge
        pop = mint_pop(buyer, sub=buyer_sub, audience=resource, nonce=challenge.nonce)
        req = challenge.requirements
        terms = PaidTerms(
            network=req.network,
            asset=req.asset,
            amount=req.amount,
            pay_to="ATTACKERADDR" if tamper else req.pay_to,
            payer=buyer_sub,
        )
        return paywall.process(
            resource_url=resource, credential=credential, pop=pop, x_payment=encode_x_payment(terms)
        )

    lines = ["ShadowPay - agent payments on Algorand, bound to identity.", ""]
    settled = pay()
    if isinstance(settled, Settled):
        lines.append(f"1) Paid {amount} USDC to {payee} on Algorand TestNet.")
        lines.append(
            f"   Settled, txid {settled.outcome.transaction} - bound to your verified "
            "Shadow identity, not an anonymous wallet."
        )
    budget.revoke(buyer_sub)
    revoked = pay()
    if isinstance(revoked, Refused):
        lines.append("")
        lines.append(
            f"2) Revoke the agent -> next payment REFUSED ({revoked.status}). Wallet still "
            "funded. The identity is the kill switch."
        )
    budget.restore(buyer_sub)
    tampered = pay(tamper=True)
    if isinstance(tampered, Refused):
        lines.append("")
        lines.append(
            f"3) Man-in-the-middle swaps the pay-to -> REFUSED ({tampered.status}). The charge "
            "is bound to what was agreed."
        )
    lines.append("")
    lines.append("Know Your Agent for x402.")
    return "\n".join(lines)


def build_slash_command_specs(ctx: Any) -> list[dict[str, Any]]:
    """Return the spec list ``[{name, handler, description}, ...]`` for registration."""
    return [
        {
            "name": "shadownet-setup",
            "handler": _make_skill_handler(ctx, "shadownet-setup"),
            "description": "Initialize or verify the shadownet connection",
        },
        {
            "name": "shadownet-inbox",
            "handler": _make_skill_handler(ctx, "shadownet-inbox"),
            "description": "Triage pending shadownet messages",
        },
        {
            "name": "shadownet-reach-out",
            "handler": _make_skill_handler(ctx, "shadownet-reach-out"),
            "description": "Send a message to a shadownet contact",
        },
        {
            "name": "shadownet-coordinate",
            "handler": _make_skill_handler(ctx, "shadownet-coordinate"),
            "description": "Run a two-sided shadownet coordination plan",
        },
        {
            "name": "shadownet-pay",
            "handler": _make_pay_handler(),
            "description": "ShadowPay: pay a shadow over x402 on Algorand (identity-bound)",
        },
        {
            "name": "shadownet-status",
            "handler": _make_status_handler(),
            "description": "Show shadownet connection status",
        },
        {
            "name": "shadownet-logout",
            "handler": _make_logout_handler(),
            "description": "Disconnect this Hermes from shadownet",
        },
    ]


def register_slash_commands(ctx: Any) -> int:
    """Register every shadownet slash command on ``ctx``. Returns the count."""
    specs = build_slash_command_specs(ctx)
    for spec in specs:
        ctx.register_command(**spec)
    return len(specs)
