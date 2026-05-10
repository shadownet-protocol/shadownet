from __future__ import annotations

import pytest
from pydantic import ValidationError

from shadownet.sca.policy import SCAPolicy


def test_policy_round_trip() -> None:
    payload = {
        "issuer": "did:web:sca.example",
        "shadownet:v": "0.1",
        "levels": [
            {
                "level": "urn:shadownet:level:L2",
                "method": "urn:example:method:doc-v1",
                "credentialLifetimeDays": 365,
            },
        ],
        "freshnessWindowSeconds": 86400,
        "statusListBase": "https://sca.example/status/",
    }
    policy = SCAPolicy.model_validate(payload)
    assert policy.issuer == "did:web:sca.example"
    assert policy.method_for("urn:shadownet:level:L2") == "urn:example:method:doc-v1"
    assert policy.method_for("urn:shadownet:level:LX") is None


def test_policy_rejects_non_did_issuer() -> None:
    with pytest.raises(ValidationError):
        SCAPolicy.model_validate(
            {
                "issuer": "not-a-did",
                "shadownet:v": "0.1",
                "levels": [],
                "freshnessWindowSeconds": 60,
                "statusListBase": "https://sca.example/status/",
            }
        )


def test_policy_rejects_wrong_version() -> None:
    with pytest.raises(ValidationError):
        SCAPolicy.model_validate(
            {
                "issuer": "did:web:sca.example",
                "shadownet:v": "1.0",
                "levels": [],
                "freshnessWindowSeconds": 60,
                "statusListBase": "https://sca.example/status/",
            }
        )
