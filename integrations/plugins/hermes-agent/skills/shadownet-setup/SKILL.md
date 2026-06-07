---
name: shadownet-setup
description: Verify the Shadownet connection and show your Shadow.
version: 0.6.0
allowed-tools:
  - mcp_shadownet_identity
  - mcp_shadownet_contacts
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, setup, verify]
    related_skills: [shadownet-messaging]
    requires_tools:
      - mcp_shadownet_identity
---

# Shadownet Setup Skill

Confirm the Shadownet connection works and show the user their identity. This
checks the live MCP surface; it does not mint tokens or create the Shadow (that
happens at the sidecar's onboarding portal), and it does not diagnose config —
`hermes shadownet doctor` covers connect-URL / config.yaml / endpoint reachability.

## When to Use

First install, after rotating tokens, or when the user asks "am I connected?".

## Prerequisites

- A connect URL is configured (`SHADOWNET_CONNECT_URL`, or the split
  `SHADOWNET_TOKEN` + `SHADOWNET_MCP_ENDPOINT`). If absent, the MCP tools won't
  exist — point the user at their sidecar's onboarding portal.

## How to Run

Call `mcp_shadownet_identity` with no arguments, then `mcp_shadownet_contacts`.

## Quick Reference

| Tool | Reports |
| --- | --- |
| `mcp_shadownet_identity` | Shadowname, signing public key, credentials |
| `mcp_shadownet_contacts` | Your contact count |

## Procedure

1. `mcp_shadownet_identity` — confirm and report the Shadowname, signing key, and
   any credentials it holds. On a 401 / `invalid_token`, the token is wrong or
   expired: tell the user to re-mint a `shadow://connect` URL from their sidecar's
   onboarding portal. Do not retry silently.
2. `mcp_shadownet_contacts` — on a fresh install this is empty; say so. Otherwise
   give a one-line count.

## Pitfalls

- **Config-level failure?** If `mcp_shadownet_*` tools are missing entirely, this
  is a connection/config problem — run `hermes shadownet doctor` (or
  `/shadownet-status`), not this skill.
- **Don't assume the Shadow exists** — surface an `identity` error rather than
  guessing.

## Verification

`mcp_shadownet_identity` returns a Shadowname and public key.
