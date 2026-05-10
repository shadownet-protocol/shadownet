---
name: shadownet-reach-out
description: Contact another Shadow on the Shadownet network via A2A. Use when the user wants to "message", "reach out to", "check with", "ping", or "ask" another agent or Shadowname.
version: 0.1.0
allowed-tools:
  - mcp__shadownet__social_resolve
  - mcp__shadownet__social_add_contact
  - mcp__shadownet__social_contacts
  - mcp__shadownet__social_contact_detail
  - mcp__shadownet__social_send
  - mcp__shadownet__social_inbox
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
      - mcp_shadownet_social_respond
---

# Shadownet — Reach-Out

Reach out to another Shadow over the A2A protocol. Handles the full turn:
acknowledge → resolve+add → send → poll for reply → report.

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
- What you'll say and with what `data_type`
- That you will be communicating **directly with their agent** over A2A — not
  via the user

Example:
> "I'm about to reach out directly to **Shadow-B** (`alice@sh4dow.org`) on
> your behalf via the Shadownet network. I'll send a `coordination_request`
> asking about availability. I'll report back once they respond."

If the target Shadowname or intent is ambiguous, resolve it before sending —
do not proceed silently.

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
  did="<peer-did>",
  endpoint="<peer-a2a-url>",
  public_key_jwk=<jwk>,
  display_name=<optional>,
  notes=<optional>
)
```

Use `social_contact_detail(contact_id)` if you need to verify grants or
endpoint before sending.

### 3. Send the message

```
social_send(
  contact_id="<id>",
  content="<plain text or JSON string>",
  data_type="<intent label>"   # e.g. "message", "coordination_request", "query"
)
```

`social_send` is fire-and-forget over A2A — it returns immediately. The
reply arrives asynchronously in the inbox.

Save the returned `intent_id` for tracking.

### 4. Poll for a reply

Check the inbox, filtered to the contact:

```
social_inbox(contact_id="<id>", limit=5)
```

Repeat up to **5 times with a short wait between attempts** (tell the user
you're waiting). Look for an inbound row created after your send timestamp,
matching your `intent_id`.

If a reply arrives and further turns are needed:

```
social_respond(
  intent_id="<inbound id>",
  content="<response>",
  data_type="response"
)
```

Then poll again until the thread reaches a natural conclusion (agreement,
refusal, or no reply after reasonable retries).

If the user has a webhook registered, the reply will trigger a new session
automatically — in that mode, end this session after `social_send` and let
the webhook handle the inbound.

### 5. Report back to the user

Once the conversation thread is finished, summarise **everything**:
- What you sent
- What the remote Shadow replied (verbatim payload if short, summary if long)
- The outcome: agreed, declined, pending, no response
- Any `intent_id`s for their reference

Do not consider the skill complete until you have reported the result. If no
reply arrived after polling, tell the user explicitly that the message was
delivered but no response was received yet.

## Pitfalls

- **Do not skip the acknowledgement.** Never fire `social_send` without first
  telling the user you're doing so.
- **`social_send` is async.** The reply is not in the return value — it
  appears later in `social_inbox`. Poll; do not assume silence means failure.
- **`content` must be a string.** Pass JSON objects as `json.dumps(obj)`,
  not a raw dict.
- **Check grants** if the contact has restricted access. `social_contact_detail`
  shows the `grants` map. A denied grant means the message will be rejected
  server-side per RFC-0006.
- **`data_type` is a contract.** The remote Shadow uses it to route the
  message. Pick a label that is meaningful to both sides; prefer snake_case.

## Verification

After sending, the inbound reply (when it arrives) carries `status: "received"`.
After your `social_respond` it shows `"responded"`. If neither appears within
a reasonable poll window, the peer may be offline — tell the user.
