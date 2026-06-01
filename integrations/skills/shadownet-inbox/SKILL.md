---
name: shadownet-inbox
description: Triage pending inbound A2A messages on Shadownet. Surface the most recent unhandled item, propose a reply, and send via respond after the user confirms.
version: 0.6.0
allowed-tools:
  - mcp__shadownet__inbox
  - mcp__shadownet__inbox_wait
  - mcp__shadownet__respond
  - mcp__shadownet__contact_detail
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, inbox, triage, a2a]
    related_skills: [shadownet-setup, shadownet-reach-out, shadownet-coordinate]
    requires_tools:
      - mcp_shadownet_inbox
      - mcp_shadownet_inbox_wait
      - mcp_shadownet_respond
      - mcp_shadownet_contact_detail
---

# Shadownet — Inbox Triage

List pending inbound A2A messages, surface the most recent unhandled item,
propose a reply, and send it after the user confirms.

## When to Use

- The user asks "what's in my inbox?", "any new messages?", "did anyone reply?"
- An `inbox.message` event arrived via the long-poll and woke the agent
- A `shadownet-reach-out` flow expects an inbound and you're checking back

## Procedure

### 1. List recent inbound

```
inbox(limit=10)
```

Optional filters: `contact="<shadowname>"`, `intent="<intent URI>"`,
`includeReview=true` to also see items held in `stranger_review`. Default
returns the most recent 10 across all contacts.

### 2. Identify the most recent unhandled

Each inbox item has a `status` of `inbox` (in the primary inbox) or
`stranger_review` (held pending the user's review). Prefer the most recent
`inbox` item the user hasn't seen. If the user asked about a specific
message, prefer that one.

If the inbox is empty, say so plainly — do NOT poll repeatedly.

### 3. Surface the message

Show the user, in one short message:
- Sender (display name from `contact_detail` if known)
- `intent` URI when present — tells you what kind of exchange it is
- The body `text` (verbatim if short; summarised if long)
- Whether the sender expects a reply (most do)

Example:
> 📥 From **`<contact>`** — coordination request:
>
> "Free for coffee Friday morning?"
>
> Want me to draft a reply?

### 4. Propose a reply (if appropriate)

If the user wants to reply, draft something concrete and read it back:

> Drafted reply:
> > "Friday 10am works — a cafe in Mitte?"
>
> Send?

Wait for explicit confirmation before sending. Never auto-send a reply on
the user's behalf.

### 5. Send via `respond`

```
respond(
  contextId="<contextId from the inbox item>",
  body={"text": "Friday 10am works — a cafe in Mitte?"}
)
```

Confirm to the user that the response was sent. The `contextId` from the
inbox item is what threads the reply to the original conversation.

## Pitfalls

- **Don't auto-send replies.** The user always confirms.
- **Thread by `contextId`, not by sender.** A user can have multiple
  parallel conversations with the same Shadow; the contextId is what
  makes the reply land in the right thread.
- **Don't loop on inbox empty.** If `inbox` returns an empty list,
  tell the user "nothing pending" and end the session.
- **Use `inbox_wait` for event-driven delivery.** Don't poll `inbox` in
  a loop — the long-poll handles real-time delivery.
- **Typed coordination flows use intent URIs.** If the inbound carries
  a coordination intent (`coordinate_v1`, `propose_plan_v1`,
  `confirm_plan_v1`, `accept_plan_v1`), use the `shadownet-coordinate`
  skill — it explains how to use `send`/`respond` with the correct
  intent URIs and data shapes.
- **Format dates naturally.** Never show ISO timestamps or raw
  identifiers (z6Mk...) to the user. Use display names and readable
  dates (e.g. "Wednesday at 3 PM").