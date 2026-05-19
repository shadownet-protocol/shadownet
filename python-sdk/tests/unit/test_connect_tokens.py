from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shadownet.connect.tokens import (
    FileTokenStore,
    KeyringTokenStore,
    TokenStore,
    default_store_path,
    default_token_store,
)

CONNECT_URL = (
    "shadownet://connect?base=https://app.example&handoff=8K3J9-W2L1Q-Y5R7T"
)


def test_file_store_round_trip(tmp_path: Path) -> None:
    store = FileTokenStore(root=tmp_path)
    assert store.load(CONNECT_URL) is None

    store.save(CONNECT_URL, "tok-abc")
    assert store.load(CONNECT_URL) == "tok-abc"


def test_file_store_keys_by_url_hash(tmp_path: Path) -> None:
    """Two different connect URLs land in two different cache files."""
    store = FileTokenStore(root=tmp_path)
    other_url = CONNECT_URL.replace("8K3J9-W2L1Q-Y5R7T", "ZZZ9-AAAAAAAAAAAAA")

    store.save(CONNECT_URL, "tok-a")
    store.save(other_url, "tok-b")

    assert store.load(CONNECT_URL) == "tok-a"
    assert store.load(other_url) == "tok-b"

    # Two distinct .json files on disk.
    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert len(files) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_file_store_writes_mode_0600(tmp_path: Path) -> None:
    store = FileTokenStore(root=tmp_path)
    store.save(CONNECT_URL, "tok-abc")

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    mode = stat.S_IMODE(files[0].stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_file_store_creates_root(tmp_path: Path) -> None:
    """save() creates the cache root if it doesn't exist."""
    root = tmp_path / "nested" / "handoff-tokens"
    assert not root.exists()

    store = FileTokenStore(root=root)
    store.save(CONNECT_URL, "tok-abc")

    assert root.is_dir()
    assert store.load(CONNECT_URL) == "tok-abc"


def test_file_store_load_missing_returns_none(tmp_path: Path) -> None:
    store = FileTokenStore(root=tmp_path)
    assert store.load(CONNECT_URL) is None


def test_file_store_load_corrupt_returns_none(tmp_path: Path) -> None:
    """A non-JSON file is treated as a cache miss, not an exception."""
    store = FileTokenStore(root=tmp_path)
    store.save(CONNECT_URL, "tok-abc")

    # Stomp the file with garbage.
    cache_file = next(tmp_path.glob("*.json"))
    cache_file.write_text("not valid json {", encoding="utf-8")

    assert store.load(CONNECT_URL) is None


def test_file_store_load_wrong_schema_returns_none(tmp_path: Path) -> None:
    store = FileTokenStore(root=tmp_path)
    cache_file = tmp_path / "abc.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    # Wrong cache file name on purpose; we'll write the expected one too.
    store.save(CONNECT_URL, "tok-abc")
    real_file = next(tmp_path.glob("*.json"))
    payload = json.loads(real_file.read_text())
    payload["version"] = 99
    real_file.write_text(json.dumps(payload))

    assert store.load(CONNECT_URL) is None


def test_file_store_satisfies_protocol(tmp_path: Path) -> None:
    """The concrete impl is structurally a TokenStore."""
    store: TokenStore = FileTokenStore(root=tmp_path)
    store.save(CONNECT_URL, "tok-abc")
    assert store.load(CONNECT_URL) == "tok-abc"


def test_default_store_path_returns_a_path() -> None:
    """default_store_path picks a path under the user's home/cache dirs."""
    path = default_store_path()
    assert isinstance(path, Path)
    assert "shadownet" in str(path)
    assert "handoff-tokens" in path.name


# ---------- KeyringTokenStore ----------------------------------------------


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Install a fake ``keyring`` module in sys.modules.

    Avoids depending on a real system keyring during tests. The fake
    behaves like a single in-memory dict.
    """
    storage: dict[tuple[str, str], str] = {}

    fake = MagicMock()
    fake.get_password.side_effect = lambda svc, key: storage.get((svc, key))

    def _set(svc: str, key: str, val: str) -> None:
        storage[(svc, key)] = val

    fake.set_password.side_effect = _set

    fake_errors = MagicMock()
    fake_errors.KeyringError = type("KeyringError", (Exception,), {})

    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setitem(sys.modules, "keyring.errors", fake_errors)
    return fake


def test_keyring_store_round_trip(fake_keyring: MagicMock) -> None:
    store = KeyringTokenStore()
    assert store.load(CONNECT_URL) is None

    store.save(CONNECT_URL, "tok-abc")
    assert store.load(CONNECT_URL) == "tok-abc"


def test_keyring_store_keys_by_url_hash(fake_keyring: MagicMock) -> None:
    store = KeyringTokenStore()
    other_url = CONNECT_URL.replace("8K3J9-W2L1Q-Y5R7T", "ZZZ9-AAAAAAAAAAAAA")

    store.save(CONNECT_URL, "tok-a")
    store.save(other_url, "tok-b")

    assert store.load(CONNECT_URL) == "tok-a"
    assert store.load(other_url) == "tok-b"


def test_keyring_store_raises_when_module_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `keyring` isn't importable the constructor errors clearly."""
    monkeypatch.setitem(sys.modules, "keyring", None)
    with pytest.raises(RuntimeError, match="requires the `keyring` package"):
        KeyringTokenStore()


def test_keyring_store_raises_on_backend_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the probe call fails (no backend reachable) we raise so the caller
    can fall back to FileTokenStore."""
    fake = MagicMock()
    fake_err = type("KeyringError", (Exception,), {})
    fake.get_password.side_effect = fake_err("no backend")
    fake_errors = MagicMock()
    fake_errors.KeyringError = fake_err
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setitem(sys.modules, "keyring.errors", fake_errors)
    with pytest.raises(RuntimeError, match="keyring backend is not available"):
        KeyringTokenStore()


def test_default_token_store_prefers_keyring(fake_keyring: MagicMock) -> None:
    store = default_token_store()
    assert isinstance(store, KeyringTokenStore)


def test_default_token_store_falls_back_when_keyring_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    store = default_token_store()
    assert isinstance(store, FileTokenStore)