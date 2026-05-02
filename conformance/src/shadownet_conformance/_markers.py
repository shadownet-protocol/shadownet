from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import pytest

MARKERS: Final[tuple[tuple[str, str], ...]] = (
    (
        "rfc(number, *, section, requirement)",
        "Tag a test with the RFC + section + requirement identifier it asserts. "
        "Required on every test under tests/{predicate,sca,sns,sidecar,e2e}.",
    ),
    (
        "class_(role)",
        "Tag a test with the conformance class it exercises: 'sca', 'sns', or 'sidecar'.",
    ),
    (
        "draft",
        "Test asserts behavior from an RFC marked Draft. Skipped unless --include-draft.",
    ),
    (
        "should",
        "Test asserts an RFC SHOULD (not MUST). Included by default; tallied separately.",
    ),
    (
        "network",
        "Test requires live network access to a configured target. "
        "Auto-skipped when the target is absent.",
    ),
    (
        "round_trip(role)",
        "Test runs only when both --target <role> and --peer-target <role> are configured.",
    ),
)


def register(config: pytest.Config) -> None:
    """Register every shadownet-conformance pytest marker on the given config."""
    for definition, description in MARKERS:
        config.addinivalue_line("markers", f"{definition}: {description}")
