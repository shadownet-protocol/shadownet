"""Unit tests for the Claude Code MCP proxy's pure helpers.

The stdio<->HTTP bridge itself is integration territory — it requires a
live MCP server upstream. We cover the failure paths and the URL parsing
hand-off here; the bridge logic is exercised by the python-sdk's existing
``test_connect_session.py`` against the in-memory MCP transport.

Run with::

    cd integrations/plugins/claude-code/bin
    python -m pytest test_mcp_shadownet_proxy.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load_proxy_module():
    """Load the proxy script as a module. It has no .py-importable name
    because it's a ``uv run --script`` PEP 723 script, but the file is
    importable directly when its deps are in scope (i.e., from this test
    suite, where pytest is invoked from the conformance/python-sdk env)."""
    spec = importlib.util.spec_from_file_location(
        "mcp_shadownet_proxy", HERE / "mcp-shadownet-proxy.py"
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["mcp_shadownet_proxy"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


proxy = _load_proxy_module()


async def test_run_exits_1_when_url_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    rc = await proxy._run()
    assert rc == 1


async def test_run_exits_1_when_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_CONNECT_URL", "   ")
    rc = await proxy._run()
    assert rc == 1


async def test_run_exits_2_on_malformed_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_CONNECT_URL", "not-a-shadownet-url")
    rc = await proxy._run()
    assert rc == 2


async def test_run_exits_3_on_handoff_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handoff URLs require a browser flow that the proxy can't drive —
    the user should re-mint a token-bearing URL."""
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        "shadownet://connect?base=https://x.example&handoff=ABCDEFGHIJ12345678",
    )
    rc = await proxy._run()
    assert rc == 3


async def test_run_exits_4_when_bundle_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token-bearing URL pointing at a sidecar that won't answer the
    integration-bundle endpoint produces a clean exit 4 (rather than an
    unhandled exception)."""
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        "shadownet://connect?base=http://localhost:1&token=tok",
    )
    rc = await proxy._run()
    assert rc == 4


def test_main_propagates_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns the integer rc from _run()."""
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    assert proxy.main() == 1
