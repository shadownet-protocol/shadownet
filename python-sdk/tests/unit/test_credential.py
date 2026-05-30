from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from shadownet.credential import (
    CREDENTIAL_TYP,
    MAX_LIFETIME_SECONDS,
    ORG_AFFILIATION,
    CredentialError,
    CredentialPayload,
    RevocationPointer,
    mint_credential,
    verify_credential,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.crypto.jwt import decode_header, sign_jwt
from shadownet.identifiers import encode_public_key


@pytest.fixture
def issuer_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def issuer_pk(issuer_key: Ed25519KeyPair) -> str:
    return encode_public_key(issuer_key.public_bytes)


@pytest.fixture
def base_payload() -> CredentialPayload:
    now = int(time.time())
    return CredentialPayload(
        iss="acme.example",
        sub="alice@sh4dow.org",
        kind=ORG_AFFILIATION,
        org="acme.example",
        iat=now,
        exp=now + 7 * 24 * 60 * 60,
        rev=RevocationPointer(epoch="2026q2", idx=42),
    )


def _resolver(pk: str):
    return lambda _: pk


def _authorize_ok(_iss: str, _org: str) -> None:
    return None


def _authorize_reject(_iss: str, _org: str) -> None:
    raise CredentialError("not authorized")


class TestMintCredential:
    def test_happy_path(self, base_payload: CredentialPayload, issuer_key: Ed25519KeyPair) -> None:
        token = mint_credential(base_payload, issuer_key)
        header = decode_header(token)
        assert header["typ"] == CREDENTIAL_TYP
        assert header["alg"] == "EdDSA"

    def test_lifetime_over_30_days_rejected(self, issuer_key: Ed25519KeyPair) -> None:
        now = int(time.time())
        payload = CredentialPayload(
            iss="acme.example",
            sub="alice@sh4dow.org",
            kind=ORG_AFFILIATION,
            org="acme.example",
            iat=now,
            exp=now + MAX_LIFETIME_SECONDS + 1,
            rev=RevocationPointer(epoch="e", idx=0),
        )
        with pytest.raises(CredentialError, match="30 days"):
            mint_credential(payload, issuer_key)


class TestVerifyCredential:
    def test_happy_path(
        self,
        base_payload: CredentialPayload,
        issuer_key: Ed25519KeyPair,
        issuer_pk: str,
    ) -> None:
        token = mint_credential(base_payload, issuer_key)
        verified = verify_credential(
            token,
            resolve_issuer_key=_resolver(issuer_pk),
            check_issuer_authorized_for_org=_authorize_ok,
        )
        assert verified.payload.sub == "alice@sh4dow.org"
        assert verified.payload.kind == ORG_AFFILIATION
        assert verified.issuer_key == issuer_pk

    def test_wrong_typ_rejected(
        self, base_payload: CredentialPayload, issuer_key: Ed25519KeyPair, issuer_pk: str
    ) -> None:
        bogus = sign_jwt(base_payload.model_dump(), issuer_key, header_extras={"typ": "JWT"})
        with pytest.raises(CredentialError, match="typ"):
            verify_credential(
                bogus,
                resolve_issuer_key=_resolver(issuer_pk),
                check_issuer_authorized_for_org=_authorize_ok,
            )

    def test_unknown_kind_rejected(
        self, base_payload: CredentialPayload, issuer_key: Ed25519KeyPair, issuer_pk: str
    ) -> None:
        claims = base_payload.model_dump()
        claims["kind"] = "personhood"
        bogus = sign_jwt(claims, issuer_key, header_extras={"typ": CREDENTIAL_TYP})
        with pytest.raises(CredentialError, match="unknown credential kind"):
            verify_credential(
                bogus,
                resolve_issuer_key=_resolver(issuer_pk),
                check_issuer_authorized_for_org=_authorize_ok,
            )

    def test_expired_rejected(
        self,
        issuer_key: Ed25519KeyPair,
        issuer_pk: str,
    ) -> None:
        now = int(time.time())
        payload = CredentialPayload(
            iss="acme.example",
            sub="alice@sh4dow.org",
            kind=ORG_AFFILIATION,
            org="acme.example",
            iat=now - 7200,
            exp=now - 3600,
            rev=RevocationPointer(epoch="e", idx=0),
        )
        token = mint_credential(payload, issuer_key)
        with pytest.raises(CredentialError, match="expired"):
            verify_credential(
                token,
                resolve_issuer_key=_resolver(issuer_pk),
                check_issuer_authorized_for_org=_authorize_ok,
            )

    def test_iat_in_future_rejected(self, issuer_key: Ed25519KeyPair, issuer_pk: str) -> None:
        now = int(time.time())
        payload = CredentialPayload(
            iss="acme.example",
            sub="alice@sh4dow.org",
            kind=ORG_AFFILIATION,
            org="acme.example",
            iat=now + 7200,
            exp=now + 14400,
            rev=RevocationPointer(epoch="e", idx=0),
        )
        token = mint_credential(payload, issuer_key)
        with pytest.raises(CredentialError, match="future"):
            verify_credential(
                token,
                resolve_issuer_key=_resolver(issuer_pk),
                check_issuer_authorized_for_org=_authorize_ok,
            )

    def test_signature_mismatch_rejected(
        self, base_payload: CredentialPayload, issuer_key: Ed25519KeyPair
    ) -> None:
        token = mint_credential(base_payload, issuer_key)
        # Wrong key when verifying.
        wrong = encode_public_key(Ed25519KeyPair.generate().public_bytes)
        with pytest.raises(CredentialError, match="signature"):
            verify_credential(
                token,
                resolve_issuer_key=_resolver(wrong),
                check_issuer_authorized_for_org=_authorize_ok,
            )

    def test_issuer_not_authorized_rejected(
        self,
        base_payload: CredentialPayload,
        issuer_key: Ed25519KeyPair,
        issuer_pk: str,
    ) -> None:
        token = mint_credential(base_payload, issuer_key)
        with pytest.raises(CredentialError, match="not authorized"):
            verify_credential(
                token,
                resolve_issuer_key=_resolver(issuer_pk),
                check_issuer_authorized_for_org=_authorize_reject,
            )

    def test_leeway_accepts_just_expired(
        self,
        issuer_key: Ed25519KeyPair,
        issuer_pk: str,
    ) -> None:
        now = int(time.time())
        # exp 30s in the past; default leeway is 60s.
        payload = CredentialPayload(
            iss="acme.example",
            sub="alice@sh4dow.org",
            kind=ORG_AFFILIATION,
            org="acme.example",
            iat=now - 1000,
            exp=now - 30,
            rev=RevocationPointer(epoch="e", idx=0),
        )
        token = mint_credential(payload, issuer_key)
        verify_credential(
            token,
            resolve_issuer_key=_resolver(issuer_pk),
            check_issuer_authorized_for_org=_authorize_ok,
        )


class TestKeyedIssuerVerification:
    def test_keyed_iss_uses_pubkey_directly(self) -> None:
        # Keyed Hub: iss IS the verification key.
        key = Ed25519KeyPair.generate()
        pk = encode_public_key(key.public_bytes)
        now = int(time.time())
        payload = CredentialPayload(
            iss=pk,
            sub="alice@sh4dow.org",
            kind=ORG_AFFILIATION,
            org=pk,  # §6.6 rule 1: iss == org
            iat=now,
            exp=now + 3600,
            rev=RevocationPointer(epoch="e", idx=0),
        )
        token = mint_credential(payload, key)
        # No DNS resolution needed for keyed iss; defaults work.
        verified = verify_credential(
            token,
            resolve_issuer_key=lambda iss: iss,  # identity function: iss IS the key
            check_issuer_authorized_for_org=_authorize_ok,
        )
        assert verified.payload.iss == pk

    def test_keyed_subject_accepted(self) -> None:
        # Direct-mode Shadow: sub is a bare pubkey.
        issuer_key = Ed25519KeyPair.generate()
        subject_pk = encode_public_key(Ed25519KeyPair.generate().public_bytes)
        now = int(time.time())
        payload = CredentialPayload(
            iss="acme.example",
            sub=subject_pk,
            kind=ORG_AFFILIATION,
            org="acme.example",
            iat=now,
            exp=now + 3600,
            rev=RevocationPointer(epoch="e", idx=0),
        )
        token = mint_credential(payload, issuer_key)
        verified = verify_credential(
            token,
            resolve_issuer_key=_resolver(encode_public_key(issuer_key.public_bytes)),
            check_issuer_authorized_for_org=_authorize_ok,
        )
        assert verified.payload.sub == subject_pk


class TestCredentialPayloadValidation:
    def test_uppercase_shadowname_rejected(self) -> None:
        # Shadowname validator forces lowercase; uppercase input becomes lowercase
        # rather than being rejected. Confirm the canonical form is applied.
        now = int(time.time())
        payload = CredentialPayload(
            iss="acme.example",
            sub="Alice@SH4DOW.org",
            kind=ORG_AFFILIATION,
            org="acme.example",
            iat=now,
            exp=now + 3600,
            rev=RevocationPointer(epoch="e", idx=0),
        )
        assert payload.sub == "alice@sh4dow.org"

    def test_unknown_kind_string_rejected_by_validator(self) -> None:
        now = int(time.time())
        with pytest.raises(ValidationError):
            CredentialPayload(
                iss="acme.example",
                sub="alice@sh4dow.org",
                kind="ORG_AFFILIATION",  # uppercase fails the pattern
                org="acme.example",
                iat=now,
                exp=now + 3600,
                rev=RevocationPointer(epoch="e", idx=0),
            )
