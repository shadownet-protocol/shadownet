from __future__ import annotations

import shadownet


def test_version_exposed() -> None:
    assert isinstance(shadownet.__version__, str)
    assert shadownet.__version__.count(".") == 2


def test_logger_namespaced() -> None:
    from shadownet.logging import get_logger

    assert get_logger("foo").name == "shadownet.foo"
    assert get_logger("shadownet.bar").name == "shadownet.bar"


def test_error_root() -> None:
    from shadownet.errors import ShadownetError

    assert issubclass(ShadownetError, Exception)
