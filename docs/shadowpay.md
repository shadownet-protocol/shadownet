# ShadowPay — how payments sit into Shadownet

Status: **research + design, build-pending.** This document consolidates an
official-source research pass on the agentic-payments landscape (AP2, x402,
Algorand) into (1) where Shadownet sits in that stack, (2) what we would build
and where to be compatible with it, (3) what Shadownet would then be *able to
do*, and (4) what is worth presenting at the Algorand x402 hackathon.

Honesty convention (same as the Hermes
[`master-plan.md`](../integrations/plugins/hermes-agent/docs/master-plan.md)):
items already in the codebase are **[done]**; everything else is **[proposed]**
— a design decision, not code that exists. External facts are cited; anything
not confirmed from a primary source is flagged **[unconfirmed]**. Effort sizes:
S = hours, M = ~a day, L = multi-day.

Spec authority is unchanged: the normative protocol lives in
[`shadownet-specs`](https://github.com/shadownet-protocol/shadownet-specs); any
new wire artefact here is a **proposed** extension that must land as a spec PR
before code depends on it (root [`CLAUDE.md`](../CLAUDE.md) — "RFC wins").

## 0. TL;DR

Agentic payments has settled into a layered stack, not a single winner. The
three things people conflate — AP2, x402, Algorand — are **different layers**,
and all of the protocol-level ones are **A2A extensions** living in disjoint URI
namespaces, so they compose on one AgentCard and one message:

```
A2A            transport + extension framework            (Google)
 └─ Shadownet  urn:shadownet:0.2          IDENTITY        who is this agent? verified, addressable, revocable
     └─ AP2    .../ap2/tree/v0.1          AUTHORIZATION   what did the human authorize? (mandates as VCs)
         └─ x402  .../a2a-x402/v0.1       SETTLEMENT      move the stablecoin (HTTP 402)
             └─ Algorand / Base / Solana  VENUE           where it settles
```

The strategic claim: **Shadownet is the identity layer the payment stack
already assumes but does not provide.** A2A explicitly ships "No Identity in
Payload"; AP2 leans on allow-lists and flags "issuance of trusted public keys"
as an open problem; x402 ties identity to a wallet, not a person or org. So
Shadownet's play is *complement, not compete*: be the verified
"Know-Your-Agent" identity that AP2 and x402 source their trust from. Keeping
zero value-transfer in the core protocol is a feature.

## Part I — Research: the agentic-payments landscape

All facts below are from primary sources (Google Cloud / AP2 GitHub + spec, the
`a2a-x402` repo, the A2A spec, Coinbase / x402 Foundation / Linux Foundation,
the GoPlausible Algorand x402 docs). Sources listed in §9.

### 1.1 x402 — the settlement rail

- HTTP-402-based **settlement** protocol. Created by Coinbase (May 2025); now
  under the **Linux Foundation x402 Foundation** (Apr 2026), backed by
  Google, Microsoft, AWS, Stripe, Visa, Mastercard, Circle, Solana, Polygon.
  This is the converging crypto-settlement standard — interoperate, don't
  replace.
- Flow: `GET` → `402` + `PaymentRequirements` (`accepts[]`) → client signs →
  retry with `X-PAYMENT` (base64 `PaymentPayload`) → server `/verify` +
  `/settle` via a **facilitator** → `200` + `X-PAYMENT-RESPONSE`. Only concrete
  scheme is `exact`. The server needs no chain infra (the facilitator settles).
- Transport-agnostic: HTTP, MCP, **and A2A**. The A2A binding is the
  `a2a-x402` extension (§1.3).
- Adoption signal: >100M tx, ~$50M cumulative, ~69k active agents by early 2026
  (Coinbase-reported, **[unconfirmed]** magnitudes — directional).

### 1.2 AP2 — the authorization rail

- Google's **Agent Payments Protocol** (announced 2025-09-16, 60+ partners,
  being donated to the FIDO Alliance). It is the **authorization / mandate**
  layer, not a settlement rail, and it is payment-method-agnostic.
- Core primitive = **Mandates** carried as Verifiable Digital Credentials. v0.1
  used Intent Mandate / Cart Mandate; **v0.2 (2026-04-28)** reworked this into
  **Checkout Mandate / Payment Mandate** with Open (autonomous / human-not-
  present) vs Closed (direct / human-present) variants, grounded in
  **OpenID4VP + SD-JWT** credentials. It solves Authorization, Authenticity,
  Accountability — i.e. "did the user authorize this, is it their true intent,
  who is liable."
- Roles: Shopping Agent, Credentials Provider, Merchant, Merchant Payment
  Processor, Trusted Surface (v0.2). Built as an extension of A2A + MCP.
