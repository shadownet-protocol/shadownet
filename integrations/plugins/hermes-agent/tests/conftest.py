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
    config_module = types.ModuleType("gateway.config")
    session_module = types.ModuleType("gateway.session")

    class Platform:
        def __init__(self, value: str = "shadownet") -> None:
            self.value = value

        def __eq__(self, other: object) -> bool:
            if isinstance(other, Platform):
                return self.value == other.value
            return NotImplemented

        def __hash__(self) -> int:
            return hash(self.value)

    @dataclass
    class SessionSource:
        platform: Any = None
        chat_id: str = ""
        user_id: str = ""
        user_name: str = ""

    @dataclass
    class MessageEvent:
        text: str = ""
        source: Any = None
        raw_message: Any = None
        internal: bool = False
        auto_skill: str | None = None
        message_type: Any = None

    @dataclass
    class SendResult:
        success: bool

    class BasePlatformAdapter:
        def __init__(self, config: Any, platform: Any = None) -> None:
            self.config = config
            self.platform = platform
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
    config_module.Platform = Platform  # type: ignore[attr-defined]
    session_module.SessionSource = SessionSource  # type: ignore[attr-defined]
    platforms_pkg.base = base_module  # type: ignore[attr-defined]

    sys.modules["gateway"] = gateway_pkg
    sys.modules["gateway.platforms"] = platforms_pkg
    sys.modules["gateway.platforms.base"] = base_module
    sys.modules["gateway.config"] = config_module
    sys.modules["gateway.session"] = session_module


_install_fake_hermes_module()
