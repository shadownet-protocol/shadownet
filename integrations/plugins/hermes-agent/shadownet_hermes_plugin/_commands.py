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


def _run_shadowpay(raw_args: str) -> str:
    from shadownet_hermes_plugin import _shadowpay

    try:
        return _shadowpay.run(raw_args)
    except Exception as e:  # noqa: BLE001 — a slash command must always answer
        return f"[shadownet] ShadowPay failed: {e}"


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
