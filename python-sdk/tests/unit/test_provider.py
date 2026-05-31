from __future__ import annotations

from unittest.mock import MagicMock

import dns.rdtypes.ANY.TXT
import dns.resolver
import pytest

from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.identifiers import encode_public_key
from shadownet.provider import (
    ProviderResolutionError,
    lookup_provider_record,
    parse_provider_txt,
)


@pytest.fixture
def pk() -> str:
    return encode_public_key(Ed25519KeyPair.generate().public_bytes)


def _txt_rdata(text: str) -> dns.rdtypes.ANY.TXT.TXT:
    # RFC 1035 §3.3.14: TXT-DATA is one-or-more <character-string>s.
    chunks = [text[i : i + 100].encode("utf-8") for i in range(0, len(text), 100)] or [b""]
    return dns.rdtypes.ANY.TXT.TXT(dns.rdataclass.IN, dns.rdatatype.TXT, chunks)


def _fake_resolver(rdatas: list[dns.rdtypes.ANY.TXT.TXT]) -> dns.resolver.Resolver:
    r = MagicMock(spec=dns.resolver.Resolver)
    r.resolve.return_value = rdatas
    return r


class TestParseProviderTxt:
    def test_minimal(self, pk: str) -> None:
        record = parse_provider_txt(
            "sh4dow.org",
            f"v=0.2; ep=https://shadow.sh4dow.org/v1; pk={pk}",
        )
        assert record.version == "0.2"
        assert record.endpoint == "https://shadow.sh4dow.org/v1"
        assert record.provider_keys == (pk,)
        assert record.is_issuer is False
        assert record.delegates == ()

    def test_issuer_flag(self, pk: str) -> None:
        record = parse_provider_txt(
            "acme.example",
            f"v=0.2; ep=https://acme.example/v1; pk={pk}; iss=true",
        )
        assert record.is_issuer is True

    def test_multiple_delegates(self, pk: str) -> None:
        record = parse_provider_txt(
            "acme.example",
            (
                f"v=0.2; ep=https://acme.example/v1; pk={pk};"
                " delegate=verify.acme.example; delegate=hr.partner.example"
            ),
        )
        assert record.delegates == ("verify.acme.example", "hr.partner.example")

    def test_loopback_endpoint(self, pk: str) -> None:
        record = parse_provider_txt(
            "aliceland.test",
            f"v=0.2; ep=http://localhost:7777/v1; pk={pk}",
        )
        assert record.endpoint == "http://localhost:7777/v1"

    def test_wrong_version_rejected(self, pk: str) -> None:
        with pytest.raises(ProviderResolutionError, match="unsupported v"):
            parse_provider_txt("x.example", f"v=0.1; ep=https://x.example; pk={pk}")

    def test_plaintext_endpoint_rejected(self, pk: str) -> None:
        with pytest.raises(ProviderResolutionError, match="must be https"):
            parse_provider_txt("x.example", f"v=0.2; ep=http://x.example; pk={pk}")

    def test_missing_required_keys(self) -> None:
        with pytest.raises(ProviderResolutionError, match="missing required key"):
            parse_provider_txt("x.example", "v=0.2; ep=https://x.example")

    def test_duplicate_required_key(self, pk: str) -> None:
        with pytest.raises(ProviderResolutionError, match="more than once"):
            parse_provider_txt(
                "x.example",
                f"v=0.2; ep=https://x.example; ep=https://y.example; pk={pk}",
            )

    def test_invalid_pk(self) -> None:
        with pytest.raises(ProviderResolutionError, match="invalid pk"):
            parse_provider_txt("x.example", "v=0.2; ep=https://x.example; pk=not-multibase")

    def test_malformed_pair(self, pk: str) -> None:
        with pytest.raises(ProviderResolutionError, match="malformed"):
            parse_provider_txt("x.example", f"v=0.2; bogus_no_equals; pk={pk}")


class TestLookupProviderRecord:
    def test_concatenates_chained_strings(self, pk: str) -> None:
        # Force string chaining: split across multiple chunks.
        text = f"v=0.2; ep=https://shadow.sh4dow.org/v1; pk={pk}"
        rdata = _txt_rdata(text)
        record = lookup_provider_record("sh4dow.org", resolver=_fake_resolver([rdata]))
        assert record.endpoint == "https://shadow.sh4dow.org/v1"

    def test_rejects_multiple_v02_records(self, pk: str) -> None:
        # Ambiguous: two distinct TXT records both parsing as v=0.2.
        a = _txt_rdata(f"v=0.2; ep=https://a.example; pk={pk}")
        b = _txt_rdata(f"v=0.2; ep=https://b.example; pk={pk}")
        with pytest.raises(ProviderResolutionError, match="multiple"):
            lookup_provider_record("ambiguous.example", resolver=_fake_resolver([a, b]))

    def test_skips_records_that_dont_parse(self, pk: str) -> None:
        # Non-Shadownet TXT (e.g. SPF) and a valid Shadownet record at the same name.
        garbage = _txt_rdata("v=spf1 -all")
        good = _txt_rdata(f"v=0.2; ep=https://sh4dow.org/v1; pk={pk}")
        record = lookup_provider_record("sh4dow.org", resolver=_fake_resolver([garbage, good]))
        assert record.endpoint == "https://sh4dow.org/v1"

    def test_nxdomain(self, pk: str) -> None:
        r = MagicMock(spec=dns.resolver.Resolver)
        r.resolve.side_effect = dns.resolver.NXDOMAIN()
        with pytest.raises(ProviderResolutionError, match="no _shadownet TXT"):
            lookup_provider_record("nowhere.example", resolver=r)

    def test_noanswer(self) -> None:
        r = MagicMock(spec=dns.resolver.Resolver)
        r.resolve.side_effect = dns.resolver.NoAnswer()
        with pytest.raises(ProviderResolutionError, match="no TXT answer"):
            lookup_provider_record("empty.example", resolver=r)
