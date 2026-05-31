from __future__ import annotations

import time

import httpx
import pytest
import respx

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import decode_header, sign_jwt
from shadownet.csr import (
    CSR_TYP,
    MAX_CSR_LIFETIME_SECONDS,
    CeremonyFailedError,
    CeremonyPendingError,
    CsrError,
    CsrPayload,
    CsrRateLimitedError,
    CsrRequest,
    build_issuer_url,
    mint_csr,
    submit_csr,
    verify_csr,
)


@pytest.fixture
def subject_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def csr_payload() -> CsrPayload:
    now = int(time.time())
    return CsrPayload(
        iss="alice@sh4dow.org",
        aud="acme.example",
        iat=now,
        exp=now + 300,
        req=CsrRequest(kind="org_affiliation", org="acme.example"),
    )


class TestMintCsr:
    def test_happy_path(self, csr_payload: CsrPayload, subject_key: Ed25519KeyPair) -> None:
        token = mint_csr(csr_payload, subject_key)
        header = decode_header(token)
        assert header["typ"] == CSR_TYP
        assert header["alg"] == "EdDSA"

    def test_lifetime_too_long(self, subject_key: Ed25519KeyPair) -> None:
        now = int(time.time())
        payload = CsrPayload(
            iss="alice@sh4dow.org",
            aud="acme.example",
            iat=now,
            exp=now + MAX_CSR_LIFETIME_SECONDS + 1,
            req=CsrRequest(kind="org_affiliation", org="acme.example"),
        )
        with pytest.raises(CsrError, match="lifetime"):
            mint_csr(payload, subject_key)

    def test_exp_not_after_iat(self, subject_key: Ed25519KeyPair) -> None:
        now = int(time.time())
        payload = CsrPayload(
            iss="alice@sh4dow.org",
            aud="acme.example",
            iat=now,
            exp=now,
            req=CsrRequest(kind="org_affiliation", org="acme.example"),
        )
        with pytest.raises(CsrError, match="greater than iat"):
            mint_csr(payload, subject_key)


class TestVerifyCsr:
    def test_happy_path(self, csr_payload: CsrPayload, subject_key: Ed25519KeyPair) -> None:
        token = mint_csr(csr_payload, subject_key)
        result = verify_csr(token, subject_key, expected_audience="acme.example")
        assert result.iss == "alice@sh4dow.org"
        assert result.req.kind == "org_affiliation"

    def test_wrong_audience_rejected(
        self, csr_payload: CsrPayload, subject_key: Ed25519KeyPair
    ) -> None:
        token = mint_csr(csr_payload, subject_key)
        with pytest.raises(CsrError, match="aud"):
            verify_csr(token, subject_key, expected_audience="other.example")

    def test_wrong_typ_rejected(self, csr_payload: CsrPayload, subject_key: Ed25519KeyPair) -> None:
        # Construct a token with a wrong typ but valid payload.
        bogus = sign_jwt(csr_payload.model_dump(), subject_key, header_extras={"typ": "JWT"})
        with pytest.raises(CsrError, match="typ"):
            verify_csr(bogus, subject_key, expected_audience="acme.example")

    def test_expired_rejected(self, subject_key: Ed25519KeyPair) -> None:
        now = int(time.time())
        payload = CsrPayload(
            iss="alice@sh4dow.org",
            aud="acme.example",
            iat=now - 500,
            exp=now - 200,
            req=CsrRequest(kind="org_affiliation", org="acme.example"),
        )
        token = mint_csr(payload, subject_key)
        with pytest.raises(CsrError, match="expired"):
            verify_csr(token, subject_key, expected_audience="acme.example")

    def test_signature_mismatch(self, csr_payload: CsrPayload, subject_key: Ed25519KeyPair) -> None:
        token = mint_csr(csr_payload, subject_key)
        other = Ed25519KeyPair.generate()
        with pytest.raises(CsrError, match="signature"):
            verify_csr(token, other, expected_audience="acme.example")


class TestSubmitCsr:
    @respx.mock
    def test_success(self) -> None:
        respx.post("https://acme.example/.well-known/shadownet/issue").mock(
            return_value=httpx.Response(200, text="header.payload.sig")
        )
        result = submit_csr("csr.body.sig", "acme.example")
        assert result.credential == "header.payload.sig"

    @respx.mock
    def test_success_strips_whitespace(self) -> None:
        respx.post("https://acme.example/.well-known/shadownet/issue").mock(
            return_value=httpx.Response(200, text="  header.payload.sig\n")
        )
        result = submit_csr("csr.body.sig", "acme.example")
        assert result.credential == "header.payload.sig"

    @respx.mock
    def test_success_rejects_non_jws_body(self) -> None:
        respx.post("https://acme.example/.well-known/shadownet/issue").mock(
            return_value=httpx.Response(200, text="not.a.jws.token")
        )
        with pytest.raises(CsrError, match="JWS-compact"):
            submit_csr("csr.body.sig", "acme.example")

    @respx.mock
    def test_ceremony_pending(self) -> None:
        respx.post("https://acme.example/.well-known/shadownet/issue").mock(
            return_value=httpx.Response(409, json={"next": "https://verify.acme.example/start"})
        )
        with pytest.raises(CeremonyPendingError) as excinfo:
            submit_csr("csr.body.sig", "acme.example")
        assert excinfo.value.next_url == "https://verify.acme.example/start"

    @respx.mock
    def test_ceremony_pending_missing_next(self) -> None:
        respx.post("https://acme.example/.well-known/shadownet/issue").mock(
            return_value=httpx.Response(409, json={})
        )
        with pytest.raises(CsrError, match="next"):
            submit_csr("csr.body.sig", "acme.example")

    @respx.mock
    def test_ceremony_failed(self) -> None:
        respx.post("https://acme.example/.well-known/shadownet/issue").mock(
            return_value=httpx.Response(403)
        )
        with pytest.raises(CeremonyFailedError):
            submit_csr("csr.body.sig", "acme.example")

    @respx.mock
    def test_rate_limited(self) -> None:
        respx.post("https://acme.example/.well-known/shadownet/issue").mock(
            return_value=httpx.Response(429)
        )
        with pytest.raises(CsrRateLimitedError):
            submit_csr("csr.body.sig", "acme.example")

    @respx.mock
    def test_unexpected_status(self) -> None:
        respx.post("https://acme.example/.well-known/shadownet/issue").mock(
            return_value=httpx.Response(500)
        )
        with pytest.raises(CsrError, match="HTTP 500"):
            submit_csr("csr.body.sig", "acme.example")


class TestBuildIssuerUrl:
    def test_happy_path(self) -> None:
        assert (
            build_issuer_url("acme.example") == "https://acme.example/.well-known/shadownet/issue"
        )

    def test_missing_domain(self) -> None:
        with pytest.raises(CsrError):
            build_issuer_url("")
