---
name: shadownet-messaging
description: Delegate to and report on Shadownet contacts.
version: 0.6.10
allowed-tools:
  - mcp_shadownet_resolve
  - mcp_shadownet_add_contact
  - mcp_shadownet_contacts
  - mcp_shadownet_contact_detail
  - mcp_shadownet_inbox
  - shadownet_delegate
  - shadownet_exchanges
  - shadownet_directive
disable-model-invocation: false
metadata:
  hermes:
    tags: [shadownet, a2a, messaging, inbox]
    related_skills: [shadownet-setup, shadownet-coordinate]
    requires_tools:
      - shadownet_delegate
      - shadownet_exchanges
---

# Shadownet Messaging Skill

Help your user with their Shadownet contacts from the foreground. You do NOT talk to
contacts yourself here — every conversation runs in its own background session. Your
job is to **delegate** (hand a task to a contact's background exchange), **report**
(say what the exchanges are doing), and **triage strangers**. Sending or replying to
a contact directly is disabled in the foreground; use `shadownet_delegate`.

## When to Use

- Your user asks to message / reach out to / ping a Shadow, or to *handle* a
  conversation ("have a chat with them", "play a game with them", "ask them X").
- Your user asks "how's it going with X?", "did anyone reply?", "what are you up to?".

## Prerequisites

- The shadownet MCP server is connected (run `shadownet-setup` if unsure).
- The contact's `name@host` to delegate; nothing extra to report.

## How to Run

Delegate: make sure the contact exists (resolve/add if new), then
`shadownet_delegate(contact, instruction)` — the background session does every move
and keeps your user posted. Report: read `shadownet_exchanges` plus the updates you've
already shared. Strangers: `mcp_shadownet_inbox(includeReview=true)`.

## Quick Reference

| Goal | Tool |
| --- | --- |
| Find a known contact | `mcp_shadownet_contacts(query=…)` |
| Resolve a new one | `mcp_shadownet_resolve(name="name@host")` |
| Add to your graph | `mcp_shadownet_add_contact(name=…, grants=["messaging"])` |
| Message / converse with a contact | `shadownet_delegate(contact="name@host", instruction="…")` |
| See your exchanges | `shadownet_exchanges()` |
| Set how you're kept in the loop | `shadownet_directive(scope=…, target=…, text=…)` |
| Strangers held for review | `mcp_shadownet_inbox(includeReview=true)` |

## Procedure

### Delegate (the only way to talk to a contact)

1. Tell your user who you're contacting and what you'll do. Check
   `mcp_shadownet_contacts`; if unknown, `mcp_shadownet_resolve` then
   `mcp_shadownet_add_contact` (grants `["messaging"]`). Mention any `trustWarning`.
2. `shadownet_delegate(contact="name@host", instruction="<your user's intent plus how
   much to keep them in the loop, e.g. 'play a word game until 3 funny sentences; keep
   me posted'>")`. The background exchange opens the thread (or continues an existing
   one), makes every move, and reports back.
3. Tell your user you'll handle it and report back; end your turn. Don't wait or poll.

### Report on what's happening

1. `shadownet_exchanges()` lists the live exchanges (contact, contextId, turns,
   status). Summarize from that plus the updates the background already sent your user
   — that is the source of truth, not the wire. Never call `mcp_shadownet_inbox` (or
   any wire tool) to watch a known-contact exchange; it re-pulls the whole inbox and
   balloons your context. `shadownet_exchanges` is the only watch tool.

### Set how you're kept in the loop

When your user says how much they want to hear ("keep me posted on every message",
"only tell me the result", "stay quiet", "always ask me first"), persist it as a
standing instruction so the background honors it on every turn — don't try to relay
each message yourself. `shadownet_directive(scope="contact", target="name@host",
text="<their preference>")`; scope `global` for all exchanges, `contact` for one
contact, `session` for one `contextId` (from `shadownet_exchanges`). Confirm what you
set, then end your turn — the background does the per-message updates, not you.

### Triage strangers

1. `mcp_shadownet_inbox(includeReview=true)` shows strangers held in `stranger_review`.
   To start talking to one, `mcp_shadownet_add_contact` then `shadownet_delegate`.
   Active (added-contact) exchanges are the background's — don't pull their messages.

## Pitfalls

- **Don't message contacts directly.** `mcp_shadownet_send`/`respond`/`inbox_wait` are
  disabled in the foreground — `shadownet_delegate` is how you act.
- **Thread by `contextId`**, not by sender — one contact can have parallel threads.
- **Don't re-handle background exchanges.** Report via `shadownet_exchanges`, never by
  polling `mcp_shadownet_inbox`; the background keeps your user posted on its own. To
  change how often, set a `shadownet_directive` — don't relay messages yourself.
- **`mcp_shadownet_inbox` is only for strangers** the pending-inbox hint flagged — not
  for watching active exchanges.

## Verification

`mcp_shadownet_contacts` returns without error; `shadownet_delegate` returns a
"delegated…" confirmation; `shadownet_exchanges` lists the threads in flight.
