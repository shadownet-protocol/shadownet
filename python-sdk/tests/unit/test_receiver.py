from __future__ import annotations

import time
from typing import Any

import pytest

from shadownet.a2a import (
    BuiltMessage,
    CredsRejectedError,
    CredsRequiredError,
    ParseError,
    PolicyError,
    ReplayError,
    SignatureError,
    UnknownRecipientError,
    build_and_sign_message,
    build_outbound_message,
)
from shadownet.agentcard import FetchedAgentCard
from shadownet.credential import (
    ORG_AFFILIATION,
    CredentialPayload,
    RevocationPointer,
    mint_credential,
)
from shadownet.crypto.ed25519 import Ed25519KeyPair
from shadownet.envelope import EnvelopeBody, EnvelopePayload
from shadownet.identifiers import encode_public_key
from shadownet.provider import ProviderRecord
from shadownet.receiver import (
    InMemoryContactGraph,
    InMemoryCredentialCache,
    InMemoryReplayCache,
    ReceiverConfig,
    ReceiverPipeline,
    ensure_extension_declared,
    header_includes_extension,
)
from shadownet.status import StatusListError
from shadownet.trust import AcceptancePolicy, TrustEntry, TrustStore

SUBJECT = "bob@example.org"


@pytest.fixture
def alice_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def alice_provider_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def acme_issuer_key() -> Ed25519KeyPair:
    return Ed25519KeyPair.generate()


@pytest.fixture
def acme_issuer_pk(acme_issuer_key: Ed25519KeyPair) -> str:
    return encode_public_key(acme_issuer_key.public_bytes)


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
    alice_key: Ed25519KeyPair, *, context_id: str | None = None, creds: tuple[str, ...] = ()
) -> BuiltMessage:
    outbound = build_outbound_message(body_text="hi", context_id=context_id)
    return build_and_sign_message(
        outbound,
        _alice_envelope_template("sha256:placeholder", creds=creds),
        alice_key,
    )


def _alice_provider_record() -> ProviderRecord:
    return ProviderRecord(
        domain="sh4dow.org",
        version="0.2",
        endpoint="https://shadow.sh4dow.org/v1",
        provider_keys=(encode_public_key(Ed25519KeyPair.generate().public_bytes),),
    )


def _alice_card(alice_key: Ed25519KeyPair) -> FetchedAgentCard:
    return FetchedAgentCard(
        shadowname="alice@sh4dow.org",
        shadow_public_key=encode_public_key(alice_key.public_bytes),
        endpoint_url="https://shadow.sh4dow.org/v1/a2a/alice",
        cache_max_age=3600,
        etag=None,
        raw={},
    )


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


def _pipeline(
    config: ReceiverConfig,
    alice_key: Ed25519KeyPair,
    *,
    contact_graph: InMemoryContactGraph | None = None,
    issuer_pk: str | None = None,
    revoked_idx: int | None = None,
) -> ReceiverPipeline:
    cg = contact_graph or InMemoryContactGraph()
    provider_record = _alice_provider_record()

    def lookup(domain: str) -> ProviderRecord:
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

    def fetcher(_name: str, _rec: ProviderRecord) -> FetchedAgentCard:
        return _alice_card(alice_key)

    def revocation_check(cred: Any) -> None:
        if revoked_idx is not None and cred.payload.rev.idx == revoked_idx:
            raise StatusListError("revoked")

    return ReceiverPipeline(
        config,
        replay_cache=InMemoryReplayCache(),
        contact_graph=cg,
        credential_cache=InMemoryCredentialCache(),
        provider_lookup=lookup,
        agent_card_fetcher=fetcher,
        revocation_check=revocation_check,
    )


