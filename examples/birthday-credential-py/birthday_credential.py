"""End-to-end Shadownet v0.2 message flow using the Python SDK only.

Demonstrates the full §8 wire path: a provider signs Alice's AgentCard, a hub
issues her an org_affiliation credential, she mints a signed envelope to Bob,
and Bob's receiver runs RFC 0001 §8.6 validation + §9 classification. No
network, no Docker — the provider DNS lookup and AgentCard fetch are injected
so the flow runs entirely in-process.

Run:
    uv run --with shadownet python birthday_credential.py
"""

from __future__ import annotations

import time

from shadownet.a2a import build_and_sign_message, build_outbound_message
from shadownet.agentcard import FetchedAgentCard, build_signed_agent_card
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
)
from shadownet.trust import AcceptancePolicy, TrustEntry, TrustStore


def main() -> None:
    print("Shadownet v0.2 end-to-end envelope flow (Python SDK)")
    print()

    # 1. Identities.
    #
    # Alice is at "alice@sh4dow.org" — her provider (sh4dow.org) signs an
    # AgentCard binding the Shadowname to her signing key. Bob is at
    # "bob@example.org". The Tiergarten Club hub issues org_affiliation
    # credentials by its own DNS-published key.
    alice_key = Ed25519KeyPair.generate()
    alice_pk = encode_public_key(alice_key.public_bytes)
    sh4dow_provider_key = Ed25519KeyPair.generate()
    sh4dow_provider_pk = encode_public_key(sh4dow_provider_key.public_bytes)

    bob_key = Ed25519KeyPair.generate()
    bob_pk = encode_public_key(bob_key.public_bytes)

    hub_key = Ed25519KeyPair.generate()
    hub_pk = encode_public_key(hub_key.public_bytes)

    alice_name = "alice@sh4dow.org"
    bob_name = "bob@example.org"
    hub_domain = "tiergarten-club.example"

    print(f"  Alice:                 {alice_name}")
    print(f"  Alice signing pk:      {alice_pk[:24]}...")
    print(f"  sh4dow.org provider:   {sh4dow_provider_pk[:24]}...")
    print(f"  Bob (recipient):       {bob_name}")
    print(f"  Hub (credential iss):  {hub_domain}")

    # 2. Provider signs Alice's AgentCard (RFC 0001 §5).
    #
    # Real deployments serve this at GET <ep>/identity/alice. Here we just
    # build the dict; the receiver pipeline gets it through an injected
    # agent_card_fetcher (step 6).
    alice_card_body = build_signed_agent_card(
        name="Alice",
        description="Alice's Shadow",
        version="1.0.0",
        a2a_url="https://shadow.sh4dow.org/v1/a2a/alice",
        shadow_public_key=alice_pk,
        provider_key=sh4dow_provider_key,
        provider_domain="sh4dow.org",
    )
    print()
    print(f"  Signed AgentCard: {len(alice_card_body['signatures'])} signature(s).")

    # 3. Hub issues Alice an org_affiliation credential (RFC 0001 §6).
    now = int(time.time())
    credential = mint_credential(
        CredentialPayload(
            iss=hub_domain,
            sub=alice_name,
            kind=ORG_AFFILIATION,
            org=hub_domain,
            iat=now,
            exp=now + 7 * 24 * 60 * 60,
            rev=RevocationPointer(epoch="2026q2", idx=42),
        ),
        hub_key,
    )
    print(f"  Issued credential JWS ({len(credential)} chars).")

    # 4. Alice builds an A2A message and stamps it with a signed envelope.
    #
    # build_and_sign_message computes msgHash over the JCS-canonical form of
    # the A2A message (with the Shadownet metadata key omitted, per §8.4),
    # then mints the envelope JWS with that hash baked in.
    outbound = build_outbound_message(
        body_text="Hi Bob, want to grab dinner Thursday?",
        context_id="01HZ7K2BV5R2K0DW3FCONTEXT0001",
    )
    built = build_and_sign_message(
        outbound,
        EnvelopePayload(
            v="0.2",
            sender=alice_name,
            recipient=bob_name,
            msg_hash="sha256:placeholder",
            iat=now,
            exp=now + 60,
            body=EnvelopeBody(text="Hi Bob, want to grab dinner Thursday?"),
            creds=(credential,),
        ),
        alice_key,
    )
    print()
    print(f"  Envelope JWS minted    ({len(built.envelope_jws)} chars).")
    print(f"  msgHash:               {built.envelope_payload.msg_hash[:16]}...")

    # 5. Bob configures his trust store and acceptance policy.
    #
    # Bob trusts the Tiergarten Club for org_affiliation credentials. Strangers
    # (anyone not yet a contact) MUST present an org_affiliation; he leaves the
    # fromContact policy empty (no credential check for existing contacts).
    bob_config = ReceiverConfig(
        subject=bob_name,
        trust_store=TrustStore(
            entries=(TrustEntry(issuer=hub_domain, accept=(ORG_AFFILIATION,)),)
        ),
        policy=AcceptancePolicy(fromStranger=(ORG_AFFILIATION,)),
    )

    # 6. Receiver wiring.
    #
    # The receiver normally hits real DNS + HTTPS to resolve the sender's
    # provider and fetch the AgentCard. Here we inject in-memory stubs so the
    # example needs zero networking. provider_lookup is consulted twice: once
    # for Alice's provider (envelope verification) and once for the hub's
    # provider (credential signature verification).
    providers = {
        "sh4dow.org": ProviderRecord(
            domain="sh4dow.org",
            version="0.2",
            endpoint="https://shadow.sh4dow.org/v1",
            provider_keys=(sh4dow_provider_pk,),
        ),
        hub_domain: ProviderRecord(
            domain=hub_domain,
            version="0.2",
            endpoint=f"https://{hub_domain}/v1",
            provider_keys=(hub_pk,),
        ),
    }

    def lookup(domain: str) -> ProviderRecord:
        return providers[domain]

    def fetch_card(_shadowname: str, _record: ProviderRecord) -> FetchedAgentCard:
        return FetchedAgentCard(
            shadowname=alice_name,
            shadow_public_key=alice_pk,
            endpoint_url="https://shadow.sh4dow.org/v1/a2a/alice",
            cache_max_age=3600,
            etag=None,
            raw=alice_card_body,
        )

    pipeline = ReceiverPipeline(
        bob_config,
        replay_cache=InMemoryReplayCache(),
        contact_graph=InMemoryContactGraph(),
        credential_cache=InMemoryCredentialCache(),
        provider_lookup=lookup,
        agent_card_fetcher=fetch_card,
        revocation_check=lambda _c: None,  # skip status list lookup
    )

    # 7. Bob receives, validates, classifies.
    print()
    print("  Bob's pipeline:")
    decision = pipeline.receive({"message": built.message})
    print(f"    sender:              {decision.sender}")
    print(f"    route:               {decision.route}")
    print(f"    auto_added_contact:  {decision.auto_added_contact}")
    print(f"    envelope body text:  {decision.envelope.body.text!r}")

    # Alice was not in Bob's contacts and the envelope carried a credential
    # satisfying his stranger policy, so the message lands in stranger_review
    # for Bob to look at. Once he adds her, future messages route to inbox.

    print()
    print("Done.")


if __name__ == "__main__":
    main()