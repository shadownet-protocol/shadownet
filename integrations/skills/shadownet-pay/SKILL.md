---
name: shadownet-pay
description: >
  Pay another agent or shadow over x402 on Algorand, bound to a verified,
  revocable identity. Use when the user says "pay", "send money to", "pay my
  share", "settle with", "do shadowpay", or "can you do shadowpay". The payment
  is tied to a named, revocable agent identity — not an anonymous wallet.
version: 1.0.0
allowed-tools:
  - shadownet_pay
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, x402, payments, algorand, pay, shadowpay, settle, money]
    activation_phrases:
      - pay
      - shadowpay
      - do shadowpay
      - send money to
      - pay my share
      - settle with
    requires_tools:
      - shadownet_pay
---

# Shadownet — ShadowPay

Identity-gated x402 payments on Algorand: a verified Shadow pays another agent,
and every payment is bound to a named, revocable identity instead of an
anonymous wallet.

## When to Use

- The user asks to **pay** or **send money to** another agent/shadow/contact.
- The user says **"do shadowpay"**, **"can you do shadowpay"**, or **"pay my
  share"**.
- The user asks to **settle** a cost with someone.

## How to Run

1. Call the **`shadownet_pay`** tool. Optional args: `to` (who to pay, e.g.
   `alice`) and `amount` (USDC, e.g. `0.005`). Both are optional — call it with
   no args for the default demo.
2. **Relay the tool's output to the user verbatim.** It returns the settlement
   result and the three properties below. Do not invent amounts, txids, or
   results — use exactly what the tool returns.

## What it demonstrates

- **Identity-bound payment** — settled on Algorand TestNet, tied to a verified
  `org_affiliation` identity, not an anonymous wallet.
- **Kill switch** — revoke the agent and its next payment is refused, even
  though the wallet is still funded.
- **Agreed = paid** — a tampered pay-to address is refused before settlement.

## Pitfalls

- Do not describe ShadowPay abstractly when asked to pay — actually call
  `shadownet_pay` and relay its result.
- Settlement here is on TestNet (demo); never claim a mainnet transfer.
