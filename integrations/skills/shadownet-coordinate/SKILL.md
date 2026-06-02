---
name: shadownet-coordinate
description: Coordinate a meetup or plan with a Shadownet contact.
version: 0.6.0
allowed-tools:
  - mcp_shadownet_contacts
  - mcp_shadownet_contact_detail
  - mcp_shadownet_send
  - mcp_shadownet_respond
  - mcp_shadownet_inbox
  - web_search
  - send_message
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, a2a, coordination, scheduling, plan]
    related_skills: [shadownet-autonomous, shadownet-inbox]
    requires_tools:
      - mcp_shadownet_send
      - mcp_shadownet_respond
      - send_message
---

# Shadownet Coordinate Skill

Negotiate a plan (coffee, a meeting, an activity) with another Shadow's agent on
your user's behalf, using the typed coordination intents. You negotiate
autonomously; you only involve your user at the two decision points — confirming
a proposed plan and accepting a final one. This skill does not free-chat with the
contact: every move is a typed `mcp_shadownet_send`/`mcp_shadownet_respond` call.

## When to Use

- Your user asks to plan / schedule / meet up / coordinate with a contact (you
  initiate), or
- A coordination intent (`coordinate_v1` … `accept_plan_v1`) arrives from a known
  contact and is auto-loaded into an autonomous shadownet session (you respond).

## Prerequisites

- The shadownet MCP server is connected and the other party is a contact.
- For venue/time research, `web_search` (and the user's city from memory/profile).

## How to Run

Make your move with a single typed tool call — `mcp_shadownet_send` to start a
thread or `mcp_shadownet_respond(contextId=…)` to continue one — with
`body = {"text": "...", "intent": "<intent URI>", "data": {...}}`. Do not also
write a free-text reply; the typed call is the whole move.

## Quick Reference

| Step | Intent URI | Who sends | Tool |
| --- | --- | --- | --- |
| 1 | `urn:shadownet:intent:coordinate_v1` | Initiator | `mcp_shadownet_send` |
| 2 | `urn:shadownet:intent:propose_plan_v1` | Receiver | `mcp_shadownet_respond` |
| 3 | `urn:shadownet:intent:confirm_plan_v1` | Initiator | `mcp_shadownet_send` |
| 4 | `urn:shadownet:intent:accept_plan_v1` | Receiver | `mcp_shadownet_respond` |

`coordinate_v1` data: `{"activity": "Coffee", "details": "Wed afternoon downtown"}`

`propose_plan_v1` / `confirm_plan_v1` data (PlanObject):
`{"activity": "Coffee", "when": "2026-06-04T15:00:00+02:00", "where": {"name": "Barn Roastery", "city": "Berlin"}, "participants": []}`

`accept_plan_v1` data: `{"acceptsMessageId": "<messageId of the confirm_plan>"}`

## Procedure

### Initiator (your user asked to coordinate)

1. Find the contact (`mcp_shadownet_contacts`), then `mcp_shadownet_send` a
   `coordinate_v1` with the activity + details. Tell your user you reached out,
   and end your turn.
2. When `propose_plan_v1` arrives, this is a decision point: `send_message` your
   user the proposed plan in plain language (never raw JSON/ISO/identifiers) and
   include the `contextId`. Do not reply to the contact yet.
3. When your user says yes, `mcp_shadownet_send(contextId=…)` a `confirm_plan_v1`
   carrying the PlanObject. Tell your user you're waiting for the contact to
   accept. If they say no, let your user know you'll decline and stop.

### Receiver (a contact's coordination intent arrived)

1. On `coordinate_v1`, propose a concrete plan **autonomously** — check the
   user's availability/city if known, `web_search` a real venue (never "TBD"),
   pick a concrete time — then `mcp_shadownet_respond(contextId=…)` a
   `propose_plan_v1`. No user involvement yet.
2. When `confirm_plan_v1` arrives, this is a decision point: `send_message` your
   user the plan and ask whether to accept; include the `contextId`. Do not
   respond to the contact yet.
3. When your user says yes, `mcp_shadownet_respond(contextId=…)` an
   `accept_plan_v1` (`acceptsMessageId` = the confirm message's id). Tell your
   user it's set. If they decline, let them know you'll pass that on and stop.

## Pitfalls

- **Your move is the typed tool call.** Do not also send a free-text reply — in
  an autonomous session a stray reply is delivered to the contact.
- **Thread by `contextId`.** Every step in one coordination shares it; `respond`
  needs it, and `send_message` to your user should include it so you can resume.
- **Surface only at the two decision points** (confirm, accept) via
  `send_message`; the rest is autonomous. Never show raw JSON, ISO timestamps, or
  identifiers to your user.
- **Real venues only.** Never propose "TBD" or a placeholder.

## Verification

The contact's agent advances to the next intent after each move, and your user is
asked exactly once to confirm and once (as receiver) to accept — nothing else
reaches them.
