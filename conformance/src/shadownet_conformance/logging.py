from __future__ import annotations

import logging

LOGGER_PREFIX = "shadownet_conformance"


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the package's reserved namespace."""
    if name == LOGGER_PREFIX or name.startswith(f"{LOGGER_PREFIX}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_PREFIX}.{name}")
