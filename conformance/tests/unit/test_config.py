from __future__ import annotations

import pytest

from shadownet_conformance.config import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    Config,
    Role,
    build_parser,
)
from shadownet_conformance.errors import ConfigError


def _parse(*argv: str) -> Config:
    parser = build_parser()
    args = parser.parse_args(list(argv))
    return Config.from_namespace(args)


def test_empty_argv_yields_defaults():
    cfg = _parse()
    assert cfg.targets == {}
    assert cfg.peer_targets == {}
    assert cfg.http_timeout_seconds == DEFAULT_HTTP_TIMEOUT_SECONDS
    assert cfg.include_draft is False
    assert cfg.include_network is True
    assert cfg.test_shadowname is None


def test_parses_target_flags():
    cfg = _parse(
        "--target",
        "provider=https://provider.example",
        "--target",
        "issuer=https://issuer.example",
    )
    assert cfg.targets[Role.PROVIDER] == "https://provider.example"
    assert cfg.targets[Role.ISSUER] == "https://issuer.example"
    assert cfg.target(Role.SIDECAR) is None


def test_parses_peer_target_flag():
    cfg = _parse(
        "--target",
        "provider=https://a.example",
        "--peer-target",
        "provider=https://b.example",
    )
    assert cfg.has_round_trip(Role.PROVIDER) is True
    assert cfg.has_round_trip(Role.ISSUER) is False
    assert cfg.peer_target(Role.PROVIDER) == "https://b.example"


def test_unknown_role_rejected():
    with pytest.raises(ConfigError, match="unknown role"):
        _parse("--target", "bogus=https://x")


def test_malformed_target_rejected():
    with pytest.raises(ConfigError, match="ROLE=URL"):
        _parse("--target", "no-equals")


def test_duplicate_role_rejected():
    with pytest.raises(ConfigError, match="more than once"):
        _parse(
            "--target",
            "provider=https://a",
            "--target",
            "provider=https://b",
        )


def test_no_network_flag():
    cfg = _parse("--no-network")
    assert cfg.include_network is False


def test_test_shadowname_override():
    cfg = _parse("--test-shadowname", "alice@sh4dow.org")
    assert cfg.test_shadowname == "alice@sh4dow.org"


def test_config_is_frozen():
    from pydantic import ValidationError

    cfg = _parse()
    with pytest.raises(ValidationError):
        cfg.test_shadowname = "tampered"
