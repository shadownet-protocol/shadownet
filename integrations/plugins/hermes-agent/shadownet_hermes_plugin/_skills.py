"""Skill discovery, registration, and materialization for the shadownet plugin."""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path
from typing import Any

from shadownet_hermes_plugin import _paths

__all__ = [
    "SHADOWNET_CATEGORY",
    "SHADOWNET_CATEGORY_DESCRIPTION",
    "SKILL_NAMES",
    "hermes_data_dir",
    "materialize_skills_into_data_dir",
    "register_skills",
    "skill_paths",
    "skill_root_candidates",
]

_log = logging.getLogger(__name__)

# Category groups skills under `<HERMES_HOME>/skills/<category>/<name>/`.
# Matches the convention used by built-in `github/`, `apple/`, etc.
SHADOWNET_CATEGORY = "shadownet"

SHADOWNET_CATEGORY_DESCRIPTION = (
    "Identity-anchored agent-to-agent messaging via the Shadownet protocol.\n"
    "Open an MCP session to the configured sidecar and use these skills to\n"
    "verify identity, reach out to contacts, triage the inbox, and run the\n"
    "two-sided coordination flow.\n"
)

SKILL_NAMES = (
    "shadownet-setup",
    "shadownet-reach-out",
    "shadownet-inbox",
    "shadownet-coordinate",
    "shadownet-autonomous",
)


def skill_root_candidates() -> tuple[Path, ...]:
    """Candidate roots containing ``<name>/SKILL.md``, in priority order.

    1. Already materialized in the data dir (deployed at container build /
       deploy time via ``docker cp`` or similar).  This is the primary
       path in production — skills are deployed independently of the
       plugin wheel.
    2. Sibling to the package — legacy bundled layout (editable installs).
    3. ``<sys.prefix>/share/hermes-plugins/shadownet/skills/`` — where wheel
       installs land the shared-data tree per ``pyproject.toml``.
    """
    return (
        _paths.skills_dir() / SHADOWNET_CATEGORY,
        Path(__file__).resolve().parent.parent / "skills",
        Path(sys.prefix) / "share" / "hermes-plugins" / "shadownet" / "skills",
    )


def skill_paths() -> dict[str, Path]:
    """Absolute paths to bundled SKILL.md files, picking the first matching root."""
    candidates = skill_root_candidates()
    for root in candidates:
        if (root / SKILL_NAMES[0] / "SKILL.md").is_file():
            return {name: root / name / "SKILL.md" for name in SKILL_NAMES}
    return {name: candidates[0] / name / "SKILL.md" for name in SKILL_NAMES}


def hermes_data_dir() -> Path:
    """Hermes' home directory, resolved via :mod:`_paths` (``HERMES_HOME``).

    Delegates to the host's ``hermes_constants.get_hermes_home()`` when Hermes
    is installed so profiles and a custom ``HERMES_HOME`` are honored exactly
    as the host resolves them.
    """
    return _paths.hermes_home()


def register_skills(ctx: Any) -> int:
    """Call ``ctx.register_skill(name, path)`` for each bundled skill.

    The guide notes plugin skills registered this way are namespaced
    ``shadownet:<name>`` and are NOT listed in the system prompt's
    ``<available_skills>`` index — they're opt-in via ``skill_view``.
    ``materialize_skills_into_data_dir`` runs alongside to populate the
    categorized layout that DOES surface in ``<available_skills>``.
    Returns the number of skills registered.
    """
    resolved = skill_paths()
    missing = [str(p) for p in resolved.values() if not p.is_file()]
    if missing:
        _log.warning(
            "shadownet plugin: SKILL.md files missing: %s — skills not registered",
            missing,
        )
        return 0
    registered = 0
    for name, path in resolved.items():
        try:
            ctx.register_skill(name, path)
        except Exception as e:  # noqa: BLE001 - one bad skill must not abort plugin load
            _log.warning("shadownet plugin: failed to register skill %s: %s", name, e)
            continue
        registered += 1
    return registered


def materialize_skills_into_data_dir(paths: dict[str, Path]) -> None:
    """Copy each skill directory into ``<data>/skills/<category>/<name>/``.

    The categorized layout sidesteps the collision risk the guide warns
    about for the flat ``~/.hermes/skills/`` copy pattern: our skills
    live under ``shadownet/`` and can't shadow built-in skill names.
    We rely on this path for ``<available_skills>`` visibility because
    ``ctx.register_skill`` deliberately omits its registrations from
    that index.
    """
    category_root = _paths.skills_dir() / SHADOWNET_CATEGORY
    try:
        category_root.mkdir(parents=True, exist_ok=True)
        description_path = category_root / "DESCRIPTION.md"
        description_path.write_text(
            f"---\ndescription: {SHADOWNET_CATEGORY_DESCRIPTION.splitlines()[0]}\n---\n\n"
            f"# Shadownet\n\n{SHADOWNET_CATEGORY_DESCRIPTION}",
            encoding="utf-8",
        )
    except OSError as e:
        _log.warning(
            "shadownet plugin: failed to prepare category dir %s: %s",
            category_root,
            e,
        )
        return

    for name, src_skill_md in paths.items():
        if not src_skill_md.is_file():
            continue
        src_dir = src_skill_md.parent
        dst_dir = category_root / name
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


def count_materialized_skills() -> int:
    """How many SKILL.md files currently live under the categorized layout.

    Used by ``hermes shadownet status`` / ``doctor`` to report skill state.
    """
    root = _paths.skills_dir() / SHADOWNET_CATEGORY
    if not root.is_dir():
        return 0
    return sum(1 for name in SKILL_NAMES if (root / name / "SKILL.md").is_file())