- **Identity is deliberately thin.** AP2 does not define personal-agent
  identity/discovery; near-term it uses allow-lists and "trust-the-issuer /
  trust-the-agent-provider," and it flags *"issuance of trusted public keys"*
  as an **open community problem**, punting durable identity to A2A/MCP. This is
  the gap Shadownet fills.
- Maturity: early/preview, standards-track. Reference samples (Python/Go/Android
  on ADK + Gemini); types not yet on PyPI.

### 1.3 The bridge: the `a2a-x402` extension

- Google + Coinbase + Ethereum Foundation + MetaMask shipped **`a2a-x402`**
  (repo `github.com/google-agentic-commerce/a2a-x402`, extension URI
  `github.com/google-a2a/a2a-x402/v0.1`) — "one of the first extensions to AP2
  and the only stablecoin facilitator." This is **x402 carried over A2A**:
  - declared in `AgentCard.capabilities.extensions`, activated per-request via
    the `A2A-Extensions` HTTP header;
  - payment data rides A2A message **metadata keyed `x402.payment.*`**
    (`required`, `payload`, `receipts`, `status`);
  - state machine: `payment-required → payment-submitted → payment-verified →
    payment-completed`;
  - roles: Client Agent, Merchant Agent, Signing Service/Wallet ("private keys
    MUST never be handled by an LLM"), Facilitator.
- **Important nuance:** full AP2↔x402 *mandate* alignment is "coming soon" — the
  a2a-x402 v0.1 spec does not yet name AP2/mandates/EIP-3009. And the reference
  is **EVM/USDC-on-Base**, *not* an Algorand binding. This matters for us (§3.4).

### 1.4 Algorand's niche

- Not volume (Base/Solana/Polygon are the "triopoly"). Algorand's credible
  differentiators for agent payments are: **~2.8s deterministic finality** (fits
  a synchronous HTTP request, no reorg), **native atomic transaction grouping**
  (bind authorization + payment + usage in one all-or-nothing group), and
  **MiCA-compliant EUR stablecoins** (Quantoz EURQ/USDQ/EURD, 102%-collateralized
  Dutch EMI) — the strongest credible rail for *regulated, euro-denominated, EU-
  compliant* agent micropayments.
- **GoPlausible** is the designated Algorand x402 facilitator
  (`facilitator.goplausible.xyz`), HTTP x402 (the `X-PAYMENT` header path),
  serving MainNet + TestNet; USDC ASA TestNet `10458941` / MainNet `31566704`.
  Full building blocks in the memory note `reference-x402-algorand-building-blocks`.
- **EURD/EURQ x402 is confirmed** (Quantoz hackathon docs): EURD ASA `1221682136`,
  EURQ `2768422954`, via a *separate* Quantoz facilitator `x402algo.ai.quantozpay.com`
  — but **custodial** (managed accounts + API key) and **KYC-whitelisted** (see
  Part VI). **[unconfirmed]** still: no AP2 primary source names Algorand.

### 1.5 Competitors and white space

- **Layer map:** open authorization = AP2; open settlement = x402; card-network
  programs (Visa Intelligent Commerce, Mastercard Agent Pay) sit *on top of* and
  align *with* the open protocols (both are AP2 launch partners and x402
  Foundation members); checkout/wallet rails = Stripe (ACP / Shared Payment
  Token), PayPal (Agent Toolkit, PYUSD).
- **The two hardest unsolved problems everyone names:** durable cryptographic
  **agent identity ("Know Your Agent")** and **liability when an agent exceeds
  its mandate.** Every payment protocol treats identity as an external input or
  a siloed per-program registry. Nobody owns a portable, neutral, person-
  anchored identity for *personal* agents. **That is the white space.**
- **Closest overlap to watch: Catena Labs** (Circle co-founder; "Agent Commerce
  Kit" = agent identity + payments + receipts; ~$48M; OCC trust-bank charter
  filing) and **Skyfire KYAPay**. Both attack the identity gap — but Catena
  *bundles* identity with a regulated bank/stablecoin. Shadownet's edge is being
  thin, neutral, unbundled, DNS-discoverable. Stay value-neutral; do not enter
  the rail/charter race.

## Part II — Where payments sit in Shadownet

### 2.1 Shadownet is the identity layer, by construction

A2A is explicit (verified against the local clone, `enterprise-ready.md`):
agents are "opaque," there is **"No Identity in Payload,"** identity is delegated
to TLS/OAuth at the transport, the AgentCard is signed but **individual messages
are not**, and there is no name-addressing and no portable org-membership proof.
Shadownet fills exactly that gap as its *own* A2A extension (`urn:shadownet:0.2`):

- name-addressing (`alice@sh4dow.org`) → `_shadownet.<domain>` DNS-TXT →
  Provider → provider-signed AgentCard carrying the Shadow's Ed25519 key;
- **per-message** Ed25519-signed envelopes (`shadownet-env+jwt`);
- a revocable Issuer-signed `org_affiliation` credential (`shadownet-cred+jwt`).

So Shadownet ≠ a payments competitor. It is the verified-identity substrate AP2
(authorization) and x402 (settlement) sit on. Three disjoint URI namespaces,
clean layering, no wire conflict.

### 2.2 The seam in our own code

Payments plug into surfaces that already exist (all verified in the SDK):

- **Opaque application slot** — `EnvelopeBody{text,intent,data}` (`extra="allow"`)
  in `python-sdk/src/shadownet/envelope.py`, carried in
  `metadata["urn:shadownet:0.2"]`, bound to the A2A message by `msgHash`, ≤300s
  lifetime. A pay/invoice/receipt intent rides `body.intent`/`body.data` exactly
  like the shipped `coordinate_v1` profile (`mcp/intents.py`).
- **Wire-error machinery** — `WIRE_ERROR_REGISTRY` + `ShadownetWireError`
  (`a2a.py`): each code maps to `urn:shadownet:error:<code>` and one HTTP status;
  `problem_response()` / `wire_error_from_problem()` handle any *registered* code
  generically. A `payment_required` (HTTP 402) challenge slots beside the
  existing `creds_required` (401) — the same RFC 7807 + agent-opacity plumbing.
- **AgentCard extension array** — `build_unsigned_agent_card_body()`
  (`agentcard.py`) declares one extension today (`urn:shadownet:0.2`,
  `required:true`); `_validate_extensions()` *tolerates additional* extensions.
  So a sibling `a2a-x402` extension composes — but it must be `required:false`
  (an x402-unaware peer must still interoperate).
- **The autonomous loop** — the Hermes `ExchangeEngine` (passive switchboard,
  contact-gated, per-`contextId`; see the master plan) is exactly where an
  agent-initiated pay/charge would fire, behind the existing "a stranger never
  triggers an autonomous turn" gate.

Repo-wide grep confirms **zero** payment code today — this is all greenfield on
top of a complete identity layer.

## Part III — What to build, and where

Two questions: (A) what supports x402 / AP2 / Algorand, and (B) what we add on
the Shadownet side to be compatible. Organized by subtree.

### 3.1 New package: `shadownet-x402` (settlement adapter)  — [proposed]

Keep blockchain deps OUT of the core `shadownet` SDK (it is the canonical, dep-
light identity SDK). Ship a **separate optional package** (a thin wrapper over
`x402-avm`) — the project's "no heavy-dep pollution" rule. It provides:

| Piece | What | Effort |
| --- | --- | --- |
| x402 **client** | catch `402`, build/sign the Algorand atomic group via `x402-avm`, set `X-PAYMENT`, retry | M |
| x402 **server middleware** | emit `402` + `PaymentRequirements`, forward to GoPlausible `/verify`+`/settle` | M |
| Algorand glue | USDC opt-in, wallet load, atomic group + fee abstraction (via `algokit-utils`/`algosdk`) | M |

`core/` (Go provider + issuer) needs **no change** for x402 — GoPlausible hosts
the facilitator. A reference paid-resource-server example under `examples/` is
optional. **[proposed]**

### 3.2 Shadownet-side: identity-binds-payment  — [proposed]

These make a payment *provably tied to a verified Shadow* — the differentiator.
Several are **spec changes** (new wire artefacts) and must land in
`shadownet-specs` first.

| Piece | Where | Notes | Spec PR? | Effort |
| --- | --- | --- | --- | --- |
| Payment intent profile | `python-sdk` `mcp/payment_intents.py` (mirror `intents.py`) | URIs `urn:shadownet:intent:{invoice,pay,receipt}_v1` + `body.data` models | additive only (RFC 0001 §8.5: unknown intents MUST NOT be rejected) | S-M |
| `payment_required` wire error | `python-sdk` `a2a.py` + `WIRE_ERROR_REGISTRY` | `PaymentRequiredError(code="payment_required", http_status=402)`; body carries x402 `PaymentRequirements` (not sender-identifying → §11-safe) | yes — new wire code | S |
| Pay-and-retry helper | `python-sdk` `a2a.py` | `send_with_retries` re-raises wire errors w/o retry; need a helper that catches `payment_required`, settles via `shadownet-x402`, re-sends | follows the code above | M |
| `payTo` binding | `agentcard.py` extras + envelope `body` | bind the Shadow's Algorand address into the **signed** AgentCard / co-sign into the envelope → defeats payTo-MITM (the core agent-payment phishing risk) | yes — new card field | M |
| Sibling `a2a-x402` extension | `agentcard.py` `build_unsigned_agent_card_body` | append the a2a-x402 URI to `capabilities.extensions` as `required:false`; teach the receiver the `x402.payment.*` metadata namespace | argue vs RFC 0002 opacity (master plan §6) | M-L |

### 3.3 Compose with AP2 (later)  — [proposed, future]

A Shadownet identity becomes the verified-identity *input* to an AP2 flow:
carry/verify AP2 mandate DataParts (`ap2.mandates.*`) as a *third* A2A extension
alongside `urn:shadownet:0.2`, binding the mandate's signer to the verified
Shadow identity (and `org_affiliation` to AP2's merchant/agent trust). This is
the "Shadownet underpins AP2" thesis made concrete. Post-hackathon, and gated on
AP2 v0.2's churn settling (it is moving fast). **[proposed, future]**

### 3.3a Where the 402 is actually raised (read this before building)

The 402 does **not** come from the `message:send` messaging endpoint in the MVP.
A Shadow has three distinct HTTP surfaces; only the third raises a 402:

- `…/identity/<local>` — the AgentCard (identity/discovery). No 402.
- `<a2a-url>/message:send` — shadownet messaging. No 402 in the MVP.
- a **dedicated x402 resource server** (the paid tool, e.g.
  `https://tools.alice.example/summarize`, running the `x402-avm` middleware) —
  **this raises a real HTTP `402`**, on the first unpaid request, and is fully
  classic-x402-compatible (real `X-PAYMENT` header + retry). The entire x402
  internet can pay it.

`message:send` is HTTP-402-*capable* (the wire-error machinery already returns
401/403/404/409/429 via problem+json, so a 402 is trivial to add) but it is a
*message-exchange RPC*, not a REST resource — so settling there means carrying
x402's **data structures inside the A2A message** (the `a2a-x402` pattern), not
the literal header dance, and it needs the external Sidecar to surface the
signal. That is **path B (north-star), not the MVP.** Keep `message:send` for
identity + discovery + invoice/negotiation; raise the 402 on a separate
resource server.

Consequence: the `payment_required` wire-error code below is **path-B only** —
the MVP raises a normal x402 402 on a resource server and leaves shadownet's
wire registry untouched.

### 3.3b What the spec actually needs

**MVP: ~nothing in `shadownet-specs`.** Standard x402 on a resource server +
*reuse* existing verification. The one real gap: the signed envelope
(`verify_envelope`) is welded to A2A (requires `msgHash` + `expected_recipient`),
so it cannot gate a plain HTTP tool call. Instead the HTTP identity gate
**verifies the standalone `org_affiliation` credential** (`credential.py` — no
`msgHash` needed, works today) **plus a proof-of-possession** (caller signs the
402 challenge nonce with the Shadow key). That PoP-over-HTTP convention is the
only new thing, and ships as a marked demo profile, not ratified v0.2.

Productionized / path-B spec PRs (file after; "RFC wins"):

| Spec delta | RFC area | Needed for |
| --- | --- | --- |
| `payment_required` wire code (402) | RFC 0001 §8.8 | path B (settle on `message:send`) |
| `invoice/pay/receipt_v1` intent profile | §8.5 additive / RFC 0002 | path B |
| `shadownet:payTo` AgentCard field | §5 | both (optional in MVP) |
| `a2a-x402` sibling extension (`required:false`) | §5.4 (one required ext today) | path B / standards alignment |
| HTTP-request identity binding (PoP) | new profile | identity-gated HTTP |

### 3.4 The binding tension to plan around

The strategically ideal binding (`a2a-x402`, x402-over-A2A) is **EVM-first**; the
mandatory "x402 on Algorand" runs over **GoPlausible's HTTP x402**. Pragmatic
resolution: build on **HTTP x402 + GoPlausible on Algorand** now, but mirror
`a2a-x402`'s `x402.payment.*` namespacing + state machine in our intent profile,
so it is a clean step toward an *Algorand a2a-x402 binding* later, not a dead end.

Keep the **three keys distinct**: Shadownet Ed25519 *identity* key ≠ the x402
*wallet* signing key ≠ (future) AP2 *VC* key. Never conflate identity with
spend authority; never let an LLM hold a wallet key (a2a-x402 rule).

#### Is the Shadow key also the Algorand wallet?  — decided: no, bind don't merge

Tempting, because both are **Ed25519**: an Algorand standard-account address is
literally an Ed25519 public key + a 4-byte checksum (base32-encoded), and the
Shadow identity key is Ed25519 (`crypto/ed25519`, EdDSA JWS). The same 32-byte
keypair *could* be both a `shadownet:pk` and an Algorand address — same key, two
encodings. We still keep them separate:

- **Blast radius:** the identity key signs envelopes constantly in a network-
  exposed Sidecar; a spending key controls funds — a messaging-path compromise
  must not drain the wallet.
- **Rotation:** identity keys rotate (RFC 0001 §5.5-5.6 + proposed pre-rotation);
  unifying makes every identity rotation a fund migration / on-chain `rekey-to`.
- **Custody/portability:** users will bring an existing Algorand wallet
  (Pera/Defly/hardware/treasury) — don't force their agent's messaging key.
- **a2a-x402 rule:** the wallet key belongs in an isolated signing service, off
  the LLM/messaging hot path.
- **Privacy:** identity==wallet links the whole on-chain history to the public
  Shadowname — against the controlled-disclosure ethos.

**Pattern: the identity key *attests* a separate `payTo` Algorand address**
(signed into the AgentCard and/or co-signed into the envelope `body`) — the
strong "this verified Shadow says pay address X" binding that kills payTo-MITM,
without conflating identity with spend authority. Escape hatch if a single
logical account is ever wanted: Algorand `rekey-to` keeps a stable address while
delegating signing to a separate key — advanced; default to bind-don't-merge.

**Privacy (and what the shared curve does *not* buy).** For unlinkability,
HD-derive a **fresh `payTo` per invoice** via Algorand's **ARC-52**
(BIP32-Ed25519) so third parties can't link an agent's payments to one address.
This is purely Algorand-wallet-side and does **not** depend on shadownet's keys
being Ed25519 (that coincidence only matters for the merge option above). Honest
limit: Algorand is a public ledger with **no native confidential/stealth
transfers**, so this is *pseudonymity from chain analysis, not confidentiality* —
the counterparty you attest the address to still learns the link, and
amounts/graph stay public; true stealth isn't native and we don't roll our own
crypto. The real synergy: shadownet's per-invoice **attestation** re-binds each
throwaway address to the verified agent, so rotating for privacy doesn't cost
recognizability.

## Part IV — What Shadownet will be able to do

A capability ladder, simplest/most-feasible first. Each is unlocked by the
pieces in Part III.

1. **A verified Shadow pays for tools/APIs across the internet** (Shadow as x402
   *client*). Your agent calls any x402-paywalled endpoint and pays USDC on
   Algorand — and, uniquely, presents its verified identity + `org_affiliation`
   so the seller can price/trust/rate-limit it. *"Known agents use paid tools."*
2. **A verified Shadow sells a tool/data and gets paid** (Shadow as x402 *resource
   server* — "PayGate"). A paid endpoint that issues its `402` only to a caller
   whose AgentCard + credential verify, with per-identity spend caps and instant
   **revocation** (key off identity, not a disposable wallet). *"Known agents
   monetize a tool."*
3. **Shadows pay each other, peer-to-peer** (A2A settlement). Two Shadows
   negotiate a price over signed `invoice`/`pay`/`receipt` intents and settle on
   Algorand — the `a2a-x402` + identity composition, with a non-repudiable,
   identity-bound on-chain receipt threaded on the `contextId`.
4. **Safe autonomous commerce** (the Hermes loop as a background buyer/seller).
   Auto-pay inherits the engine's hard "a stranger never triggers an autonomous
   turn" gate, plus a per-contact USDC budget and the runaway backstop — so an
   injected/stranger message can never drain a wallet.
5. **Bring-your-own-identity to any payment protocol** (the substrate play). A
   Shadownet identity + org credential is the verified-identity input an AP2
   mandate or an x402 facilitator's authorization check needs — *"Know Your
   Agent" as a service*, reusable across AP2 / x402 / card programs.
6. **Regulated EUR micropayments.** Settle in Quantoz EURD/EURQ on Algorand for
   MiCA-compliant, euro-denominated agent payments — a differentiator Base/Solana
   cannot match. Confirmed buildable, but custodial + KYC-gated (Part VI), so a
   bonus, not the core path.

Net: Shadows go from "agents that can find, verify, and message each other" to
"agents that can also *transact* — pay for tools, get paid, and settle with each
other — without ever paying or being paid by an agent they cannot cryptographically
name and revoke."

## Part V — The pitch and the demo

Mandatory bar: x402 on Algorand + topic "Agentic Commerce". We enter the
**Existing Projects** sub-track (shadownet's identity/coordinate layer pre-exists;
the *entire* payments layer is the weekend's work). Coding window is **~28h**
(09:00 Sat → 13:00 Sun) — scope to that, not 36h.

**The honest delta (why this isn't just "agents can pay").** Two agents that swap
wallets + an x402 plugin can already pay each other — shadownet adds nothing
*there*. The value appears where the wallet-swap baseline breaks: discover and pay
an agent you never pre-arranged with **by name**, with a **central kill switch**
(revoke the agent → its payments stop, funded wallet and all) and **budgets** —
i.e. it removes the human coordination layer, the same way the birthday demo
removed the group chat. **Do not pitch anti-phishing:** for a business with a
website, TLS already answers "pay the real one." Lead with *no-coordination +
revocable autonomy*, which the web and Venmo don't give an unattended agent.

**One-liner:** *Know Your Agent — the identity layer the agent-payments stack
forgot. shadownet binds every x402 payment on Algorand to a named, revocable agent
instead of an anonymous wallet.*

**The demo — "the birthday, booked and paid"** (continues the birthday-coordinate
demo; same friends, now there's a cost):

- **Coordinate (reuse shipped):** the friends' Shadows — and the **venue's own
  agent** — converge on a slot that fits everyone (multi-party `coordinate_v1`).
  The venue agent is a *coordinator* (finds an open slot), not just a checkout —
  that's shadownet's turf, which the web can't do.
- **Pay (new, path A):** on agreement, the organizer's Shadow pays **by name** (it
  resolves + verifies the venue, never pasting a wallet), settling on Algorand
  TestNet; Lora shows a settlement stamped with a *verified identity*.
- **Differentiated beats (feasible; not web/Venmo-solved for an unattended agent):**
  **kill switch** (revoke → next payment dies) and **agreed = paid** (the charge is
  bound to the signed agreement — a baby AP2 Cart Mandate).
- **Narrated, not built (north-star):** the friends splitting the bill
  shadow-to-shadow (path B). 36h fallback: each friend pays the venue directly by
  name — still "no wallets, no group chat".

**Pitch line:** *"Last year five friends planned a birthday with no group chat —
their Shadows did it. This year the venue's agent finds a table that fits everyone,
the reservation locks the moment they agree, and each Shadow pays its share — no
group chat, no wallets, no chasing anyone for a tenner. They didn't just talk; they
closed the deal."*

**Track + bonus:** Agentic Commerce primary; **Quantoz EURD/EURQ** as the one bonus
(regulated euro, on-theme for Berlin) — note its custodial caveat (Part VI). Don't
scatter across Folks/Alpha (the guide penalizes it).

**What NOT to say:** don't claim full shadow↔shadow settlement (north-star, not
built), AP2-on-Algorand, or that the regulated-euro rail is non-custodial. Say "the
layer the stack assumes," not "the layer we finished."

## Part VI — Build reality: scope, repos, dependencies, testing

**Win read.** A realistic shot at **Existing/Agentic-Commerce 1st (~$3k)** + the
**Quantoz bonus (900 EUR)** *if* scoped tightly. The bigger prize is ecosystem
positioning (Algorand Foundation / Quantoz / x402) for shadownet as the identity
layer, plus the 50/50 milestone relationship. The losing move is the full
five-agent coordinate-pay-split arc live in 28h — build the disciplined slice
(Part V), narrate the rest.

**Repos affected — two you write in:**

- *forked hackathon x402 template* — the deployable demo (resource server + buyer,
  GoPlausible, USDC TestNet) + the shadownet **identity gate** + the kill-switch /
  agreed=paid logic.
- *this monorepo* — `docs/` (this) + light `python-sdk` adds
  (`mcp/payment_intents.py`, optional `payTo`) + optionally a thin `shadownet-x402`
  adapter and a `shadownet-pay` skill.
- *Not touched for the MVP:* `core/` (Go), `conformance/`, `shadownet-specs`, the
  external `shadownet-local` Sidecar, the host repos. (Path B would pull in the
  Sidecar + a spec PR — avoid.)
- *Demo host:* a standalone script/app is lowest-risk; driving it through Hermes (a
  `register_tool` pay tool) is more convincing but heavier — narrate it.

**Dependencies (provenance verified) — pin exact versions + lockfile + audit:**

- `@x402/avm` (npm) — **x402 Foundation**, Coinbase maintainers, trusted-publisher
  + SLSA provenance, Apache-2.0. **High trust.**
- `x402-avm` (PyPI) — author Coinbase, **maintainer GoPlausible**, MIT. Good; stay
  current (the Mar-2026 `GHSA-qr2g-p6q7-w82m` signature bug was a *Solana*
  facilitator issue fixed ≥2.6.0 — we're Algorand, but pin a recent version).
- `algokit-utils` / `py-algorand-sdk` — **Algorand Foundation official**; signing
  stays local. **High trust.**
- **GoPlausible facilitator** — endorsed by the official Algorand x402 docs;
  **non-custodial** (submits the group *you* signed; cannot move unsigned funds);
  a centralized availability/privacy dependency. Fine on TestNet.
- `@ever_amsterdam/x402-euro-eurd` (EURD) — **referenced by Quantoz's own docs**
  (real, not a typosquat) but **v0.1, days old, single maintainer, custodial**
  (managed accounts + API key), **KYC-whitelisted** addresses, separate facilitator.
  **Watch — keep off the critical path; bonus only.**
- Protocol must-dos if you run the *selling* side: **single-use payment proofs
  (nonce + short expiry)** against replay; current SDK.

**Testing & environments — offline-first:**

- *Identity / gate logic* (most of the differentiator): **no network** — unit tests
  with in-memory keys/fixtures.
- *Algorand mechanics* (ASA, opt-in, atomic group): **AlgoKit LocalNet** (local
  Docker chain, pre-funded) — no TestNet.
- *Full x402 round-trip:* the hosted facilitator only reaches TestNet/MainNet (not
  your LocalNet), so either **stub the facilitator** for CI **or** use **TestNet +
  GoPlausible** for real e2e (throwaway account, test ALGO + USDC, USDC opt-in).
  Gate facilitator/TestNet tests behind an env var, skip-when-unset (like
  `SHADOWNET_TEST_PG_DSN`).
- *EURD:* no offline path — requires the Quantoz hosted facilitator + API key + KYC
  whitelist.
- *Demo day:* TestNet + the hosted facilitator are external → rehearse and keep a
  **recorded fallback** (pre-captured Lora txid) against flakiness.
- *Safety:* TestNet + a throwaway wallet (test funds only) removes real-money risk
  for the whole build; keep the wallet key off the LLM.

## Part VII — Demo runbook (0 → 100)

TestNet + throwaway accounts only. Never MainNet, never real funds.

**Tooling (once):**

- Install AlgoKit: `brew install algorandfoundation/tap/algokit` (or
  `pipx install algokit`); Docker for LocalNet dev. Lora (`lora.algokit.io`) and
  the Circle faucet are web — nothing to install.
- Wallet app: for the *agent's* signing you do **not** use a GUI wallet — the
  agent signs with a generated key (25-word mnemonic in `.env`). Install **Pera
  Wallet** only as a visual aid: import the throwaway mnemonic to show balances on
  screen, and as a manual opt-in / faucet fallback.

**Accounts (two: buyer Shadow + venue payTo):**

1. Generate two TestNet accounts (algosdk `generateAccount` / AlgoKit account
   tooling); save both mnemonics to `.env`. Disposable.
2. Fund ALGO (both): `algokit dispenser login` then `algokit dispenser fund
   --receiver <addr> --amount 5 --whole-units`, or the Lora/web faucet. (Needed for
   fees + the 0.1-ALGO-per-ASA min-balance.)
3. Fund USDC (buyer): Circle TestNet faucet (`faucet.circle.com`, network =
   Algorand).
4. Opt-in to USDC (ASA `10458941`) on **both** accounts — the receiver MUST opt in;
   this is the silent demo-killer. (0-amount self-transfer via algosdk, or in Pera.)
5. Verify on Lora: both hold ALGO and are opted into USDC; buyer holds test USDC.

**App config (`.env` for the forked template + gate):**

- `FACILITATOR_URL=https://facilitator.goplausible.xyz`
- `NETWORK_CAIP2=algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=`
- `USDC_ASSET_ID=10458941`
- `ALGOD_URL=` a public TestNet node (e.g. AlgoNode) or your AlgoKit config
- `PAYER_MNEMONIC=…`, `VENUE_PAYTO=<venue addr>`, `PRICE=…`, `BUDGET=…`
- Pin exact SDK versions; commit the lockfile.

**Hermes route (only if demoing through Hermes — heavier; standalone is safer for
28h):**

- Identity/messaging already works: the shadownet plugin writes
  `mcp_servers.shadownet` into Hermes `config.yaml` under `HERMES_HOME` via a
  `shadow://connect` token.
- Payments need the new pay tool (to build) + its config — payer mnemonic,
  facilitator URL, asset id, budget — as plugin env/config. **Those keys don't
  exist yet; they ship with the tool.** Don't assume they're configurable today.

**Demo day:**

- Rehearse the full TestNet flow end-to-end ≥2×; time it.
- Pre-fund + pre-opt-in everything; keep a second funded + opted-in buyer account
  in reserve.
- Wire and rehearse the **revoke** beat and the **agreed=paid** mismatch.
- Pre-capture a **Lora txid + screen recording** as fallback — the facilitator and
  TestNet are external and can stall mid-pitch.
- On stage: terminal + Lora side by side; show the live txid stamped with the
  verified identity.

## Part VIII — Landing-page update brief (for Lovable)

The Shadownet site is built in Lovable; Lovable decides the design/build — this
section only supplies context. Five changes we want:

1. A small ShadowPay section mid-homepage.
2. An announcement / highlight flagging it as newly added.
3. A top-nav button to a new `/shadowpay` (or `/pay`) page.
4. That new page, explaining the project and what we built.
5. A new scene-player presentation (same format as the existing 2-min one) for the
   new payment scenario.

**Context to feed Lovable**

*Positioning (one line):* the site already says Shadownet lets agents *find, verify,
and negotiate* on behalf of their humans — ShadowPay adds the missing verb, **pay**:
every payment bound to a named, revocable agent identity instead of an anonymous
wallet ("Know Your Agent").

*Protocols / stack (name + one line each):*

- **Shadownet** — the identity layer (A2A + MCP extension; Ed25519 AgentCards
  resolved by Shadowname; revocable org-affiliation credentials). Already on the site.
- **x402** — the open HTTP-402 settlement protocol (Linux Foundation / Coinbase).
- **Algorand** — the settlement venue (~2.8s finality, low fees); pays in **USDC**,
  and **EURD/EURQ** (Quantoz, MiCA-regulated euro) for the regulated-euro angle.
- **GoPlausible** — the hosted x402 facilitator that verifies + settles on Algorand.
- **AP2** (Google) — the authorization layer ShadowPay composes *under*; roadmap,
  not built — use only for the "where this fits" framing.

*Brief how (general):* an agent discovers another by name → verifies its identity +
org → they agree a price (signed, so *agreed = paid*) → the buyer pays over x402 on
Algorand → the payment is identity-bound, budgeted, and **revocable** (revoke the
agent and its payments stop, even with a funded wallet). No wallets exchanged, no
group chat.

*Scenario (for the section + the new presentation) — "the birthday, booked and
paid":* a direct continuation of the existing birthday presentation. After the
friends' Shadows plan the day, the **venue's own agent** joins, finds a table that
fits everyone, the reservation locks the moment they agree, and each Shadow pays its
share — no group chat, no wallets, no chasing anyone for a tenner. Standout beats:
the **kill switch** (revoke an agent → payments stop) and **agreed = paid**.

*Presentation:* mirror the existing scene-player (cold-open → scenes → ~2 min); the
new one picks up where the birthday-coordinate demo ends and carries it through
booking, payment, and the split.

## 6. Open questions, risks, caveats

- **[unconfirmed]** No AP2 primary source names Algorand. (EURD/EURQ x402 on
  Algorand is now confirmed via Quantoz — but custodial + KYC-gated; Part VI.)
- **[unconfirmed]** a2a-x402 is EVM/USDC-on-Base in its reference; an Algorand
  a2a-x402 binding is not documented. The HTTP-x402-via-GoPlausible path is what
  is actually runnable on Algorand today.
- **Spec discipline:** `payment_required`, the pay/invoice/receipt intents, the
  `payTo` card field, and the a2a-x402 sibling declaration are **proposed wire
  changes**; per "RFC wins" they must land in `shadownet-specs` (and get cross-
  impl conformance vectors) before non-demo code depends on them. For the
  hackathon they ship as a clearly-marked extension, not ratified v0.2.
- **Protocol churn:** AP2 changed shape between v0.1 and v0.2 (Intent/Cart →
  Checkout/Payment, OpenID4VP, FIDO donation); target v0.2+ and assume more.
- **x402 metrics** cited here are Coinbase-reported and directional, not audited.

## 7. Relationship to the Hermes master plan

The Hermes
[`master-plan.md`](../integrations/plugins/hermes-agent/docs/master-plan.md) is
the *autonomous-loop* build (per-`contextId` sessions, the engine, directives,
surfacing). ShadowPay is the *payments* layer that would ride on top of that
loop for capabilities #3–#4. The two share the spec-vs-code discipline and the
intent-profile mechanism; build the loop first, then the money leg.

## 8. Sources

AP2: `ap2-protocol.org`, `github.com/google-agentic-commerce/AP2` (spec,
`agent_authorization.md`, `checkout_mandate.md`, `flows.md`), Google Cloud
announcement blog, the FIDO-donation blog. · x402 / a2a-x402:
`github.com/google-agentic-commerce/a2a-x402` (+ `spec/v0.1/spec.md`),
`docs.x402.org`, Coinbase CDP x402 docs, Linux Foundation x402 Foundation press.
· A2A: local clone `../../dev/A2A` (`docs/topics/extensions.md`,
`docs/specification.md` §4.4/§4.6/§8.4, `docs/topics/enterprise-ready.md`),
`a2a-protocol.org`, upstream issue #1672 (agent-identity), `sigstore-a2a`. ·
Algorand x402: `dev.algorand.co/resources/x402-on-algorand/`,
`facilitator.goplausible.xyz`, `algorand-devrel/x402-demo`. · Landscape: Visa
Intelligent Commerce, Mastercard Agent Pay, Stripe ACP, PayPal Agent Toolkit,
Skyfire KYAPay, Catena Labs Agent Commerce Kit announcements. · Quantoz:
`docs.ai.quantozpay.com/hackathon`, npm `@ever_amsterdam/x402-euro-eurd` (EURD ASA
`1221682136` / EURQ `2768422954`, facilitator `x402algo.ai.quantozpay.com`). · SDK
provenance: npm `@x402/avm`, PyPI `x402-avm`. · x402 security: `GHSA-qr2g-p6q7-w82m`,
Halborn x402 analysis, arXiv "Five Attacks on x402". · Key derivation: ARC-52
BIP32-Ed25519 (`algorandfoundation/xHD-Wallet-API`). · Dev: AlgoKit LocalNet, Lora
explorer, Circle TestNet USDC faucet.