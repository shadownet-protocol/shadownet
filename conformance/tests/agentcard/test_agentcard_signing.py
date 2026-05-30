"""AgentCard signing — RFC 0001 §5.2, §5.3, A2A §8.4.

Wire invariants for both addressing modes: Shadowname-mode cards are
provider-signed with ``kid = shadownet@<provider-domain>``; direct-mode
cards are self-signed by the Shadow with ``kid`` equal to the embedded
``shadownet:pk``. Both pass through the canonical-payload pipeline
(JCS-canonical message minus the ``signatures`` field and recursively
stripped empty values) per A2A §8.4.
"""

from __future__ import annotations

import pytest
from shadownet.agentcard import (
    PINNED_SELF_SIGNED_SCHEME,
    AgentCardError,
    AgentCardSignatureError,
    build_direct_signed_agent_card,
    build_signed_agent_card,
    verify_agent_card,
    verify_self_signed_agent_card,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key
from shadownet.provider import ProviderRecord


@pytest.fixture
def alice_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def provider_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def provider_record(provider_key: Ed25519KeyPair) -> ProviderRecord:
    return ProviderRecord(
        domain="sh4dow.org",
        version="0.2",
        endpoint="https://shadow.sh4dow.org/v1",
        provider_keys=(encode_public_key(provider_key.public_bytes),),
    )


@pytest.mark.rfc("0001", section="5.2", requirement="Shadowname AgentCard kid")
def test_shadowname_agent_card_signed_and_verified(
    alice_key: Ed25519KeyPair,
    provider_key: Ed25519KeyPair,
    provider_record: ProviderRecord,
) -> None:
    card = build_signed_agent_card(
        name="Alice",
        description="Alice's Shadow",
        version="1.0.0",
        a2a_url="https://shadow.sh4dow.org/v1/a2a/alice",
        shadow_public_key=encode_public_key(alice_key.public_bytes),
        provider_key=provider_key,
        provider_domain="sh4dow.org",
    )
    result = verify_agent_card(card, provider_record, "alice@sh4dow.org")
    assert result.shadow_public_key == encode_public_key(alice_key.public_bytes)


@pytest.mark.rfc("0001", section="5.3", requirement="direct AgentCard self-signed")
def test_direct_agent_card_signed_and_verified(alice_key: Ed25519KeyPair) -> None:
    card = build_direct_signed_agent_card(
        name="Alice",
        description="Alice's Shadow",
        version="1.0.0",
        a2a_url="https://vps.example.com:8443/a2a",
        shadow_key=alice_key,
    )
    alice_pk = encode_public_key(alice_key.public_bytes)
    result = verify_self_signed_agent_card(card, alice_pk)
    assert result.shadow_public_key == alice_pk


@pytest.mark.rfc("0001", section="5.4", requirement="direct AgentCard declares pinned-self-signed")
def test_direct_agent_card_requires_security_scheme(alice_key: Ed25519KeyPair) -> None:
    card = build_direct_signed_agent_card(
        name="Alice",
        description="Alice's Shadow",
        version="1.0.0",
        a2a_url="https://vps.example.com:8443/a2a",
        shadow_key=alice_key,
    )
    assert card["securitySchemes"][PINNED_SELF_SIGNED_SCHEME] == {}


@pytest.mark.rfc("0001", section="5.3", requirement="direct AgentCard kid MUST equal embedded pk")
def test_direct_agent_card_rejects_mismatched_expected_key(alice_key: Ed25519KeyPair) -> None:
    card = build_direct_signed_agent_card(
        name="Alice",
        description="Alice's Shadow",
        version="1.0.0",
        a2a_url="https://vps.example.com:8443/a2a",
        shadow_key=alice_key,
    )
    wrong = encode_public_key(Ed25519KeyPair.generate().public_bytes)
    with pytest.raises(AgentCardSignatureError):
        verify_self_signed_agent_card(card, wrong)


@pytest.mark.rfc("0001", section="5.2", requirement="Shadowname AgentCard rejects wrong shadowname")
def test_shadowname_agent_card_rejects_wrong_shadowname(
    alice_key: Ed25519KeyPair,
    provider_key: Ed25519KeyPair,
    provider_record: ProviderRecord,
) -> None:
    card = build_signed_agent_card(
        name="Alice",
        description="Alice's Shadow",
        version="1.0.0",
        a2a_url="https://shadow.sh4dow.org/v1/a2a/alice",
        shadow_public_key=encode_public_key(alice_key.public_bytes),
        provider_key=provider_key,
        provider_domain="sh4dow.org",
    )
    with pytest.raises(AgentCardError):
        verify_agent_card(card, provider_record, "alice@other.example")
