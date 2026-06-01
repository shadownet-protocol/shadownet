"""Shadownet plugin for Hermes Agent — entry point.

Wires every canonical Hermes plugin surface used by shadownet:

- ``ctx.register_skill`` for namespaced access (``shadownet:<name>``)
  plus a categorized materialization into ``<data>/skills/shadownet/``
  so the four skills also surface in ``<available_skills>``.
- ``mcp_servers.shadownet`` in ``~/.hermes/config.yaml`` so the agent
  sees ``mcp_shadownet_*`` tools (no Python API for MCP registration
  per the guide — config.yaml is canonical).
- ``ctx.register_platform`` for the long-poll inbound adapter, with a
  ``platform_hint`` telling the agent what shadownet is and an
  ``env_enablement_fn`` so the platform shows up in gateway status
  from env alone.
- ``ctx.register_hook`` for ``on_session_start`` (cheap inbox check)
  and ``pre_llm_call`` (first-turn injection of pending count).
- ``ctx.register_command`` for six ``/shadownet-*`` slash commands.
- ``ctx.register_cli_command`` for ``hermes shadownet {status,doctor,sync,logout}``.

The module imports Hermes lazily — :mod:`._adapter` defers Hermes types
to runtime so this package stays importable in CI/tests without a
Hermes install.
"""

from __future__ import annotations

import logging
from typing import Any

from shadownet_hermes_plugin import _cli, _commands, _hooks, _mcp_config, _skills
from shadownet_hermes_plugin._adapter import (
    build_adapter_class,
    check_shadownet_requirements,
    env_enablement,
)
from shadownet_hermes_plugin._skills import (
    SHADOWNET_CATEGORY,
    SHADOWNET_CATEGORY_DESCRIPTION,
    SKILL_NAMES,
)

__all__ = [
    "SHADOWNET_CATEGORY",
    "SHADOWNET_CATEGORY_DESCRIPTION",
    "SKILL_NAMES",
    "register",
]

_log = logging.getLogger(__name__)


# Re-exported helpers used by existing tests. Kept as thin shims so the
# legacy test paths continue to work while we settle into the split.
_skill_root_candidates = _skills.skill_root_candidates
_skill_paths = _skills.skill_paths
_hermes_data_dir = _skills.hermes_data_dir
_materialize_skills_into_data_dir = _skills.materialize_skills_into_data_dir


_PLATFORM_HINT = (
    "Shadownet is connected. Use mcp_shadownet_contacts to list contacts, "
    "mcp_shadownet_send to send messages (with body.text, body.intent, "
    "body.data), mcp_shadownet_respond to reply in a thread (by contextId), "
    "and mcp_shadownet_inbox / mcp_shadownet_inbox_wait for inbox triage. "
    "IMPORTANT: When the user asks to plan a meeting, coordinate, schedule, "
    "or meet up with a contact, ALWAYS load the 'shadownet-coordinate' skill "
    "first — it contains the exact intent URIs and data shapes required. "
    "Do NOT invent your own intent values or body formats."
)


def register(ctx: Any) -> None:
    """Hermes plugin entry point — invoked once at Hermes startup."""
    skill_count = _skills.register_skills(ctx)
    if skill_count:
        _skills.materialize_skills_into_data_dir(_skills.skill_paths())
        _log.info(
            "registered %d shadownet skills (materialized into %s)",
            skill_count,
            _skills.hermes_data_dir() / "skills" / SHADOWNET_CATEGORY,
        )

    _mcp_config.ensure_mcp_server_in_config()

    adapter_class = build_adapter_class()
    _register_platform_compat(
        ctx,
        name="shadownet",
        label="Shadownet",
        adapter_factory=lambda cfg: adapter_class(cfg),
        check_fn=check_shadownet_requirements,
        env_enablement_fn=env_enablement,
        platform_hint=_PLATFORM_HINT,
        allowed_users_env="SHADOWNET_ALLOWED_USERS",
        allow_all_env="SHADOWNET_ALLOW_ALL_USERS",
    )
    _log.info("registered shadownet platform (long-poll inbox)")

    _safe_register_hook(ctx, "on_session_start", _hooks.on_session_start_callback)
    _safe_register_hook(ctx, "pre_llm_call", _hooks.pre_llm_call_callback)
    _safe_register_hook(ctx, "on_session_end", _hooks.on_session_end_callback)

    command_count = _safe_register_slash_commands(ctx)
    if command_count:
        _log.info("registered %d shadownet slash commands", command_count)

    _safe_register_cli_command(ctx)


def _register_platform_compat(ctx: Any, **kwargs: Any) -> None:
    """Call ``ctx.register_platform`` tolerating older Hermes runtimes.

    Newer Hermes versions accept ``platform_hint``, ``env_enablement_fn``,
    ``cron_deliver_env_var``, etc. via ``**entry_kwargs`` forwarded to
    ``PlatformEntry``. Older runtimes' ``PlatformEntry.__init__`` raises
    ``TypeError`` on unknown kwargs and aborts plugin load entirely.
    Drop the offending kwarg and retry until the call succeeds — every
    one of these is optional metadata; the platform still works without
    them. Required kwargs (``name``, ``label``, ``adapter_factory``,
    ``check_fn``) are protected from removal.
    """
    required = {"name", "label", "adapter_factory", "check_fn"}
    attempt_kwargs = dict(kwargs)
    while True:
        try:
            ctx.register_platform(**attempt_kwargs)
            return
        except TypeError as e:
            msg = str(e)
            dropped: str | None = None
            for kw in list(attempt_kwargs):
                if kw in required:
                    continue
                if f"'{kw}'" in msg:
                    attempt_kwargs.pop(kw)
                    dropped = kw
                    break
            if dropped is None:
                raise
            _log.warning(
                "shadownet plugin: register_platform rejected `%s` (%s) on this "
                "Hermes runtime; retrying without it",
                dropped,
                e,
            )


def _safe_register_hook(ctx: Any, name: str, callback: Any) -> None:
    register_fn = getattr(ctx, "register_hook", None)
    if register_fn is None:
        return
    try:
        register_fn(name, callback)
    except Exception as e:  # noqa: BLE001
        _log.warning("shadownet plugin: failed to register hook %s: %s", name, e)


def _safe_register_slash_commands(ctx: Any) -> int:
    if getattr(ctx, "register_command", None) is None:
        return 0
    try:
        return _commands.register_slash_commands(ctx)
    except Exception as e:  # noqa: BLE001
        _log.warning("shadownet plugin: failed to register slash commands: %s", e)
        return 0


def _safe_register_cli_command(ctx: Any) -> None:
    register_fn = getattr(ctx, "register_cli_command", None)
    if register_fn is None:
        return
    try:
        register_fn(
            name="shadownet",
            help="Manage shadownet integration",
            setup_fn=_cli.setup,
            handler_fn=_cli.handle,
        )
    except Exception as e:  # noqa: BLE001
        _log.warning("shadownet plugin: failed to register CLI subcommand: %s", e)
