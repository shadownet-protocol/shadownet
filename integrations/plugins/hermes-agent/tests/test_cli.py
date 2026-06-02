from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from shadownet_hermes_plugin import _cli


def _make_args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def test_setup_registers_all_subcommands() -> None:
    """The argparse tree exposes status / doctor / sync / logout."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers().add_parser("shadownet")
    _cli.setup(sub)
    parsed = parser.parse_args(["shadownet", "status"])
    assert parsed.shadownet_cmd == "status"
    parsed = parser.parse_args(["shadownet", "logout"])
    assert parsed.shadownet_cmd == "logout"


def test_handle_prints_usage_for_unknown_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    _cli.handle(_make_args(shadownet_cmd=None))
    captured = capsys.readouterr()
    assert "Usage: hermes shadownet" in captured.out


def test_do_status_reports_each_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("SHADOWNET_CONNECT_URL=shadow://connect?mcp=https://x&token=t\n")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"mcp_servers": {"shadownet": {"url": "https://api/mcp"}}})
    )
    out = _cli.do_status()
    assert "shadownet plugin status" in out
    assert "https://api/mcp" in out
    assert "materialized skills" in out


def test_do_logout_removes_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "SHADOWNET_CONNECT_URL=shadow://connect?mcp=https://x&token=t\nOTHER=1\n"
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "mcp_servers": {"shadownet": {"url": "https://x/mcp"}},
                "gateway": {"platforms": {"shadownet": {"enabled": True}}},
            }
        )
    )
    out = _cli.do_logout()
    assert "Disconnected" in out
    env = (tmp_path / ".env").read_text()
    assert "SHADOWNET_CONNECT_URL" not in env
    assert "OTHER=1" in env
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert "mcp_servers" not in cfg
    assert cfg["gateway"]["platforms"]["shadownet"]["enabled"] is False


def test_do_logout_idempotent_when_nothing_to_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    out = _cli.do_logout()
    assert "already disconnected" in out


def test_do_doctor_returns_overall_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """doctor emits OK/FAIL per check and an overall summary line."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    out = _cli.do_doctor()
    assert "shadownet plugin doctor" in out
    assert "overall: FAIL" in out
    assert "[FAIL]" in out


def test_do_sync_writes_skills_and_returns_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sync re-materializes skills under the categorized layout and returns a confirmation."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("SHADOWNET_CONNECT_URL", raising=False)
    out = _cli.do_sync()
    assert "sync complete" in out
    from shadownet_hermes_plugin import _skills

    cat_root = tmp_path / "skills" / _skills.SHADOWNET_CATEGORY
    # At least the DESCRIPTION.md should land even when bundled SKILLs aren't.
    assert (cat_root / "DESCRIPTION.md").is_file()
