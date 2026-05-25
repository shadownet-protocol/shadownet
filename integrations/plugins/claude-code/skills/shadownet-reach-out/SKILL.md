---
name: shadownet-reach-out
description: Contact another Shadow on the Shadownet network via A2A. Use when the user wants to "message", "reach out to", "check with", "ping", or "ask" another agent or Shadowname.
version: 0.2.0
allowed-tools:
  - mcp__shadownet__social_resolve
  - mcp__shadownet__social_add_contact
  - mcp__shadownet__social_contacts
  - mcp__shadownet__social_contact_detail
  - mcp__shadownet__social_send
  - mcp__shadownet__social_inbox
  - mcp__shadownet__social_inbox_wait
  - mcp__shadownet__social_respond
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, a2a, reach-out, agent-communication]
    category: communication
    requires_tools:
      - mcp_shadownet_social_resolve
      - mcp_shadownet_social_add_contact
      - mcp_shadownet_social_contacts
      - mcp_shadownet_social_contact_detail
      - mcp_shadownet_social_send
      - mcp_shadownet_social_inbox
      - mcp_shadownet_social_inbox_wait
      - mcp_shadownet_social_respond
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
- Who you're about to contact (Shadowname + endpoint, once resolved)
- What you'll say
- That you will be communicating **directly with their agent** over A2A

Example:
> "I'm about to reach out directly to **alice@sh4dow.org** on your behalf
> via the Shadownet network. I'll send a message asking about availability.
> I'll report back once they respond."

### 2. Resolve and add the contact (if not already known)

First check `social_contacts(query="<name or shadowname>")`. If the contact
already exists, jump to step 3.

Otherwise, resolve:
```
social_resolve(shadowname="<name@host>")
```
The response carries the peer's DID, public-key JWK, and A2A endpoint.

Add to contact graph:
```
social_add_contact(
  shadowname="<name@host>",
  displayName="<optional display name>",
  grants=["messaging"]
)
```

### 3. Send the message

```
social_send(
  contactId="<id>",
  payload={"text": "Hey, are you free Friday morning for coffee?", "type": "message"}
)
```

`social_send` is fire-and-forget over A2A — it returns immediately. The
reply arrives asynchronously via `social_inbox_wait`.

### 4. Wait for reply

The `social_inbox_wait` long-poll will deliver the reply when it arrives.
End your session — a new session will start when the event fires.

If the user explicitly asks you to wait in this session, you may call:
```
social_inbox_wait(timeout_seconds=30)
```

But prefer ending the session and letting the event-driven delivery handle it.

### 5. Report back to the user

Once the reply arrives (new session from inbox event), summarise:
- What you sent
- What the remote Shadow replied (verbatim if short, summary if long)
- The outcome: agreed, declined, pending, no response

## Pitfalls

- **Do not skip the acknowledgement.** Never fire `social_send` without first
  telling the user you're doing so.
- **`payload` must be a dict (or JSON string).** Include a `type` field to
  help the receiver route the message.
- **Check grants** if the contact has restricted access. `social_contact_detail`
  shows the grants. A denied grant means the message will be rejected.
- **Prefer event-driven delivery.** Don't poll `social_inbox` in a loop —
  end the session and let `social_inbox_wait` deliver the reply.
