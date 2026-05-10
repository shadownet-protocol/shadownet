from __future__ import annotations

import os
from pathlib import Path

import pytest

# Default assumes shadownet-specs is cloned alongside the shadownet monorepo
# (i.e. <workspace>/shadownet/py/tests/conftest.py and
# <workspace>/shadownet-specs/). CI overrides via SHADOWNET_SPECS_PATH.
DEFAULT_SPECS_PATH = Path(__file__).resolve().parents[3] / "shadownet-specs"


@pytest.fixture(scope="session")
def specs_path() -> Path:
    """Filesystem path to the shadownet-specs checkout used for conformance tests.

    Override with the ``SHADOWNET_SPECS_PATH`` environment variable.
    """
    raw = os.environ.get("SHADOWNET_SPECS_PATH")
    path = Path(raw).expanduser().resolve() if raw else DEFAULT_SPECS_PATH
    if not path.is_dir():
        pytest.skip(f"shadownet-specs not found at {path}; set SHADOWNET_SPECS_PATH")
    return path
