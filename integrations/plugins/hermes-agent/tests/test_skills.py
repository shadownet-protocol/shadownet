from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shadownet_hermes_plugin import _skills


def test_register_skills_calls_ctx_register_skill_per_skill() -> None:
    """register_skills(ctx) calls ctx.register_skill once per bundled SKILL.md."""
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    count = _skills.register_skills(ctx)
    assert count == len(_skills.SKILL_NAMES)
    assert {name for name, _ in ctx.skills} == set(_skills.SKILL_NAMES)


def test_materialize_skills_lands_under_category_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skills are copied to <data>/skills/shadownet/<name>/ with DESCRIPTION.md."""
    src_root = tmp_path / "src"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("HERMES_DATA_DIR", str(data_dir))

    paths: dict[str, Path] = {}
    for name in _skills.SKILL_NAMES:
        d = src_root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {name}")
        paths[name] = d / "SKILL.md"

    _skills.materialize_skills_into_data_dir(paths)

    cat = _skills.SHADOWNET_CATEGORY
    for name in _skills.SKILL_NAMES:
        assert (data_dir / "skills" / cat / name / "SKILL.md").is_file()
    assert (data_dir / "skills" / cat / "DESCRIPTION.md").is_file()


def test_count_materialized_skills_reports_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """count_materialized_skills counts SKILL.md files under the category dir."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("HERMES_DATA_DIR", str(data_dir))

    assert _skills.count_materialized_skills() == 0

    cat_root = data_dir / "skills" / _skills.SHADOWNET_CATEGORY
    for i, name in enumerate(_skills.SKILL_NAMES):
        d = cat_root / name
        d.mkdir(parents=True)
        if i % 2 == 0:
            (d / "SKILL.md").write_text("# stub")

    expected_present = sum(1 for i in range(len(_skills.SKILL_NAMES)) if i % 2 == 0)
    assert _skills.count_materialized_skills() == expected_present


def test_register_skills_warns_when_files_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the bundled SKILL.md files cannot be resolved, no registrations happen."""
    nonexistent = tmp_path / "no-skills"
    monkeypatch.setattr(_skills, "skill_root_candidates", lambda: (nonexistent,))
    from tests.conftest import FakeCtx

    ctx = FakeCtx()
    count = _skills.register_skills(ctx)
    assert count == 0
    assert ctx.skills == []


def _unused_typing_import_anchor() -> Any:
    return None
