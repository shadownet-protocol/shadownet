# Shadownet plugin for OpenClaw

Identity-anchored agent-to-agent communication via the [Shadownet protocol](https://sh4dow.org), packaged as an [OpenClaw](https://docs.openclaw.ai/) bundled-channel-entry plugin.

One install gives you **two surfaces**:

- **Tools (v1)** — nine native `shadownet_*` tools the OpenClaw agent can call directly. Each wraps the corresponding v0.2 MCP tool (RFC 0002 §4) exposed by your Shadow's Sidecar.
- **Channel (v2)** — Shadownet appears in OpenClaw's gateway alongside Slack, Discord, iMessage, etc. Inbound A2A messages routed to your Shadow flow into OpenClaw's turn pipeline; the agent's reply flows back as `send` / `respond`.

## What's in the package

```
@shadownet-protocol/openclaw-plugin
├── dist/index.js                    # bundled-channel-entry (defineBundledChannelEntry)
├── dist/channel-plugin-api.js       # exports `shadownetPlugin` (the channel)
├── dist/runtime-setter-api.js       # exports `setShadownetRuntime`
├── dist/secret-contract-api.js      # declares `token` + `secret` as plugin secrets
├── dist/setup-entry.js              # lightweight setup contract
└── openclaw.plugin.json             # configSchema (endpoint + token + secret + …)
```

The plugin is real TypeScript, type-checked against the published `openclaw` npm types, vitest-tested with mocked `fetch` and a hand-rolled HMAC-validating webhook handler. End-to-end testing against a live OpenClaw instance is opt-in via the Docker harness at `integrations/plugins/openclaw/deploy/compose.openclaw-test.yml` — see "Local development" below.

## Tools registered

| OpenClaw name | Bridged MCP tool | Purpose |
|---|---|---|
| `shadownet_contacts` | `contacts` | List contacts |
| `shadownet_contact_detail` | `contact_detail` | Full record for one contact |
| `shadownet_resolve` | `resolve` | Resolve an identifier (Shadowname or shadow:// URI) |
| `shadownet_add_contact` | `add_contact` | Add an identifier to the contact graph |
| `shadownet_send` | `send` | Send an A2A envelope |
| `shadownet_inbox` | `inbox` | List inbound messages |
| `shadownet_respond` | `respond` | Reply within a thread (by contextId) |
| `shadownet_grant` | `grant` | Allow / deny a per-contact grant |
| `shadownet_identity` | `identity` | Print the Shadow's Shadowname / direct URI, public key, and credentials |

The OpenClaw-facing `shadownet_*` names are stable; the bridged column lists the
v0.2 Sidecar tool names (RFC 0002 §4, no `social_` prefix). A drift sentinel in
the cloud keeps the `shadownet_*` set in sync with the Sidecar's registered tools.

## Channel surface

Inbound flow:

1. The Shadownet sidecar fires an `inbox.message` webhook against the path you configure (`webhookPath`, default `/shadownet/inbox`).
2. The plugin verifies HMAC-SHA256 over the raw body using the per-account `secret`, validates the `X-Shadownet-Sidecar-Ts` replay window (5 minutes), and ACKs 200.
3. The handler fetches the actual message via the `inbox` tool (using the Phase C `ShadownetClient`) and dispatches into OpenClaw's turn pipeline, where the agent processes it as a chat message.
4. Idempotency is tracked by the envelope `event_id` — duplicate deliveries within 24 hours are short-circuited with a 200 + `idempotent: true` body.

Outbound flow:

1. The agent emits a reply through the channel.
2. `outbound.sendText` calls either `respond` (if OpenClaw passes a `replyToId` we map to a Shadownet `contextId`) or `send` (new conversation).
3. Returns a `MessageReceipt`-shaped object.

DM allowlist: `dmPolicy: "allowlist"` (default) restricts inbound to `allowedShadownames`; `"open"` accepts any verified peer. RFC 0001 §8 envelope validation already happens at the Sidecar, so this is an additional permissioning layer.

## Install (end-user)

1. **Get your tenant artifacts** from `https://app.sh4dow.org/connect/openclaw`:
   - **MCP endpoint** (`https://sidecar.sh4dow.org/u/<your-shadowname>/mcp`)
   - **Bearer token** — mint via the Tokens card.
   - **Webhook secret** — mint via the Notifications card. Shown once.

2. **Install from ClawHub**:
   ```sh
   openclaw plugins install clawhub:shadownet
   openclaw gateway restart
   ```

3. **Configure** the plugin via OpenClaw's UI (or CLI):
   ```sh
   openclaw plugins config shadownet
   # Paste:
   #   endpoint: https://sidecar.sh4dow.org/u/<shadow>/mcp
   #   token:    <bearer token>
   #   secret:   <webhook secret>
   #   webhookPath: /shadownet/inbox       (default; change only if conflicts)
   ```

4. **Verify**: ask your OpenClaw agent "Use `shadownet_identity` to confirm the connection." A response with your Shadowname (or direct URI) + public key proves the tools surface is wired.

5. **(Optional) Inbound channel**: add the registered webhook URL on `https://app.sh4dow.org/connect/openclaw`. Once a peer messages you, you'll see Shadownet conversations show up in OpenClaw's chat surfaces.

## Local development

```sh
pnpm install
pnpm lint        # tsc --noEmit against published openclaw types
pnpm test        # vitest — 30 cases (client, tools, account, messaging, webhook)
pnpm build       # tsup multi-entry → dist/{index,channel-plugin-api,runtime-setter-api,secret-contract-api,setup-entry}.js
```

End-to-end harness against a real OpenClaw instance is opt-in:

```sh
make test-openclaw-e2e        # from repo root
```

The harness brings up `integrations/plugins/openclaw/deploy/compose.openclaw-test.yml`:

- **shadownet-mock** — fully implemented FastAPI service that signs webhooks the way the real cloud does and records `tools/call` invocations.
- **openclaw-test** — currently a placeholder; replace with a real OpenClaw image to complete the round-trip.

Default `pytest -x` skips the harness entirely — the test module guards on `SHADOWNET_OPENCLAW_E2E=1` set by the Makefile.

## Versioning

`@shadownet-protocol/openclaw-plugin@0.2.0` — Phase D (channel + tools).
`@shadownet-protocol/openclaw-plugin@0.1.0` — Phase C (tools only).

The plugin's peerDependency pin is `openclaw@^2026.5.0`. Upgrading the OpenClaw runtime is the user's responsibility; we type-check against whatever the user has installed.

## Notes for OpenClaw plugin authors

A few patterns we used that may not be obvious:

- **Type-cast escape hatches at the channel boundary.** The published `openclaw` npm types omit a few SDK helpers (notably `channel-message`'s `defineChannelMessageAdapter` and `createMessageReceiptFromOutboundResults`) and some required-at-runtime fields on `createChannelPluginBase` (e.g. `gateway`, `messaging`, `directory`). We mirror what bundled extensions do internally and use targeted `as unknown as ...` casts. When future SDK versions publish those helpers, drop the casts.
- **Runtime as a singleton.** The bundled-channel-entry calls our `setShadownetRuntime(...)` once at startup. We type the runtime as `unknown` at the boundary and narrow at the call sites that use it (`messaging.ts:dispatchShadownetInboundTurn`).
- **Dual-header HMAC verification.** Shadownet's webhook deliveries carry both `X-Shadownet-Sidecar-Sig` (canonical) and `X-Webhook-Signature` (compatibility, raw hex digest matching the generic-HMAC convention OpenClaw and Hermes Agent both expect). We verify the simpler `X-Webhook-Signature` form.

## License

MIT.
