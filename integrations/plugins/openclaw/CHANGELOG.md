# Changelog

Notable changes to `@shadownet-protocol/openclaw-plugin`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). In the
monorepo, npm releases are cut by tagging `openclaw-plugin-vX.Y.Z`.

## [Unreleased]

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
