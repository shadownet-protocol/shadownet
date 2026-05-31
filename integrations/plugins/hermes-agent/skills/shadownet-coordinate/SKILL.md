---
name: shadownet-coordinate
description: Coordinate a meetup, call, or task between two Shadows via fully autonomous agent-to-agent negotiation. Both Shadows use their users' calendars and preferences to agree on a plan, then present it for one-tap user confirmation.
version: 0.6.0
allowed-tools:
  - mcp__shadownet__contacts
  - mcp__shadownet__contact_detail
  - mcp__shadownet__send
  - mcp__shadownet__respond
  - mcp__shadownet__inbox_wait
disable-model-invocation: true
metadata:
  hermes:
    tags: [shadownet, coordination, meetups, scheduling, a2a]
    related_skills: [shadownet-setup, shadownet-reach-out, shadownet-inbox]
    requires_tools:
      - mcp_shadownet_contacts
      - mcp_shadownet_send
      - mcp_shadownet_respond
      - mcp_shadownet_inbox_wait
---

# Shadownet — Autonomous Coordination

Coordinate plans (coffee, dinner, meetings, joint tasks) with another
person's Shadow. Agents negotiate **fully autonomously** using each user's
calendar, preferences, and local knowledge. Users only see the final
agreed plan and confirm.

This skill is **user-invocable only** (`disable-model-invocation: true`) —
the model should not auto-trigger autonomous coordination on its own
inference of intent.

## Intent flow

Per RFC 0002 §1 + §3 the MCP surface is **content-agnostic**: intent
profile payloads ride opaquely in `body.intent` / `body.data` on the
generic `send` / `respond` tools. The three coordination steps each
use a distinct intent URI:

| Intent URI | Sent by | `body.data` shape |
| --- | --- | --- |
| `urn:shadownet:intent:coordinate_v1` | Initiator | `{activity, details?}` |
| `urn:shadownet:intent:confirm_plan_v1` | Receiver (after autonomous negotiation) | `PlanObject` (activity, when, where, participants) |
| `urn:shadownet:intent:accept_plan_v1` | Initiator (after user confirms) | `{acceptsMessageId}` |

## Roles

Every coordination has an **initiator** and a **receiver**.

## INITIATOR FLOW (your user asked to plan something)

### Step 1 — Start coordination

Look up the contact, then send a `coordinate_v1` envelope via `send`:

```
contacts(query="<name>")
send(
  to="bob@sh4dow.org",
  body={
    "text": "Want to grab coffee Friday?",
    "intent": "urn:shadownet:intent:coordinate_v1",
    "data": {"activity": "coffee", "details": "Friday morning in Mitte"}
  }
)
```

The response carries `messageId` and `contextId`; remember the
`contextId` — every subsequent message in this coordination uses it.

### Step 2 — End the session

> Sent a coordination request to **bob@sh4dow.org**. I'll let you know
> when we've agreed on a plan.

DONE. Do NOT poll. The `inbox_wait` long-poll handles delivery.

### Step 3 — Confirmation arrives (new session via inbox event)

When the receiver's Shadow responds with `confirm_plan_v1`, it carries
the **agreed PlanObject** in `body.data`. Present ONE clean message to
your user:

> ☕ Agreed with **bob@sh4dow.org**: Coffee at The Daily Grind,
> Friday at 10am. Confirm?

### Step 4 — User accepts

Send `accept_plan_v1` back via `respond` (same context):

```
respond(
  contextId="<contextId from the confirm_plan inbox item>",
  body={
    "text": "accepted",
    "intent": "urn:shadownet:intent:accept_plan_v1",
    "data": {"acceptsMessageId": "<messageId of the confirm_plan envelope>"}
  }
)
```

After this returns the coordination is **complete on both sides**. Tell
the user the plan is set:

> ✅ Coffee at The Daily Grind, Friday 10am. Confirmed.

DONE.

## RECEIVER FLOW (another Shadow sent you a coordination request)

**THIS IS THE CRITICAL PART. You must negotiate AUTONOMOUSLY.**

### Step 1 — Read the request and YOUR user's data

The inbound `coordinate_v1` body carries `{activity, details?}`. Load
YOUR user's calendar / preferences from local memory or profile skills.

### Step 2 — Find the best match AUTONOMOUSLY

Compare both users' data and pick the best option:
- Overlapping free time slots
- Shared interests both enjoy
- A specific venue that fits (use your local knowledge)
- A concrete date, time, and place

DO NOT ask your user for input. YOU decide based on what you know.

### Step 3 — Respond with the agreed plan

Send `confirm_plan_v1` via `respond` (same context):

```
respond(
  contextId="<contextId from the inbound coordinate envelope>",
  body={
    "text": "Coffee at The Daily Grind, Friday 10am — great spot in Mitte.",
    "intent": "urn:shadownet:intent:confirm_plan_v1",
    "data": {
      "activity": "Coffee",
      "when": "2026-05-15T10:00:00+02:00",
      "where": {"city": "Berlin", "name": "The Daily Grind", "type": "cafe"},
      "participants": ["alice@sh4dow.org", "bob@sh4dow.org"]
    }
  }
)
```

**CRITICAL: Do NOT write ANY text to the user during this phase.**
Your only output is the `respond` tool call. Say nothing.
End the session immediately after the tool call.

### Step 4 — Final acceptance arrives (new session via inbox event)

When the initiator's Shadow sends `accept_plan_v1`, the plan is
committed on both sides:

> ✅ Coffee Friday 10am at The Daily Grind. Confirmed with **alice@sh4dow.org**.

DONE.

## Output Rules

- **ONE message per step.** Never multiple bot messages.
- **No narration.** Don't say "Loading skill...", "Checking inbox...". Just do it.
- **Be concise.** "Coffee at X, Friday 10am. Confirm?" — that's it.
- **Use the typed intent URIs.** `confirm_plan_v1` / `accept_plan_v1`
  let the peer's sidecar route the envelope to its own coordination
  handler. Don't fall back to free-form text mid-flow.

## Pitfalls

- **DO NOT ask the receiver's user during negotiation.** This is the #1
  rule. You have their preferences and calendar — use them.
- **DO NOT poll with `inbox`.** The `inbox_wait` long-poll handles all
  inbound delivery.
- **Reuse the contextId across the whole flow.** Every step uses the
  SAME `contextId`. That's how the sidecar threads the conversation.
- **STOP after sending `accept_plan_v1` (initiator) or receiving it
  (receiver).** The flow is terminal — do not respond to the acceptance.
- **One tool call per session** for coordination flows.