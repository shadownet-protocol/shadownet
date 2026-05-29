from __future__ import annotations

import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import (
    InvalidIdentifierError,
    encode_public_key,
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
