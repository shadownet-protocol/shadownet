from __future__ import annotations


class ConformanceError(Exception):
    """Root for every error this package raises."""


class ConfigError(ConformanceError):
    """Raised when CLI flags or environment produce an invalid Config."""


class TargetUnreachable(ConformanceError):
    """Raised when a configured target URL cannot be contacted."""


class FixtureMissing(ConformanceError):
    """Raised when a required fixture file is absent or unreadable."""


class FixtureDrift(ConformanceError):
    """Raised by the regen --check path when a committed fixture differs from a freshly emitted one."""


class WireMismatch(ConformanceError):
    """Raised when a target's response disagrees with the spec at the byte/JSON level."""
