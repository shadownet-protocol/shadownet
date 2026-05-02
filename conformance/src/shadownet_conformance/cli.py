from __future__ import annotations

import logging
import os
import sys
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shadownet_conformance.config import Config
from shadownet_conformance.errors import ConformanceError
from shadownet_conformance.logging import LOGGER_PREFIX, get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

_logger = get_logger(__name__)

CONFIG_ENV_PATH = "_SHADOWNET_CONFORMANCE_CONFIG"


def main(argv: Sequence[str] | None = None) -> int:
    """Parse args, materialize a Config, hand off to pytest."""
    _configure_logging()
    try:
        config, _ = Config.from_argv(list(argv) if argv is not None else None)
    except ConformanceError as exc:
        print(f"shadownet-conformance: {exc}", file=sys.stderr)
        return 2

    _logger.info(
        "starting conformance run",
        extra={
            "targets": {role.value: url for role, url in config.targets.items()},
            "peer_targets": {role.value: url for role, url in config.peer_targets.items()},
        },
    )

    pytest_args = _build_pytest_args(config)
    _serialize_config_for_pytest(config)
    return pytest.main(pytest_args)


def _build_pytest_args(config: Config) -> list[str]:
    tests_dir = _tests_dir()
    args: list[str] = [str(tests_dir)]
    if config.report_junit is not None:
        args.append(f"--junit-xml={config.report_junit}")
    if config.marker_expr:
        args.extend(["-m", config.marker_expr])
    return args


def _tests_dir() -> Path:
    """Locate the shipped tests/ directory.

    During development the source tree's tests/ directory is used directly.
    When installed from a wheel, tests are not packaged; users invoke pytest
    against their own checkout. We resolve relative to this file.
    """
    pkg_root = files("shadownet_conformance")
    candidate = Path(str(pkg_root)).parent.parent / "tests"
    if candidate.is_dir():
        return candidate
    raise ConformanceError(
        "could not locate the bundled tests/ directory; "
        "install the package from source or pass paths explicitly to pytest"
    )


def _serialize_config_for_pytest(config: Config) -> None:
    """Hand the Config to the pytest process via env (works across pytest.main)."""
    os.environ[CONFIG_ENV_PATH] = config.model_dump_json()


def _configure_logging() -> None:
    level_name = os.environ.get(f"{LOGGER_PREFIX.upper()}_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
