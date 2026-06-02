"""Plugin-owned slash commands registered via ``ctx.register_command``.

Only commands with no native skill equivalent live here (status, logout).
The four skill-backed ``/shadownet-*`` commands are surfaced by the
materialized SKILL.md files as native skill commands; registering
same-named plugin commands would shadow them and only print the raw
``skill_view`` JSON, so they are intentionally omitted. Handler signatures
conform to the guide: ``def handler(raw_args: str) -> str | None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shadownet_hermes_plugin import _cli

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["build_slash_command_specs", "register_slash_commands"]


def _make_status_handler() -> Callable[[str], str | None]:
    def _handler(raw_args: str) -> str | None:
        return _cli.do_status()

    return _handler


def _make_logout_handler() -> Callable[[str], str | None]:
    def _handler(raw_args: str) -> str | None:
        return _cli.do_logout()

    return _handler


def build_slash_command_specs(ctx: Any) -> list[dict[str, Any]]:
    """Return the spec list ``[{name, handler, description}, ...]`` for registration.

    Only plugin-owned commands with no native skill equivalent. ``ctx`` is
    accepted for forward compatibility (later phases add ctx-driven commands).
    """
    return [
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
