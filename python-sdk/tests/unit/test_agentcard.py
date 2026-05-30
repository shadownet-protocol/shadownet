from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
import respx

from shadownet.agentcard import (
    AgentCardError,
    AgentCardSignatureError,
    build_signed_agent_card,
    fetch_and_verify_agent_card,
    verify_agent_card,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key
from shadownet.jcs import canonicalize
from shadownet.provider import ProviderRecord


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _strip_empty(value: Any) -> Any:
    if isinstance(value, dict):
        out = {k: c for k, v in value.items() if (c := _strip_empty(v)) is not None}
        return out or None
    if isinstance(value, list):
        cleaned = [c for v in value if (c := _strip_empty(v)) is not None]
        return cleaned or None
    if isinstance(value, str) and not value:
        return None
    return value


def _sign_card(
    card: dict[str, Any],
    provider_key: Ed25519KeyPair,
    *,
    kid: str,
    alg: str = "EdDSA",
    typ: str = "JOSE",
) -> dict[str, Any]:
    pruned = _strip_empty({k: v for k, v in card.items() if k != "signatures"})
    payload_b64 = _b64url(canonicalize(pruned))
    protected = {"alg": alg, "typ": typ, "kid": kid}
    protected_b64 = _b64url(json.dumps(protected, separators=(",", ":")).encode("utf-8"))
    signing_input = (protected_b64 + "." + payload_b64).encode("ascii")
    signature_b64 = _b64url(provider_key.sign(signing_input))
    out = dict(card)
    out["signatures"] = [{"protected": protected_b64, "signature": signature_b64}]
    return out


@pytest.fixture
def provider_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def shadow_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def provider_record(provider_key: Ed25519KeyPair) -> ProviderRecord:
    return ProviderRecord(
        domain="sh4dow.org",
        version="0.2",
        endpoint="https://shadow.sh4dow.org/v1",
        provider_keys=(encode_public_key(provider_key.public_bytes),),
    )


@pytest.fixture
def card_body(shadow_key: Ed25519KeyPair) -> dict[str, Any]:
    return {
        "name": "Alice",
        "description": "Alice's Shadow",
        "version": "1.0.0",
        "supportedInterfaces": [
            {
                "url": "https://shadow.sh4dow.org/v1/a2a/alice",
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            },
        ],
        "capabilities": {
            "extensions": [
                {
                    "uri": "urn:shadownet:0.2",
                    "required": True,
                    "description": "Shadownet identity envelope",
                },
            ],
        },
        "shadownet:v": "0.2",
        "shadownet:pk": encode_public_key(shadow_key.public_bytes),
    }


@pytest.fixture
def signed_card(card_body: dict[str, Any], provider_key: Ed25519KeyPair) -> dict[str, Any]:
    return _sign_card(card_body, provider_key, kid="shadownet@sh4dow.org")


class TestVerifyAgentCard:
    def test_happy_path(
        self,
        signed_card: dict[str, Any],
        provider_record: ProviderRecord,
        shadow_key: Ed25519KeyPair,
    ) -> None:
        result = verify_agent_card(signed_card, provider_record, "alice@sh4dow.org")
        assert result.shadowname == "alice@sh4dow.org"
        assert result.shadow_public_key == encode_public_key(shadow_key.public_bytes)
        assert result.endpoint_url == "https://shadow.sh4dow.org/v1/a2a/alice"

    def test_canonicalizes_with_default_stripping(
        self,
        card_body: dict[str, Any],
        provider_key: Ed25519KeyPair,
        provider_record: ProviderRecord,
    ) -> None:
        # Add fields that A2A §8.4 canonicalization strips: "", [], {}.
        # Sign over the stripped form, then send the un-stripped form on the wire;
        # verification must still pass.
        signed = _sign_card(card_body, provider_key, kid="shadownet@sh4dow.org")
        polluted = dict(signed)
        polluted["iconUrl"] = ""
        polluted["skills"] = []
        polluted["securitySchemes"] = {}
        verify_agent_card(polluted, provider_record, "alice@sh4dow.org")

    def test_wrong_kid_rejected(
        self,
        card_body: dict[str, Any],
        provider_key: Ed25519KeyPair,
        provider_record: ProviderRecord,
    ) -> None:
        signed = _sign_card(card_body, provider_key, kid="provider@sh4dow.org")
        with pytest.raises(AgentCardSignatureError, match="kid"):
            verify_agent_card(signed, provider_record, "alice@sh4dow.org")

    def test_wrong_alg_rejected(
        self,
        card_body: dict[str, Any],
        provider_key: Ed25519KeyPair,
        provider_record: ProviderRecord,
    ) -> None:
        signed = _sign_card(card_body, provider_key, kid="shadownet@sh4dow.org", alg="ES256")
        with pytest.raises(AgentCardSignatureError, match="alg"):
            verify_agent_card(signed, provider_record, "alice@sh4dow.org")

    def test_wrong_typ_rejected(
        self,
        card_body: dict[str, Any],
        provider_key: Ed25519KeyPair,
        provider_record: ProviderRecord,
    ) -> None:
        signed = _sign_card(card_body, provider_key, kid="shadownet@sh4dow.org", typ="JWT")
        with pytest.raises(AgentCardSignatureError, match="typ"):
            verify_agent_card(signed, provider_record, "alice@sh4dow.org")

    def test_tampered_card_rejected(
        self, signed_card: dict[str, Any], provider_record: ProviderRecord
    ) -> None:
        tampered = dict(signed_card)
        tampered["name"] = "Mallory"
        with pytest.raises(AgentCardSignatureError):
            verify_agent_card(tampered, provider_record, "alice@sh4dow.org")

    def test_missing_signatures_rejected(
        self, card_body: dict[str, Any], provider_record: ProviderRecord
    ) -> None:
        with pytest.raises(AgentCardSignatureError, match="no signatures"):
            verify_agent_card(card_body, provider_record, "alice@sh4dow.org")

    def test_extension_not_required_rejected(
        self,
        card_body: dict[str, Any],
        provider_key: Ed25519KeyPair,
        provider_record: ProviderRecord,
    ) -> None:
        card_body["capabilities"]["extensions"][0]["required"] = False
        signed = _sign_card(card_body, provider_key, kid="shadownet@sh4dow.org")
        with pytest.raises(AgentCardError, match="required"):
            verify_agent_card(signed, provider_record, "alice@sh4dow.org")

    def test_missing_extension_rejected(
        self,
        card_body: dict[str, Any],
        provider_key: Ed25519KeyPair,
        provider_record: ProviderRecord,
    ) -> None:
        card_body["capabilities"]["extensions"] = []
        signed = _sign_card(card_body, provider_key, kid="shadownet@sh4dow.org")
        with pytest.raises(AgentCardError, match="extension"):
            verify_agent_card(signed, provider_record, "alice@sh4dow.org")

    def test_missing_shadow_pk_rejected(
        self,
        card_body: dict[str, Any],
        provider_key: Ed25519KeyPair,
        provider_record: ProviderRecord,
    ) -> None:
        del card_body["shadownet:pk"]
        signed = _sign_card(card_body, provider_key, kid="shadownet@sh4dow.org")
        with pytest.raises(AgentCardError, match="shadownet:pk"):
            verify_agent_card(signed, provider_record, "alice@sh4dow.org")

    def test_wrong_shadownet_version_rejected(
        self,
        card_body: dict[str, Any],
        provider_key: Ed25519KeyPair,
        provider_record: ProviderRecord,
    ) -> None:
        card_body["shadownet:v"] = "0.3"
        signed = _sign_card(card_body, provider_key, kid="shadownet@sh4dow.org")
        with pytest.raises(AgentCardError, match="shadownet:v"):
            verify_agent_card(signed, provider_record, "alice@sh4dow.org")

    def test_missing_supported_interfaces_rejected(
        self,
        card_body: dict[str, Any],
        provider_key: Ed25519KeyPair,
        provider_record: ProviderRecord,
    ) -> None:
        del card_body["supportedInterfaces"]
        signed = _sign_card(card_body, provider_key, kid="shadownet@sh4dow.org")
        with pytest.raises(AgentCardError, match="supportedInterfaces"):
            verify_agent_card(signed, provider_record, "alice@sh4dow.org")

    def test_shadowname_provider_mismatch(
        self, signed_card: dict[str, Any], provider_record: ProviderRecord
    ) -> None:
        with pytest.raises(AgentCardError, match="does not match"):
            verify_agent_card(signed_card, provider_record, "alice@other.example")

    def test_signing_helper_roundtrips(
        self,
        shadow_key: Ed25519KeyPair,
        provider_key: Ed25519KeyPair,
        provider_record: ProviderRecord,
    ) -> None:
        signed = build_signed_agent_card(
            name="Alice",
            description="Alice's Shadow",
            version="1.0.0",
            a2a_url="https://shadow.sh4dow.org/v1/a2a/alice",
            shadow_public_key=encode_public_key(shadow_key.public_bytes),
            provider_key=provider_key,
            provider_domain="sh4dow.org",
        )
        result = verify_agent_card(signed, provider_record, "alice@sh4dow.org")
        assert result.shadow_public_key == encode_public_key(shadow_key.public_bytes)

    def test_multi_key_split_acceptance(
        self,
        card_body: dict[str, Any],
        provider_key: Ed25519KeyPair,
    ) -> None:
        # §5.5: during rotation, provider may publish multiple pk= keys; any
        # accepted signature passes.
        old_key = Ed25519KeyPair.generate()
        record = ProviderRecord(
            domain="sh4dow.org",
            version="0.2",
            endpoint="https://shadow.sh4dow.org/v1",
            provider_keys=(
                encode_public_key(old_key.public_bytes),
                encode_public_key(provider_key.public_bytes),
            ),
        )
        signed = _sign_card(card_body, provider_key, kid="shadownet@sh4dow.org")
        verify_agent_card(signed, record, "alice@sh4dow.org")


class TestFetchAndVerifyAgentCard:
    @respx.mock
    def test_fetch_and_verify(
        self,
        signed_card: dict[str, Any],
        provider_record: ProviderRecord,
    ) -> None:
        respx.get("https://shadow.sh4dow.org/v1/identity/alice").mock(
            return_value=httpx.Response(
                200,
                json=signed_card,
                headers={"Cache-Control": "max-age=3600", "ETag": '"abc"'},
            )
        )
        result = fetch_and_verify_agent_card("alice@sh4dow.org", provider_record)
        assert result.cache_max_age == 3600
        assert result.etag == '"abc"'

    @respx.mock
    def test_fetch_404(self, provider_record: ProviderRecord) -> None:
        respx.get("https://shadow.sh4dow.org/v1/identity/missing").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(AgentCardError, match="HTTP 404"):
            fetch_and_verify_agent_card("missing@sh4dow.org", provider_record)

    @respx.mock
    def test_fetch_non_json(self, provider_record: ProviderRecord) -> None:
        respx.get("https://shadow.sh4dow.org/v1/identity/alice").mock(
            return_value=httpx.Response(200, text="not json")
        )
        with pytest.raises(AgentCardError, match="not valid JSON"):
            fetch_and_verify_agent_card("alice@sh4dow.org", provider_record)

    @respx.mock
    def test_fetch_json_not_object(self, provider_record: ProviderRecord) -> None:
        respx.get("https://shadow.sh4dow.org/v1/identity/alice").mock(
            return_value=httpx.Response(200, json=["array", "not", "object"])
        )
        with pytest.raises(AgentCardError, match="not a JSON object"):
            fetch_and_verify_agent_card("alice@sh4dow.org", provider_record)
