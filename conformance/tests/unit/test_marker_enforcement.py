"""Verify the rfc-marker enforcement raises on tests in required dirs without one."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

pytest_plugins = ["pytester"]


CONFTEST = """
import pytest
from shadownet_conformance import _markers

pytest_plugins = ["shadownet_conformance.reporters.rfc_json"]

def pytest_configure(config):
    _markers.register(config)
"""


def test_missing_rfc_marker_in_required_dir_blocks_collection(pytester: pytest.Pytester):
    pytester.makeini(
        "[pytest]\n"
        "asyncio_mode = auto\n"
        "asyncio_default_fixture_loop_scope = function\n"
        "filterwarnings = error\n"
    )
    pytester.makeconftest(CONFTEST)
    pytester.mkdir("tests")
    pytester.mkdir("tests/sca")
    test_path = pytester.path / "tests" / "sca" / "test_missing_marker.py"
    test_path.write_text(
        "def test_no_marker():\n    assert True\n",
        encoding="utf-8",
    )
    result = pytester.runpytest()
    # Plugin raises ConformanceError from inside pytest_collection_modifyitems,
    # which pytest surfaces as an internal error / non-zero exit.
    assert result.ret != 0
    combined = "\n".join(result.outlines + result.errlines)
    assert "lack a complete rfc(" in combined
    assert "tests/sca/test_missing_marker.py::test_no_marker" in combined


def test_present_rfc_marker_in_required_dir_collects(pytester: pytest.Pytester):
    pytester.makeini(
        "[pytest]\n"
        "asyncio_mode = auto\n"
        "asyncio_default_fixture_loop_scope = function\n"
        "filterwarnings = error\n"
    )
    pytester.makeconftest(CONFTEST)
    pytester.mkdir("tests")
    pytester.mkdir("tests/sca")
    test_path = pytester.path / "tests" / "sca" / "test_with_marker.py"
    test_path.write_text(
        (
            "import pytest\n"
            "@pytest.mark.rfc('0004', section='Issuance', requirement='hello')\n"
            "def test_present():\n    assert True\n"
        ),
        encoding="utf-8",
    )
    result = pytester.runpytest()
    assert result.ret == 0
