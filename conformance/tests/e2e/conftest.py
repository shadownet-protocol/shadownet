from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from shadownet_conformance.config import Role

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shadownet_conformance.config import Config


@pytest.fixture(scope="session")
def sca_url(conformance_config: Config) -> str:
    url = conformance_config.target(Role.SCA)
    if url is None:
        pytest.skip("no --target sca configured")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def peer_sca_url(conformance_config: Config) -> str:
    url = conformance_config.peer_target(Role.SCA)
    if url is None:
        pytest.skip("no --peer-target sca configured (round-trip needs both URLs)")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def sns_url(conformance_config: Config) -> str:
    url = conformance_config.target(Role.SNS)
    if url is None:
        pytest.skip("no --target sns configured")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def peer_sns_url(conformance_config: Config) -> str:
    url = conformance_config.peer_target(Role.SNS)
    if url is None:
        pytest.skip("no --peer-target sns configured (round-trip needs both URLs)")
    return url.rstrip("/")


@pytest.fixture
async def http(conformance_config: Config) -> Iterator[httpx.AsyncClient]:
    timeout = httpx.Timeout(conformance_config.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        yield client
