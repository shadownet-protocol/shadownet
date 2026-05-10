"""Render an RFC-keyed report as GitHub Actions step-summary markdown."""

from __future__ import annotations

from typing import Any

_OUTCOME_GLYPH = {"passed": "✓", "failed": "✗", "skipped": "—"}


def render_summary_markdown(report: dict[str, Any]) -> str:
    """Render `report` (the RFC-JSON report) as markdown for $GITHUB_STEP_SUMMARY."""
    lines: list[str] = []
    summary = report.get("summary", {})
    lines.append("## Shadownet conformance")
    lines.append("")
    lines.append(
        f"**Total {summary.get('total', 0)}** — "
        f"passed {summary.get('passed', 0)}, "
        f"failed {summary.get('failed', 0)}, "
        f"skipped {summary.get('skipped', 0)}"
    )
    lines.append("")

    by_rfc = report.get("by_rfc", {})
    for rfc in sorted(by_rfc):
        sections = by_rfc[rfc]
        lines.append(f"### RFC-{rfc}")
        lines.append("")
        lines.append("| Section | Requirement | Result |")
        lines.append("| --- | --- | --- |")
        for section in sorted(sections):
            for requirement in sorted(sections[section]):
                outcome = sections[section][requirement]
                glyph = _OUTCOME_GLYPH.get(outcome, "?")
                lines.append(f"| {section} | `{requirement}` | {glyph} {outcome} |")
        lines.append("")

    return "\n".join(lines)
