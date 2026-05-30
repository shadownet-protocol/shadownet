from __future__ import annotations

import time

import httpx
import pytest
import respx

from shadownet.credential import (
    ORG_AFFILIATION,
    CredentialPayload,
    RevocationPointer,
    VerifiedCredential,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key
from shadownet.status import (
    StatusList,
    StatusListError,
    build_status_list_url,
    check_revocation,
    decode_status_list,
    encode_status_list,
    fetch_status_list,
)


def _credential_at(
    idx: int, *, iss: str = "acme.example", epoch: str = "2026q2"
) -> VerifiedCredential:
    now = int(time.time())
    payload = CredentialPayload(
        iss=iss,
        sub="alice@sh4dow.org",
        kind=ORG_AFFILIATION,
        org=iss,
        iat=now,
        exp=now + 3600,
        rev=RevocationPointer(epoch=epoch, idx=idx),
    )
    return VerifiedCredential(
        payload=payload,
        issuer_key=encode_public_key(Ed25519KeyPair.generate().public_bytes),
        raw_jws="header.payload.sig",
    )


class TestStatusList:
    def test_empty(self) -> None:
        s = StatusList.empty(64)
        assert s.size == 64
        assert s.is_revoked(0) is False
        assert s.is_revoked(63) is False

    def test_with_revoked(self) -> None:
        s = StatusList.empty(64)
        s2 = s.with_revoked(5)
        assert s.is_revoked(5) is False  # immutable
        assert s2.is_revoked(5) is True
        assert s2.is_revoked(4) is False

    def test_out_of_range_raises(self) -> None:
        s = StatusList.empty(8)
        with pytest.raises(StatusListError, match="out of range"):
            s.is_revoked(8)

    def test_big_endian_within_byte(self) -> None:
        # idx 0 = MSB of byte 0. Matches v0.1 Go `1<<(7 - idx%8)` and W3C.
        s = StatusList(bits=b"\x80", size=8)
        assert s.is_revoked(0) is True
        assert s.is_revoked(7) is False
        s2 = StatusList(bits=b"\x01", size=8)
        assert s2.is_revoked(0) is False
        assert s2.is_revoked(7) is True


class TestEncodeDecode:
    def test_roundtrip(self) -> None:
        original = StatusList.empty(64).with_revoked(0).with_revoked(63)
        encoded = encode_status_list(original)
        decoded = decode_status_list(encoded)
        assert decoded.bits == original.bits
        assert decoded.size == original.size

    def test_decode_empty_body(self) -> None:
        with pytest.raises(StatusListError, match="empty"):
            decode_status_list("")

    def test_decode_bad_base64(self) -> None:
        with pytest.raises(StatusListError):
            decode_status_list("not!!base64??")

    def test_decode_bad_gzip(self) -> None:
        # Valid base64url but not gzip.
        bad = "aGVsbG8"  # "hello"
        with pytest.raises(StatusListError, match="gunzip"):
            decode_status_list(bad)


class TestBuildUrl:
    def test_happy_path(self) -> None:
        assert (
            build_status_list_url("acme.example", "2026q2")
            == "https://acme.example/.well-known/shadownet/status/2026q2"
        )

    def test_missing_components(self) -> None:
        with pytest.raises(StatusListError):
            build_status_list_url("", "epoch")
        with pytest.raises(StatusListError):
            build_status_list_url("acme.example", "")


class TestFetchStatusList:
    @respx.mock
    def test_happy_path(self) -> None:
        body = encode_status_list(StatusList.empty(1024).with_revoked(871))
        respx.get("https://acme.example/.well-known/shadownet/status/2026q2").mock(
            return_value=httpx.Response(200, text=body, headers={"Cache-Control": "max-age=300"})
        )
        status_list, max_age = fetch_status_list("acme.example", "2026q2")
        assert max_age == 300
        assert status_list.is_revoked(871) is True

    @respx.mock
    def test_http_error(self) -> None:
        respx.get("https://acme.example/.well-known/shadownet/status/2026q2").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(StatusListError, match="HTTP 404"):
            fetch_status_list("acme.example", "2026q2")

    @respx.mock
    def test_no_cache_header(self) -> None:
        body = encode_status_list(StatusList.empty(64))
        respx.get("https://acme.example/.well-known/shadownet/status/2026q2").mock(
            return_value=httpx.Response(200, text=body)
        )
        _, max_age = fetch_status_list("acme.example", "2026q2")
        assert max_age is None


class TestCheckRevocation:
    def test_not_revoked_passes(self) -> None:
        list64 = StatusList.empty(64)
        check_revocation(
            _credential_at(42),
            fetch=lambda _iss, _ep, *, client=None: (list64, None),
        )

    def test_revoked_raises(self) -> None:
        revoked = StatusList.empty(64).with_revoked(42)
        with pytest.raises(StatusListError, match="revoked"):
            check_revocation(
                _credential_at(42),
                fetch=lambda _iss, _ep, *, client=None: (revoked, None),
            )

    def test_fetch_failure_fails_closed(self) -> None:
        def fetcher(_iss: str, _ep: str, *, client: httpx.Client | None = None):
            raise StatusListError("upstream down")

        with pytest.raises(StatusListError, match="upstream down"):
            check_revocation(_credential_at(0), fetch=fetcher)
