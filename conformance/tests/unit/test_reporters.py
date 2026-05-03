from __future__ import annotations

from shadownet_conformance.reporters.gha_summary import render_summary_markdown


def test_render_summary_markdown_includes_section_table():
    report = {
        "summary": {"passed": 1, "failed": 1, "skipped": 0, "total": 2},
        "by_rfc": {
            "0004": {
                "Issuance": {
                    "csr_invalid": "passed",
                    "session_consumed": "failed",
                }
            }
        },
    }
    md = render_summary_markdown(report)
    assert "## Shadownet conformance" in md
    assert "**Total 2**" in md
    assert "passed 1" in md
    assert "failed 1" in md
    assert "### RFC-0004" in md
    assert "| Issuance | `csr_invalid` |" in md
    assert "✓ passed" in md
    assert "✗ failed" in md


def test_render_summary_markdown_with_no_results():
    md = render_summary_markdown({"summary": {}, "by_rfc": {}})
    assert "## Shadownet conformance" in md
    assert "**Total 0**" in md
