"""Client-side persistence for tokens redeemed from handoff connect URLs.

RFC-0008 connect URLs come in two forms: inline (`?token=<jwt>`) carries
the bearer in the URL; handoff (`?handoff=<code>`) carries a single-use
short code that the client trades for a long-lived token via
``POST <base>/v1/account/connect/handoff/<code>``. After redemption the
code returns 404 — by design.

Most host agents (Claude Code, Hermes Agent, OpenClaw) re-resolve their
credentials on every process start. Without persistence between those
starts, handoff URLs work exactly once. The :class:`TokenStore` Protocol
plus the two concrete implementations let a plugin redeem once and reuse
the token across the host's normal restart cycle.

:class:`KeyringTokenStore` is the **recommended default**: it stores the
redeemed token in the OS-managed secret store (Login keychain on macOS,
Secret Service on Linux, Credential Manager on Windows) via the
``keyring`` library. The token is encrypted at rest, locked on user
logout (macOS Login keychain), and skipped by typical backup software.

:class:`FileTokenStore` is the fallback for environments without an OS
secret store (headless Linux without DBus, CI runners, integration
tests). It writes a 0o600 JSON file under the OS state directory:

* macOS:   ``~/Library/Application Support/shadownet/handoff-tokens/``
* Linux:   ``$XDG_STATE_HOME/shadownet/handoff-tokens/`` (default
  ``~/.local/state/shadownet/handoff-tokens/``)
* Windows: ``%LOCALAPPDATA%\\shadownet\\handoff-tokens\\``

Use :func:`default_token_store` to get the best-available store
(``KeyringTokenStore`` if ``keyring`` is importable and a backend
responds, else ``FileTokenStore``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

__all__ = [
    "FileTokenStore",
    "KeyringTokenStore",
    "TokenStore",
    "default_store_path",
    "default_token_store",
]

_log = logging.getLogger(__name__)
_SCHEMA_VERSION = 1


@runtime_checkable
class TokenStore(Protocol):
    """Persistence Protocol for handoff-redeemed tokens."""

    def load(self, connect_url: str) -> str | None:
        """Return the cached token for ``connect_url``, or None."""

    def save(self, connect_url: str, token: str) -> None:
        """Persist ``token`` against ``connect_url``."""


def default_store_path() -> Path:
    """Return the OS-appropriate cache directory for redeemed tokens."""
    # Type as `str` (not `Literal`) so mypy's per-platform unreachable
    # analysis doesn't flag the linux branch on macOS/Windows builds.
    platform: str = sys.platform
    if platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "shadownet" / "handoff-tokens"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "shadownet" / "handoff-tokens"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "shadownet" / "handoff-tokens"


def _cache_key(connect_url: str) -> str:
    return hashlib.sha256(connect_url.encode("utf-8")).hexdigest()


class FileTokenStore:
    """JSON-file token cache: one file per ``sha256(connect_url)``.

    Cache layout::

        <root>/
          <hex-sha256>.json    {"version": 1, "token": "...", "redeemed_at": "..."}

    A corrupt or unreadable file is treated as a cache miss so the caller
    can re-redeem; corruption is logged at WARNING.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or default_store_path()

    @property
    def root(self) -> Path:
        return self._root

    def load(self, connect_url: str) -> str | None:
        path = self._path(connect_url)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            _log.warning("token cache at %s unreadable: %s", path, exc)
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            _log.warning("token cache at %s is not valid JSON; ignoring", path)
            return None
        if not isinstance(payload, dict) or payload.get("version") != _SCHEMA_VERSION:
            return None
        token = payload.get("token")
        return token if isinstance(token, str) and token else None

    def save(self, connect_url: str, token: str) -> None:
        path = self._path(connect_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        # POSIX: keep the parent dir private. Windows ignores mode but it's harmless.
        with contextlib.suppress(OSError):
            os.chmod(path.parent, 0o700)
        payload = {
            "version": _SCHEMA_VERSION,
            "token": token,
            "redeemed_at": datetime.now(UTC).isoformat(),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        # Open with explicit mode so the file is 0o600 from creation, no
        # umask race window.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        os.replace(tmp, path)

    def _path(self, connect_url: str) -> Path:
        return self._root / f"{_cache_key(connect_url)}.json"


_KEYRING_SERVICE = "shadownet-handoff-tokens"


class KeyringTokenStore:
    """Token store backed by the OS-managed secret store via ``keyring``.

    On macOS this uses the Login keychain; on Linux the Secret Service
    (gnome-keyring / KWallet); on Windows the Credential Manager. The
    ``keyring`` package is an optional dependency — instantiating this
    class raises :class:`RuntimeError` when the library isn't importable
    or when no backend responds (headless Linux without DBus is the
    common case).
    """

    def __init__(self) -> None:
        try:
            import keyring
            from keyring.errors import KeyringError
        except ImportError as exc:
            raise RuntimeError(
                "KeyringTokenStore requires the `keyring` package. "
                "Install with `pip install keyring`, or use FileTokenStore."
            ) from exc
        # Sanity-check that a backend is reachable. `keyring.get_keyring()`
        # returns a backend object even when the system has none reachable
        # (e.g. headless Linux); we probe with a no-op get_password call.
        try:
            keyring.get_password(_KEYRING_SERVICE, "__probe__")
        except KeyringError as exc:
            raise RuntimeError(
                f"keyring backend is not available: {exc}. "
                "Fall back to FileTokenStore in this environment."
            ) from exc
        self._keyring = keyring

    def load(self, connect_url: str) -> str | None:
        return cast(
            "str | None",
            self._keyring.get_password(_KEYRING_SERVICE, _cache_key(connect_url)),
        )

    def save(self, connect_url: str, token: str) -> None:
        self._keyring.set_password(_KEYRING_SERVICE, _cache_key(connect_url), token)


def default_token_store() -> TokenStore:
    """Return the best-available :class:`TokenStore` for this environment.

    Prefers :class:`KeyringTokenStore`; falls back to
    :class:`FileTokenStore` when ``keyring`` isn't installed or no
    backend responds.
    """
    try:
        return KeyringTokenStore()
    except RuntimeError as exc:
        _log.info(
            "KeyringTokenStore unavailable (%s); falling back to FileTokenStore",
            exc,
        )
        return FileTokenStore()
