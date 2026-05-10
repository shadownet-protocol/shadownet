from __future__ import annotations

import logging

_ROOT = "shadownet"


def get_logger(name: str) -> logging.Logger:
    """Return a logger namespaced under ``shadownet.``.

    Library code does not configure handlers or levels — that is the consumer's
    responsibility. Pass ``__name__`` from the caller for module-scoped loggers.
    """
    if name == _ROOT or name.startswith(_ROOT + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT}.{name}")
