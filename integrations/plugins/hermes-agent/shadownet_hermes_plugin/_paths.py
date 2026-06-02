"""Hermes home/path resolution.

Mirrors the host's ``hermes_constants`` so the plugin honors ``HERMES_HOME``
and active profiles exactly as Hermes does. When Hermes is not importable
(CI, unit tests, tooling) we fall back to ``HERMES_HOME`` then ``~/.hermes``.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["config_path", "env_path", "hermes_home", "skills_dir"]


def _fallback_home() -> Path:
    env = os.environ.get("HERMES_HOME", "").strip()
    return Path(env) if env else Path.home() / ".hermes"


def hermes_home() -> Path:
    """Resolve Hermes' home directory the way the host does (``HERMES_HOME``)."""
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 - path resolution must never crash the plugin
        return _fallback_home()


def config_path() -> Path:
    """Path to ``config.yaml`` — the file Hermes reads at startup."""
    try:
        from hermes_constants import get_config_path

        return Path(get_config_path())
    except Exception:  # noqa: BLE001
        return hermes_home() / "config.yaml"


def env_path() -> Path:
    """Path to ``.env`` — Hermes' env-var file."""
    try:
        from hermes_constants import get_env_path

        return Path(get_env_path())
    except Exception:  # noqa: BLE001
        return hermes_home() / ".env"


def skills_dir() -> Path:
    """Path to the skills tree Hermes scans for ``<available_skills>``."""
    try:
        from hermes_constants import get_skills_dir

        return Path(get_skills_dir())
    except Exception:  # noqa: BLE001
        return hermes_home() / "skills"
