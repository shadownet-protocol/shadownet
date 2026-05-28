---
name: shadownet-invitations
description: Triage pending invitations from unknown senders. List quarantined items, surface the most recent one to the user, and accept (with grants and an optional ContactProfile) or reject after explicit user confirmation. Never auto-process quarantine.
version: 0.1.0
allowed-tools:
  - mcp__shadownet__social_quarantine_list
  - mcp__shadownet__social_quarantine_review
  - mcp__shadownet__social_contact_detail
  - mcp__shadownet__social_set_contact_profile
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, invitations, quarantine, a2a]
    related_skills: [shadownet-setup, shadownet-inbox, shadownet-reach-out]
    requires_tools:
      - mcp_shadownet_social_quarantine_list
      - mcp_shadownet_social_quarantine_review
      - mcp_shadownet_social_contact_detail
      - mcp_shadownet_social_set_contact_profile
---

# Shadownet — Pending Invitations

Triage invitations from unknown senders. Quarantine is the receiver's
spam-and-stranger filter — unsolicited inbound never reaches the host
agent's reasoning loop on its own (RFC-0006 §Cost guarantee). This skill
surfaces the user's pending invitations so they can decide who to add to
their contact graph.

## When to Use

- The user asks "any new invitations?", "who's trying to reach me?", or
  "did I get any introductions?"
- A `quarantine.pending` event arrived via `social_inbox_wait` or an MCP
  notification and woke the agent
- The user explicitly requests an invitation review

## Procedure

### 1. List pending quarantine items

```
social_quarantine_list(limit=20)
```

Returns invitations from unknown senders. Each item carries:

- `senderShadowname` and `senderDid` — who is asking
- `purpose` — typically `"invitation"`
- `summary` — the sender's own short message (verbatim; never
  LLM-rewritten by the receiver)
- `affiliation` — the sender's claimed org affiliation, if any
- `introducer` — DID of a mutual contact who vouched (UI hint only;
  must not bypass the user's decision)
- `flags` — gateway annotations (e.g. `rate-limited`, `suspected-spam`)

If the list is empty, say so plainly and end. Do NOT poll repeatedly.

### 2. Surface the most recent unhandled item

Show the user, in one short message:

- Sender (Shadowname + display affiliation if present)
- Purpose
- The sender's summary verbatim
- Any introducer or gateway flags
- A direct question: accept, reject, or skip for now

### 3. Ask before deciding

You MUST get explicit user direction before calling
`social_quarantine_review`. Never auto-accept on the user's behalf, even
if the sender carries an affiliation the user has previously interacted
with — that decision is the user's. Quarantine review is a one-way door:
the routing matrix will treat the sender as a contact after acceptance,
so a mistake costs an unwanted relationship.

### 4. Accept (with grants and an optional ContactProfile)

When the user says yes:

```
social_quarantine_review(
  quarantineId="<id>",
  decision="accept",
  displayName="<user-supplied name>",
  grants=["messaging"],
  profile={
    "notes": "<user-supplied context>",
    "priority": "normal",
    "collaborate_on": ["<topic>"],
  },
)
```

`grants` defaults to `["messaging"]`. Add `"coordinate"` only if the user
explicitly grants it. The `profile` is local-only — it stays on this
Sidecar and never crosses the wire. Use it to capture what the user
remembers about this contact ("Met at the platform-team offsite",
"Friend-of-Bob, work on Y").

### 5. Reject (and optionally block)

When the user says no:

```
social_quarantine_review(quarantineId="<id>", decision="reject")
```

When the sender is abusive or unwanted permanently:

```
social_quarantine_review(quarantineId="<id>", decision="reject_and_block")
```

`reject_and_block` records the sender DID in a local block list so
future inbound from the same DID is dropped at the gateway before
quarantine. Use this for spam or harassment; default to plain `reject`
for a polite no.

The sender's task transitions to `failed` with reason `peer_declined`.
The sender learns the request was not accepted but receives no detail
about why.

### 6. Update the ContactProfile post-acceptance (optional)

If the user adds context after the initial accept:

```
social_set_contact_profile(
  contactId="<contact_id>",
  profile={ ... }
)
```

A `profile` of `{}` clears all fields. Partial updates are not defined;
read the current profile via `social_contact_detail` and submit the full
desired state.

## Pitfalls

- **Do not auto-process quarantine.** Every accept/reject is a user
  decision, even when the sender looks "obviously" legitimate. RFC-0006
  §Cost guarantee depends on this.
- **Do not summarize the sender's payload.** The `summary` field is
  what the sender supplied; surface it verbatim. Do not re-write it,
  do not "improve" it. That's how the cost guarantee survives.
- **`introducer_contact` is a hint, not authorization.** A vouching
  contact in the local graph does NOT mean the user accepts the new
  stranger automatically.
- **Don't loop on empty quarantine.** Empty list → tell the user
  "no pending invitations" and end the session.
- **ContactProfile stays local.** Never include profile fields in
  `social_send`, `social_respond`, or any other outbound. The Sidecar
  enforces this, but skills should not even try.
