"""Locate and load committed fixtures from `fixtures/` at runtime.

Tests get fixture data via these helpers rather than via direct path
manipulation, so the resolution logic lives in one place and the same
helpers work whether the suite runs from a source checkout or from an
installed wheel that bundles `fixtures/`.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from shadownet_conformance.errors import FixtureMissing

if TYPE_CHECKING:
    from typing import Any


@cache
def fixtures_root() -> Path:
    """Locate the bundled `fixtures/` tree."""
    here = Path(__file__).resolve()
    # src/shadownet_conformance/fixtures.py -> repo root is parents[2]
    candidate = here.parents[2] / "fixtures"
    if candidate.is_dir():
        return candidate
    raise FixtureMissing(
        f"could not locate fixtures/ near {here}; "
        "the package must be installed from source or bundled with fixtures/"
    )


def load_jwt(rel_path: str) -> str:
    """Load a JWT fixture as the compact-serialization string."""
    return _read_text(rel_path).strip()


def load_json(rel_path: str) -> dict[str, Any]:
    """Load a JSON fixture as a dict."""
    return json.loads(_read_text(rel_path))


def load_bytes(rel_path: str) -> bytes:
    """Load a raw byte fixture (e.g., for byte-equality assertions)."""
    return _path(rel_path).read_bytes()


def fixture_path(rel_path: str) -> Path:
    """Return the resolved Path for a fixture without reading it."""
    return _path(rel_path)


def _path(rel_path: str) -> Path:
    full = fixtures_root() / rel_path
    if not full.is_file():
        raise FixtureMissing(f"fixture {rel_path!r} not found at {full}")
    return full


def _read_text(rel_path: str) -> str:
    return _path(rel_path).read_text(encoding="utf-8")
