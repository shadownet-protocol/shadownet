"""Unit tests for the Claude Code MCP proxy's pure helpers.

The stdio<->streamable_http bridge itself is integration territory — it
requires a live MCP server upstream. We cover the failure paths and the
URL parsing hand-off here; the bridge logic is exercised by the
python-sdk's MCP client tests against the in-memory transport.

Run with::

    cd integrations/plugins/claude-code/bin
    python -m pytest test_mcp_shadownet_proxy.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

HERE = Path(__file__).resolve().parent

# v0.2 inline / handoff URI templates. The MCP endpoint must be https://
# (or loopback http://) per RFC 0003 §3.
_INLINE_URI = f"shadow://connect?mcp={quote('http://localhost:1/mcp', safe='')}&token=tok-from-plugin"
_HANDOFF_URI = f"shadow://connect?mcp={quote('https://x.example/mcp', safe='')}&handoff=ABCDEFGHIJ12345678"


def _load_proxy_module():
    """Load the proxy script as a module. It has no .py-importable name
    because it's a ``uv run --script`` PEP 723 script, but the file is
    importable directly when its deps are in scope."""
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
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    rc = await proxy._run()
    assert rc == 1


async def test_run_exits_1_when_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_CONNECT_URL", "   ")
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    rc = await proxy._run()
    assert rc == 1


async def test_run_exits_2_on_malformed_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOWNET_CONNECT_URL", "not-a-shadow-url")
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    rc = await proxy._run()
    assert rc == 2


async def test_run_exits_3_when_handoff_cannot_be_redeemed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handoff URL whose code has been consumed/expired: surface a clean
    exit 3 instead of crashing on the HTTP 404."""
    from shadownet.onboarding import HandoffError

    async def _fail_redeem(
        mcp_origin: str, code: str, *, client: object = None
    ) -> object:
        raise HandoffError("handoff code rejected (404)")

    monkeypatch.setattr(proxy, "aredeem_handoff", _fail_redeem)
    monkeypatch.setenv("SHADOWNET_CONNECT_URL", _HANDOFF_URI)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    rc = await proxy._run()
    assert rc == 3


async def test_run_prefers_claude_plugin_option_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude Code exports userConfig as CLAUDE_PLUGIN_OPTION_* — including
    ``sensitive: true`` fields, whose ``${user_config.X}`` substitution into
    .mcp.json env blocks doesn't always resolve. The proxy must read the
    plugin-option form first.

    We assert the URL was picked up by patching the resolver to fail with
    a known exception once a value reached it.
    """
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", _INLINE_URI)

    seen: dict[str, str] = {}

    async def _record_resolver(url: str) -> tuple[str, str]:
        seen["url"] = url
        # Force a clean post-resolution exit by raising before the bridge
        # would try to dial localhost:1.
        raise RuntimeError("stop here")

    monkeypatch.setattr(proxy, "_resolve_endpoint_and_token", _record_resolver)
    with pytest.raises(RuntimeError, match="stop here"):
        await proxy._run()
    assert seen["url"] == _INLINE_URI


def test_main_propagates_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns the integer rc from _run()."""
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONNECT_URL", raising=False)
    assert proxy.main() == 1
