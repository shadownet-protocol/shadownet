"""Async surface — parity coverage for the dual-surface async siblings.

Mirrors selected sync tests in ``test_provider.py``, ``test_status.py``,
``test_agentcard.py``, ``test_csr.py``, ``test_a2a.py``, ``test_onboarding.py``
and ``test_receiver.py`` so the new ``a*`` functions and
``AsyncReceiverPipeline`` are exercised through their full happy-path +
canonical-error contracts.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import dns.asyncresolver
import dns.rdataclass
import dns.rdatatype
import dns.rdtypes.ANY.TXT
import dns.resolver
import httpx
import pytest
import respx

from shadownet.a2a import (
    BuiltMessage,
    TransportError,
    asend_envelope,
    build_and_sign_message,
    build_outbound_message,
)
from shadownet.agentcard import (
    FetchedAgentCard,
    afetch_agent_card_json,
    afetch_and_verify_agent_card,
    build_signed_agent_card,
)
from shadownet.credential import (
    ORG_AFFILIATION,
    CredentialPayload,
    RevocationPointer,
    averify_credential,
    mint_credential,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.csr import CsrError, asubmit_csr
from shadownet.envelope import EnvelopeBody, EnvelopePayload
from shadownet.identifiers import encode_public_key
from shadownet.onboarding import (
    HandoffExpiredError,
    HandoffUnknownError,
    RefreshError,
    aredeem_handoff,
    arefresh_access_token,
)
from shadownet.provider import (
    ProviderRecord,
    ProviderResolutionError,
    alookup_provider_record,
)
from shadownet.receiver import (
    AsyncInMemoryContactGraph,
    AsyncInMemoryCredentialCache,
    AsyncInMemoryReplayCache,
    AsyncReceiverPipeline,
    ReceiverConfig,
)
from shadownet.status import (
    StatusList,
    StatusListError,
    acheck_revocation,
    afetch_status_list,
    encode_status_list,
)
from shadownet.trust import AcceptancePolicy, TrustEntry, TrustStore

SUBJECT = "bob@example.org"


def _txt_rdata(text: str) -> dns.rdtypes.ANY.TXT.TXT:
    chunks = [text[i : i + 100].encode("utf-8") for i in range(0, len(text), 100)] or [b""]
    return dns.rdtypes.ANY.TXT.TXT(dns.rdataclass.IN, dns.rdatatype.TXT, chunks)


def _fake_async_resolver(rdatas: list[dns.rdtypes.ANY.TXT.TXT]) -> dns.asyncresolver.Resolver:
    r = MagicMock(spec=dns.asyncresolver.Resolver)
    r.resolve = AsyncMock(return_value=rdatas)
    return r


class TestAsyncLookupProviderRecord:
    async def test_happy_path(self) -> None:
        pk = encode_public_key(Ed25519KeyPair.generate().public_bytes)
        resolver = _fake_async_resolver(
            [_txt_rdata(f"v=0.2; ep=https://shadow.sh4dow.org/v1; pk={pk}")]
        )
        record = await alookup_provider_record("sh4dow.org", resolver=resolver)
        assert record.version == "0.2"
        assert record.endpoint == "https://shadow.sh4dow.org/v1"
        assert record.provider_keys == (pk,)

    async def test_nxdomain_maps_to_resolution_error(self) -> None:
        resolver = MagicMock(spec=dns.asyncresolver.Resolver)
        resolver.resolve = AsyncMock(side_effect=dns.resolver.NXDOMAIN())
        with pytest.raises(ProviderResolutionError, match="no _shadownet TXT"):
            await alookup_provider_record("ghost.example", resolver=resolver)

    async def test_noanswer_maps_to_resolution_error(self) -> None:
        resolver = MagicMock(spec=dns.asyncresolver.Resolver)
        resolver.resolve = AsyncMock(side_effect=dns.resolver.NoAnswer())
        with pytest.raises(ProviderResolutionError, match="no TXT answer"):
            await alookup_provider_record("empty.example", resolver=resolver)


class TestAsyncFetchStatusList:
    @respx.mock
    async def test_happy_path(self) -> None:
        body = encode_status_list(StatusList.empty(1024).with_revoked(871))
        respx.get("https://acme.example/.well-known/shadownet/status/2026q2").mock(
            return_value=httpx.Response(200, text=body, headers={"Cache-Control": "max-age=300"})
        )
        status_list, max_age = await afetch_status_list("acme.example", "2026q2")
        assert max_age == 300
        assert status_list.is_revoked(871) is True

    @respx.mock
    async def test_http_error(self) -> None:
        respx.get("https://acme.example/.well-known/shadownet/status/2026q2").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(StatusListError, match="HTTP 404"):
            await afetch_status_list("acme.example", "2026q2")


class TestAsyncCheckRevocation:
    async def test_not_revoked_passes(self) -> None:
        list64 = StatusList.empty(64)

        async def fetch(_iss: str, _ep: str, *, client: httpx.AsyncClient | None = None) -> Any:
            return list64, None

        await acheck_revocation(_credential_at(42), fetch=fetch)

    async def test_revoked_raises(self) -> None:
        revoked = StatusList.empty(64).with_revoked(42)

        async def fetch(_iss: str, _ep: str, *, client: httpx.AsyncClient | None = None) -> Any:
            return revoked, None

        with pytest.raises(StatusListError, match="revoked"):
            await acheck_revocation(_credential_at(42), fetch=fetch)


class TestAsyncAgentCard:
    @respx.mock
    async def test_fetch_agent_card_json_happy(self) -> None:
        provider_record = ProviderRecord(
            domain="sh4dow.org",
            version="0.2",
            endpoint="https://shadow.sh4dow.org/v1",
            provider_keys=("z6MkTestKey",),
        )
        respx.get("https://shadow.sh4dow.org/v1/identity/alice").mock(
            return_value=httpx.Response(200, json={"hello": "world"})
        )
        body, _ = await afetch_agent_card_json("alice@sh4dow.org", provider_record)
        assert body == {"hello": "world"}

    @respx.mock
    async def test_fetch_and_verify_happy(self) -> None:
        provider_key = Ed25519KeyPair.generate()
        provider_pk = encode_public_key(provider_key.public_bytes)
        shadow_key = Ed25519KeyPair.generate()
        shadow_pk = encode_public_key(shadow_key.public_bytes)

        provider_record = ProviderRecord(
            domain="sh4dow.org",
            version="0.2",
            endpoint="https://shadow.sh4dow.org/v1",
            provider_keys=(provider_pk,),
        )
        card = build_signed_agent_card(
            name="alice",
            description="a shadow",
            version="1.0",
            a2a_url="https://shadow.sh4dow.org/v1/a2a/alice",
            shadow_public_key=shadow_pk,
            provider_key=provider_key,
            provider_domain="sh4dow.org",
        )
        respx.get("https://shadow.sh4dow.org/v1/identity/alice").mock(
            return_value=httpx.Response(200, json=card)
        )
        verified = await afetch_and_verify_agent_card("alice@sh4dow.org", provider_record)
        assert verified.shadowname == "alice@sh4dow.org"
        assert verified.shadow_public_key == shadow_pk


class TestAsyncCsrSubmit:
    @respx.mock
    async def test_unexpected_status_raises(self) -> None:
        respx.post("https://acme.example/.well-known/shadownet/issue").mock(
            return_value=httpx.Response(500)
        )
        with pytest.raises(CsrError, match="unexpected HTTP 500"):
            await asubmit_csr("aaa.bbb.ccc", "acme.example")


class TestAsyncSendEnvelope:
    @respx.mock
    async def test_transport_error_maps_to_transport_error(self) -> None:
        # httpx-level connect failure → asend_envelope raises TransportError
        # (distinct from ParseError, which is for envelope/JSON unparseable
        # responses). This split lets retry callers branch correctly per §8.10.
        respx.post("https://shadow.sh4dow.org/v1/a2a/alice/message:send").mock(
            side_effect=httpx.ConnectError("nope")
        )
        built = _fake_built_message()
        with pytest.raises(TransportError, match="transport failed"):
            await asend_envelope(built, "https://shadow.sh4dow.org/v1/a2a/alice")


class TestAsyncOnboarding:
    @respx.mock
    async def test_handoff_404_maps_to_unknown(self) -> None:
        respx.post(
            "https://mcp.example/.well-known/shadownet/onboard/handoff/A1B2C3D4E5F6G7H8"
        ).mock(return_value=httpx.Response(404))
        with pytest.raises(HandoffUnknownError):
            await aredeem_handoff("https://mcp.example", "A1B2C3D4E5F6G7H8")

    @respx.mock
    async def test_handoff_410_maps_to_expired(self) -> None:
        respx.post(
            "https://mcp.example/.well-known/shadownet/onboard/handoff/A1B2C3D4E5F6G7H8"
        ).mock(return_value=httpx.Response(410))
        with pytest.raises(HandoffExpiredError):
            await aredeem_handoff("https://mcp.example", "A1B2C3D4E5F6G7H8")

    @respx.mock
    async def test_refresh_unexpected_status(self) -> None:
        respx.post("https://mcp.example/.well-known/shadownet/onboard/refresh").mock(
            return_value=httpx.Response(500)
        )
        with pytest.raises(RefreshError, match="HTTP 500"):
            await arefresh_access_token("https://mcp.example", "tok-abc")


class TestAsyncVerifyCredential:
    async def test_happy_path_awaits_callbacks(self) -> None:
        issuer_key = Ed25519KeyPair.generate()
        issuer_pk = encode_public_key(issuer_key.public_bytes)
        now = int(time.time())
        payload = CredentialPayload(
            iss="acme.example",
            sub="alice@sh4dow.org",
            kind=ORG_AFFILIATION,
            org="acme.example",
            iat=now,
            exp=now + 3600,
            rev=RevocationPointer(epoch="2026q2", idx=1),
        )
        token = mint_credential(payload, issuer_key)
        resolver_calls: list[str] = []
        authorize_calls: list[tuple[str, str]] = []

        async def resolver(iss: str) -> str:
            resolver_calls.append(iss)
            return issuer_pk

        async def authorize(iss: str, org: str) -> None:
            authorize_calls.append((iss, org))

        verified = await averify_credential(
            token,
            resolve_issuer_key=resolver,
            check_issuer_authorized_for_org=authorize,
        )
        assert verified.payload.iss == "acme.example"
        assert resolver_calls == ["acme.example"]
        assert authorize_calls == [("acme.example", "acme.example")]


class TestAsyncReceiverPipeline:
    async def test_contact_routes_to_inbox(self) -> None:
        alice_key = Ed25519KeyPair.generate()
        cg = AsyncInMemoryContactGraph()
        await cg.add_contact("alice@sh4dow.org")
        config = _config()
        pipeline = _async_pipeline(config, alice_key, contact_graph=cg)
        built = _alice_built(alice_key)
        decision = await pipeline.receive({"message": built.message})
        assert decision.route == "inbox"
        assert decision.sender == "alice@sh4dow.org"
        assert decision.auto_added_contact is False

    async def test_auto_add_on_outbound_context(self) -> None:
        alice_key = Ed25519KeyPair.generate()
        cg = AsyncInMemoryContactGraph()
        await cg.record_outbound(context_id="ctx-auto", peer="alice@sh4dow.org")
        config = _config()
        pipeline = _async_pipeline(config, alice_key, contact_graph=cg)
        built = _alice_built(alice_key, context_id="ctx-auto")
        decision = await pipeline.receive({"message": built.message})
        assert decision.route == "inbox"
        assert decision.auto_added_contact is True
        assert await cg.is_contact("alice@sh4dow.org") is True

    async def test_stranger_with_valid_cred_routes_to_stranger_review(self) -> None:
        alice_key = Ed25519KeyPair.generate()
        acme_issuer_key = Ed25519KeyPair.generate()
        acme_issuer_pk = encode_public_key(acme_issuer_key.public_bytes)
        config = _config(
            trust=TrustStore(
                entries=(TrustEntry(issuer="acme.example", accept=(ORG_AFFILIATION,)),)
            ),
            policy=AcceptancePolicy(fromStranger=(ORG_AFFILIATION,)),
        )
        cred = _alice_credential(acme_issuer_key)
        built = _alice_built(alice_key, creds=(cred,))
        pipeline = _async_pipeline(config, alice_key, issuer_pk=acme_issuer_pk)
        decision = await pipeline.receive({"message": built.message})
        assert decision.route == "stranger_review"

    async def test_keyed_hub_credential_skips_dns(self) -> None:
        """Async sibling of the §3.3 / §6.6 rule 1 sync test.

        Guards that ``AsyncReceiverPipeline._resolve_issuer_key`` short-circuits
        on a multibase issuer instead of asking ``provider_lookup`` to resolve
        ``"z6Mk..."``.
        """
        alice_key = Ed25519KeyPair.generate()
        keyed_issuer = Ed25519KeyPair.generate()
        keyed_iss = encode_public_key(keyed_issuer.public_bytes)
        now = int(time.time())
        cred = mint_credential(
            CredentialPayload(
                iss=keyed_iss,
                sub="alice@sh4dow.org",
                kind=ORG_AFFILIATION,
                org=keyed_iss,
                iat=now,
                exp=now + 3600,
                rev=RevocationPointer(epoch="2026q2", idx=7),
            ),
            keyed_issuer,
        )
        config = _config(
            trust=TrustStore(entries=(TrustEntry(issuer=keyed_iss, accept=(ORG_AFFILIATION,)),)),
            policy=AcceptancePolicy(fromStranger=(ORG_AFFILIATION,)),
        )
        built = _alice_built(alice_key, creds=(cred,))
        pipeline = _async_pipeline(config, alice_key)
        decision = await pipeline.receive({"message": built.message})
        assert decision.route == "stranger_review"


def _config(
    trust: TrustStore | None = None,
    policy: AcceptancePolicy | None = None,
    *,
    same_provider_org: bool = False,
) -> ReceiverConfig:
    return ReceiverConfig(
        subject=SUBJECT,
        trust_store=trust or TrustStore(),
        policy=policy or AcceptancePolicy(),
        same_provider_org=same_provider_org,
    )


def _async_pipeline(
    config: ReceiverConfig,
    alice_key: Ed25519KeyPair,
    *,
    contact_graph: AsyncInMemoryContactGraph | None = None,
    issuer_pk: str | None = None,
) -> AsyncReceiverPipeline:
    cg = contact_graph or AsyncInMemoryContactGraph()
    provider_record = ProviderRecord(
        domain="sh4dow.org",
        version="0.2",
        endpoint="https://shadow.sh4dow.org/v1",
        provider_keys=(encode_public_key(Ed25519KeyPair.generate().public_bytes),),
    )

    async def lookup(domain: str) -> ProviderRecord:
        if domain == "sh4dow.org":
            return provider_record
        if domain == "acme.example" and issuer_pk:
            return ProviderRecord(
                domain="acme.example",
                version="0.2",
                endpoint="https://acme.example/v1",
                provider_keys=(issuer_pk,),
            )
        raise AssertionError(f"unexpected provider lookup: {domain}")

    async def fetcher(_name: str, _rec: ProviderRecord) -> FetchedAgentCard:
        return FetchedAgentCard(
            shadowname="alice@sh4dow.org",
            shadow_public_key=encode_public_key(alice_key.public_bytes),
            endpoint_url="https://shadow.sh4dow.org/v1/a2a/alice",
            cache_max_age=3600,
            etag=None,
            raw={},
        )

    async def revocation(_cred: Any) -> None:
        return None

    return AsyncReceiverPipeline(
        config,
        replay_cache=AsyncInMemoryReplayCache(),
        contact_graph=cg,
        credential_cache=AsyncInMemoryCredentialCache(),
        provider_lookup=lookup,
        agent_card_fetcher=fetcher,
        revocation_check=revocation,
    )


def _alice_envelope_template(msg_hash: str, *, creds: tuple[str, ...] = ()) -> EnvelopePayload:
    now = int(time.time())
    return EnvelopePayload(
        v="0.2",
        **{
            "from": "alice@sh4dow.org",
            "to": SUBJECT,
            "msgHash": msg_hash,
        },
        iat=now,
        exp=now + 60,
        body=EnvelopeBody(text="hi", intent=None, data=None),
        creds=creds,
    )


def _alice_built(
    alice_key: Ed25519KeyPair,
    *,
    context_id: str | None = None,
    creds: tuple[str, ...] = (),
) -> BuiltMessage:
    outbound = build_outbound_message(body_text="hi", context_id=context_id)
    return build_and_sign_message(
        outbound,
        _alice_envelope_template("sha256:placeholder", creds=creds),
        alice_key,
    )


def _alice_credential(issuer_key: Ed25519KeyPair) -> str:
    now = int(time.time())
    payload = CredentialPayload(
        iss="acme.example",
        sub="alice@sh4dow.org",
        kind=ORG_AFFILIATION,
        org="acme.example",
        iat=now,
        exp=now + 3600,
        rev=RevocationPointer(epoch="2026q2", idx=42),
    )
    return mint_credential(payload, issuer_key)


def _credential_at(idx: int) -> Any:
    """Build the minimal VerifiedCredential shape acheck_revocation needs."""
    issuer_key = Ed25519KeyPair.generate()
    issuer_pk = encode_public_key(issuer_key.public_bytes)
    now = int(time.time())
    payload = CredentialPayload(
        iss="acme.example",
        sub="alice@sh4dow.org",
        kind=ORG_AFFILIATION,
        org="acme.example",
        iat=now,
        exp=now + 3600,
        rev=RevocationPointer(epoch="2026q2", idx=idx),
    )
    from shadownet.credential import VerifiedCredential

    return VerifiedCredential(payload=payload, issuer_key=issuer_pk, raw_jws="dummy.jws.token")


def _fake_built_message() -> BuiltMessage:
    alice_key = Ed25519KeyPair.generate()
    outbound = build_outbound_message(body_text="hi")
    return build_and_sign_message(
        outbound,
        _alice_envelope_template("sha256:placeholder"),
        alice_key,
    )
