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
import os
import shutil
import sys
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


def _skill_root_candidates() -> tuple[Path, ...]:
    """Candidate roots containing ``<name>/SKILL.md``, in priority order.

    1. Sibling to the package (``<pkg>/../skills/``) — used by
       ``hermes plugins install <repo>`` (git-clone layout) and editable
       installs.
    2. ``<sys.prefix>/share/hermes-plugins/shadownet/skills/`` — where
       wheel installs land the shared-data tree, per ``pyproject.toml``'s
       ``[tool.hatch.build.targets.wheel.shared-data]``.
    """
    return (
        Path(__file__).resolve().parent.parent / "skills",
        Path(sys.prefix) / "share" / "hermes-plugins" / "shadownet" / "skills",
    )


def _skill_paths() -> dict[str, Path]:
    """Resolve absolute paths to bundled SKILL.md files.

    Returns paths under the first candidate root that contains the
    canonical SKILL.md files; falls back to the primary (sibling-package)
    location if none match, so any warning message names a useful path.
    """
    candidates = _skill_root_candidates()
    for root in candidates:
        if (root / SKILL_NAMES[0] / "SKILL.md").is_file():
            return {name: root / name / "SKILL.md" for name in SKILL_NAMES}
    return {name: candidates[0] / name / "SKILL.md" for name in SKILL_NAMES}


def _hermes_data_dir() -> Path:
    """Resolve Hermes' data directory (canonical home for skill files).

    Precedence: ``HERMES_DATA_DIR`` env var → container default
    ``/opt/data`` if it exists → ``~/.hermes`` on bare-metal installs.
    """
    env = os.environ.get("HERMES_DATA_DIR")
    if env:
        return Path(env)
    container_default = Path("/opt/data")
    if container_default.is_dir():
        return container_default
    return Path.home() / ".hermes"


def _materialize_skills_into_data_dir(skill_paths: dict[str, Path]) -> None:
    """Copy each skill directory into ``<HERMES_DATA_DIR>/skills/<name>/``.

    Hermes's ``ctx.register_skill(name, path)`` registers metadata only —
    it does NOT materialize the SKILL.md (or sibling files in the skill
    directory) into ``~/.hermes/skills/``, which is where the agent's
    skill-loader actually reads from. Without this copy, the LLM's
    available-skills prompt does not include our skills even though the
    platform adapter is connected.
    """
    target_root = _hermes_data_dir() / "skills"
    for name, src_skill_md in skill_paths.items():
        if not src_skill_md.is_file():
            continue
        src_dir = src_skill_md.parent
        dst_dir = target_root / name
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            for src_file in src_dir.iterdir():
                if src_file.is_file():
                    shutil.copy2(src_file, dst_dir / src_file.name)
        except OSError as e:
            _log.warning(
                "shadownet plugin: failed to materialize skill `%s` into %s: %s",
                name,
                dst_dir,
                e,
            )


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
        _materialize_skills_into_data_dir(skill_paths)
        _log.info(
            "registered %d Shadownet skills (materialized into %s)",
            len(skill_paths),
            _hermes_data_dir() / "skills",
        )

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
