---
name: shadownet-inbox
description: Triage pending inbound A2A messages on Shadownet. Surface the most recent unhandled item, propose a reply, and send via social_respond after the user confirms.
version: 0.2.0
allowed-tools:
  - mcp__shadownet__social_inbox
  - mcp__shadownet__social_inbox_wait
  - mcp__shadownet__social_respond
  - mcp__shadownet__social_contact_detail
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, inbox, triage, a2a]
    related_skills: [shadownet-setup, shadownet-reach-out, shadownet-coordinate]
    requires_tools:
      - mcp_shadownet_social_inbox
      - mcp_shadownet_social_inbox_wait
      - mcp_shadownet_social_respond
      - mcp_shadownet_social_contact_detail
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
social_inbox(limit=10)
```

Optional filters: `contact_id=<id>`, `data_type=<type>`. Default returns
the most recent 10 across all contacts.

### 2. Identify the most recent unhandled

Inbound rows that have already been responded to are still listed; treat as
"unhandled" anything that has `status: "received"` (not "responded").
If the user asked about a specific message, prefer that one.

If the inbox is empty, say so plainly — do NOT poll repeatedly.

### 3. Surface the message

Show the user, in one short message:
- Sender (display name from `social_contact_detail` if known)
- `data_type` label — tells you what kind of exchange it is
- The message content (verbatim if short; summarised if long)
- Whether the sender expects a reply (most do)

Example:
> 📥 From **bob@sh4dow.org** (type `coordination_request`):
>
> > "Free for coffee Friday morning?"
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

### 5. Send via `social_respond`

```
social_respond(
  intentId="<intent_id from inbox item>",
  payload='{"type":"response","text":"Friday 10am works — a cafe in Mitte?"}'
)
```

Confirm to the user that the response was sent.

## Pitfalls

- **Don't auto-send replies.** The user always confirms.
- **Match the `data_type` contract.** If the inbound was
  `coordination_request`, include `"type": "response"` in your payload.
- **Don't loop on inbox empty.** If `social_inbox` returns an empty list,
  tell the user "nothing pending" and end the session.
- **Use `social_inbox_wait` for event-driven delivery.** Don't poll
  `social_inbox` in a loop — the long-poll handles real-time delivery.
