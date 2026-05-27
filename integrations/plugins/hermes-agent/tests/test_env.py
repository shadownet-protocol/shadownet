from __future__ import annotations

from pathlib import Path

import pytest

from shadownet_hermes_plugin import _env


def test_read_connect_url_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "OTHER_VAR=foo\n"
        "SHADOWNET_CONNECT_URL=shadownet://connect?base=https://x&token=t\n"
        "MORE=bar\n"
    )
    value = _env.read_connect_url_from_env()
    assert value == "shadownet://connect?base=https://x&token=t"


def test_read_connect_url_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("OTHER=1\n")
    assert _env.read_connect_url_from_env() is None


def test_read_connect_url_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    assert _env.read_connect_url_from_env() is None


def test_strip_connect_url_removes_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "OTHER=1\nSHADOWNET_CONNECT_URL=shadownet://connect?base=https://x&token=t\nTRAILING=2\n"
    )
    assert _env.strip_connect_url_from_env() is True
    remaining = (tmp_path / ".env").read_text()
    assert "SHADOWNET_CONNECT_URL" not in remaining
    assert "OTHER=1" in remaining
    assert "TRAILING=2" in remaining


def test_strip_connect_url_noop_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text("OTHER=1\n")
    assert _env.strip_connect_url_from_env() is False


def test_strip_connect_url_handles_export_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    (tmp_path / ".env").write_text(
        "OTHER=1\nexport SHADOWNET_CONNECT_URL=shadownet://connect?base=https://x&token=t\n"
    )
    assert _env.strip_connect_url_from_env() is True
    remaining = (tmp_path / ".env").read_text()
    assert "SHADOWNET_CONNECT_URL" not in remaining
