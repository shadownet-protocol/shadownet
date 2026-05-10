#!/usr/bin/env python3
"""Sync canonical SKILL.md files into per-plugin trees.

Why this exists: Claude Code plugins are copied into ``~/.claude/plugins/cache``
on install and cannot reference files outside the plugin directory (no
``../shared/`` paths). Hermes Agent reads SKILL.md content from URLs we publish
ourselves, so its bundle could in principle reference the canonical path
directly — but keeping a copy under each plugin tree is simpler and lets
contributors browse the full plugin in one directory. The single source of
truth stays at ``integrations/skills/``; this script materialises it into
``integrations/plugins/{claude-code,hermes-agent}/skills/``.

Usage::

    python integrations/scripts/sync_skills.py            # copy canonical -> plugins
    python integrations/scripts/sync_skills.py --check    # exit non-zero on drift
    python integrations/scripts/sync_skills.py --from-canonical  # also drop stale entries

(Repo root has a Makefile shortcut: ``make sync-skills`` / ``make check-skills``.)

The frontmatter of each SKILL.md is dual-flavoured (top-level Claude fields +
``metadata.hermes.*``), so the same byte-for-byte content works in both
ecosystems and the sync is a verbatim copy.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

INTEGRATIONS_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = INTEGRATIONS_ROOT / "skills"
PLUGIN_TARGETS = (
    INTEGRATIONS_ROOT / "plugins" / "claude-code" / "skills",
    INTEGRATIONS_ROOT / "plugins" / "hermes-agent" / "skills",
)


def _canonical_skills() -> dict[str, Path]:
    """Map skill name -> SKILL.md path in the canonical dir."""
    out: dict[str, Path] = {}
    if not CANONICAL_DIR.exists():
        return out
    for entry in sorted(CANONICAL_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        out[entry.name] = skill_md
    return out


def _plugin_skills(target_dir: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not target_dir.exists():
        return out
    for entry in sorted(target_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        skill_md = entry / "SKILL.md"
        if skill_md.exists():
            out[entry.name] = skill_md
    return out


def _check_drift(canonical: dict[str, Path], target_dir: Path) -> list[str]:
    """Return a list of human-readable drift messages, empty if clean."""
    messages: list[str] = []
    plugin = _plugin_skills(target_dir)
    canonical_names = set(canonical)
    plugin_names = set(plugin)
    for missing in sorted(canonical_names - plugin_names):
        messages.append(f"  missing in {target_dir}: {missing}/")
    for extra in sorted(plugin_names - canonical_names):
        messages.append(f"  stale in {target_dir}: {extra}/ (no longer canonical)")
    for name in sorted(canonical_names & plugin_names):
        canonical_bytes = canonical[name].read_bytes()
        plugin_bytes = plugin[name].read_bytes()
        if canonical_bytes != plugin_bytes:
            messages.append(f"  drift in {target_dir}: {name}/SKILL.md differs from canonical")
    return messages


def _sync(canonical: dict[str, Path], target_dir: Path, *, drop_stale: bool) -> int:
    """Copy canonical entries into target_dir. Returns count of changes."""
    changes = 0
    target_dir.mkdir(parents=True, exist_ok=True)
    plugin = _plugin_skills(target_dir)
    for name, src in canonical.items():
        dst_dir = target_dir / name
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / "SKILL.md"
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dst)
            changes += 1
    if drop_stale:
        for name in plugin:
            if name not in canonical:
                shutil.rmtree(target_dir / name)
                changes += 1
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any plugin tree drifts from the canonical source.",
    )
    parser.add_argument(
        "--from-canonical",
        action="store_true",
        help="Also remove plugin entries that no longer exist in the canonical source.",
    )
    args = parser.parse_args(argv)

    canonical = _canonical_skills()
    if not canonical:
        print(f"no skills found at {CANONICAL_DIR}", file=sys.stderr)
        return 1

    if args.check:
        all_messages: list[str] = []
        for target in PLUGIN_TARGETS:
            all_messages.extend(_check_drift(canonical, target))
        if all_messages:
            print("Skill drift detected:", file=sys.stderr)
            for msg in all_messages:
                print(msg, file=sys.stderr)
            print(
                "\nRun `python integrations/scripts/sync_skills.py` (or `make sync-skills`) to refresh plugin trees.",
                file=sys.stderr,
            )
            return 1
        print(f"all skill trees in sync ({len(canonical)} skills × {len(PLUGIN_TARGETS)} plugins)")
        return 0

    total_changes = 0
    for target in PLUGIN_TARGETS:
        changed = _sync(canonical, target, drop_stale=args.from_canonical)
        if changed:
            print(f"  synced {changed} change(s) into {target}")
        total_changes += changed
    if total_changes == 0:
        print("nothing to sync — all plugin trees already match canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
