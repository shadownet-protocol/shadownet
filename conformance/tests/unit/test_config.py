from __future__ import annotations

import pytest

from shadownet_conformance.config import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_PROOF_METHOD_URI,
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
    assert cfg.proof_method_uri == DEFAULT_PROOF_METHOD_URI
    assert cfg.http_timeout_seconds == DEFAULT_HTTP_TIMEOUT_SECONDS
    assert cfg.include_draft is False
    assert cfg.include_network is True


def test_parses_target_flags():
    cfg = _parse(
        "--target",
        "sca=https://sca.example",
        "--target",
        "sns=https://sns.example",
    )
    assert cfg.targets[Role.SCA] == "https://sca.example"
    assert cfg.targets[Role.SNS] == "https://sns.example"
    assert cfg.target(Role.SIDECAR) is None


def test_parses_peer_target_flag():
    cfg = _parse(
        "--target",
        "sca=https://a.example",
        "--peer-target",
        "sca=https://b.example",
    )
    assert cfg.has_round_trip(Role.SCA) is True
    assert cfg.has_round_trip(Role.SNS) is False
    assert cfg.peer_target(Role.SCA) == "https://b.example"


def test_unknown_role_rejected():
    with pytest.raises(ConfigError, match="unknown role"):
        _parse("--target", "bogus=https://x")


def test_malformed_target_rejected():
    with pytest.raises(ConfigError, match="ROLE=URL"):
        _parse("--target", "no-equals")


def test_duplicate_role_rejected():
    with pytest.raises(ConfigError, match="more than once"):
        _parse("--target", "sca=https://a", "--target", "sca=https://b")


def test_no_network_flag():
    cfg = _parse("--no-network")
    assert cfg.include_network is False


def test_proof_method_override():
    cfg = _parse("--proof-method", "urn:custom:method")
    assert cfg.proof_method_uri == "urn:custom:method"


def test_config_is_frozen():
    from pydantic import ValidationError

    cfg = _parse()
    with pytest.raises(ValidationError):
        cfg.proof_method_uri = "tampered"
