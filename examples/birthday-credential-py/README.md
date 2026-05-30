# Birthday-credential example (Python)

End-to-end Shadownet **v0.2** message flow using the Python SDK only. No
network, no servers, no Docker — the DNS lookups and AgentCard fetch are
injected so the script runs entirely in-process.

## What it shows

1. Four Ed25519 keypairs are generated: Alice (Shadow), her provider's
   AgentCard signer, Bob (recipient Shadow), and the Tiergarten Club (a hub
   that issues `org_affiliation` credentials).
2. The provider signs an **A2A AgentCard** for Alice — the §5 binding from
   Shadowname to signing key.
3. The hub issues a **`shadownet-cred+jwt`** credential attesting Alice's
   affiliation per RFC 0001 §6.
4. Alice builds an A2A message and stamps it with a signed **envelope JWS**
   that carries her credential and a `msgHash` binding to the surrounding
   message (RFC 0001 §8).
5. Bob's **`ReceiverPipeline`** runs §8.6 validation (envelope signature
   against Alice's AgentCard, msgHash recomputed and compared, credential
   verified, replay cache checked) and §9 classification. Alice is not yet
   in Bob's contacts but the credential satisfies his stranger policy, so
   the message lands in `stranger_review`.

This exercises the substrate end-to-end:
`shadownet.{crypto, identifiers, jcs, provider, agentcard, credential,
envelope, a2a, trust, receiver}`. For an operator-side walkthrough that
boots the reference Provider and Issuer servers, see
[`core/README.md`](../../core/README.md).

## Run it

```sh
# From this directory:
uv run --with shadownet python birthday_credential.py

# Or, if you've already installed shadownet (pip install shadownet):
python birthday_credential.py
```

The script depends only on `shadownet>=0.5.0` — no extras.

## Expected output

```
Shadownet v0.2 end-to-end envelope flow (Python SDK)

  Alice:                 alice@sh4dow.org
  Alice signing pk:      z6Mk...
  sh4dow.org provider:   z6Mk...
  Bob (recipient):       bob@example.org
  Hub (credential iss):  tiergarten-club.example

  Signed AgentCard: 1 signature(s).
  Issued credential JWS (~400 chars).

  Envelope JWS minted    (~1000 chars).
  msgHash:               sha256:...

  Bob's pipeline:
    sender:              alice@sh4dow.org
    route:               stranger_review
    auto_added_contact:  False
    envelope body text:  'Hi Bob, want to grab dinner Thursday?'

Done.
```