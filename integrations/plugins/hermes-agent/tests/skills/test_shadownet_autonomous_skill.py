from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# Canonical single-source skill tree: integrations/skills/<name>/SKILL.md
_SKILL = Path(__file__).resolve().parents[4] / "skills" / "shadownet-autonomous" / "SKILL.md"


def _frontmatter() -> dict:
    if not _SKILL.is_file():
        pytest.skip(f"canonical skill not found at {_SKILL}")
    text = _SKILL.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "SKILL.md must start with a YAML frontmatter block"
    return yaml.safe_load(m.group(1))


def test_description_meets_hardline_bar() -> None:
    desc = _frontmatter()["description"]
    assert len(desc) <= 60, f"description is {len(desc)} chars (> 60)"
    assert desc.endswith("."), "description must end with a period"
    assert desc.count(".") == 1, "description must be a single sentence"


def test_name_and_no_dead_metadata() -> None:
    fm = _frontmatter()
    assert fm["name"] == "shadownet-autonomous"
    hermes_meta = fm.get("metadata", {}).get("hermes", {})
    assert "activation_phrases" not in hermes_meta, "activation_phrases is dead metadata in Hermes"


def test_allowed_tools_use_hermes_single_underscore_form() -> None:
    for tool in _frontmatter().get("allowed-tools", []):
        assert "mcp__" not in tool, f"{tool} uses Claude double-underscore; Hermes uses mcp_*"
