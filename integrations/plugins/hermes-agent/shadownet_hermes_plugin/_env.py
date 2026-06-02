"""Read/write helpers for the ``SHADOWNET_CONNECT_URL`` line in Hermes' ``.env``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shadownet_hermes_plugin import _paths

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "env_path",
    "read_connect_url_from_env",
    "strip_connect_url_from_env",
]

_log = logging.getLogger(__name__)

_VAR = "SHADOWNET_CONNECT_URL"


def env_path() -> Path:
    """Path to ``.env`` — the file Hermes loads env vars from (``HERMES_HOME``)."""
    return _paths.env_path()


def _is_target_line(line: str) -> bool:
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return False
    return stripped.startswith(f"{_VAR}=") or stripped.startswith(f"export {_VAR}=")


def _extract_value(line: str) -> str:
    after = line.split("=", 1)[1] if "=" in line else ""
    after = after.strip().rstrip("\n")
    if (after.startswith('"') and after.endswith('"')) or (
        after.startswith("'") and after.endswith("'")
    ):
        after = after[1:-1]
    return after


def read_connect_url_from_env() -> str | None:
    """Return the current ``SHADOWNET_CONNECT_URL`` value from ``.env``, or None."""
    path = env_path()
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8-sig") as f:
            for line in f:
                if _is_target_line(line):
                    return _extract_value(line) or None
    except OSError as e:
        _log.warning("shadownet plugin: failed to read %s (%s)", path, e)
    return None


def strip_connect_url_from_env() -> bool:
    """Remove any ``SHADOWNET_CONNECT_URL=…`` line from ``.env``. Returns True if changed."""
    path = env_path()
    if not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8-sig") as f:
            lines = f.readlines()
    except OSError as e:
        _log.warning("shadownet plugin: failed to read %s (%s)", path, e)
        return False
    kept = [line for line in lines if not _is_target_line(line)]
    if len(kept) == len(lines):
        return False
    try:
        with path.open("w", encoding="utf-8") as f:
            f.writelines(kept)
    except OSError as e:
        _log.warning("shadownet plugin: failed to write %s (%s)", path, e)
        return False
    return True
