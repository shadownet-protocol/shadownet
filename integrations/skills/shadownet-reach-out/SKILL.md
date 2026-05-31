---
name: shadownet-reach-out
description: Contact another Shadow on the Shadownet network via A2A. Use when the user wants to "message", "reach out to", "check with", "ping", or "ask" another agent or Shadowname.
version: 0.5.0
allowed-tools:
  - mcp__shadownet__resolve
  - mcp__shadownet__add_contact
  - mcp__shadownet__contacts
  - mcp__shadownet__contact_detail
  - mcp__shadownet__send
  - mcp__shadownet__inbox
  - mcp__shadownet__inbox_wait
  - mcp__shadownet__respond
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, a2a, reach-out, agent-communication]
    related_skills: [shadownet-setup, shadownet-inbox, shadownet-coordinate]
    requires_tools:
      - mcp_shadownet_resolve
      - mcp_shadownet_add_contact
      - mcp_shadownet_contacts
      - mcp_shadownet_contact_detail
      - mcp_shadownet_send
      - mcp_shadownet_inbox
      - mcp_shadownet_inbox_wait
      - mcp_shadownet_respond
---

# Shadownet — Reach-Out

Reach out to another Shadow over the A2A protocol. Handles the full turn:
acknowledge → resolve+add → send → wait for reply → report.

## When to Use

- The user wants to contact another person's Shadow or agent
- A task requires coordinating with a remote Shadow (scheduling, info exchange,
  negotiation)
- The user asks to "message", "reach out to", "check with", "ask", or "ping"
  another Shadowname

## Procedure

### 1. Acknowledge to the user

Before doing anything, tell the user in plain language:
- Who you're about to contact (Shadowname, once resolved)
- What you'll say
- That you will be communicating **directly with their agent** over A2A

Example:
> "I'm about to reach out directly to **alice@sh4dow.org** on your behalf
> via the Shadownet network. I'll send a message asking about availability.
> I'll report back once they respond."

### 2. Resolve and add the contact (if not already known)

First check `contacts(query="<name or shadowname>")`. If the contact
already exists, jump to step 3.

Otherwise, resolve:
```
resolve(name="<name@host>")
```
The response carries the peer's Shadowname, multibase Ed25519 public key,
and A2A endpoint.

Add to contact graph:
```
add_contact(
  name="<name@host>",
  displayName="<optional display name>",
  grants=["messaging"]
)
```

If the response carries a `trustWarning.untrustedIssuers`, mention this to
the user — it means the contact's credentials are signed by an issuer not
in your trust store. The contact is still added; the warning is
informational.

### 3. Send the message

```
send(
  to="<name@host>",
  body={"text": "Hey, are you free Friday morning for coffee?"}
)
```

`send` is fire-and-forget over A2A — it returns immediately with a
`messageId` and a `contextId`. The reply (when it arrives) carries the
same `contextId`, which is how you'll match it to this conversation.

### 4. Wait for reply

The `inbox_wait` long-poll will deliver the reply when it arrives. End
your session — a new session will start when the event fires.

If the user explicitly asks you to wait in this session, you may call:
```
inbox_wait(timeout_seconds=30)
```

But prefer ending the session and letting the event-driven delivery handle
it.

### 5. Report back to the user

Once the reply arrives (new session from inbox event), summarise:
- What you sent
- What the remote Shadow replied (verbatim if short, summary if long)
- The outcome: agreed, declined, pending, no response

## Pitfalls

- **Do not skip the acknowledgement.** Never fire `send` without first
  telling the user you're doing so.
- **`body` is an object with `text` / `intent` / `data`.** For a free-form
  message just include `text`. Typed flows (coordinate / confirm_plan /
  accept_plan) use the dedicated tools — do not hand-roll an `intent` URI.
- **Check grants** if the contact has restricted access.
  `contact_detail(name=...)` shows the grants. A denied grant means
  `send` will return a `rejected` status with a `policy` error.
- **Prefer event-driven delivery.** Don't poll `inbox` in a loop —
  end the session and let `inbox_wait` deliver the reply.