class TestPipelineHappyPaths:
    def test_contact_routes_to_inbox(self, alice_key: Ed25519KeyPair) -> None:
        cg = InMemoryContactGraph()
        cg.add_contact("alice@sh4dow.org")
        config = _config()
        pipeline = _pipeline(config, alice_key, contact_graph=cg)
        built = _alice_built(alice_key)
        decision = pipeline.receive({"message": built.message})
        assert decision.route == "inbox"
        assert decision.sender == "alice@sh4dow.org"
        assert decision.auto_added_contact is False

    def test_stranger_with_valid_cred_routes_to_stranger_review(
        self,
        alice_key: Ed25519KeyPair,
        acme_issuer_key: Ed25519KeyPair,
        acme_issuer_pk: str,
    ) -> None:
        config = _config(
            trust=TrustStore(
                entries=(TrustEntry(issuer="acme.example", accept=(ORG_AFFILIATION,)),)
            ),
            policy=AcceptancePolicy(fromStranger=(ORG_AFFILIATION,)),
        )
        cred = _alice_credential(acme_issuer_key)
        built = _alice_built(alice_key, creds=(cred,))
        pipeline = _pipeline(config, alice_key, issuer_pk=acme_issuer_pk)
        decision = pipeline.receive({"message": built.message})
        assert decision.route == "stranger_review"

    def test_auto_add_on_outbound_context(self, alice_key: Ed25519KeyPair) -> None:
        cg = InMemoryContactGraph()
        cg.record_outbound(context_id="ctx-auto", peer="alice@sh4dow.org")
        config = _config()
        pipeline = _pipeline(config, alice_key, contact_graph=cg)
        built = _alice_built(alice_key, context_id="ctx-auto")
        decision = pipeline.receive({"message": built.message})
        assert decision.route == "inbox"
        assert decision.auto_added_contact is True
        assert cg.is_contact("alice@sh4dow.org") is True

    def test_same_provider_org_shortcut(self, alice_key: Ed25519KeyPair) -> None:
        # Both Alice and Bob are at sh4dow.org with single-tenant-org flag.
        config = ReceiverConfig(
            subject="bob@sh4dow.org",
            trust_store=TrustStore(),
            policy=AcceptancePolicy(),
            same_provider_org=True,
        )
        pipeline = _pipeline(config, alice_key)
        outbound = build_outbound_message(body_text="hi")
        built = build_and_sign_message(
            outbound,
            EnvelopePayload(
                v="0.2",
                **{
                    "from": "alice@sh4dow.org",
                    "to": "bob@sh4dow.org",
                    "msgHash": "sha256:placeholder",
                },
                iat=int(time.time()),
                exp=int(time.time()) + 60,
                body=EnvelopeBody(text="hi"),
            ),
            alice_key,
        )
        decision = pipeline.receive({"message": built.message})
        assert decision.route == "inbox"


