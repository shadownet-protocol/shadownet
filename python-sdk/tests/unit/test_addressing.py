from __future__ import annotations

import pytest

from shadownet.addressing import (
    DEFAULT_DIRECT_PORT,
    DirectAddress,
    ShadowAddressError,
    ShadownameAddress,
    parse_shadow_address,
    parse_tls_pin,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key


@pytest.fixture
def alice_pk() -> str:
    return encode_public_key(Ed25519KeyPair.generate().public_bytes)


class TestShadownameForms:
    def test_friendly_form(self) -> None:
        result = parse_shadow_address("alice@sh4dow.org")
        assert isinstance(result, ShadownameAddress)
        assert result.shadowname == "alice@sh4dow.org"

    def test_friendly_form_canonicalized(self) -> None:
        result = parse_shadow_address("Alice@SH4DOW.org")
        assert isinstance(result, ShadownameAddress)
        assert result.shadowname == "alice@sh4dow.org"

    def test_scheme_form(self) -> None:
        result = parse_shadow_address("shadow://alice@sh4dow.org")
        assert isinstance(result, ShadownameAddress)
        assert result.shadowname == "alice@sh4dow.org"

    def test_shadowname_uri_rejects_port(self) -> None:
        with pytest.raises(ShadowAddressError, match="port"):
            parse_shadow_address("shadow://alice@sh4dow.org:443")

    def test_shadowname_uri_rejects_fragment(self) -> None:
        with pytest.raises(ShadowAddressError, match="TLS pin"):
            parse_shadow_address("shadow://alice@sh4dow.org#sha256:abc")


class TestDirectAddress:
    def test_minimal(self, alice_pk: str) -> None:
        result = parse_shadow_address(f"shadow://key:{alice_pk}@192.0.2.10:8443")
        assert isinstance(result, DirectAddress)
        assert result.public_key == alice_pk
        assert result.host == "192.0.2.10"
        assert result.port == 8443
        assert result.tls_pin_sha256 is None
        assert result.endpoint == "https://192.0.2.10:8443"

    def test_with_hostname(self, alice_pk: str) -> None:
        result = parse_shadow_address(f"shadow://key:{alice_pk}@vps.example.com:8443")
        assert isinstance(result, DirectAddress)
        assert result.host == "vps.example.com"

    def test_default_port_when_omitted(self, alice_pk: str) -> None:
        result = parse_shadow_address(f"shadow://key:{alice_pk}@vps.example.com")
        assert isinstance(result, DirectAddress)
        assert result.port == DEFAULT_DIRECT_PORT

    def test_ipv6(self, alice_pk: str) -> None:
        result = parse_shadow_address(f"shadow://key:{alice_pk}@[2001:db8::1]:8443")
        assert isinstance(result, DirectAddress)
        assert result.host == "2001:db8::1"
        assert result.endpoint == "https://[2001:db8::1]:8443"

    def test_with_tls_pin(self, alice_pk: str) -> None:
        # 32 bytes of \x00 -> 43 chars base64url without padding.
        pin = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        uri = f"shadow://key:{alice_pk}@vps.example.com:8443#sha256:{pin}"
        result = parse_shadow_address(uri)
        assert isinstance(result, DirectAddress)
        assert result.tls_pin_sha256 == pin

    def test_invalid_pubkey(self) -> None:
        with pytest.raises(ShadowAddressError, match="pubkey"):
            parse_shadow_address("shadow://key:zNotAKey@vps.example.com:8443")

    def test_unknown_userinfo_tag(self) -> None:
        with pytest.raises(ShadowAddressError, match="unrecognized"):
            parse_shadow_address("shadow://x509:something@vps.example.com:8443")


class TestParseTLSPin:
    def test_with_prefix(self) -> None:
        pin = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        assert parse_tls_pin(f"sha256:{pin}") == pin

    def test_bare_fingerprint(self) -> None:
        pin = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        assert parse_tls_pin(pin) == pin

    def test_wrong_length(self) -> None:
        with pytest.raises(ShadowAddressError, match="32 bytes"):
            parse_tls_pin("sha256:AAA")

    def test_non_base64url(self) -> None:
        with pytest.raises(ShadowAddressError, match="base64url"):
            parse_tls_pin("sha256:not!base64??")


class TestRejections:
    def test_empty(self) -> None:
        with pytest.raises(ShadowAddressError, match="empty"):
            parse_shadow_address("")

    def test_wrong_scheme(self) -> None:
        with pytest.raises(ShadowAddressError, match="scheme"):
            parse_shadow_address("https://example.com")

    def test_no_authority(self) -> None:
        with pytest.raises(ShadowAddressError):
            parse_shadow_address("shadow://")

    def test_no_userinfo(self, alice_pk: str) -> None:
        with pytest.raises(ShadowAddressError, match="userinfo"):
            parse_shadow_address("shadow://example.com")

    def test_query_rejected(self) -> None:
        with pytest.raises(ShadowAddressError, match="query"):
            parse_shadow_address("shadow://alice@sh4dow.org?foo=bar")
