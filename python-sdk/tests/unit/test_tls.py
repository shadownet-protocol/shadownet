from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from shadownet.addressing import DirectAddress, parse_shadow_address
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key
from shadownet.tls import (
    InMemoryTLSPinStore,
    TLSPinMismatchError,
    TLSPinStore,
    compute_cert_fingerprint,
    make_pinned_httpx_client,
    verify_tls_pin,
)


def _self_signed_der(common_name: str = "vps.example.com") -> bytes:
    """Return a fresh self-signed certificate in DER form."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(seconds=60))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


@pytest.fixture
def alice_pk() -> str:
    return encode_public_key(Ed25519KeyPair.generate().public_bytes)


@pytest.fixture
def direct_address(alice_pk: str) -> DirectAddress:
    result = parse_shadow_address(f"shadow://key:{alice_pk}@vps.example.com:8443")
    assert isinstance(result, DirectAddress)
    return result


class TestComputeCertFingerprint:
    def test_stable_across_calls(self) -> None:
        der = _self_signed_der()
        assert compute_cert_fingerprint(der) == compute_cert_fingerprint(der)

    def test_distinct_certs_distinct_fingerprints(self) -> None:
        assert compute_cert_fingerprint(_self_signed_der()) != compute_cert_fingerprint(
            _self_signed_der()
        )

    def test_no_padding(self) -> None:
        # base64url SHA-256 of 32 bytes → 43 chars, no '=' padding.
        fp = compute_cert_fingerprint(_self_signed_der())
        assert len(fp) == 43
        assert "=" not in fp


class TestInMemoryTLSPinStore:
    def test_record_and_get(self) -> None:
        store = InMemoryTLSPinStore()
        assert store.get("a:1") is None
        store.record("a:1", "fp1")
        assert store.get("a:1") == "fp1"

    def test_overwrite(self) -> None:
        store = InMemoryTLSPinStore()
        store.record("a:1", "fp1")
        store.record("a:1", "fp2")
        assert store.get("a:1") == "fp2"

    def test_protocol_compliance(self) -> None:
        assert isinstance(InMemoryTLSPinStore(), TLSPinStore)


class TestVerifyTLSPin:
    def test_uri_pin_match(self) -> None:
        der = _self_signed_der()
        expected = compute_cert_fingerprint(der)
        store = InMemoryTLSPinStore()
        verify_tls_pin(
            der, expected_pin=expected, tofu_store=store, host_port="vps.example.com:8443"
        )
        # TOFU store untouched when URI pin is supplied.
        assert store.get("vps.example.com:8443") is None

    def test_uri_pin_mismatch(self) -> None:
        der = _self_signed_der()
        store = InMemoryTLSPinStore()
        with pytest.raises(TLSPinMismatchError, match="URI pin"):
            verify_tls_pin(
                der,
                expected_pin="WRONGFINGERPRINTWRONGFINGERPRINTWRONGFINGERPR",
                tofu_store=store,
                host_port="vps.example.com:8443",
            )

    def test_tofu_first_use_records(self) -> None:
        der = _self_signed_der()
        store = InMemoryTLSPinStore()
        actual = verify_tls_pin(
            der, expected_pin=None, tofu_store=store, host_port="vps.example.com:8443"
        )
        assert store.get("vps.example.com:8443") == actual

    def test_tofu_subsequent_match(self) -> None:
        der = _self_signed_der()
        store = InMemoryTLSPinStore()
        verify_tls_pin(der, expected_pin=None, tofu_store=store, host_port="vps.example.com:8443")
        # Same cert on next visit — must match.
        verify_tls_pin(der, expected_pin=None, tofu_store=store, host_port="vps.example.com:8443")

    def test_tofu_subsequent_mismatch(self) -> None:
        der_a = _self_signed_der()
        der_b = _self_signed_der()
        store = InMemoryTLSPinStore()
        verify_tls_pin(der_a, expected_pin=None, tofu_store=store, host_port="vps.example.com:8443")
        # A different cert on the same host — TOFU pin MUST reject.
        with pytest.raises(TLSPinMismatchError, match="recorded TOFU pin"):
            verify_tls_pin(
                der_b,
                expected_pin=None,
                tofu_store=store,
                host_port="vps.example.com:8443",
            )

    def test_uri_pin_takes_precedence_over_tofu(self) -> None:
        der = _self_signed_der()
        actual = compute_cert_fingerprint(der)
        store = InMemoryTLSPinStore()
        store.record("vps.example.com:8443", "STALEFINGERPRINTSTALEFINGERPRINTSTALEFINGER")
        # URI pin must be checked, not the stale TOFU entry.
        verify_tls_pin(
            der,
            expected_pin=actual,
            tofu_store=store,
            host_port="vps.example.com:8443",
        )


class TestMakePinnedHttpxClient:
    def test_returns_client_using_pinned_transport(self, direct_address: DirectAddress) -> None:
        client = make_pinned_httpx_client(direct_address)
        try:
            assert isinstance(client, httpx.Client)
            transport = client._transport
            assert transport.__class__.__name__ == "_PinnedTransport"
        finally:
            client.close()

    def test_default_store_is_in_memory(self, direct_address: DirectAddress) -> None:
        client = make_pinned_httpx_client(direct_address)
        try:
            transport = client._transport
            assert isinstance(transport._tofu_store, InMemoryTLSPinStore)
        finally:
            client.close()

    def test_explicit_store_wired_through(self, direct_address: DirectAddress) -> None:
        store = InMemoryTLSPinStore()
        client = make_pinned_httpx_client(direct_address, tofu_store=store)
        try:
            transport = client._transport
            assert transport._tofu_store is store
            assert transport._host_port == "vps.example.com:8443"
        finally:
            client.close()

    def test_uri_pin_carried_into_transport(self, alice_pk: str) -> None:
        der = _self_signed_der()
        pin = compute_cert_fingerprint(der)
        result = parse_shadow_address(f"shadow://key:{alice_pk}@vps.example.com:8443#sha256:{pin}")
        assert isinstance(result, DirectAddress)
        client = make_pinned_httpx_client(result)
        try:
            transport = client._transport
            assert transport._expected_pin == pin
        finally:
            client.close()


class TestPinnedTransportHandleRequest:
    """Exercise the transport's verification branches with a mocked network_stream."""

    def _transport_with_mocked_response(
        self,
        peer_cert_der: bytes,
        *,
        expected_pin: str | None,
        tofu_store: TLSPinStore,
    ) -> Any:
        from shadownet.tls import _PinnedTransport

        transport = _PinnedTransport(
            host_port="vps.example.com:8443",
            expected_pin=expected_pin,
            tofu_store=tofu_store,
            timeout=10.0,
        )
        ssl_object = MagicMock()
        ssl_object.getpeercert.return_value = peer_cert_der
        network_stream = MagicMock()
        network_stream.get_extra_info.return_value = ssl_object
        response = MagicMock(spec=httpx.Response)
        response.extensions = {"network_stream": network_stream}
        # Hook the call chain so our handle_request sees the mocked response.
        transport.__class__.__bases__[0].handle_request = (  # type: ignore[method-assign]
            lambda self, request: response
        )
        return transport, response

    def test_match_returns_response(self) -> None:
        der = _self_signed_der()
        store = InMemoryTLSPinStore()
        transport, response = self._transport_with_mocked_response(
            der, expected_pin=compute_cert_fingerprint(der), tofu_store=store
        )
        request = httpx.Request("GET", "https://vps.example.com:8443/")
        assert transport.handle_request(request) is response

    def test_mismatch_closes_and_raises(self) -> None:
        der = _self_signed_der()
        store = InMemoryTLSPinStore()
        transport, response = self._transport_with_mocked_response(
            der,
            expected_pin="WRONGPINWRONGPINWRONGPINWRONGPINWRONGPINWRO",
            tofu_store=store,
        )
        request = httpx.Request("GET", "https://vps.example.com:8443/")
        with pytest.raises(TLSPinMismatchError, match="URI pin"):
            transport.handle_request(request)
        response.close.assert_called_once()
