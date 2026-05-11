"""Pytest fixtures for the Shadownet Hermes plugin tests.

The plugin's adapter module defers all Hermes imports to runtime so the
package loads without a Hermes install. To test the adapter we install a
minimal fake ``gateway.platforms.base`` module in ``sys.modules`` before
the adapter class is built. This mirrors the real Hermes adapter contract
closely enough to exercise the plugin's lifecycle and dispatch logic.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Any

# asyncio_mode = auto is configured in pytest.ini at the plugin root, so
# async def tests are picked up without explicit @pytest.mark.asyncio.


def _install_fake_hermes_module() -> None:
    """Install ``gateway.platforms.base`` shims into ``sys.modules``.

    Called at collection time so any test that imports the adapter sees
    them. We deliberately keep the shim narrow: a ``BasePlatformAdapter``
    base class with the methods the adapter calls (``handle_message``,
    ``_mark_connected``, ``_mark_disconnected``), plus a ``MessageEvent``
    dataclass and a ``SendResult`` dataclass.
    """
    if "gateway" in sys.modules:
        return  # already installed (e.g., a real Hermes install)

    gateway_pkg = types.ModuleType("gateway")
    gateway_pkg.__path__ = []  # type: ignore[attr-defined]
    platforms_pkg = types.ModuleType("gateway.platforms")
    platforms_pkg.__path__ = []  # type: ignore[attr-defined]
    base_module = types.ModuleType("gateway.platforms.base")

    @dataclass
    class MessageEvent:
        platform: str
        chat_id: str
        sender_id: str
        text: str
        raw: dict[str, Any]

    @dataclass
    class SendResult:
        success: bool

    class BasePlatformAdapter:
        def __init__(self, config: Any) -> None:
            self.config = config
            self.connected = False
            self.handled: list[MessageEvent] = []

        def _mark_connected(self) -> None:
            self.connected = True

        def _mark_disconnected(self) -> None:
            self.connected = False

        async def handle_message(self, event: MessageEvent) -> None:
            self.handled.append(event)

    base_module.BasePlatformAdapter = BasePlatformAdapter  # type: ignore[attr-defined]
    base_module.MessageEvent = MessageEvent  # type: ignore[attr-defined]
    base_module.SendResult = SendResult  # type: ignore[attr-defined]
    platforms_pkg.base = base_module  # type: ignore[attr-defined]

    sys.modules["gateway"] = gateway_pkg
    sys.modules["gateway.platforms"] = platforms_pkg
    sys.modules["gateway.platforms.base"] = base_module


_install_fake_hermes_module()