class TestPipelineRejections:
    def test_wrong_recipient(self, alice_key: Ed25519KeyPair) -> None:
        pipeline = _pipeline(_config(), alice_key)
        # Build a message addressed to someone else.
        built = build_and_sign_message(
            build_outbound_message(body_text="hi"),
            EnvelopePayload(
                v="0.2",
                **{
                    "from": "alice@sh4dow.org",
                    "to": "eve@elsewhere.org",
                    "msgHash": "sha256:placeholder",
                },
                iat=int(time.time()),
                exp=int(time.time()) + 60,
                body=EnvelopeBody(text="hi"),
            ),
            alice_key,
        )
        with pytest.raises(UnknownRecipientError):
            pipeline.receive({"message": built.message})

    def test_signature_mismatch(self, alice_key: Ed25519KeyPair) -> None:
        # The pipeline fetches AgentCard with alice_key, but we sign with a
        # different key, so signature verification fails.
        wrong_key = Ed25519KeyPair.generate()
        built = _alice_built(wrong_key)
        pipeline = _pipeline(_config(), alice_key)
        with pytest.raises(SignatureError):
            pipeline.receive({"message": built.message})

    def test_msg_hash_tampered(self, alice_key: Ed25519KeyPair) -> None:
        cg = InMemoryContactGraph()
        cg.add_contact("alice@sh4dow.org")
        pipeline = _pipeline(_config(), alice_key, contact_graph=cg)
        built = _alice_built(alice_key)
        # Mutate the message body after signing → msgHash mismatch.
        built.message["parts"] = [{"text": "different"}]
        with pytest.raises(ParseError, match="msgHash"):
            pipeline.receive({"message": built.message})

    def test_replay(self, alice_key: Ed25519KeyPair) -> None:
        cg = InMemoryContactGraph()
        cg.add_contact("alice@sh4dow.org")
        pipeline = _pipeline(_config(), alice_key, contact_graph=cg)
        built = _alice_built(alice_key)
        pipeline.receive({"message": built.message})
        with pytest.raises(ReplayError):
            pipeline.receive({"message": built.message})

    def test_stranger_no_creds_rejects(self, alice_key: Ed25519KeyPair) -> None:
        config = _config(policy=AcceptancePolicy(fromStranger=(ORG_AFFILIATION,)))
        pipeline = _pipeline(config, alice_key)
        built = _alice_built(alice_key)
        with pytest.raises(CredsRequiredError):
            pipeline.receive({"message": built.message})

    def test_stranger_untrusted_issuer_rejects(
        self,
        alice_key: Ed25519KeyPair,
        acme_issuer_key: Ed25519KeyPair,
        acme_issuer_pk: str,
    ) -> None:
        # Trust store doesn't list acme.example.
        config = _config(policy=AcceptancePolicy(fromStranger=(ORG_AFFILIATION,)))
        cred = _alice_credential(acme_issuer_key)
        built = _alice_built(alice_key, creds=(cred,))
        pipeline = _pipeline(config, alice_key, issuer_pk=acme_issuer_pk)
        with pytest.raises(CredsRejectedError):
            pipeline.receive({"message": built.message})

    def test_empty_stranger_policy_rejects_with_policy_error(
        self, alice_key: Ed25519KeyPair
    ) -> None:
        config = _config(policy=AcceptancePolicy(fromStranger=()))
        pipeline = _pipeline(config, alice_key)
        built = _alice_built(alice_key)
        with pytest.raises(PolicyError):
            pipeline.receive({"message": built.message})

    def test_revoked_credential_rejected(
        self,
        alice_key: Ed25519KeyPair,
        acme_issuer_key: Ed25519KeyPair,
        acme_issuer_pk: str,
    ) -> None:
        config = _config(
            trust=TrustStore(
                entries=(TrustEntry(issuer="acme.example", accept=(ORG_AFFILIATION,)),)
            ),
            policy=AcceptancePolicy(fromStranger=(ORG_AFFILIATION,)),
        )
        cred = _alice_credential(acme_issuer_key)
        built = _alice_built(alice_key, creds=(cred,))
        pipeline = _pipeline(config, alice_key, issuer_pk=acme_issuer_pk, revoked_idx=42)
        with pytest.raises(CredsRejectedError, match="revoked"):
            pipeline.receive({"message": built.message})


class TestExtensionsHeader:
    def test_header_match(self) -> None:
        assert header_includes_extension("urn:shadownet:0.2") is True
        assert header_includes_extension("foo, urn:shadownet:0.2 , bar") is True

    def test_header_mismatch(self) -> None:
        assert header_includes_extension(None) is False
        assert header_includes_extension("") is False
        assert header_includes_extension("urn:shadownet:0.3") is False

    def test_ensure_raises(self) -> None:
        with pytest.raises(ParseError, match="A2A-Extensions"):
            ensure_extension_declared(None)


def test_replay_cache_prune() -> None:
    cache = InMemoryReplayCache()
    cache.remember("alice@sh4dow.org", "m1", retention_seconds=0)
    # The next call to seen() prunes expired entries.
    time.sleep(0.001)
    assert cache.seen("alice@sh4dow.org", "m1") is False


def test_credential_cache_expiry() -> None:
    cache = InMemoryCredentialCache()
    now = int(time.time())
    payload = CredentialPayload(
        iss="acme.example",
        sub="alice@sh4dow.org",
        kind=ORG_AFFILIATION,
        org="acme.example",
        iat=now,
        exp=now + 3600,
        rev=RevocationPointer(epoch="e", idx=0),
    )
    from shadownet.credential import VerifiedCredential

    vc = VerifiedCredential(
        payload=payload,
        issuer_key=encode_public_key(Ed25519KeyPair.generate().public_bytes),
        raw_jws="h.p.s",
    )
    cache.cache("alice@sh4dow.org", vc, expires_at=now - 1)
    assert cache.for_sender("alice@sh4dow.org") == []
    cache.cache("alice@sh4dow.org", vc, expires_at=now + 60)
    assert cache.for_sender("alice@sh4dow.org") == [vc]
