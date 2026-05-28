"""Lint-style guard: ContactProfile field names MUST NOT appear in any module
under shadownet.a2a, shadownet.connect, or shadownet.sns.

Per RFC-0007 §Contact profile, the profile is local-only — it MUST NOT cross
any wire egress. This is the SDK-level analog of the cloud/local repo lint
the spec brief asks for: if any wire-emitting module accidentally references
``notes`` / ``priority`` / ``collaborate_on`` / ``expires_at`` in the context
of a ContactProfile, we want CI to fail before the leak ships.
"""

from __future__ import annotations

from pathlib import Path

# Field names that uniquely identify a ContactProfile shape. We scan for any
# of these as substrings on a line whose lexical context plausibly references
# a profile — to keep false positives low we just check that the symbol
# "ContactProfile" never appears in the wire modules.
FORBIDDEN_SYMBOL = "ContactProfile"

WIRE_PACKAGES = ("shadownet/a2a", "shadownet/connect", "shadownet/sns")


def _source_files() -> list[Path]:
    src = Path(__file__).resolve().parent.parent.parent / "src"
    out: list[Path] = []
    for pkg in WIRE_PACKAGES:
        pkg_dir = src / pkg
        if not pkg_dir.is_dir():
            continue
        out.extend(p for p in pkg_dir.rglob("*.py") if p.is_file())
    return out


def test_contact_profile_does_not_appear_in_wire_modules() -> None:
    offenders: list[Path] = []
    for path in _source_files():
        body = path.read_text()
        if FORBIDDEN_SYMBOL in body:
            offenders.append(path)
    assert not offenders, (
        f"ContactProfile leaked into wire-emitting modules: {[str(p) for p in offenders]}. "
        "ContactProfile MUST NOT cross any wire egress per RFC-0007 §Contact profile."
    )


def test_some_wire_files_are_actually_present() -> None:
    """Guard against the previous test trivially passing on an empty discovery."""
    files = _source_files()
    assert len(files) >= 3, f"expected ≥3 wire modules under {WIRE_PACKAGES}, found {files}"
