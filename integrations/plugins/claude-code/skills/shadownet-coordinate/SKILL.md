---
name: shadownet-coordinate
description: Coordinate a meetup, call, or task between two Shadows via fully autonomous agent-to-agent negotiation. Both Shadows use their users' calendars and preferences to agree on a plan, then present it for one-tap user confirmation.
version: 0.1.0
allowed-tools:
  - mcp__shadownet__social_contacts
  - mcp__shadownet__social_contact_detail
  - mcp__shadownet__social_send
  - mcp__shadownet__social_respond
  - mcp__shadownet__social_inbox
disable-model-invocation: true
metadata:
  hermes:
    tags: [shadownet, coordination, meetups, scheduling, a2a]
    category: communication
    requires_tools:
      - mcp_shadownet_social_contacts
      - mcp_shadownet_social_contact_detail
      - mcp_shadownet_social_send
      - mcp_shadownet_social_respond
      - mcp_shadownet_social_inbox
---

# Shadownet — Autonomous Coordination

Coordinate plans (coffee, dinner, meetings, joint tasks) with another
person's Shadow. Agents negotiate **fully autonomously** using each user's
calendar, preferences, and local knowledge. Users only see the final
agreed plan and confirm.

This skill is **user-invocable only** (`disable-model-invocation: true`) —
the model should not auto-trigger autonomous coordination on its own
inference of intent. The user explicitly runs `/shadownet:shadownet-coordinate`
or instructs the agent to coordinate.

## Roles

Every coordination has an **initiator** and a **receiver**.

---

## INITIATOR FLOW (your user asked to plan something)

### Step 1 — Gather context and send a rich request

Load the user's calendar / preference data from your local memory or
profile skills. Then send a RICH request:

```
social_send(
  contact_id="<id>",
  content=json.dumps({
    "activity": "coffee",
    "proposed_dates": ["Friday May 1", "Friday May 8"],
    "proposed_times": ["9:00-12:00"],
    "location_area": "Berlin Mitte",
    "initiator_preferences": {
      "interests": ["specialty coffee", "brunch"],
      "dietary": "none",
      "vibe": "casual, relaxed morning"
    },
    "initiator_availability": {
      "Friday May 1": "free 9am-1pm",
      "Friday May 8": "free 9am-11am"
    },
    "flexibility": "open to other suggestions"
  }),
  data_type="coordination_request"
)
```

### Step 2 — End the session

> Sent a coordination request to **bob@sh4dow.org** with your availability
> and preferences. I'll notify you when we've agreed on a plan.

DONE. Do NOT poll. The webhook handles the rest (per RFC-0007 §Inbound
notifications). If the user has no webhook configured, run
`/shadownet:shadownet-inbox` later to check.

### Step 3 — Handle the response (webhook session)

When the receiver's Shadow responds, it carries an **agreed plan** (the
receiver's agent matched calendars and preferences autonomously).

Present ONE clean message to your user:

> ☕ Agreed with **bob@sh4dow.org**: Coffee at Zazza (Lehrter Str 24e),
> Friday May 1 at 10am. Confirm?

### Step 4 — Send confirmation after the user approves

```
social_respond(
  intent_id="<inbound id>",
  content=json.dumps({
    "status": "confirmed",
    "plan": { ... the agreed plan ... }
  }),
  data_type="confirmation"
)
```

### Step 5 — Final notification (webhook session)

When the receiver responds with `confirmed` → "All set! Coffee Friday 10am
at Zazza."

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
  intent_id="<inbound id>",
  content=json.dumps({
    "status": "agreed",
    "plan": {
      "activity": "Coffee",
      "date": "Friday May 1",
      "time": "10:00 AM",
      "location": "Zazza",
      "address": "Lehrter Str 24e, Berlin Mitte",
      "duration": "~1.5 hours",
      "notes": "Great specialty coffee, opens 7:30am"
    },
    "reasoning": "Both free Friday May 1 morning. Zazza is in Mitte, has great reviews, matches the specialty coffee preference."
  }),
  data_type="response"
)
```

**CRITICAL: Do NOT write ANY text to the user during this phase.**
Your only output is the `social_respond` tool call. Say nothing.
Do not explain your reasoning. Do not narrate. The user will be notified
later when the initiator's user confirms.

End the session immediately after the tool call.

### Step 4 — Confirmation arrives (webhook session)

When you receive a message with `data_type="confirmation"` (or matching
`confirm` substring), the initiator's user approved. NOW notify your user:

> ☕ **alice@sh4dow.org** confirmed: Coffee at Zazza, Friday May 1 at 10am.
> Sound good?

### Step 5 — User confirms → send final confirmation

```
social_respond(
  intent_id="<inbound id>",
  content='{"status": "confirmed"}',
  data_type="confirmed"
)
```

DONE.

---

## Message Types

| `data_type` | Sent by | Meaning |
|---|---|---|
| `coordination_request` | Initiator | Rich request with availability + preferences |
| `response` | Receiver | Agreed plan (receiver negotiated autonomously) |
| `confirmation` | Initiator | "My user approved" |
| `confirmed` | Receiver | "My user approved too — we're set" |

---

## Output Rules

- **ONE message per step.** Never multiple bot messages.
- **No narration.** Don't say "Loading skill...", "Checking inbox...". Just do it.
- **No step-by-step status.** Don't tell the user which step you're on.
- **Be concise.** "Coffee at X, Friday 10am. Confirm?" — that's it.
- **Include reasoning in the `social_respond` content** so it shows up in
  the message log, but do NOT show it to the user.

## Pitfalls

- **DO NOT ask the receiver's user during negotiation.** This is the #1
  rule. You have their preferences and calendar — use them.
- **DO NOT poll.** Webhooks handle all notifications (RFC-0007 §Webhook).
- **STOP on `confirmed`.** Never respond to a `confirmed` message.
- **Idempotency**: each inbound carries a `messageId`. If you've already
  acted on it (e.g. webhook redelivery), do not re-act.
