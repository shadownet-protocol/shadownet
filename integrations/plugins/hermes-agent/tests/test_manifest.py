from __future__ import annotations

from pathlib import Path

import yaml


def _load_manifest() -> dict:
    path = Path(__file__).resolve().parent.parent / "plugin.yaml"
    with path.open() as f:
        return yaml.safe_load(f)


def _pyproject_version() -> str:
    import tomllib

    path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with path.open("rb") as f:
        data = tomllib.load(f)
    return str(data["project"]["version"])


def test_manifest_version_matches_pyproject_and_kind() -> None:
    """plugin.yaml version is valid SemVer, matches pyproject, and kind is platform."""
    manifest = _load_manifest()
    version = manifest["version"]
    assert isinstance(version, str)
    parts = version.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)
    assert version == _pyproject_version()
    assert manifest["kind"] == "platform"


def test_manifest_lists_every_registered_surface() -> None:
    """provides_hooks / provides_commands / provides_skills are documented."""
    manifest = _load_manifest()
    assert set(manifest["provides_hooks"]) == {
        "on_session_start",
        "pre_llm_call",
        "on_session_end",
    }
    assert set(manifest["provides_commands"]) == {
        "shadownet-setup",
        "shadownet-inbox",
        "shadownet-reach-out",
        "shadownet-coordinate",
        "shadownet-status",
        "shadownet-logout",
    }
    assert set(manifest["provides_skills"]) == {
        "shadownet:shadownet-setup",
        "shadownet:shadownet-reach-out",
        "shadownet:shadownet-inbox",
        "shadownet:shadownet-coordinate",
        "shadownet:shadownet-autonomous",
    }
    # MCP tools come from config.yaml, not from register_tool — must NOT
    # appear under provides_tools.
    assert "provides_tools" not in manifest
