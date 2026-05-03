from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from shadownet_conformance.config import Role

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shadownet_conformance.config import Config


@pytest.fixture(scope="session")
def sns_url(conformance_config: Config) -> str:
    url = conformance_config.target(Role.SNS)
    if url is None:
        pytest.skip("no --target sns configured")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def sns_test_shadowname(conformance_config: Config) -> str:
    name = conformance_config.sns_test_shadowname
    if not name:
        pytest.skip(
            "no --sns-test-shadowname configured (operator must pre-register one shadowname "
            "against the SNS target for resolve happy-path tests)"
        )
    return name


@pytest.fixture
async def http(conformance_config: Config) -> Iterator[httpx.AsyncClient]:
    timeout = httpx.Timeout(conformance_config.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        yield client
