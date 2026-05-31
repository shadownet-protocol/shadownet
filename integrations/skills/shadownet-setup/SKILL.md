---
name: shadownet-setup
description: Verify the Shadownet MCP connection and print this Shadow's identity. Use on first install, after rotating tokens, or when troubleshooting the connection.
version: 0.5.0
allowed-tools:
  - mcp__shadownet__identity
  - mcp__shadownet__contacts
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, setup, verify, a2a]
    related_skills: [shadownet-reach-out, shadownet-inbox, shadownet-coordinate]
    requires_tools:
      - mcp_shadownet_identity
      - mcp_shadownet_contacts
---

# Shadownet — Setup & Verify

First-run wiring for a Shadownet tenant. Confirms the MCP endpoint and bearer
token are configured and prints this Shadow's identity.

## Procedure

### 1. Verify identity

Call `identity` with no arguments. The response carries the Shadow's
Shadowname, signing public key, and any `org_affiliation` credentials it
holds (issuer, org, expiresAt).

> Connected. Your Shadow is **alice@sh4dow.org** (pk `z6Mk…`),
> with 1 active affiliation (`tiergarten-club.example`).

If the call fails with a 401 / `invalid_token`, the user's bearer token is
wrong or expired. Tell them to re-mint via the `shadow://connect?...` URI
from their sidecar's onboarding portal. Do not retry silently.

### 2. List contacts

Call `contacts()` with no arguments. On a fresh install this returns an
empty list — that's expected. Acknowledge to the user briefly:

> No contacts yet. Use `/shadownet:shadownet-reach-out <shadowname>` to add
> your first one.

If contacts exist, summarise: count, names, last-seen timestamps.

### 3. Confirm inbox delivery

The plugin uses `inbox_wait` for long-poll delivery of inbound messages
(RFC 0002 §4). No additional configuration needed.

> Inbox delivery: active (long-poll via `inbox_wait`).

## Pitfalls

- **Do not assume the Shadow exists** — if `identity` returns an error,
  surface it and direct the user to their sidecar's onboarding portal.
- **Inbound delivery is automatic** — the plugin's adapter handles it
  via `inbox_wait`.

## Verification

After the skill finishes the user should see:
- Their Shadowname and public key rendered
- A contact count (likely zero on fresh install)
- Confirmation that inbox delivery is active