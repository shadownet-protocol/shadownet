"""RFC-keyed JSON report + marker enforcement.

This module is a pytest plugin loaded from `tests/conftest.py`. It does three
things:

1. Enforces that every test under `tests/{predicate,sca,sns,sidecar,e2e}/`
   carries an `rfc(number, section=..., requirement=...)` marker. Tests
   without one cause collection to fail with a clear message.
2. Builds an RFC-keyed JSON report keyed by `(rfc, section, requirement)`
   and writes it to `Config.report_json` if set.
3. Returns the same data so the GHA summary writer can reuse it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from shadownet_conformance._version import __version__
from shadownet_conformance.errors import ConformanceError

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

# Test directories where `pytest.mark.rfc(...)` is required.
RFC_REQUIRED_DIRS: Final[tuple[str, ...]] = (
    "predicate",
    "sca",
    "sns",
    "sidecar",
    "e2e",
)


@dataclass(slots=True)
class _Outcome:
    rfc: str
    section: str
    requirement: str
    nodeid: str
    outcome: str  # passed | failed | skipped
    duration_seconds: float = 0.0
    longrepr: str | None = None


@dataclass(slots=True)
class _Report:
    outcomes: list[_Outcome] = field(default_factory=list)

    def add(self, outcome: _Outcome) -> None:
        self.outcomes.append(outcome)

    def to_json(self) -> dict[str, object]:
        by_rfc: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
        summary = {"passed": 0, "failed": 0, "skipped": 0, "total": 0}
        for o in self.outcomes:
            by_rfc[o.rfc][o.section][o.requirement] = o.outcome
            summary["total"] += 1
            if o.outcome in summary:
                summary[o.outcome] += 1
        return {
            "version": "0.1",
            "tool_version": __version__,
            "summary": summary,
            "by_rfc": {
                rfc: {section: dict(reqs) for section, reqs in sections.items()}
                for rfc, sections in by_rfc.items()
            },
            "tests": [
                {
                    "rfc": o.rfc,
                    "section": o.section,
                    "requirement": o.requirement,
                    "nodeid": o.nodeid,
                    "outcome": o.outcome,
                    "duration_seconds": o.duration_seconds,
                    "longrepr": o.longrepr,
                }
                for o in self.outcomes
            ],
        }


_REPORT_KEY = pytest.StashKey[_Report]()


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_REPORT_KEY] = _Report()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    missing: list[str] = []
    for item in items:
        if not _is_in_required_dir(item):
            continue
        marker = item.get_closest_marker("rfc")
        if marker is None or not marker.args:
            missing.append(item.nodeid)
            continue
        # Require explicit section + requirement kwargs.
        section = marker.kwargs.get("section")
        requirement = marker.kwargs.get("requirement")
        if not section or not requirement:
            missing.append(f"{item.nodeid} — rfc marker missing section= or requirement=")
    if missing:
        formatted = "\n  ".join(missing)
        raise ConformanceError(
            "the following tests are under tests/{"
            + ",".join(RFC_REQUIRED_DIRS)
            + "}/ but lack a complete rfc(number, section=..., requirement=...) marker:\n  "
            + formatted
        )


def _is_in_required_dir(item: pytest.Item) -> bool:
    parts = Path(item.nodeid.split("::", 1)[0]).parts
    if len(parts) < 2 or parts[0] != "tests":
        return False
    return parts[1] in RFC_REQUIRED_DIRS


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, None]:
    outcome_obj = yield
    test_report = outcome_obj.get_result()
    if test_report.when != "call" and not (test_report.when == "setup" and test_report.skipped):
        return

    marker = item.get_closest_marker("rfc")
    if (
        marker is None
        or not marker.args
        or "section" not in marker.kwargs
        or "requirement" not in marker.kwargs
    ):
        # Tests outside the required dirs may run without the marker; we skip
        # them in the structured report rather than fail collection here.
        return

    rfc = str(marker.args[0])
    section = str(marker.kwargs["section"])
    requirement = str(marker.kwargs["requirement"])

    if test_report.passed:
        outcome = "passed"
    elif test_report.skipped:
        outcome = "skipped"
    else:
        outcome = "failed"

    longrepr: str | None = None
    if outcome == "failed" and test_report.longrepr is not None:
        longrepr = str(test_report.longrepr)

    report = item.config.stash[_REPORT_KEY]
    report.add(
        _Outcome(
            rfc=rfc,
            section=section,
            requirement=requirement,
            nodeid=item.nodeid,
            outcome=outcome,
            duration_seconds=getattr(test_report, "duration", 0.0),
            longrepr=longrepr,
        )
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    report = config.stash.get(_REPORT_KEY, None)
    if report is None:
        return

    report_path = _resolve_report_path(config)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report.to_json(), indent=2) + "\n")

    summary_path = _resolve_gha_summary_path(config)
    if summary_path is not None:
        from shadownet_conformance.reporters.gha_summary import (
            render_summary_markdown,
        )

        markdown = render_summary_markdown(report.to_json())
        with summary_path.open("a", encoding="utf-8") as fh:
            fh.write(markdown)
            fh.write("\n")


def _resolve_report_path(config: pytest.Config) -> Path | None:
    return _config_path(config, "report_json")


def _resolve_gha_summary_path(config: pytest.Config) -> Path | None:
    return _config_path(config, "gha_summary")


def _config_path(config: pytest.Config, attr: str) -> Path | None:
    cfg = _serialized_config(config)
    if cfg is None:
        return None
    raw = cfg.get(attr)
    if not isinstance(raw, str) or not raw:
        return None
    return Path(raw)


def _serialized_config(config: pytest.Config) -> dict[str, object] | None:
    import os

    from shadownet_conformance.cli import CONFIG_ENV_PATH

    serialized = os.environ.get(CONFIG_ENV_PATH)
    if not serialized:
        return None
    parsed: dict[str, object] = json.loads(serialized)
    return parsed


# Re-export for tests / programmatic use
__all__ = ["RFC_REQUIRED_DIRS", "_REPORT_KEY"]


def collected_outcomes(config: pytest.Config) -> Iterable[_Outcome]:
    """For internal/test use: return outcomes collected by this plugin."""
    return tuple(config.stash[_REPORT_KEY].outcomes)
