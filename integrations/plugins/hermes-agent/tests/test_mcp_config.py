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
    """ensure_mcp_server_in_config writes mcp_servers.shadownet from an inline connect URI.

    RFC 0003 §3 inline URIs carry the MCP endpoint and bearer token directly,
    so the v0.2 code path makes no HTTP call to fetch a bundle.
    """
    from urllib.parse import quote

    mcp_endpoint = "https://api.example/mcp/v1"
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        f"shadow://connect?mcp={quote(mcp_endpoint, safe='')}&token=tok-abc",
    )

    _mcp_config.ensure_mcp_server_in_config()

    loaded = yaml.safe_load((tmp_path / "config.yaml").read_text())
    entry = loaded["mcp_servers"]["shadownet"]
    assert entry["url"] == mcp_endpoint
    assert entry["headers"]["Authorization"] == "Bearer tok-abc"


def test_ensure_mcp_server_skips_handoff_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handoff URIs require redemption first — the plugin skips the config write."""
    from urllib.parse import quote

    mcp_endpoint = "https://api.example/mcp/v1"
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "SHADOWNET_CONNECT_URL",
        f"shadow://connect?mcp={quote(mcp_endpoint, safe='')}&handoff=8K3J9-W2L1Q-Y5R7T-V1234",
    )

    _mcp_config.ensure_mcp_server_in_config()

    # No config.yaml should have been created; handoff redemption is a host
    # LLM concern, not the plugin's runtime concern.
    assert not (tmp_path / "config.yaml").is_file()
