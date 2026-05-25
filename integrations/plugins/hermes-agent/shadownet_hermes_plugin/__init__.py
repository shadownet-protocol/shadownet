"""Shadownet plugin for Hermes Agent.

Entry point: :func:`register`, discovered by Hermes Agent's plugin loader
via the ``hermes_agent.plugins`` entry-points group declared in
``pyproject.toml``.

The plugin wires:

- The four canonical Shadownet skills (``shadownet-setup``,
  ``shadownet-reach-out``, ``shadownet-inbox``, ``shadownet-coordinate``)
  via ``ctx.register_skill`` — they ship inside this package's
  ``skills/`` directory, synced from the canonical
  ``integrations/skills/`` tree by ``integrations/scripts/sync_skills.py``.
- A ``shadownet`` platform adapter via ``ctx.register_platform`` —
  see :mod:`shadownet_hermes_plugin._adapter` for the long-poll
  inbound loop and outbound ``social_send`` mapping.

Configuration is via environment variables (``SHADOWNET_TOKEN``,
``SHADOWNET_SIDECAR_BASE_URL``, ``SHADOWNET_CONNECT_URL``) declared in
``plugin.yaml``'s ``requires_env``. Hermes prompts on install.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from shadownet_hermes_plugin._adapter import (
    build_adapter_class,
    check_shadownet_requirements,
)

__all__ = ["register"]

_log = logging.getLogger(__name__)

# Skill names match `integrations/skills/<name>/SKILL.md` exactly.
SKILL_NAMES = (
    "shadownet-setup",
    "shadownet-reach-out",
    "shadownet-inbox",
    "shadownet-coordinate",
)


def _skill_paths() -> dict[str, Path]:
    """Resolve absolute paths to bundled SKILL.md files.

    The plugin ships ``skills/<name>/SKILL.md`` next to its package root
    so ``hermes plugins install <repo>`` picks them up. We resolve
    relative to ``__file__`` so the path works regardless of how the
    package was installed (sdist, wheel, editable, git clone).
    """
    root = Path(__file__).resolve().parent.parent / "skills"
    return {name: root / name / "SKILL.md" for name in SKILL_NAMES}


def register(ctx: Any) -> None:
    """Hermes plugin entry point — invoked once at Hermes startup.

    Args:
        ctx: Hermes ``PluginContext``. See
            https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins
            for the documented surface.
    """
    skill_paths = _skill_paths()
    missing = [str(p) for p in skill_paths.values() if not p.is_file()]
    if missing:
        _log.warning(
            "shadownet plugin: SKILL.md files missing: %s — skills not registered",
            missing,
        )
    else:
        for name, path in skill_paths.items():
            ctx.register_skill(name, path)
        _log.debug("registered %d Shadownet skills", len(skill_paths))

    adapter_class = build_adapter_class()
    ctx.register_platform(
        name="shadownet",
        label="Shadownet",
        adapter_factory=lambda cfg: adapter_class(cfg),
        check_fn=check_shadownet_requirements,
        allowed_users_env="SHADOWNET_ALLOWED_USERS",
        allow_all_env="SHADOWNET_ALLOW_ALL_USERS",
    )
    _log.info("registered shadownet platform (long-poll inbox)")
