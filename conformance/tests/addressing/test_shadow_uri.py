"""Shadow addressing URI grammar — RFC 0001 §3.2.

Pins the two address forms and the discriminator (presence of `key:` in the
userinfo). Direct mode admits an optional ``#sha256:`` TLS-pin fragment;
Shadowname mode MUST NOT carry a port or fragment.
"""

from __future__ import annotations

import pytest
from shadownet.addressing import (
    DirectAddress,
    ShadowAddressError,
    ShadownameAddress,
    parse_shadow_address,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key


@pytest.fixture
def alice_pk() -> str:
    return encode_public_key(Ed25519KeyPair.generate().public_bytes)


@pytest.mark.rfc("0001", section="3.2", requirement="Shadowname friendly form")
def test_friendly_shadowname_parses() -> None:
    result = parse_shadow_address("alice@sh4dow.org")
    assert isinstance(result, ShadownameAddress)
    assert result.shadowname == "alice@sh4dow.org"


@pytest.mark.rfc("0001", section="3.2", requirement="Shadowname URI form")
def test_scheme_shadowname_parses(alice_pk: str) -> None:
    result = parse_shadow_address("shadow://alice@sh4dow.org")
    assert isinstance(result, ShadownameAddress)
    assert result.shadowname == "alice@sh4dow.org"


@pytest.mark.rfc("0001", section="3.2", requirement="direct addressing key: tag")
def test_direct_uri_with_port(alice_pk: str) -> None:
    uri = f"shadow://key:{alice_pk}@vps.example.com:8443"
    result = parse_shadow_address(uri)
    assert isinstance(result, DirectAddress)
    assert result.public_key == alice_pk
    assert result.host == "vps.example.com"
    assert result.port == 8443
    assert result.tls_pin_sha256 is None


@pytest.mark.rfc("0001", section="3.2", requirement="direct addressing #sha256 pin")
def test_direct_uri_with_tls_pin(alice_pk: str) -> None:
    pin = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    uri = f"shadow://key:{alice_pk}@vps.example.com:8443#sha256:{pin}"
    result = parse_shadow_address(uri)
    assert isinstance(result, DirectAddress)
    assert result.tls_pin_sha256 == pin


@pytest.mark.rfc("0001", section="3.2", requirement="Shadowname URI rejects port")
def test_shadowname_uri_rejects_port() -> None:
    with pytest.raises(ShadowAddressError):
        parse_shadow_address("shadow://alice@sh4dow.org:443")


@pytest.mark.rfc("0001", section="3.2", requirement="Shadowname URI rejects fragment")
def test_shadowname_uri_rejects_fragment() -> None:
    with pytest.raises(ShadowAddressError):
        parse_shadow_address("shadow://alice@sh4dow.org#sha256:abc")
