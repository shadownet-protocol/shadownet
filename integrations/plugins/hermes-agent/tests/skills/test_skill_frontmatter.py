from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# Plugin-local skill tree: integrations/plugins/hermes-agent/skills/<name>/SKILL.md
_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

# The full skill set, all held to the Hermes HARDLINE bar.
_COMPLIANT = [
    "shadownet-setup",
    "shadownet-messaging",
    "shadownet-coordinate",
    "shadownet-autonomous",
]


def _frontmatter(name: str) -> dict:
    path = _SKILLS_DIR / name / "SKILL.md"
    if not path.is_file():
        pytest.skip(f"canonical skill not found at {path}")
    m = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.DOTALL)
    assert m, f"{name}: SKILL.md must start with a YAML frontmatter block"
    return yaml.safe_load(m.group(1))


@pytest.mark.parametrize("name", _COMPLIANT)
def test_description_meets_hardline_bar(name: str) -> None:
    desc = _frontmatter(name)["description"]
    assert len(desc) <= 60, f"{name}: description is {len(desc)} chars (> 60)"
    assert desc.endswith("."), f"{name}: description must end with a period"
    assert desc.count(".") == 1, f"{name}: description must be a single sentence"


@pytest.mark.parametrize("name", _COMPLIANT)
def test_name_matches_and_no_dead_metadata(name: str) -> None:
    fm = _frontmatter(name)
    assert fm["name"] == name
    hermes_meta = fm.get("metadata", {}).get("hermes", {})
    assert "activation_phrases" not in hermes_meta, f"{name}: activation_phrases is dead metadata"


@pytest.mark.parametrize("name", _COMPLIANT)
def test_allowed_tools_use_hermes_single_underscore_form(name: str) -> None:
    for tool in _frontmatter(name).get("allowed-tools", []):
        assert "mcp__" not in tool, (
            f"{name}: {tool} uses Claude double-underscore; Hermes uses mcp_*"
        )
