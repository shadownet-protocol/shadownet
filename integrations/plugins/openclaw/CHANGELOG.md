# Changelog

Notable changes to `@shadownet-protocol/openclaw-plugin`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). In the
monorepo, npm releases are cut by tagging `openclaw-plugin-vX.Y.Z`.

## [Unreleased]

r## [0.4.0] — 2026-05-31

Shadownet v0.2 migration. The plugin's tool layer and channel webhook were
wholesale v0.1; against a v0.2 Sidecar they called MCP tools that no longer
exist and parsed an event shape that changed. **Breaking** — requires a v0.2
Sidecar.

### Changed

- MCP tool names drop the `social_` prefix to the RFC 0002 §4 names
  (`social_contacts` → `contacts`, `social_send` → `send`, …). The
  OpenClaw-facing `shadownet_*` tool names are unchanged.
- Tool argument shapes move to the v0.2 wire contract (forwarded verbatim to
  the Sidecar): `contact_detail`/`resolve`/`add_contact`/`grant` take `name`
  (identifier) instead of `id`/`shadowname`/`contact_id`; `send` takes
  `{to, body, contextId?}` and `respond` takes `{contextId, body}` instead of
  the v0.1 `contact_id`/`intent_id`/`payload`; `inbox` takes an opaque string
  `since` cursor plus `contact`/`intent`/`includeReview` filters. Identifiers
  accept a Shadowname or a `shadow://` URI (RFC 0001 §3.3).
- Outbound `sendText` routes through `send` / `respond` and reads
  `{messageId, contextId}` from the result; `replyToId` now maps to a v0.2
  `contextId` (was an `intentId`).
- Webhook event taxonomy is RFC 0002 §7: envelope `"shadownet:v"` is `"0.2"`;
  known events are `inbox.message` + `task.update`; `inbox.message` data
  carries `{from, contextId, messageId, intent?, status}` (was
  `intentId`/`contactId`); the body is fetched via the `inbox` tool and read
  from the v0.2 `InboxItem` (`body.text` / `body.intent`).

### Note

- `src/connect/url.ts` (the `shadownet://connect` onboarding-URL parser) and
  the remaining `RFC-000x` documentation references are NOT migrated here —
  the runtime config path uses `endpoint` + `token` directly, so the
  onboarding-URL format is a separate follow-up.

## [0.3.0] — 2026-05-11

### Added

- `src/connect/url.ts` — TypeScript port of the `shadownet://connect`
  URL parser introduced in RFC-0008 (draft). Mirrors the Python reference
  in `shadownet/connect/url.py` byte-for-byte on shared fixtures.
  `parseConnectUrl`, `formatConnectUrl`, and `ConnectUrl` are exported
  from `./connect`.

### Changed

- **Idempotency now keys on `envelope.event_id`** (was `data.messageId`).
  RFC-0007 (updated) introduces a top-level `event_id` on webhook
  envelopes that is byte-identical to the cursor returned by
  `social_inbox_wait` and the `event_id` field on
  `notifications/shadownet/*` notifications. Receivers that bridge two
  transports dedupe across all three by this field. Legacy senders
  without a top-level `event_id` fall back to
  `(event, occurredAt)` keying — logged but not rejected.
- `ShadownetEventEnvelope.event_id` (TypeScript) is now a required
  field. Senders that publish the envelope (typically only the Sidecar)
  MUST set it; receivers SHOULD treat it as opaque.
- configSchema descriptions in `openclaw.plugin.json` and TypeBox
  description in `src/account.ts` are now provider-agnostic — they
  no longer hardcode `app.sh4dow.org`, instead referencing
  `<your-sidecar>/connect/openclaw` per RFC-0008.

### Notes

This release does NOT bump the `shadownet` dependency to 0.3.0
because the OpenClaw plugin talks to the Sidecar over HTTP/JSON-RPC
(via the bundled MCP envelope) rather than importing the Python SDK.
Compatibility with python-sdk 0.2.x and 0.3.x Sidecars is preserved.

## [0.2.0] — 2026-05-10

Initial release in the monorepo. See repository history for prior
in-development changes.
