---
name: shadownet-coordinate
description: >
  Plan a meeting, coffee, dinner, lunch, call, or any activity with a contact.
  Use when the user says "plan a meeting with", "set up a coffee with",
  "schedule lunch with", "coordinate with", "meet up with", or asks to do
  something with another person/contact/friend. Agents negotiate autonomously —
  humans only confirm the final plan.
version: 1.1.0
allowed-tools:
  - mcp__shadownet__contacts
  - mcp__shadownet__contact_detail
  - mcp__shadownet__send
  - mcp__shadownet__respond
  - mcp__shadownet__inbox
  - mcp__shadownet__inbox_wait
  - web_search
  - brave_search
  - google_search
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, coordination, meetups, scheduling, a2a, plan, meeting, coffee, dinner, lunch, call]
    activation_phrases:
      - plan a meeting with
      - set up a coffee with
      - schedule lunch with
      - coordinate with
      - meet up with
      - plan something with
      - grab coffee with
      - have dinner with
    related_skills: [shadownet-setup, shadownet-reach-out, shadownet-inbox]
    requires_tools:
      - mcp_shadownet_contacts
      - mcp_shadownet_send
      - mcp_shadownet_respond
      - mcp_shadownet_inbox_wait
---

# Shadownet Coordination

Coordinate plans (coffee, dinner, meetings, tasks) with another Shadow.
Agents negotiate autonomously using each user's calendar, preferences,
and local knowledge. Humans only confirm or reject the final agreed plan.

## Protocol

All coordination messages use the primitive `send` and `respond` tools
with `body.intent` set to the appropriate intent URI. The sidecar is
content-agnostic — it transports any intent.

### Intent URIs

| Step | Intent URI | Direction | Tool |
| --- | --- | --- | --- |
| 1 | `urn:shadownet:intent:coordinate_v1` | Initiator sends | `send` |
| 2 | `urn:shadownet:intent:propose_plan_v1` | Receiver replies | `respond` |
| 3 | `urn:shadownet:intent:confirm_plan_v1` | Initiator sends | `send` |
| 4 | `urn:shadownet:intent:accept_plan_v1` | Receiver replies | `respond` |

### Data shapes

**coordinate_v1** `body.data`:
```json
{"activity": "Coffee", "details": "Wednesday afternoon downtown"}
```

**propose_plan_v1 / confirm_plan_v1** `body.data` (PlanObject):
```json
{
  "activity": "Coffee",
  "when": "2026-06-04T15:00:00+02:00",
  "where": {"name": "Barn Roastery", "city": "Berlin"},
  "participants": []
}
```

**accept_plan_v1** `body.data`:
```json
{"acceptsMessageId": "<messageId of the confirm_plan message>"}
```

## INITIATOR FLOW (your user wants to plan something)

### Step 1 — Start the coordination

Look up the contact, then send a coordination request:

```
contacts(query="<name>")

send(
  to="<contact>",
  body={
    "text": "Let's coordinate Coffee — Wednesday afternoon downtown",
    "intent": "urn:shadownet:intent:coordinate_v1",
    "data": {"activity": "Coffee", "details": "Wednesday afternoon downtown"}
  }
)
```

Tell the user:

> Sent a coordination request to `<contact>` for coffee. I'll let you
> know when they propose a plan.

DONE for now. End your turn. Do NOT poll.

### Step 2 — Proposal arrives (injected as a new event)

The receiver's agent sends back a proposal with a PlanObject.
Present it to your user in natural language:

> `<contact>` proposes coffee at Barn Roastery on Wednesday at 3 PM.
> Would you like to confirm?

Never show raw JSON, ISO timestamps, or identifiers to the user.
Format dates naturally (e.g. "Wednesday at 3 PM").

### Step 3 — User confirms

When the user says yes, send a confirmation with the plan:

