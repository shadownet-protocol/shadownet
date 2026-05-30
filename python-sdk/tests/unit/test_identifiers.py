from __future__ import annotations

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import (
    InvalidIdentifierError,
    canonicalize_identifier,
    canonicalize_issuer_or_org_identifier,
    encode_public_key,
    is_public_key_identifier,
    is_shadowname,
    is_subdomain_of,
    parse_public_key,
    parse_shadowname,
    split_shadowname,
)


class TestShadowname:
    def test_canonical(self) -> None:
        assert parse_shadowname("alice@sh4dow.org") == "alice@sh4dow.org"

    def test_lowercased(self) -> None:
        assert parse_shadowname("Alice@SH4DOW.ORG") == "alice@sh4dow.org"

    def test_local_with_dots_and_hyphens(self) -> None:
        assert parse_shadowname("a.b-c_d@sh4dow.org") == "a.b-c_d@sh4dow.org"

    def test_split(self) -> None:
        assert split_shadowname("alice@sh4dow.org") == ("alice", "sh4dow.org")

    @pytest.mark.parametrize(
        "value",
        [
            "no-at-sign",
            "@sh4dow.org",
            "alice@",
            "alice@-bad-.org",
            "a" * 64 + "@sh4dow.org",
            "ali ce@sh4dow.org",
            "alice@.org",
        ],
    )
    def test_rejected(self, value: str) -> None:
        with pytest.raises(InvalidIdentifierError):
            parse_shadowname(value)


class TestDomain:
    def test_subdomain_match(self) -> None:
        assert is_subdomain_of("hr.acme.example", "acme.example") is True

    def test_same_domain(self) -> None:
        assert is_subdomain_of("acme.example", "acme.example") is True

    def test_unrelated(self) -> None:
        assert is_subdomain_of("evil.example", "acme.example") is False

    def test_suffix_not_subdomain(self) -> None:
        # "fakeacme.example" must not match "acme.example".
        assert is_subdomain_of("fakeacme.example", "acme.example") is False

    def test_case_insensitive(self) -> None:
        assert is_subdomain_of("HR.ACME.example", "Acme.Example") is True


class TestMultibasePublicKey:
    def test_roundtrip(self) -> None:
        key = Ed25519KeyPair.generate()
        encoded = encode_public_key(key.public_bytes)
        assert encoded.startswith("z6Mk")
        assert parse_public_key(encoded) == key.public_bytes

    def test_rejects_bad_prefix(self) -> None:
        with pytest.raises(InvalidIdentifierError):
            parse_public_key("zNotMk0000")


class TestDiscriminator:
    def test_is_shadowname(self) -> None:
        assert is_shadowname("alice@sh4dow.org") is True
        assert is_shadowname("z6MkAlice123") is False
        assert is_shadowname("") is False

    def test_is_public_key_identifier(self) -> None:
        key = Ed25519KeyPair.generate()
        encoded = encode_public_key(key.public_bytes)
        assert is_public_key_identifier(encoded) is True
        assert is_public_key_identifier("alice@sh4dow.org") is False
        assert is_public_key_identifier("zSomethingElse") is False


class TestCanonicalizeIdentifier:
    def test_shadowname_passthrough(self) -> None:
        assert canonicalize_identifier("Alice@SH4DOW.org") == "alice@sh4dow.org"

    def test_public_key_passthrough(self) -> None:
        encoded = encode_public_key(Ed25519KeyPair.generate().public_bytes)
        assert canonicalize_identifier(encoded) == encoded

    def test_garbage_rejected(self) -> None:
        with pytest.raises(InvalidIdentifierError, match="Shadowname or"):
            canonicalize_identifier("just-some-string")


class TestCanonicalizeIssuerOrOrg:
    def test_domain(self) -> None:
        assert canonicalize_issuer_or_org_identifier("ACME.example") == "acme.example"

    def test_public_key(self) -> None:
        encoded = encode_public_key(Ed25519KeyPair.generate().public_bytes)
        assert canonicalize_issuer_or_org_identifier(encoded) == encoded

    def test_shadowname_rejected(self) -> None:
        # iss / org accept domain or pubkey; Shadowname is not a valid issuer
        # or org identifier — the domain validator should fail it via @ char.
        with pytest.raises(InvalidIdentifierError):
            canonicalize_issuer_or_org_identifier("alice@sh4dow.org")
