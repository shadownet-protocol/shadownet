---
name: shadownet-coordinate
description: Coordinate a meetup, call, or task between two Shadows via fully autonomous agent-to-agent negotiation. Both Shadows use their users' calendars and preferences to agree on a plan, then present it for one-tap user confirmation.
version: 0.2.0
allowed-tools:
  - mcp__shadownet__social_contacts
  - mcp__shadownet__social_contact_detail
  - mcp__shadownet__social_coordinate
  - mcp__shadownet__social_confirm_plan
  - mcp__shadownet__social_accept_plan
  - mcp__shadownet__social_respond
  - mcp__shadownet__social_inbox_wait
disable-model-invocation: true
metadata:
  hermes:
    tags: [shadownet, coordination, meetups, scheduling, a2a]
    category: communication
    requires_tools:
      - mcp_shadownet_social_contacts
      - mcp_shadownet_social_coordinate
      - mcp_shadownet_social_confirm_plan
      - mcp_shadownet_social_accept_plan
      - mcp_shadownet_social_respond
      - mcp_shadownet_social_inbox_wait
---

# Shadownet — Autonomous Coordination

Coordinate plans (coffee, dinner, meetings, joint tasks) with another
person's Shadow. Agents negotiate **fully autonomously** using each user's
calendar, preferences, and local knowledge. Users only see the final
agreed plan and confirm.

This skill is **user-invocable only** (`disable-model-invocation: true`) —
the model should not auto-trigger autonomous coordination on its own
inference of intent.

## Roles

Every coordination has an **initiator** and a **receiver**.

---

## INITIATOR FLOW (your user asked to plan something)

### Step 1 — Start coordination

Look up the contact, then call `social_coordinate`:

```
social_contacts(query="<name>")
social_coordinate(contactId="<id>", activity="coffee", details="Friday morning in Mitte")
```

The sidecar sends a `coordination_request` to the receiver's Shadow. Their
agent will negotiate autonomously and respond with an agreed plan.

### Step 2 — End the session

> Sent a coordination request to **bob@sh4dow.org**. I'll let you know
> when we've agreed on a plan.

DONE. Do NOT poll. The `social_inbox_wait` long-poll handles delivery.

### Step 3 — Response arrives (new session via inbox event)

When the receiver's Shadow responds, it carries an **agreed plan**. Present
ONE clean message to your user:

> ☕ Agreed with **bob@sh4dow.org**: Coffee at The Daily Grind,
> Friday at 10am. Confirm?

### Step 4 — User confirms

```
social_confirm_plan()
```

No arguments needed — it auto-finds the pending plan. End session.

### Step 5 — Final acceptance arrives (new session via inbox event)

When the receiver accepts:

> All set! Coffee Friday 10am at The Daily Grind.

DONE.

---

## RECEIVER FLOW (another Shadow sent you a coordination request)

**THIS IS THE CRITICAL PART. You must negotiate AUTONOMOUSLY.**

### Step 1 — Read the request and YOUR user's data

The inbound message contains the initiator's availability, preferences, and
proposed dates/times. Load YOUR user's calendar / preferences from local
memory or profile skills.

### Step 2 — Find the best match AUTONOMOUSLY

Compare both users' data and pick the best option:
- Overlapping free time slots
- Shared interests both enjoy
- A specific venue that fits (use your local knowledge)
- A concrete date, time, and place

DO NOT ask your user for input. YOU decide based on what you know.

### Step 3 — Respond with the agreed plan

```
social_respond(
  intentId="<inbound intent_id>",
  payload='{"type":"response","status":"agreed","plan":{"activity":"Coffee","date":"Friday","time":"10:00 AM","location":"The Daily Grind","notes":"Great spot in Mitte"}}'
)
```

**CRITICAL: Do NOT write ANY text to the user during this phase.**
Your only output is the `social_respond` tool call. Say nothing.
End the session immediately after the tool call.

### Step 4 — Confirmation arrives (new session via inbox event)

When you receive a confirmation, NOW notify your user:

> ☕ **alice@sh4dow.org** confirmed: Coffee at The Daily Grind, Friday 10am.
> Accept?

### Step 5 — User accepts

```
social_accept_plan()
```

No arguments needed — it auto-finds the pending confirmation. DONE.

---

## Message Types

| `data_type` | Sent by | Meaning |
|---|---|---|
| `coordination_request` | Initiator | Rich request with activity + details |
| `response` | Receiver | Agreed plan (receiver negotiated autonomously) |
| `confirmation` | Initiator | "My user approved" |
| `confirmed` | Receiver | "My user approved too — we're set" |

---

## Output Rules

- **ONE message per step.** Never multiple bot messages.
- **No narration.** Don't say "Loading skill...", "Checking inbox...". Just do it.
- **Be concise.** "Coffee at X, Friday 10am. Confirm?" — that's it.
- **Include reasoning in the `social_respond` payload** so it shows up in
  the message log, but do NOT show it to the user.

## Pitfalls

- **DO NOT ask the receiver's user during negotiation.** This is the #1
  rule. You have their preferences and calendar — use them.
- **DO NOT poll with `social_inbox`.** The `social_inbox_wait` long-poll
  handles all inbound delivery.
- **STOP on `confirmed`.** Never respond to a `confirmed` message.
- **One tool call per session** for coordination flows.