```
send(
  to="<contact>",
  contextId="<contextId from the original send>",
  body={
    "text": "Confirmed.",
    "intent": "urn:shadownet:intent:confirm_plan_v1",
    "data": <the PlanObject from the proposal>
  }
)
```

Tell the user:

> Sent confirmation to `<contact>` — waiting for them to accept.

Do NOT say "confirmed" or "finalized" — the plan is not done until
the receiver sends `accept_plan_v1`. End your turn.

### Step 4 — Acceptance arrives

When `accept_plan_v1` arrives, the coordination is fully complete:

> All set! Coffee at Barn Roastery, Wednesday at 3 PM.

NOW the plan is confirmed. DONE. No further tool calls.

## RECEIVER FLOW (another Shadow sent you a coordination request)

When a `coordinate_v1` arrives, YOU must propose a concrete plan
AUTONOMOUSLY — do NOT ask your user for input. The user's only role
is to accept or reject the final agreed plan.

### Step 1 — Research and propose a plan

Use everything available to build a great proposal:

1. **Check calendar/availability.** If you have access to a calendar
   tool, check your user's schedule. If not, assume they are free.
2. **Know the user's city/area.** Use your memory, user profile, or
   any available context to determine where your user lives.
3. **Search the web for venues.** Use web search to find a real,
   specific venue that fits the activity. For coffee, find a popular
   specialty cafe. For dinner, a well-reviewed restaurant. For a
   meeting, a coworking space or cafe with wifi. Pick a real place
   with a real name — never use "TBD" or generic placeholders.
4. **Pick a concrete date and time.** Interpret the request details
   (e.g. "Sunday morning" means next Sunday at 10:00 AM). If no time
   is specified, pick a reasonable default for the activity.

Then respond with a proposal:

```
respond(
  contextId="<contextId from the inbound coordinate>",
  body={
    "text": "Coffee at Barn Roastery on Wednesday at 3 PM",
    "intent": "urn:shadownet:intent:propose_plan_v1",
    "data": {
      "activity": "Coffee",
      "when": "2026-06-04T15:00:00+02:00",
      "where": {"name": "Barn Roastery", "city": "Berlin"},
      "participants": []
    }
  }
)
```

After calling `respond`, say only: "Replying to `<contact>`'s agent."
Do not reveal proposal details — this step is autonomous.
Do not propose more than once.

### Step 2 — Confirmation arrives

When the initiator sends `confirm_plan_v1`, present the plan details
to your user and ask if they want to accept:

> `<contact>` wants to have coffee with you at Barn Roastery, Wednesday
> at 3 PM. Would you like to accept?

Do NOT call respond with accept_plan yet — wait for the user.

### Step 3 — User accepts

When the user says yes:

```
respond(
  contextId="<contextId>",
  body={
    "text": "Accepted.",
    "intent": "urn:shadownet:intent:accept_plan_v1",
    "data": {"acceptsMessageId": "<messageId of the confirm_plan message>"}
  }
)
```

If the user declines, let them know you'll inform the other party.

DONE. The coordination is complete on both sides.

## Output rules

- **One message per step.** No narration, no "Loading...", no "Checking...".
- **Natural language only.** Never show JSON, ISO timestamps, URIs, or
  raw identifiers (z6Mk...) to the user. Use display names and readable dates.
- **Receiver proposes autonomously.** When you receive `coordinate_v1`,
  research a real venue, pick a time, and propose immediately. Do NOT
  ask the user for input — use their calendar, preferences, and web
  search to make the best proposal you can.
- **Humans only confirm/accept.** The user's role is to approve or
  reject the plan, not to provide input during negotiation.
- **Thread by contextId.** Every step in the same coordination uses the
  same contextId. Use `send` with `contextId` for initiator follow-ups,
  `respond` with `contextId` for receiver replies.
- **Do not poll.** The inbox_wait long-poll handles delivery. End your
  turn after each tool call.
- **Stop after accept_plan_v1.** The flow is terminal.
