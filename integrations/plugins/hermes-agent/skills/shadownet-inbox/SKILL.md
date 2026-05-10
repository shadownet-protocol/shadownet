---
name: shadownet-inbox
description: Triage pending inbound A2A messages on Shadownet. Surface the most recent unhandled item, propose a reply, and send via social_respond after the user confirms.
version: 0.1.0
allowed-tools:
  - mcp__shadownet__social_inbox
  - mcp__shadownet__social_respond
  - mcp__shadownet__social_contact_detail
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, inbox, triage, a2a]
    category: communication
    requires_tools:
      - mcp_shadownet_social_inbox
      - mcp_shadownet_social_respond
      - mcp_shadownet_social_contact_detail
---

# Shadownet — Inbox Triage

List pending inbound A2A messages, surface the most recent unhandled item,
propose a reply, and send it after the user confirms.

## When to Use

- The user asks "what's in my inbox?", "any new messages?", "did anyone reply?"
- A webhook fired and woke the agent — the payload carried an
  `intentId`/`messageId` but not the content
- A `shadownet-reach-out` flow expects an inbound and you're checking back

## Procedure

### 1. List recent inbound

```
social_inbox(limit=10)
```

Optional filters: `interaction=<name>`, `contact_id=<id>`. Default returns
the most recent 10 across all contacts and interactions.

### 2. Identify the most recent unhandled

Inbound rows that have already been responded to are still listed; treat as
"unhandled" anything that has no outbound row threaded under the same
`intent_id`. If the user asked about a specific message, prefer that one.

If the inbox is empty, say so plainly — do NOT poll repeatedly.

### 3. Surface the message

Show the user, in one short message:
- Sender (Shadowname + display name from `social_contact_detail` if known)
- `data_type` and `interaction` labels — these tell you what kind of
  exchange it is (message, coordination_request, response, etc.)
- The message content (verbatim if short; summarised if long)
- Whether the sender expects a reply (most do)

Example:
> 📥 From **bob@sh4dow.org** (intent `int-001`, type `coordination_request`):
>
> > "Free for coffee Friday morning?"
>
> Want me to draft a reply?

### 4. Propose a reply (if appropriate)

If the user wants to reply, draft something concrete and read it back:

> Drafted reply:
> > "Friday 10am works — Zazza in Mitte? Can do 11am instead if that's better."
>
> Send?

Wait for explicit confirmation before sending. Never auto-send a reply on
the user's behalf.

### 5. Send via `social_respond`

```
social_respond(
  intent_id="<id>",
  content="<the drafted reply>",
  data_type="response"   # or whatever the contract calls for
)
```

Confirm to the user that the response was sent and surface the new
`intent_id` (or task id) for tracking.

## Pitfalls

- **Don't auto-send replies.** The user always confirms.
- **Match the `data_type` contract.** If the inbound was
  `coordination_request`, replying with `data_type="response"` is the right
  move (per RFC-0006 §Errors). Don't invent new labels mid-thread.
- **Idempotency.** If a webhook fired and the agent has already responded
  to the same `messageId`, do not re-respond. Check for an existing
  outbound thread first.
- **Don't loop on inbox empty.** If `social_inbox` returns an empty list,
  tell the user "nothing pending" and end the session.

## Verification

After `social_respond` the user should see confirmation that the reply was
queued for delivery. The next inbox poll will show the threaded reply.
