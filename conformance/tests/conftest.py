from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx
import pytest

from shadownet_conformance import _markers
from shadownet_conformance.cli import CONFIG_ENV_PATH
from shadownet_conformance.config import Config, Role

pytest_plugins = ["shadownet_conformance.reporters.rfc_json"]

if TYPE_CHECKING:
    from collections.abc import Iterator


def pytest_configure(config: pytest.Config) -> None:
    _markers.register(config)


@pytest.fixture(scope="session")
def conformance_config() -> Config:
    """The Config under which this run was launched.

    Populated by the CLI entry point. When pytest is invoked directly without
    the CLI, this falls back to env vars (`SHADOWNET_CONFORMANCE_*`) only.
    """
    serialized = os.environ.get(CONFIG_ENV_PATH)
    if serialized:
        return Config.model_validate_json(serialized)
    return Config.from_namespace(_empty_namespace())


def _empty_namespace() -> object:
    """Build a namespace with every CLI flag set to its 'absent' default."""
    import argparse

    return argparse.Namespace(
        target=None,
        peer_target=None,
        proof_method=None,
        specs_path=None,
        http_timeout=None,
        report_junit=None,
        report_json=None,
        gha_summary=None,
        include_draft=False,
        no_network=False,
        marker_expr=None,
        peer_listen_host=None,
        sns_test_shadowname=None,
    )


@pytest.fixture
async def http_client(conformance_config: Config) -> Iterator[httpx.AsyncClient]:
    timeout = httpx.Timeout(conformance_config.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        yield client


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip target-bound tests when their target is absent."""
    serialized = os.environ.get(CONFIG_ENV_PATH)
    if serialized:
        cfg = Config.model_validate_json(serialized)
    else:
        cfg = Config.from_namespace(_empty_namespace())

    skip_no_target = {
        Role.SCA: pytest.mark.skip(reason="no --target sca configured"),
        Role.SNS: pytest.mark.skip(reason="no --target sns configured"),
        Role.SIDECAR: pytest.mark.skip(reason="no --target sidecar configured"),
    }
    skip_no_peer = {
        Role.SCA: pytest.mark.skip(reason="no --peer-target sca configured"),
        Role.SNS: pytest.mark.skip(reason="no --peer-target sns configured"),
        Role.SIDECAR: pytest.mark.skip(reason="no --peer-target sidecar configured"),
    }
    skip_no_network = pytest.mark.skip(reason="--no-network in effect")
    skip_draft = pytest.mark.skip(reason="draft RFC; pass --include-draft to enable")

    for item in items:
        if "draft" in item.keywords and not cfg.include_draft:
            item.add_marker(skip_draft)

        class_marker = item.get_closest_marker("class_")
        if class_marker is not None:
            role_str = class_marker.args[0] if class_marker.args else None
            if role_str:
                role = Role(role_str)
                if cfg.target(role) is None:
                    item.add_marker(skip_no_target[role])
                elif not cfg.include_network and "network" in item.keywords:
                    item.add_marker(skip_no_network)

        rt_marker = item.get_closest_marker("round_trip")
        if rt_marker is not None:
            role_str = rt_marker.args[0] if rt_marker.args else None
            if role_str:
                role = Role(role_str)
                if not cfg.has_round_trip(role):
                    item.add_marker(skip_no_peer[role])
