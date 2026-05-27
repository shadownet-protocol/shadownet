from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shadownet_hermes_plugin import _mcp_config


def _seed_config(data_dir: Path, payload: dict) -> Path:
    config_path = data_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload))
    return config_path


def test_read_mcp_server_config_returns_existing_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    _seed_config(
        tmp_path,
        {
            "mcp_servers": {
                "shadownet": {"url": "https://api.example/mcp", "timeout": 120},
            }
        },
    )
    entry = _mcp_config.read_mcp_server_config()
    assert entry is not None
    assert entry["url"] == "https://api.example/mcp"


def test_read_mcp_server_config_absent_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    _seed_config(tmp_path, {"gateway": {"platforms": {}}})
    assert _mcp_config.read_mcp_server_config() is None


def test_remove_mcp_server_drops_entry_and_empty_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    config_path = _seed_config(
        tmp_path,
        {"mcp_servers": {"shadownet": {"url": "https://x/mcp"}}, "other": 1},
    )
    changed = _mcp_config.remove_mcp_server_from_config()
    assert changed is True
    loaded = yaml.safe_load(config_path.read_text())
    assert "mcp_servers" not in loaded
    assert loaded["other"] == 1


def test_remove_mcp_server_noop_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    _seed_config(tmp_path, {"other": 2})
    assert _mcp_config.remove_mcp_server_from_config() is False


def test_set_platform_enabled_toggles_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    _seed_config(tmp_path, {})

    assert _mcp_config.set_platform_enabled("shadownet", False) is True
    loaded = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert loaded["gateway"]["platforms"]["shadownet"]["enabled"] is False

    # Idempotent: setting the same value again should not rewrite.
    assert _mcp_config.set_platform_enabled("shadownet", False) is False

    # Flipping to True changes the file again.
    assert _mcp_config.set_platform_enabled("shadownet", True) is True
    loaded = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert loaded["gateway"]["platforms"]["shadownet"]["enabled"] is True


def test_ensure_mcp_server_skips_without_connect_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ensure_mcp_server_in_config is a no-op when SHADOWNET_CONNECT_URL is unset."""
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    _mcp_config.ensure_mcp_server_in_config()
    # config.yaml should not have been created.
    assert not (tmp_path / "config.yaml").is_file()


def test_ensure_mcp_server_writes_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ensure_mcp_server_in_config writes mcp_servers.shadownet using the bundle's endpoint."""
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        "shadownet://connect?base=https://app.example&token=tok-abc",
    )

    class _FakeResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"mcp_endpoint": "https://api.example/mcp/v1"}

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get(self, url: str, headers: dict) -> _FakeResp:
            assert headers["Authorization"] == "Bearer tok-abc"
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    _mcp_config.ensure_mcp_server_in_config()

    loaded = yaml.safe_load((tmp_path / "config.yaml").read_text())
    entry = loaded["mcp_servers"]["shadownet"]
    assert entry["url"] == "https://api.example/mcp/v1"
    assert entry["headers"]["Authorization"] == "Bearer tok-abc"
